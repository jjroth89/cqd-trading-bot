#!/usr/bin/env python3
"""
crypto-quant-desk — Sandbox Paper-Trading Engine
==================================================
Two modes:
  --execute <payload.json>   Ingests a Hermes EXECUTE payload, sizes the
                              position, deducts margin, and opens the trade.
  --monitor                   Checks all open positions against live prices
                              and closes any that hit SL or TP.

State persisted in PROJECT_ROOT/state/wallet_state.json
Audit trail in PROJECT_ROOT/state/tg_sent_log.csv
Master log in PROJECT_ROOT/logs/cqd_master_log.csv

Canonical paths: PROJECT_ROOT/
"""

import json
import sys
import os
import argparse
import traceback
import fcntl
import atexit
import threading
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

try:
    import ccxt
    import requests
except ImportError as e:
    sys.exit(1)

# ─── Project Root & Dynamic Paths ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent  # /opt/data/cqd-trading-bot/

# Ensure project root is in sys.path for module imports
sys.path.insert(0, str(PROJECT_ROOT))

# Dynamic path constants - all relative to PROJECT_ROOT
WALLET_FILE = str(PROJECT_ROOT / "state" / "wallet_state.json")
WALLET_LOCK_FILE = str(PROJECT_ROOT / "state" / "wallet_state.lock")
TG_SENT_LOG = str(PROJECT_ROOT / "state" / "tg_sent_log.csv")
MASTER_LOG = str(PROJECT_ROOT / "logs" / "cqd_master_log.csv")
CONFIG_PATH = str(PROJECT_ROOT / "config" / "config.json")
MONITOR_LOCK = str(PROJECT_ROOT / "state" / "cqd_monitor.lock")
CORE_DIR = str(PROJECT_ROOT / "core")

# Ensure directories exist
for p in [PROJECT_ROOT / "state", PROJECT_ROOT / "logs", PROJECT_ROOT / "config"]:
    p.mkdir(parents=True, exist_ok=True)

# ─── Environment Loading ───────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# ─── FAIL-FAST SECURITY GUARDRAIL ─────────────────────────────────────────────
# Halt immediately if the global Hermes Telegram credential leaks into this
# process at import time.  The CQD bot is strictly isolated — only its own
# CQD_TG_BOT_TOKEN and CQD_TG_CHAT_ID tokens are permitted.
if os.getenv("TG_BOT_TOKEN") is not None or os.getenv("TG_CHAT_ID") is not None:
    raise RuntimeError(
        "SECURITY VIOLATION: TG_BOT_TOKEN or TG_CHAT_ID detected at import time. "
        "CQD bot requires strict isolation — only CQD_TG_BOT_TOKEN and CQD_TG_CHAT_ID "
        "are permitted. Halted to prevent credential leakage."
    )

# ─── cqd_logger (graceful import — never blocks execution) ──────────────────
LOGGER_AVAILABLE = False
cqd_logger = None
try:
    from core.cqd_logger import cqd_logger
    LOGGER_AVAILABLE = True
except Exception:
    pass


# ─── Thread-Safe File Locking with Reentrant Support ───────────────────────────

# Module-level lock state for reentrant locking within the same thread
_lock_state = threading.local()

def _get_lock_state():
    """Get or initialize thread-local lock state."""
    if not hasattr(_lock_state, 'lock_count'):
        _lock_state.lock_count = 0
        _lock_state.lock_fd = None
    return _lock_state


@contextmanager
def wallet_lock(exclusive: bool = True):
    """
    Context manager for wallet_state.json file locking using fcntl.flock.
    
    Supports reentrant locking within the same thread - if the same thread
    already holds the lock, it just increments a counter instead of blocking.
    
    Args:
        exclusive: If True, acquire exclusive lock (LOCK_EX) for writes.
                   If False, acquire shared lock (LOCK_SH) for reads.
                   Note: Using exclusive for both to ensure consistency.
    """
    state = _get_lock_state()
    
    # If we already hold the lock in this thread, just increment counter
    if state.lock_count > 0 and state.lock_fd is not None:
        state.lock_count += 1
        try:
            yield
        finally:
            state.lock_count -= 1
        return
    
    # First time acquiring - open lock file and acquire
    Path(WALLET_LOCK_FILE).touch(exist_ok=True)
    lock_fd = os.open(WALLET_LOCK_FILE, os.O_RDWR)
    state.lock_fd = lock_fd
    state.lock_count = 1
    
    try:
        lock_op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock_fd, lock_op)
        yield
    finally:
        state.lock_count -= 1
        # Only release when count reaches zero
        if state.lock_count == 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                os.close(lock_fd)
            except Exception:
                pass
            state.lock_fd = None


def _atomic_write_wallet(wallet: dict) -> None:
    """
    Atomically write wallet state to disk using a temporary file + rename.
    This prevents corruption if the process crashes mid-write.
    """
    # Write to temp file first
    temp_file = WALLET_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(wallet, f, indent=2)
    # Atomic rename (POSIX guarantee)
    os.replace(temp_file, WALLET_FILE)


# ─── State I/O (with file locking) ────────────────────────────────────────────

def load_wallet() -> dict:
    """Load wallet state from disk with file locking."""
    with wallet_lock(exclusive=False):
        if not os.path.exists(WALLET_FILE):
            return {"balance_usdt": 10000.0, "open_positions": {}, "trade_history": []}
        try:
            with open(WALLET_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            # Corrupted or unreadable file - return default
            return {"balance_usdt": 10000.0, "open_positions": {}, "trade_history": []}


def save_wallet(wallet: dict) -> None:
    """Save wallet state to disk with file locking and atomic write."""
    with wallet_lock(exclusive=True):
        _atomic_write_wallet(wallet)


def modify_wallet(updater) -> dict:
    """
    Atomic read-modify-write transaction on wallet state.

    Args:
        updater: A callable that receives the current wallet dict and returns
                 a (modified_wallet, should_save) tuple. If should_save is False,
                 no write occurs. This lets callers bail out early without a race.

    Returns:
        The final wallet dict (post-updater), whether or not it was saved.

    Holds the exclusive lock for the ENTIRE read-modify-write cycle to prevent
    lost updates when multiple threads/processes attempt concurrent modifications.
    """
    with wallet_lock(exclusive=True):
        # Load current state
        if os.path.exists(WALLET_FILE):
            try:
                with open(WALLET_FILE, "r") as f:
                    wallet = json.load(f)
            except (json.JSONDecodeError, OSError):
                wallet = {"balance_usdt": 10000.0, "open_positions": {}, "trade_history": []}
        else:
            wallet = {"balance_usdt": 10000.0, "open_positions": {}, "trade_history": []}

        # Apply modifications
        wallet, should_save = updater(wallet)

        # Persist only if updater signalled a change
        if should_save:
            _atomic_write_wallet(wallet)

        return wallet


# ─── Telegram Notification ─────────────────────────────────────────────────────

def _load_telegram_creds() -> tuple[str | None, str | None]:
    # Environment variables are loaded from .env via load_dotenv() above
    token = os.getenv("CQD_TG_BOT_TOKEN")
    chat_id = os.getenv("CQD_TG_CHAT_ID")
    if token and chat_id:
        return token, chat_id
    return None, None


def _tg_sent_log_path() -> str:
    return TG_SENT_LOG


def send_telegram_alert(message_text: str) -> None:
    """
    Atomic POST to the Telegram Bot API. Quietly skips if credentials are
    unavailable — never raises, never blocks, zero LLM tokens consumed.

    Every successful send is appended to cqd/state/tg_sent_log.csv

    SECURITY GUARDRAIL: Raises ValueError if TG_BOT_TOKEN is present in
    environment to prevent credential leakage to CQD bot channel.
    """
    # ── SANDBOX ISOLATION: Reject any global TG credentials ──────────────────
    if os.getenv("TG_BOT_TOKEN") is not None:
        raise RuntimeError(
            "SECURITY VIOLATION: TG_BOT_TOKEN detected in environment. "
            "CQD bot requires isolation. Use CQD_TG_BOT_TOKEN only."
        )

    token, chat_id = _load_telegram_creds()
    if not token or not chat_id:
        return

    if "POSITION OPENED" in message_text:
        event_class = "ENTRY"
    elif "POSITION CLOSED" in message_text:
        event_class = "EXIT"
    elif "CONNECTION TEST" in message_text:
        event_class = "TEST"
    else:
        event_class = "OTHER"

    import re
    pair_match = re.search(r"Asset:\s+([A-Z]+/(?:USDT|BUSD|BTC))", message_text)
    pair = pair_match.group(1) if pair_match else "UNKNOWN"

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message_text, "parse_mode": "Markdown"}
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        snippet = message_text.split("\n")[0]
        sent_log = _tg_sent_log_path()
        with open(sent_log, "a") as f:
            f.write(f"{ts},{event_class},{pair},\"{snippet}\"\n")

    except requests.exceptions.RequestException as e:
        if LOGGER_AVAILABLE and cqd_logger:
            cqd_logger.log_tg_error(
                details=f"RequestException: {type(e).__name__} {e}"
            )
    except Exception:
        pass


def build_entry_ticket(
    pair: str,
    direction: str,
    entry_price: float,
    position_size_usdt: float,
    sl_price: float,
    sl_pct: float,
    tp_price: float,
    tp_pct: float,
) -> str:
    return (
        f"\U0001f680 *QUANT DESK POSITION OPENED*\n\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"Asset:      {pair}\n"
        f"Direction:  {direction.upper()}\n"
        f"Entry:      ${entry_price:,.2f}\n"
        f"Allocated:  {position_size_usdt:.2f} USDT\n\n"
        f"\U0001f3af Target TP: ${tp_price:,.2f} ({tp_pct:.4f}%)\n"
        f"\U0001f6e1\ufe0f Stop Loss: ${sl_price:,.2f} ({sl_pct:.4f}%)\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )


def build_exit_ticket(
    pair: str,
    close_reason: str,
    direction: str,
    entry_price: float,
    current_price: float,
    pnl: float,
    price_diff_pct: float,
) -> str:
    reason = close_reason.replace("_", "-")
    return (
        f"\U0001f3c1 *QUANT DESK POSITION CLOSED*\n\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"Asset:      {pair}\n"
        f"Reason:    {reason}\n"
        f"Direction:  {direction.upper()}\n"
        f"Entry:      ${entry_price:,.2f}\n"
        f"Exit:       ${current_price:,.2f}\n\n"
        f"\U0001f4b0 Realized PnL: {pnl:+.2f} USDT ({price_diff_pct:+.2f}%)\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )


EXCHANGE = ccxt.binance({"enableRateLimit": True})


# ─── Market Data ──────────────────────────────────────────────────────────────

def get_current_price(symbol: str) -> float:
    ticker = EXCHANGE.fetch_ticker(symbol)
    return ticker["last"]


# ─── Execute ──────────────────────────────────────────────────────────────────

def execute_trade(payload_path: str) -> None:
    with open(payload_path, "r") as f:
        data = json.load(f)

    if data.get("action") != "EXECUTE":
        print("[SANDBOX] Veto received. Ignoring.")
        return

    params = data.get("trade_parameters", {})
    pair = params.get("pair")
    direction = params.get("direction")
    risk_pct = params.get("max_risk_capital_pct")
    sl_pct = params.get("stop_loss_pct")
    tp_pct = params.get("take_profit_pct")

    if not pair:
        print("[SANDBOX] ERROR: No pair in trade_parameters.")
        if LOGGER_AVAILABLE and cqd_logger:
            cqd_logger.log_event(component="SANDBOX", event_type="ERROR",
                                 pair="UNKNOWN", details="No pair in trade_parameters")
        return

    if direction is None or direction == "neutral":
        print(f"[SANDBOX] Neutral signal for {pair}. Ignoring.")
        return

    position_size_usdt = params.get("position_size_usdt")

    # Load wallet with lock
    wallet = load_wallet()

    # Guard: respect global_max_open_positions from config
    try:
        cfg = json.loads(Path(CONFIG_PATH).read_text()).get("sandbox_rules", {})
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        cfg = {}
    max_pos = cfg.get("global_max_open_positions", 5)

    if len(wallet.get("open_positions", {})) >= max_pos:
        print(f"[SANDBOX] global_max_open_positions ({max_pos}) reached. Skipping.")
        return

    if position_size_usdt is None:
        if risk_pct is None:
            risk_pct = 2.5
        position_size_usdt = wallet["balance_usdt"] * (risk_pct / 100.0)

    min_sz = cfg.get("min_position_size_usdt", 50.0)
    max_sz = cfg.get("max_position_size_usdt", 500.0)
    position_size_usdt = max(min_sz, min(max_sz, float(position_size_usdt)))

    if pair in wallet["open_positions"]:
        print(f"[SANDBOX] Position already open for {pair}. Skipping.")
        return

    entry_price = get_current_price(pair)

    # Pre-compute SL/TP prices (needed post-transaction for logging/alerts)
    if sl_pct is None:
        sl_pct = 2.0
    if tp_pct is None:
        tp_pct = 5.0
    if direction == "long":
        sl_price = entry_price * (1 - (sl_pct / 100.0))
        tp_price = entry_price * (1 + (tp_pct / 100.0))
    else:
        sl_price = entry_price * (1 + (sl_pct / 100.0))
        tp_price = entry_price * (1 - (tp_pct / 100.0))

    # ── Atomic wallet transaction ──────────────────────────────────────────────
    # Hold the exclusive lock for the FULL read-modify-write cycle so that
    # concurrent cron ticks cannot interleave and cause lost updates.
    wallet = modify_wallet(
        lambda w: _execute_trade_into_wallet(
            w, cfg, pair, direction, entry_price, position_size_usdt,
            risk_pct, sl_pct, tp_pct,
        )
    )

    if LOGGER_AVAILABLE and cqd_logger:
        conviction = str(data.get("trade_parameters", {}).get("max_risk_capital_pct", ""))
        cqd_logger.log_execute(
            pair=str(pair),
            conviction=conviction,
            entry_price=f"{entry_price:.4f}",
            size_usdt=f"{position_size_usdt:.2f}",
            details=f"direction={direction} "
                    f"SL={sl_price:.4f}({sl_pct:.4f}%) "
                    f"TP={tp_price:.4f}({tp_pct:.4f}%)",
        )

    print(
        f"[SANDBOX ENTRY] {direction.upper()} {pair} @ {entry_price} "
        f"| Size: {position_size_usdt:.2f} USDT "
        f"| SL({sl_pct:.4f}%): {sl_price:.4f} | TP({tp_pct:.4f}%): {tp_price:.4f}"
    )

    send_telegram_alert(
        build_entry_ticket(
            pair=pair,
            direction=direction,
            entry_price=entry_price,
            position_size_usdt=position_size_usdt,
            sl_price=sl_price,
            sl_pct=sl_pct,
            tp_price=tp_price,
            tp_pct=tp_pct,
        )
    )


def _execute_trade_into_wallet(
    wallet: dict,
    cfg: dict,
    pair: str,
    direction: str,
    entry_price: float,
    position_size_usdt: float | None,
    risk_pct: float | None,
    sl_pct: float | None,
    tp_pct: float | None,
) -> tuple[dict, bool]:
    """
    Pure-function updater for execute_trade.
    Returns (wallet, should_save). Does NOT raise — returns (wallet, False) on rejection.
    """
    max_pos = cfg.get("global_max_open_positions", 5)
    if len(wallet.get("open_positions", {})) >= max_pos:
        return wallet, False

    if position_size_usdt is None:
        if risk_pct is None:
            risk_pct = 2.5
        position_size_usdt = wallet["balance_usdt"] * (risk_pct / 100.0)

    min_sz = cfg.get("min_position_size_usdt", 50.0)
    max_sz = cfg.get("max_position_size_usdt", 500.0)
    position_size_usdt = max(min_sz, min(max_sz, float(position_size_usdt)))

    if pair in wallet["open_positions"]:
        return wallet, False

    # Compute SL/TP prices (same logic as execute_trade)
    if sl_pct is None:
        sl_pct = 2.0
    if tp_pct is None:
        tp_pct = 5.0
    if direction == "long":
        sl_price = entry_price * (1 - (sl_pct / 100.0))
        tp_price = entry_price * (1 + (tp_pct / 100.0))
    else:
        sl_price = entry_price * (1 + (sl_pct / 100.0))
        tp_price = entry_price * (1 - (tp_pct / 100.0))

    wallet["open_positions"][pair] = {
        "direction": direction,
        "entry_price": entry_price,
        "size_usdt": round(position_size_usdt, 2),
        "sl_price": round(sl_price, 8),
        "tp_price": round(tp_price, 8),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    wallet["balance_usdt"] -= position_size_usdt
    return wallet, True


# ─── Monitor ──────────────────────────────────────────────────────────────────

def monitor_positions() -> None:
    """Check all open positions and close any that hit SL/TP.

    FCNTL FLOCK GUARD (Phase 3 Hotfix #1):
    A non-blocking file lock on cqd/state/cqd_monitor.lock prevents duplicate
    exit processing when overlapping cron ticks fire. The monitor lock is
    released on exit (normal or exception) via atexit.
    """

    # ── Acquire monitor lock (non-blocking) ────────────────────────────────
    if not os.path.exists(MONITOR_LOCK):
        Path(MONITOR_LOCK).touch()
    lock_fd = os.open(MONITOR_LOCK, os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        os.close(lock_fd)
        print("[SANDBOX LOCK] Another monitor instance running. Exiting.")
        return

    def _release_lock():
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except Exception:
            pass
    atexit.register(_release_lock)

    # ── Idempotency check: snapshot closed positions before processing ─────
    if not os.path.exists(WALLET_FILE):
        print(f"[SANDBOX] ERROR: wallet_state.json not found at {WALLET_FILE}")
        if LOGGER_AVAILABLE and cqd_logger:
            cqd_logger.log_event(component="SANDBOX", event_type="ERROR",
                                 pair="SYSTEM",
                                 details=f"wallet_state.json missing: {WALLET_FILE}")
        return

    # Load wallet with lock
    wallet = load_wallet()
    positions = list(wallet["open_positions"].keys())

    if not positions:
        print("[SANDBOX] No open positions to monitor.")
        return

    # Snapshot of already-closed trades to prevent duplicate processing
    closed_pairs = {t["pair"] for t in wallet.get("trade_history", [])}
    closed_this_tick = set()

    for pair in positions:
        # ── Idempotency: skip if already closed in a prior tick ──────────
        if pair in closed_pairs:
            print(f"[SANDBOX] {pair} already closed (idempotency skip).")
            # Clean up stale open_position entry
            del wallet["open_positions"][pair]
            continue

        pos = wallet["open_positions"][pair]
        current_price = get_current_price(pair)

        close_reason = None
        if pos["direction"] == "long":
            if current_price <= pos["sl_price"]:
                close_reason = "STOP_LOSS"
            elif current_price >= pos["tp_price"]:
                close_reason = "TAKE_PROFIT"
        else:
            if current_price >= pos["sl_price"]:
                close_reason = "STOP_LOSS"
            elif current_price <= pos["tp_price"]:
                close_reason = "TAKE_PROFIT"

        if close_reason:
            price_diff_pct = (current_price - pos["entry_price"]) / pos["entry_price"]
            if pos["direction"] == "short":
                price_diff_pct *= -1

            pnl = pos["size_usdt"] * price_diff_pct

            wallet["balance_usdt"] += pos["size_usdt"] + pnl

            if LOGGER_AVAILABLE and cqd_logger:
                cqd_logger.log_exit(
                    pair=str(pair),
                    pnl=f"{round(pnl, 2):.2f}",
                    close_reason=str(close_reason),
                    details=(
                        f"direction={pos['direction']} "
                        f"entry={pos['entry_price']:.4f} "
                        f"exit={current_price:.4f} "
                        f"pnl_pct={round(price_diff_pct * 100, 2):.2f}%"
                    ),
                )

            log_entry = {
                "pair": pair,
                "direction": pos["direction"],
                "entry": pos["entry_price"],
                "exit": current_price,
                "pnl_usdt": round(pnl, 2),
                "reason": close_reason,
                "close_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            wallet["trade_history"].append(log_entry)
            del wallet["open_positions"][pair]
            closed_this_tick.add(pair)

            print(
                f"[SANDBOX EXIT] {pair} closed via {close_reason} "
                f"| Entry: {pos['entry_price']} → Exit: {current_price} "
                f"| PnL: {round(pnl, 2)} USDT"
            )

            send_telegram_alert(
                build_exit_ticket(
                    pair=pair,
                    close_reason=close_reason,
                    direction=pos["direction"],
                    entry_price=pos["entry_price"],
                    current_price=current_price,
                    pnl=round(pnl, 2),
                    price_diff_pct=round(price_diff_pct * 100, 2),
                )
            )

    # Persist any changes (including idempotency cleanups) with lock
    save_wallet(wallet)


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sandbox Paper-Trading Engine")
    parser.add_argument(
        "--execute",
        help="Path to Hermes JSON payload (e.g. /tmp/cqd_trigger.json) to execute a trade",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Check all open positions and close any that hit SL or TP",
    )
    args = parser.parse_args()

    if args.execute:
        execute_trade(args.execute)
    elif args.monitor:
        monitor_positions()
    else:
        parser.print_help()