#!/usr/bin/env python3
"""CQD status-report data collector (model-free).

Gathers REAL operational data for the daily status report: wallet state, open
positions with live unrealized PnL, closed-trade tally, master-log activity
stats, and exchange connectivity. No LLM is used — price fetch goes through
ccxt, everything else reads local state. Output is JSON so the report renderer
(agent or template) can format it without re-parsing.

Run: python3 scripts/cqd_report_data.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WALLET = PROJECT_ROOT / "state" / "wallet_state.json"
MASTER_LOG = PROJECT_ROOT / "logs" / "cqd_master_log.csv"

# Open config (no global TG token leak): isolate before importing bot code.
import os
os.environ.pop("TG_BOT_TOKEN", None)
os.environ.pop("TG_CHAT_ID", None)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_wallet() -> dict:
    if not WALLET.exists():
        return {"balance_usdt": None, "open_positions": {}, "trade_history": []}
    return json.loads(WALLET.read_text())


def live_price(symbol: str) -> float | None:
    """Fetch current price via ccxt (model-free). Returns None on any failure."""
    try:
        import ccxt
        ex = ccxt.binance({"enableRateLimit": True, "timeout": 10000})
        ticker = ex.fetch_ticker(symbol)
        return float(ticker["last"])
    except Exception:
        return None


def compute_unrealized(pos: dict, price: float | None) -> dict:
    entry = float(pos["entry_price"])
    size = float(pos["size_usdt"])
    direction = pos.get("direction", "long")
    if price is None or not price:
        return {"price": None, "unrealized_pct": None, "unrealized_usdt": None}
    if direction == "long":
        pct = (price - entry) / entry * 100.0
    else:
        pct = (entry - price) / entry * 100.0
    # size_usdt is notional; unrealized USDT ~ pct * notional
    usdt = pct / 100.0 * size
    return {"price": round(price, 8), "unrealized_pct": round(pct, 4),
            "unrealized_usdt": round(usdt, 2)}


def log_stats() -> dict:
    if not MASTER_LOG.exists():
        return {"rows": 0, "first": None, "last": None,
                "event_types": {}, "pairs_seen": 0}
    rows = 0
    first = last = None
    event_types: dict[str, int] = {}
    pairs = set()
    with MASTER_LOG.open(encoding="utf-8") as f:
        header = f.readline()
        for line in f:
            line = line.strip()
            if not line or line.startswith("Timestamp"):
                continue
            rows += 1
            parts = line.split(",")
            ts = parts[0].strip().strip('"')
            if not first:
                first = ts
            last = ts
            if len(parts) > 2:
                event_types[parts[2].strip()] = event_types.get(parts[2].strip(), 0) + 1
            if len(parts) > 3 and parts[3].strip():
                pairs.add(parts[3].strip())
    return {"rows": rows, "first": first, "last": last,
            "event_types": event_types, "pairs_seen": len(pairs)}


def main() -> int:
    wallet = load_wallet()
    opens = wallet.get("open_positions", {})
    open_detail = []
    unrealized_total = 0.0
    for sym, pos in opens.items():
        price = live_price(sym.replace("/", ""))
        u = compute_unrealized(pos, price)
        if u["unrealized_usdt"] is not None:
            unrealized_total += u["unrealized_usdt"]
        open_detail.append({
            "symbol": sym,
            "direction": pos.get("direction"),
            "entry_price": pos.get("entry_price"),
            "size_usdt": pos.get("size_usdt"),
            "sl_price": pos.get("sl_price"),
            "tp_price": pos.get("tp_price"),
            "entered": pos.get("timestamp"),
            "live_price": u["price"],
            "unrealized_pct": u["unrealized_pct"],
            "unrealized_usdt": u["unrealized_usdt"],
        })

    balance = wallet.get("balance_usdt")
    history = wallet.get("trade_history", [])
    total_value = None
    if balance is not None:
        total_value = round(balance + sum(float(p.get("size_usdt", 0)) for p in opens.values()), 2)

    report = {
        "generated_at": _now(),
        "wallet": {
            "balance_usdt": balance,
            "open_position_notional": round(
                sum(float(p.get("size_usdt", 0)) for p in opens.values()), 2),
            "total_value_usdt": total_value,
            "closed_trades": len(history),
            "unrealized_total_usdt": round(unrealized_total, 2) if opens else 0.0,
        },
        "open_positions": open_detail,
        "closed_trade_history": history,
        "log": log_stats(),
        "mode": "SANDBOX (simulation only — no live exchange keys)",
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
