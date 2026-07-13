#!/usr/bin/env python3
"""
crypto-quant-desk — Quantitative Evaluator
===========================================
Heavy-lifting engine. Zero LLM dependencies.
Computes technical indicators, volatility clustering, cash-flow metrics,
and sentiment. Outputs a structured JSON payload.

Dependencies (install once via uv):
    uv pip install ccxt pandas pandas-ta numpy requests python-dotenv

Usage:
    python3 quant_evaluator.py --pair BTC/USDT --exchange binance --output /tmp/cqd_payload.json
    python3 quant_evaluator.py --pair ETH/USDT --exchange coinbase --output /tmp/cqd_payload.json
"""

import argparse
import json
import os
import sys
import datetime
import math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# ─── Project Root & Dynamic Paths ──────────────────────────────────────────────
# Resolve project root dynamically from this script's location
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent  # /opt/data/cqd-trading-bot/

# Ensure project root is in sys.path for module imports
sys.path.insert(0, str(PROJECT_ROOT))

# Dynamic path constants - all relative to PROJECT_ROOT
CONFIG_DIR = PROJECT_ROOT / "config"
STATE_DIR = PROJECT_ROOT / "state"
LOGS_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"

# Ensure directories exist
for d in (CONFIG_DIR, STATE_DIR, LOGS_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ─── cqd_logger (graceful import — never blocks execution) ───────────────────
try:
    from core.cqd_logger import cqd_logger
    LOGGER_AVAILABLE = True
except Exception:
    LOGGER_AVAILABLE = False
    cqd_logger = None  # type: ignore

# ─── Environment & Dependencies ────────────────────────────────────────────────
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(PROJECT_ROOT / ".env")

try:
    import ccxt
    import pandas as pd
    import numpy as np
    import pandas_ta as ta
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util import Retry
except ImportError as e:
    print(json.dumps({
        "error": f"Missing dependency: {e.name}",
        "fix": "uv pip install ccxt pandas pandas-ta numpy requests python-dotenv"
    }))
    sys.exit(1)


# ─── Configuration ────────────────────────────────────────────────────────────
DEFAULT_TIMEFRAME = "1h"
DEFAULT_LIMIT = 200          # candles to fetch
DEFAULT_LOOKBACK_DAYS = 30   # sentiment lookback
SENTIMENT_TIMEOUT = 10       # seconds
MACRO_CACHE_TTL_SECONDS = 7200  # 2 hours


# ─── Macro Cache I/O ───────────────────────────────────────────────────────────


def _read_macro_cache() -> dict | None:
    """Read macro_cache.json if it exists and is less than TTL seconds old."""
    cache_path = STATE_DIR / "macro_cache.json"
    if not cache_path.is_file():
        return None
    try:
        import time
        mtime = cache_path.stat().st_mtime
        if time.time() - mtime < MACRO_CACHE_TTL_SECONDS:
            data = json.loads(cache_path.read_text())
            return data
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        pass
    return None


def _write_macro_cache(data: dict) -> None:
    """Write macro data to macro_cache.json for future cache hits."""
    cache_path = STATE_DIR / "macro_cache.json"
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, indent=2, default=str))
    except (OSError, TypeError):
        pass  # Never block execution on cache write failure


# ─── Exchange ─────────────────────────────────────────────────────────────────

# Module-level exchange cache to avoid repeated HTTP session creation
_exchange_cache: dict[str, ccxt.Exchange] = {}


def _get_cached_exchange(exchange_id: str) -> ccxt.Exchange:
    """Get or create a cached exchange instance for connection reuse."""
    if exchange_id not in _exchange_cache:
        exchange_class = getattr(ccxt, exchange_id)
        if exchange_class is None:
            raise ValueError(f"Unknown exchange: {exchange_id}")
        _exchange_cache[exchange_id] = exchange_class({"enableRateLimit": True})
    return _exchange_cache[exchange_id]


def fetch_ohlcv(symbol: str, exchange_id: str = "binance",
                timeframe: str = DEFAULT_TIMEFRAME, limit: int = DEFAULT_LIMIT) -> pd.DataFrame:
    """Fetch OHLCV candles from ccxt exchange."""
    ex = _get_cached_exchange(exchange_id)
    ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


# ─── Indicators (pandas-ta) ───────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators and volatility metrics in-place."""

    # RSI
    rsi = ta.rsi(df["close"], length=14)
    if rsi is not None:
        df["rsi"] = rsi

    # MACD
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df.rename(columns={
            "MACD_12_26_9": "macd_line",
            "MACDh_12_26_9": "macd_histogram",
            "MACDs_12_26_9": "macd_signal"
        }, inplace=True)

    # EMA crossovers
    df["ema_9"] = ta.ema(df["close"], length=9)
    df["ema_21"] = ta.ema(df["close"], length=21)
    df["ema_50"] = ta.ema(df["close"], length=50)

    # Bollinger Bands
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None:
        df["bb_upper"] = bb.iloc[:, 0]
        df["bb_middle"] = bb.iloc[:, 1]
        df["bb_lower"] = bb.iloc[:, 2]
        df["bb_width"] = (bb.iloc[:, 0] - bb.iloc[:, 2]) / bb.iloc[:, 1]
        df["bb_position"] = (df["close"] - bb.iloc[:, 2]) / (bb.iloc[:, 0] - bb.iloc[:, 2])

    # ATR
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    if atr is not None:
        df["atr"] = atr
        df["atr_pct"] = atr / df["close"] * 100

    # Rolling volatility (20-period annualised std dev of returns)
    df["returns"] = df["close"].pct_change()
    df["volatility_20"] = df["returns"].rolling(20).std() * math.sqrt(365 * 24)

    # Volume SMA + ratio
    df["volume_sma_20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_sma_20"].replace(0, np.nan)

    # Money Flow Index
    mfi = ta.mfi(df["high"], df["low"], df["close"], df["volume"], length=14)
    if mfi is not None:
        df["mfi"] = mfi

    # Chaikin Money Flow
    cmf = ta.cmf(df["high"], df["low"], df["close"], df["volume"], length=20)
    if cmf is not None:
        df["cmf"] = cmf

    # Volume-Weighted Average Price
    df["vwap"] = (df["volume"] * ((df["high"] + df["low"] + df["close"]) / 3)).rolling(20).sum() / df["volume"].rolling(20).sum()

    # Cumulative Volume Delta (simplified proxy)
    df["cvd_delta"] = df["volume"] * (2 * (df["close"] >= df["open"]).astype(int) - 1)
    df["cvd"] = df["cvd_delta"].cumsum()

    return df


# ─── Volatility Clustering ──────────────────────────────────────────────────

def volatility_clustering(df: pd.DataFrame) -> dict:
    """Detect high-volatility regimes via rolling stats."""
    recent = df.tail(48)
    if len(recent) < 10:
        return {"regime": "insufficient_data", "cluster_score": 0}

    mean_vol = recent["volatility_20"].mean()
    current_vol = recent["volatility_20"].iloc[-1] if not recent["volatility_20"].empty else 0
    vol_percentile = (recent["volatility_20"].rank(pct=True).iloc[-1]
                      if not recent["volatility_20"].empty else 0.5)

    atr_ratio = 1.0
    if "atr_pct" in recent.columns and not recent["atr_pct"].empty:
        atr_mean = recent["atr_pct"].mean()
        atr_current = recent["atr_pct"].iloc[-1]
        atr_ratio = atr_current / atr_mean if atr_mean > 0 else 1.0

    bb_squeeze = 0
    if "bb_width" in recent.columns and not recent["bb_width"].empty:
        bb_width_mean = recent["bb_width"].mean()
        bb_width_current = recent["bb_width"].iloc[-1]
        bb_squeeze = 1 if bb_width_current < bb_width_mean * 0.7 else 0

    if vol_percentile > 0.85 and atr_ratio > 1.3:
        regime = "high_volatility_expansion"
    elif vol_percentile > 0.70:
        regime = "elevated_volatility"
    elif vol_percentile < 0.20 and bb_squeeze:
        regime = "low_volatility_squeeze"
    elif vol_percentile < 0.30:
        regime = "low_volatility"
    else:
        regime = "normal"

    return {
        "regime": regime,
        "current_volatility": round(float(current_vol), 6),
        "mean_volatility": round(float(mean_vol), 6),
        "vol_percentile": round(float(vol_percentile), 4),
        "atr_expansion_ratio": round(float(atr_ratio), 4),
        "bb_squeeze_detected": bb_squeeze
    }


# ─── Cash Flow / Volume Profile ─────────────────────────────────────────────

def cashflow_analysis(df: pd.DataFrame) -> dict:
    """Analyze buying vs selling pressure and cash flow."""
    recent = df.tail(48)
    if len(recent) < 5:
        return {"net_flow_bias": "neutral", "flow_strength": 0}

    cvd_trend = "neutral"
    if "cvd" in recent.columns and not recent["cvd"].empty:
        cvd_change = recent["cvd"].iloc[-1] - recent["cvd"].iloc[0]
        cvd_trend = "bullish" if cvd_change > 0 else "bearish" if cvd_change < 0 else "neutral"

    cmf_signal = 0
    if "cmf" in recent.columns and not recent["cmf"].empty:
        cmf_signal = recent["cmf"].iloc[-1]

    mfi_signal = 50
    if "mfi" in recent.columns and not recent["mfi"].empty:
        mfi_signal = recent["mfi"].iloc[-1]

    vwap_bias = 0
    if "vwap" in recent.columns and not recent["vwap"].empty:
        last_close = recent["close"].iloc[-1]
        last_vwap = recent["vwap"].iloc[-1]
        vwap_bias = (last_close - last_vwap) / last_vwap * 100 if last_vwap > 0 else 0

    return {
        "cvd_trend": cvd_trend,
        "chaikin_money_flow": round(float(cmf_signal), 6),
        "money_flow_index": round(float(mfi_signal), 2),
        "vwap_deviation_pct": round(float(vwap_bias), 4),
        "volume_spike": bool(recent["volume_ratio"].iloc[-1] > 1.5) if "volume_ratio" in recent.columns else False
    }


# ─── Sentiment / Macro APIs ─────────────────────────────────────────────────

def _build_session() -> requests.Session:
    """Construct a requests.Session with a robust HTTP retry strategy.

    Retries transient failures (429 rate-limit + 5xx gateway errors) up to
    2 times with an exponential backoff, mounted on both http:// and https://.
    """
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _fetch_fear_greed(session: requests.Session) -> dict | None:
    """Fetch the alternative.me Fear & Greed Index.

    Returns a partial result dict on success, or None if the fetch fails
    (connection error, timeout, or non-200 / malformed response).
    """
    try:
        resp = session.get(
            "https://api.alternative.me/fng/?limit=1&format=json",
            timeout=SENTIMENT_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and "data" in data and len(data["data"]) > 0:
                fg = data["data"][0]
                return {
                    "fear_greed_index": int(fg.get("value", 50)),
                    "fear_greed_label": fg.get("value_classification", "Neutral").lower(),
                    "source": "alternative_me_fng",
                }
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    return None


def _fetch_global_metrics(session: requests.Session) -> dict | None:
    """Fetch global market cap percentages via CoinGecko.

    CoinMarketCap's public v3 API was retired (now 404). CoinGecko's /global
    endpoint returns market_cap_percentage which includes BTC dominance directly.

    Returns a partial result dict on success, or None if the fetch fails
    (connection error, timeout, or non-200 / malformed response).
    """
    try:
        resp = session.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=SENTIMENT_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            mcp = data.get("data", {}).get("market_cap_percentage", {})
            btc_dom = mcp.get("btc")
            if btc_dom is not None:
                return {
                    "btc_dominance": round(float(btc_dom), 2),
                    "total_market_cap": data.get("data", {}).get("total_market_cap", {}).get("usd", 0),
                    "source": "coingecko_global",
                }
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    return None


def fetch_sentiment() -> dict:
    """Fetch Fear & Greed Index and global market metrics concurrently.

    Both HTTP requests fire in parallel via a ThreadPoolExecutor over a shared
    retry-hardened session. Each fetch fails gracefully (returns None) without
    aborting the other; successful results are merged cleanly into the payload.

    MACRO CACHE:
    Before firing HTTP requests, reads PROJECT_ROOT/state/macro_cache.json.
    If cache is fresh (< 2 hours), returns cached data. Otherwise, fetches live
    and writes results back to cache for subsequent runs.
    """
    # ── Check cache first ─────────────────────────────────────────────────────
    cached = _read_macro_cache()
    if cached:
        return {
            "fear_greed_index": cached.get("fear_greed_index"),
            "fear_greed_label": cached.get("fear_greed_label", "unknown"),
            "macro_pulse": cached.get("macro_pulse", "neutral"),
            "btc_dominance": cached.get("btc_dominance"),
            "total_market_cap": cached.get("total_market_cap"),
            "sources_reached": cached.get("sources_reached", ["cache_hit"]),
        }

    result = {
        "fear_greed_index": None,
        "fear_greed_label": "unknown",
        "macro_pulse": "neutral",
        "sources_reached": []
    }

    session = _build_session()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_fng = executor.submit(_fetch_fear_greed, session)
            future_global = executor.submit(_fetch_global_metrics, session)
            fng_data = future_fng.result()
            global_data = future_global.result()
    finally:
        session.close()

    # Merge Fear & Greed result
    if fng_data:
        result["fear_greed_index"] = fng_data["fear_greed_index"]
        result["fear_greed_label"] = fng_data["fear_greed_label"]
        result["sources_reached"].append(fng_data["source"])

    # Merge CoinMarketCap global metrics result
    if global_data:
        result["btc_dominance"] = global_data["btc_dominance"]
        result["total_market_cap"] = global_data["total_market_cap"]
        result["sources_reached"].append(global_data["source"])

    # Macro pulse from fear & greed
    fg = result.get("fear_greed_index")
    if fg is not None:
        if fg <= 25:
            result["macro_pulse"] = "extreme_fear"
        elif fg <= 45:
            result["macro_pulse"] = "fear"
        elif fg <= 55:
            result["macro_pulse"] = "neutral"
        elif fg <= 75:
            result["macro_pulse"] = "greed"
        else:
            result["macro_pulse"] = "extreme_greed"

    # ── Write cache for future runs ───────────────────────────────────────
    cache_write_data = {
        "fear_greed_index": result.get("fear_greed_index"),
        "fear_greed_label": result.get("fear_greed_label"),
        "macro_pulse": result.get("macro_pulse"),
        "btc_dominance": result.get("btc_dominance"),
        "total_market_cap": result.get("total_market_cap"),
        "sources_reached": result.get("sources_reached"),
    }
    _write_macro_cache(cache_write_data)

    return result


# ─── Config Loader ───────────────────────────────────────────────────────────

def load_config(config_path: Path | None = None) -> dict:
    """Load config.json from the project's config directory.

    Falls back to hardcoded defaults if the file is absent or corrupt.
    This keeps capital guardrails external and human-editable without
    touching the Python layer.
    """
    DEFAULTS = {
        "global_max_open_positions": 5,
        "min_position_size_usdt": 50.0,
        "max_position_size_usdt": 500.0,
        "default_position_size_pct": 2.5,
        "atr_stop_loss_multiplier": 1.5,
        "atr_take_profit_multiplier": 3.0,
        "trailing_stop_activation_multiplier": 2.0,
        "trailing_stop_distance_multiplier": 0.5,
    }
    if config_path is None:
        config_path = CONFIG_DIR / "config.json"
    try:
        if config_path.is_file():
            data = json.loads(config_path.read_text())
            return {**DEFAULTS, **data.get("sandbox_rules", {})}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        pass
    return DEFAULTS


# ─── ATR-Based Trade Parameters ──────────────────────────────────────────────

def compute_trade_parameters(
    df: pd.DataFrame,
    config: dict,
    signal_direction: str,
    wallet_balance: float = 10_000.0,
) -> dict:
    """Compute entry, SL, TP, and position size using live ATR.

    Formulas (all pct values are expressed as a fraction of last_price
    for the sandbox engine's price-level calculation):

      atr_abs         = current ATR in price units
      atr_pct         = atr_abs / last_price  (already in df as atr_pct)

      stop_loss_pct   = (atr_stop_loss_multiplier * atr_abs) / last_price * 100
      take_profit_pct = (atr_take_profit_multiplier * atr_abs) / last_price * 100

      raw_size        = wallet_balance * (default_position_size_pct / 100)
      position_size   = clamp(raw_size, min, max)

    Returns a trade_parameters dict ready to drop into the LLM payload.
    """
    last = df.iloc[-1] if not df.empty else {}
    last_price = float(last.get("close", 0)) if not last.empty else 0.0
    atr_abs = float(last.get("atr")) if not last.empty and not pd.isna(last.get("atr")) else 0.0

    if last_price <= 0 or atr_abs <= 0:
        return {
            "pair": None,
            "direction": "neutral",
            "entry_strategy": "wait_and_observe",
            "stop_loss_pct": None,
            "take_profit_pct": None,
            "position_size_usdt": 0.0,
            "atr_value": None,
            "atr_stop_multiplier": config.get("atr_stop_loss_multiplier", 1.5),
            "atr_tp_multiplier": config.get("atr_take_profit_multiplier", 3.0),
        }

    # ATR fractions
    sl_mult = config.get("atr_stop_loss_multiplier", 1.5)
    tp_mult = config.get("atr_take_profit_multiplier", 3.0)
    stop_loss_pct = round((sl_mult * atr_abs) / last_price * 100, 4)
    take_profit_pct = round((tp_mult * atr_abs) / last_price * 100, 4)

    # Position sizing
    raw_size = wallet_balance * (config.get("default_position_size_pct", 2.5) / 100.0)
    position_size = round(
        max(
            config.get("min_position_size_usdt", 50.0),
            min(config.get("max_position_size_usdt", 500.0), raw_size),
        ),
        2,
    )
    # Expose risk_pct so sandbox_engine can derive size independently if needed
    risk_pct = config.get("default_position_size_pct", 2.5)

    # Entry strategy label
    if signal_direction == "long":
        entry_strategy = "limit_buy_bb_lower"  # pullback entry near EMA/BB lower
    elif signal_direction == "short":
        entry_strategy = "limit_sell_bb_upper"
    else:
        entry_strategy = "wait_and_observe"

    return {
        "pair": str(df.name) if hasattr(df, "name") else None,
        "direction": signal_direction,
        "entry_strategy": entry_strategy,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "position_size_usdt": position_size,
        "max_risk_capital_pct": risk_pct,
        "atr_value": round(atr_abs, 4),
        "atr_stop_multiplier": sl_mult,
        "atr_tp_multiplier": tp_mult,
    }


# ─── Signal Generation ──────────────────────────────────────────────────────

def generate_signal(df: pd.DataFrame, volatility: dict,
                    cashflow: dict, sentiment: dict) -> dict:
    """
    Rule-based signal generation. Returns structured signal with
    direction (long/short/neutral), conviction_score (1-10), and key_metrics.
    """
    last = df.iloc[-1] if not df.empty else {}
    if last.empty:
        return {"signal_direction": "neutral", "conviction_score": 1,
                "key_metrics": {}, "sentiment_summary": sentiment}

    score = 5
    reasons = []
    direction = "neutral"

    # ── RSI ──
    rsi = last.get("rsi", 50)
    if isinstance(rsi, (int, float)) and not np.isnan(rsi):
        if rsi < 30:
            score += 2
            reasons.append(f"oversold_rsi({rsi:.1f})")
        elif rsi < 40:
            score += 1
            reasons.append(f"near_oversold_rsi({rsi:.1f})")
        elif rsi > 70:
            score -= 2
            reasons.append(f"overbought_rsi({rsi:.1f})")
        elif rsi > 60:
            score -= 1
            reasons.append(f"near_overbought_rsi({rsi:.1f})")

    # ── MACD ──
    macd_hist = last.get("macd_histogram", 0)
    if isinstance(macd_hist, (int, float)) and not np.isnan(macd_hist):
        if macd_hist > 0:
            score += 1
            reasons.append(f"macd_positive({macd_hist:.2f})")
        else:
            score -= 1
            reasons.append(f"macd_negative({macd_hist:.2f})")

    # ── Bollinger Position ──
    bb_pos = last.get("bb_position", 0.5)
    if isinstance(bb_pos, (int, float)) and not np.isnan(bb_pos):
        if bb_pos < 0.05:
            score += 2
            reasons.append("bb_lower_band_touch")
        elif bb_pos < 0.2:
            score += 1
            reasons.append("bb_near_lower")
        elif bb_pos > 0.95:
            score -= 2
            reasons.append("bb_upper_band_touch")
        elif bb_pos > 0.8:
            score -= 1
            reasons.append("bb_near_upper")

    # ── Volume Spike ──
    vol_ratio = last.get("volume_ratio", 1)
    if isinstance(vol_ratio, (int, float)) and not np.isnan(vol_ratio):
        if vol_ratio > 2.0:
            score += 1 if score >= 5 else -1
            reasons.append(f"volume_spike({vol_ratio:.1f}x)")

    # ── EMA Crossovers ──
    ema_9 = last.get("ema_9")
    ema_21 = last.get("ema_21")
    ema_50 = last.get("ema_50")
    close = last.get("close")

    if all(isinstance(v, (int, float)) and not np.isnan(v) for v in [close, ema_9, ema_21]):
        if close > ema_9 > ema_21:
            score += 1
            reasons.append("ema_bullish_alignment")
        elif close < ema_9 < ema_21:
            score -= 1
            reasons.append("ema_bearish_alignment")

    if all(isinstance(v, (int, float)) and not np.isnan(v) for v in [close, ema_50]):
        if close > ema_50:
            score += 0.5
            reasons.append("above_ema50")
        else:
            score -= 0.5
            reasons.append("below_ema50")

    # ── Volatility Regime ──
    regime = volatility.get("regime", "normal")
    if regime == "low_volatility_squeeze":
        score += 1
        reasons.append("vol_squeeze_breakout_setup")
    elif regime == "high_volatility_expansion":
        score -= 1
        reasons.append("high_vol_expansion_risk")
    elif regime == "elevated_volatility":
        score -= 0.5
        reasons.append("elevated_vol")

    # ── Cash Flow ──
    cvd_trend = cashflow.get("cvd_trend", "neutral")
    if cvd_trend == "bullish":
        score += 1
        reasons.append("cvd_bullish")
    elif cvd_trend == "bearish":
        score -= 1
        reasons.append("cvd_bearish")

    cmf = cashflow.get("chaikin_money_flow", 0)
    if isinstance(cmf, (int, float)):
        if cmf > 0.05:
            score += 0.5
            reasons.append("cmf_positive")
        elif cmf < -0.05:
            score -= 0.5
            reasons.append("cmf_negative")

    mfi = cashflow.get("money_flow_index", 50)
    if isinstance(mfi, (int, float)):
        if mfi < 20:
            score += 1
            reasons.append("mfi_oversold")
        elif mfi > 80:
            score -= 1
            reasons.append("mfi_overbought")

    # ── Sentiment ──
    fg = sentiment.get("fear_greed_index")
    if fg is not None:
        if fg <= 20:
            score += 1.5
            reasons.append("extreme_fear_contrarian_buy")
        elif fg >= 80:
            score -= 1.5
            reasons.append("extreme_greed_caution")

    # ── Clamp & Classify ──
    score = max(1, min(10, round(score)))
    if score >= 7:
        direction = "long"
    elif score >= 5:
        direction = "neutral"
    elif score >= 3:
        direction = "neutral_short_bias"
    else:
        direction = "short"

    return {
        "signal_direction": direction,
        "conviction_score": score,
        "key_metrics": {
            "rsi": round(float(rsi), 2) if isinstance(rsi, (int, float)) and not np.isnan(rsi) else None,
            "macd_histogram": round(float(macd_hist), 6) if isinstance(macd_hist, (int, float)) and not np.isnan(macd_hist) else None,
            "bb_position": round(float(bb_pos), 4) if isinstance(bb_pos, (int, float)) and not np.isnan(bb_pos) else None,
            "volume_ratio": round(float(vol_ratio), 2) if isinstance(vol_ratio, (int, float)) and not np.isnan(vol_ratio) else None,
            "mfi": round(float(mfi), 2),
            "cmf": round(float(cmf), 6),
            "atr_pct": round(float(last.get("atr_pct", 0)), 4) if isinstance(last.get("atr_pct"), (int, float)) and not np.isnan(last.get("atr_pct", 0)) else None,
            "volatility_regime": regime,
            "vwap_deviation": cashflow.get("vwap_deviation_pct")
        },
        "aggregate_reasons": "; ".join(reasons),
        "sentiment_summary": {
            "fear_greed_index": sentiment.get("fear_greed_index"),
            "fear_greed_label": sentiment.get("fear_greed_label"),
            "macro_pulse": sentiment.get("macro_pulse"),
            "btc_dominance": sentiment.get("btc_dominance"),
            "total_market_cap": sentiment.get("total_market_cap")
        }
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Crypto Quant Evaluator")
    parser.add_argument("--pair", default="BTC/USDT", help="Trading pair (default: BTC/USDT)")
    parser.add_argument("--exchange", default="binance", help="CCXT exchange ID (default: binance)")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME, help="Candle timeframe (default: 1h)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Number of candles (default: 200)")
    parser.add_argument("--output", default="/tmp/cqd_payload.json", help="Output JSON path")
    args = parser.parse_args()

    # ── Concurrent-run guard (pid-lock) ─────────────────────────────────────
    # Prevents duplicate rows when cron fires while a previous tick is still
    # running.  The lock is held for the entire evaluator run and released on
    # exit (normal or exception).  Uses flock(2) for true advisory locking.
    import fcntl

    LOCK_FILE = Path("/tmp/cqd_evaluator.lock")

    lock_fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(lock_fd, f"{os.getpid()}".encode())
    except (IOError, OSError):
        # Another instance holds the lock — exit silently
        os.close(lock_fd)
        sys.exit(0)

    # Lock is now held.  Release it on any exit from this invocation.
    import atexit
    atexit.register(lambda: (fcntl.flock(lock_fd, fcntl.LOCK_UN), os.close(lock_fd)))
    try:
        df = fetch_ohlcv(args.pair, args.exchange, args.timeframe, args.limit)
    except Exception as e:
        output = {"error": f"OHLCV fetch failed: {e}", "pair": args.pair, "exchange": args.exchange}
        Path(args.output).write_text(json.dumps(output, indent=2))
        if LOGGER_AVAILABLE and cqd_logger:
            cqd_logger.log_error("EVALUATOR", args.pair, e,
                                 details="OHLCV fetch failed")
        print(json.dumps(output))
        sys.exit(1)

    # 2. Compute indicators
    df = compute_indicators(df)

    # 3. Volatility clustering
    vol = volatility_clustering(df)

    # 4. Cash flow analysis
    cf = cashflow_analysis(df)

    # 5. Sentiment / macro
    sent = {}
    try:
        sent = fetch_sentiment()
    except Exception as e:
        if LOGGER_AVAILABLE and cqd_logger:
            cqd_logger.log_error("EVALUATOR", args.pair, e,
                                 details="fetch_sentiment failed")
        sent = {}

    # 6. Generate signal
    signal = {}
    try:
        signal = generate_signal(df, vol, cf, sent)
    except Exception as e:
        if LOGGER_AVAILABLE and cqd_logger:
            cqd_logger.log_error("EVALUATOR", args.pair, e,
                                 details="generate_signal failed")
        signal = {"signal_direction": "neutral", "conviction_score": 0,
                  "key_metrics": {}, "aggregate_reasons": ""}

    # 7. Load sandbox rules + current wallet balance
    config = load_config()
    wallet_path = STATE_DIR / "wallet_state.json"
    try:
        wallet = json.loads(wallet_path.read_text())
        wallet_balance = wallet.get("balance_usdt", 10_000.0)
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        wallet_balance = 10_000.0

    # 8. Compute ATR-based trade parameters
    trade_params = compute_trade_parameters(
        df,
        config,
        signal["signal_direction"],
        wallet_balance=wallet_balance,
    )
    # Pair is always the requested ticker
    trade_params["pair"] = args.pair

    # 9. Build final payload
    payload = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "pair": args.pair,
        "exchange": args.exchange,
        "timeframe": args.timeframe,
        "candles_analyzed": len(df),
        "last_price": float(df["close"].iloc[-1]) if not df.empty else None,
        "signal_direction": signal["signal_direction"],
        "conviction_score": signal["conviction_score"],
        "key_metrics": signal["key_metrics"],
        "volatility_cluster": vol,
        "cashflow": cf,
        "sentiment_summary": signal["sentiment_summary"],
        "aggregate_reasons": signal["aggregate_reasons"],
        "trade_parameters": trade_params,
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(payload, indent=2, default=str))

    # ── SCAN event log (zero-token, always runs after payload is written) ──
    if LOGGER_AVAILABLE and cqd_logger:
        conviction = str(signal.get("conviction_score", "") or "")
        sent_sum   = signal.get("sentiment_summary", {})

        # FGI: prefer nested sentiment_summary (v2), fall back to top-level (v1)
        fgi_val = (
            str(sent_sum["fear_greed_index"])
            if sent_sum.get("fear_greed_index") is not None else
            str(sent.get("fear_greed_index", ""))
        )
        # BTC dominance: nested sentiment_summary > top-level fallback
        btc_dom = (
            str(sent_sum["btc_dominance"])
            if sent_sum.get("btc_dominance") is not None else
            str(sent.get("btc_dominance", ""))
        )

        last_close = float(df["close"].iloc[-1]) if not df.empty else 0.0
        details = (
            f"dir={signal.get('signal_direction','?')} "
            f"reasons={signal.get('aggregate_reasons','')} "
            f"price={last_close:.4f}"
        )
        cqd_logger.log_scan(
            pair=str(args.pair),
            conviction=conviction,
            fgi=fgi_val,
            btc_dom=btc_dom,
            details=details,
        )

    print(json.dumps(payload))


if __name__ == "__main__":
    main()