#!/usr/bin/env python3
"""
crypto-quant-desk — Dynamic Watchlist Rotator
===============================================
Executes daily at 04:00 UTC.
Keeps 5 fixed core pairs: BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, ADA/USDT.
Dynamically screen-ranks and rotates another 5 satellite pairs from Binance
based on:
  1. Liquidity (24h volume)
  2. Volatility (24h high-low range percentile)
  3. Directional Sentiment (recent momentum & price trend)

Writes the active 10-pair list to a local JSON configuration.
"""

import argparse
import json
import math
import sys
from pathlib import Path

# ─── Project Root & Dynamic Paths ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent  # /opt/data/cqd-trading-bot/

# Ensure project root is in sys.path for module imports
sys.path.insert(0, str(PROJECT_ROOT))

# ─── Environment & Dependencies ────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

try:
    import ccxt
    import requests
except ImportError as e:
    print(json.dumps({
        "error": f"Missing dependency: {e.name}",
        "fix": "Ensure dependencies are installed in your crypto-quant-desk environment"
    }))
    sys.exit(1)

# Configuration
CORE_PAIRS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "ADA/USDT"]
STABLECOIN_KEYWORDS = ["USD", "EUR", "GBP", "DAI", "BUSD", "FDUSD", "TUSD", "USDC", "USDP", "AEUR", "LD", "DOWN", "UP", "BULL", "BEAR"]


def is_valid_candidate(symbol: str) -> bool:
    """Filter out stablecoins, pegged tokens, leveraged tokens, and non-USDT pairs."""
    if not symbol.endswith("/USDT"):
        return False
    if symbol in CORE_PAIRS:
        return False

    base = symbol.split("/")[0].upper()
    # Skip stablecoins, leveraged pairs and weird pegs
    for kw in STABLECOIN_KEYWORDS:
        if kw in base or base.endswith(kw):
            return False
    return True


def fetch_binance_candidates() -> list:
    """Fetch 24h tickers for all USDT pairs on Binance."""
    exchange = ccxt.binance({"enableRateLimit": True})
    tickers = exchange.fetch_tickers()

    candidates = []
    for symbol, ticker in tickers.items():
        if not is_valid_candidate(symbol):
            continue

        # Get metrics
        vol_usd = ticker.get("quoteVolume", 0) or 0
        high = ticker.get("high", 0) or 0
        low = ticker.get("low", 0) or 0
        close = ticker.get("close", 0) or 0
        change_pct = ticker.get("percentage", 0) or 0

        if vol_usd < 1_000_000 or close <= 0:  # Minimum $1M daily volume threshold
            continue

        # Volatility metric: 24h percentage range width
        range_pct = ((high - low) / close) * 100 if close > 0 else 0

        candidates.append({
            "symbol": symbol,
            "volume": vol_usd,
            "volatility": range_pct,
            "change_pct": change_pct
        })

    return candidates


def fetch_macro_fng() -> int:
    """Fetch macro sentiment (Fear & Greed Index) as a baseline bias."""
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1&format=json", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data and "data" in data and len(data["data"]) > 0:
                return int(data["data"][0].get("value", 50))
    except Exception:
        pass
    return 50


def score_assets(candidates: list, fng_index: int) -> list:
    """
    Score assets based on liquidity, volatility, and sentiment/momentum alignment.
    - Liquidity score: proportional to log of volume (stabilizes high volumes).
    - Volatility score: proportional to 24-hour high-low percentage range.
    - Sentiment scoring behavior:
      - If extreme fear (FnG <= 30): penalty on highly negative momentum (avoid falling knives),
        reward moderate positive breakouts or oversold stabilizing charts.
      - If neutral/greed (FnG > 30): reward high positive momentum (trend-following / breakout continuation).
    """
    scored = []

    # Extract min/max values for normalizing
    volumes = [c["volume"] for c in candidates]
    volatilities = [c["volatility"] for c in candidates]

    if not volumes or not volatilities:
        return []

    min_log_v = math.log10(min(volumes))
    max_log_v = math.log10(max(volumes))
    v_diff = (max_log_v - min_log_v) or 1

    min_volat = min(volatilities)
    max_volat = max(volatilities)
    volat_diff = (max_volat - min_volat) or 1

    for c in candidates:
        # Normalized Log Volume Score (0 to 1)
        log_v = math.log10(c["volume"])
        norm_volume = (log_v - min_log_v) / v_diff

        # Normalized Volatility Score (0 to 1)
        norm_volat = (c["volatility"] - min_volat) / volat_diff

        # Sentiment/Momentum Adjustment (0 to 1)
        # We classify momentum using 24h percent reward. High momentum is favored when market is not panicking.
        norm_momentum = 0.5 + (max(-50, min(50, c["change_pct"])) / 100.0)  # centered around 0.5

        if fng_index <= 30:
            # Extreme Fear: Look for strong but stable volume, highly volatile assets with slightly positive or neutral daily change.
            # Avoid high-negative momentum but do not chase high-greed spikes.
            momentum_score = 1.0 - abs(c["change_pct"]) / 100.0  # favor zero/stable momentum
            score = (norm_volume * 0.40) + (norm_volat * 0.40) + (momentum_score * 0.20)
        else:
            # Greed/Neutral: Prioritize volume leaders with strong upward breakout momentum.
            score = (norm_volume * 0.35) + (norm_volat * 0.35) + (norm_momentum * 0.30)

        c["composite_score"] = round(score, 4)
        scored.append(c)

    # Sort descending by composite score
    scored_sorted = sorted(scored, key=lambda x: x["composite_score"], reverse=True)
    return scored_sorted


def main():
    parser = argparse.ArgumentParser(description="Watchlist Rotator")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "config" / "watchlist.json"),
                        help="Path to output active watchlist JSON file")
    args = parser.parse_args()

    print("[CQD-ROTATOR] Executing Daily Watchlist Rotation...")

    try:
        candidates = fetch_binance_candidates()
        print(f"[CQD-ROTATOR] Found {len(candidates)} valid high-volume candidate assets.")
    except Exception as e:
        print(f"[CQD-ROTATOR] ERROR fetching candidates: {e}")
        sys.exit(1)

    fng_index = fetch_macro_fng()
    print(f"[CQD-ROTATOR] Current Macro Fear & Greed Index: {fng_index}")

    scored_candidates = score_assets(candidates, fng_index)

    selected_satellites = [item["symbol"] for item in scored_candidates[:5]]
    final_watchlist = CORE_PAIRS + selected_satellites

    print("\n=== SYSTEM SELECTION SCORE DETAILS ===")
    for item in scored_candidates[:5]:
        print(f"Asset: {item['symbol']} | Score: {item['composite_score']} | Vol: ${item['volume']:,.0f} | 24h Range: {item['volatility']:.2f}% | 24h Change: {item['change_pct']}%")

    print("\n--- FINAL CONSOLIDATED 10-PAIR WATCHLIST ---")
    for i, symbol in enumerate(final_watchlist, 1):
        core_or_sat = "CORE" if i <= 5 else "SATELLITE"
        print(f"{i:02d}. {symbol:<12} [{core_or_sat}]")

    # Save watchlist
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(final_watchlist, indent=2))
    print(f"\n[CQD-ROTATOR] Active 10-pair watch list successfully written to {output_path}")


if __name__ == "__main__":
    main()