#!/bin/bash
# =============================================================================
# crypto-quant-desk — Telegram Verification Report (no_agent mode)
# =============================================================================
# Compares tg_sent_log.csv entries against cqd_master_log.csv EXECUTE/EXIT events
# to verify all position events were successfully delivered to Telegram.
#
# Compares UNIQUE pairs to avoid counting duplicate re-write attempts.
# =============================================================================

set -euo pipefail

# ── Dynamic Project Root ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON_BIN="${PROJECT_ROOT}/../cqd_venv/bin/python"

TG_SENT_LOG="${PROJECT_ROOT}/state/tg_sent_log.csv"
MASTER_LOG="${PROJECT_ROOT}/logs/cqd_master_log.csv"
WALLET_STATE="${PROJECT_ROOT}/state/wallet_state.json"

# Defensive handling for missing files
if [ ! -f "$TG_SENT_LOG" ]; then
    tg_entries=0
    tg_exits=0
else
    # Check if file has header (first field contains 'Timestamp' or 'timestamp')
    header_line=$(head -1 "$TG_SENT_LOG" | grep -c "^timestamp\|^Timestamp" || true)
    if [ "$header_line" -gt 0 ]; then
        tg_entries=$(awk -F',' 'NR>1 && $2=="ENTRY" {print $3}' "$TG_SENT_LOG" | sort -u | wc -l)
        tg_exits=$(awk -F',' 'NR>1 && $2=="EXIT" {print $3}' "$TG_SENT_LOG" | sort -u | wc -l)
    else
        tg_entries=$(awk -F',' 'NR>=1 && $2=="ENTRY" {print $3}' "$TG_SENT_LOG" | sort -u | wc -l)
        tg_exits=$(awk -F',' 'NR>=1 && $2=="EXIT" {print $3}' "$TG_SENT_LOG" | sort -u | wc -l)
    fi
fi

if [ ! -f "$MASTER_LOG" ]; then
    csv_entries=0
    csv_exits=0
else
    csv_entries=$(awk -F',' 'NR>1 && $3=="EXECUTE" {print $4}' "$MASTER_LOG" | sort -u | wc -l)
    csv_exits=$(awk -F',' 'NR>1 && $3=="EXIT" {print $4}' "$MASTER_LOG" | sort -u | wc -l)
fi

# Get actual unique closed positions from wallet
if [ -f "$WALLET_STATE" ]; then
    wallet_exits=$("$PYTHON_BIN" -c "
import json
with open('$WALLET_STATE') as f:
    data = json.load(f)
print(len(data.get('trade_history', [])))
" 2>/dev/null || echo 0)
else
    wallet_exits=0
fi

# Check for mismatch
if [ "$tg_entries" -eq "$csv_entries" ] && [ "$tg_exits" -eq "$csv_exits" ] && [ "$tg_exits" -eq "$wallet_exits" ]; then
    echo "✅ TELEGRAM VERIFICATION PASSED"
    echo "   ENTRY: CSV=$csv_entries Telegram=$tg_entries wallet=$wallet_exits ✓"
    echo "   EXIT:  CSV=$csv_exits Telegram=$tg_exits wallet=$wallet_exits ✓"
else
    echo "⚠️ TELEGRAM VERIFICATION MISMATCH DETECTED"
    echo "   ENTRY: CSV=$csv_entries Telegram=$tg_entries"
    echo "   EXIT:  CSV=$csv_exits Telegram=$tg_exits wallet=$wallet_exits"
    echo ""
    if [ "$csv_exits" -gt "$wallet_exits" ]; then
        echo "   ⚠️ More EXIT logs than closed positions: duplicate writes detected"
    fi
    echo "   Missing deliveries may indicate network issues or script bugs."
fi