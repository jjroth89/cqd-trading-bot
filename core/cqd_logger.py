#!/usr/bin/env python3
"""
crypto-quant-desk — Unified CSV Logger
=======================================
Zero-token, script-intensive CSV appender. All formatting and escaping is
handled deterministically by the Python stdlib — no LLM involvement ever.

Canonical log: PROJECT_ROOT/logs/cqd_master_log.csv
Headers: Timestamp, Component, Event_Type, Pair, Conviction, FGI, BTC_Dom, PnL_USDT, Details

Usage (import):
    from cqd_logger import log_event
    log_event(component="EVALUATOR", event_type="SCAN", pair="BTC/USDT",
              conviction=8, fgi=42, btc_dom=52.1, pnl="", details="RSI=28")

The file is lock-free and appends atomically via open(..., "a") so multiple
processes (evaluator + sandbox) can write to the same file without corruption.
"""

import csv
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

# ─── Project Root & Dynamic Paths ─────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent  # /opt/data/cqd-trading-bot/

# Target: canonical production log the user opens in Excel/Numbers.
# Both the evaluator and sandbox_engine write to the same file.
LOG_FILE = PROJECT_ROOT / "logs" / "cqd_master_log.csv"

# ─── FAIL-FAST SECURITY GUARDRAIL ─────────────────────────────────────────────
# Halt immediately if the global Hermes Telegram credential leaks into this
# process at import time.  Prevents the CQD bot from silently routing alerts
# to the wrong (global Hermes) Telegram bot.
if os.getenv("TG_BOT_TOKEN") is not None or os.getenv("TG_CHAT_ID") is not None:
    raise RuntimeError(
        "SECURITY VIOLATION: TG_BOT_TOKEN or TG_CHAT_ID detected at import time. "
        "CQD bot requires strict isolation — only CQD_TG_BOT_TOKEN and CQD_TG_CHAT_ID "
        "are permitted. Halted to prevent credential leakage."
    )

# CSV column order — must match the header line exactly
FIELDNAMES = [
    "Timestamp",
    "Component",
    "Event_Type",
    "Pair",
    "Conviction",
    "FGI",
    "BTC_Dom",
    "PnL_USDT",
    "Details",
]

# Module-level lock so concurrent calls (evaluator + sandbox same tick) don't
# interleave writes. Smallest possible critical section — only the actual file
# write is locked.
_lock = threading.Lock()


def _escape(val: str) -> str:
    """Escape any embedded double-quotes (CSV quoting convention) and trim."""
    if val is None:
        return ""
    s = str(val).strip()
    if any(c in s for c in ('"', ",", "\n", "\r")):
        s = s.replace('"', '""')
        s = f'"{s}"'
    return s


def _now_utc() -> str:
    """Return UTC ISO8601 timestamp string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_header() -> None:
    """Write the header row if LOG_FILE does not yet exist (idempotent)."""
    if LOG_FILE.exists():
        return
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()


def _log_event(
    component: str = "SYSTEM",
    event_type: str = "INFO",
    pair: str = "SYSTEM",
    conviction: str = "",
    fgi: str = "",
    btc_dom: str = "",
    pnl: str = "",
    details: str = "",
) -> None:
    """
    Append one row to cqd_master_log.csv.

    All parameters are plain strings — the logger handles conversion and
    escaping internally so callers never need to think about CSV quoting.
    """
    conviction = str(conviction) if conviction != "" else ""
    fgi        = str(fgi)        if fgi        != "" else ""
    btc_dom    = str(btc_dom)    if btc_dom    != "" else ""
    pnl        = str(pnl)        if pnl        != "" else ""

    row = {
        "Timestamp":  _now_utc(),
        "Component":  _escape(component),
        "Event_Type": _escape(event_type),
        "Pair":       _escape(pair),
        "Conviction": _escape(conviction),
        "FGI":        _escape(fgi),
        "BTC_Dom":    _escape(btc_dom),
        "PnL_USDT":   _escape(pnl),
        "Details":    _escape(details),
    }

    with _lock:
        _ensure_header()
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
            writer.writerow(row)


def log_event(
    component: str = "SYSTEM",
    event_type: str = "INFO",
    pair: str = "SYSTEM",
    conviction: str = "",
    fgi: str = "",
    btc_dom: str = "",
    pnl: str = "",
    details: str = "",
) -> None:
    """
    Public alias for _log_event.  Exists for callers that import this function
    directly (e.g.  from cqd_logger import log_event).
    """
    _log_event(
        component=component,
        event_type=event_type,
        pair=pair,
        conviction=conviction,
        fgi=fgi,
        btc_dom=btc_dom,
        pnl=pnl,
        details=details,
    )


# ─── Convenience wrappers ────────────────────────────────────────────────────

def log_scan(pair: str, conviction: str, fgi: str, btc_dom: str, details: str = "") -> None:
    _log_event(component="EVALUATOR", event_type="SCAN", pair=pair,
              conviction=conviction, fgi=fgi, btc_dom=btc_dom, pnl="", details=details)


def log_execute(pair: str, conviction: str, entry_price: str,
                size_usdt: str, details: str = "") -> None:
    msg = f"entry={entry_price} size={size_usdt}USDT"
    if details:
        msg = f"{msg} | {details}"
    _log_event(component="SANDBOX", event_type="EXECUTE", pair=pair,
              conviction=conviction, pnl="", details=msg)


def log_exit(pair: str, pnl: str, close_reason: str, details: str = "") -> None:
    msg = f"reason={close_reason}"
    if details:
        msg = f"{msg} | {details}"
    _log_event(component="SANDBOX", event_type="EXIT", pair=pair,
              conviction="", pnl=pnl, details=msg)


def log_error(component: str, pair: str, exception: Exception, details: str = "") -> None:
    import traceback
    tb = traceback.format_exception(type(exception), exception, exception.__traceback__)
    tb_str = " | ".join(line.strip() for line in tb if line.strip())
    msg = f"{type(exception).__name__}: {exception}"
    if details:
        msg = f"{msg} | {details}"
    _log_event(component=component, event_type="ERROR", pair=pair,
              conviction="", pnl="", details=msg)


def log_tg_error(details: str = "") -> None:
    _log_event(component="TG", event_type="TG_ERROR", pair="SYSTEM",
              conviction="", pnl="", details=details)


class CqdLogger:
    """
    Idiomatic wrapper exposing logger methods as instance methods.

    Usage::

        from core.cqd_logger import cqd_logger
        cqd_logger.log_scan("BTC/USDT", conviction=8, fgi=45, btc_dom=52.1)

    All methods delegate to the internal _log_event function.
    """

    @staticmethod
    def log_scan(pair: str, conviction: str, fgi: str, btc_dom: str,
                details: str = "") -> None:
        _log_event(component="EVALUATOR", event_type="SCAN", pair=pair,
                   conviction=conviction, fgi=fgi, btc_dom=btc_dom,
                   pnl="", details=details)

    @staticmethod
    def log_execute(pair: str, conviction: str, entry_price: str,
                    size_usdt: str, details: str = "") -> None:
        msg = f"entry={entry_price} size={size_usdt}USDT"
        if details:
            msg = f"{msg} | {details}"
        _log_event(component="SANDBOX", event_type="EXECUTE", pair=pair,
                   conviction=conviction, pnl="", details=msg)

    @staticmethod
    def log_exit(pair: str, pnl: str, close_reason: str,
                details: str = "") -> None:
        msg = f"reason={close_reason}"
        if details:
            msg = f"{msg} | {details}"
        _log_event(component="SANDBOX", event_type="EXIT", pair=pair,
                   conviction="", pnl=pnl, details=msg)

    @staticmethod
    def log_event(component: str, event_type: str, pair: str,
                  conviction: str = "", fgi: str = "", btc_dom: str = "",
                  pnl: str = "", details: str = "") -> None:
        _log_event(component=component, event_type=event_type, pair=pair,
                   conviction=conviction, fgi=fgi, btc_dom=btc_dom,
                   pnl=pnl, details=details)

    @staticmethod
    def log_error(component: str, pair: str, exception: Exception,
                  details: str = "") -> None:
        log_error(component=component, pair=pair,
                  exception=exception, details=details)

    @staticmethod
    def log_tg_error(details: str = "") -> None:
        log_tg_error(details=details)


# Module-level singleton instance
cqd_logger = CqdLogger()


if __name__ == "__main__":
    print(f"[cqd_logger] self-test — LOG_FILE={LOG_FILE}")
    log_event(component="LOGGER", event_type="INFO", pair="SYSTEM",
              conviction="", details="cqd_logger initialised")
    print(f"[cqd_logger] self-test complete — see {LOG_FILE}")