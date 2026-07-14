#!/bin/bash
# =============================================================================
# crypto-quant-desk — Health Check Endpoint
# =============================================================================
# Returns JSON health report. Checks: venv, ccxt, wallet state, config, and
# monitor lock status.  Zero LLM involvement.
#
# Usage: bash cqd_health.sh
# Cron:   cronjob action=create schedule='0 * * * *'
#           script=cqd_health.sh no_agent=true name='cqd-health'
# =============================================================================

set -euo pipefail

# ── Security: never inherit the global Hermes Telegram token ──────────────────
# CQD uses its OWN bot (CQD_TG_BOT_TOKEN). If the global TG_BOT_TOKEN/TG_CHAT_ID
# leaked in, alerts could route to the wrong bot. Strip them before any Python run.
unset TG_BOT_TOKEN TG_CHAT_ID

# ── Dynamic Project Root ─────────────────────────────────────────────────────
# Resolve symlink to get actual project location when run via /opt/data/scripts/
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_PATH")")"
PYTHON_BIN="/opt/data/cqd_venv/bin/python"

# ── Helpers ───────────────────────────────────────────────────────────────────
json_bool() { [ "$1" -eq 0 ] && echo "true" || echo "false"; }

# ── Checks ─────────────────────────────────────────────────────────────────────

# 1. Virtual environment
if [ -x "$PYTHON_BIN" ]; then
    VENV_OK=0
else
    VENV_OK=1
fi

# 2. ccxt import
CCXT_OK=0
"$PYTHON_BIN" -c "import ccxt" 2>/dev/null || CCXT_OK=1

# 3. wallet_state.json readable + valid JSON
WALLET_OK=0
WALLET_BALANCE="null"
WALLET_POSITIONS=0
if [ -f "${PROJECT_ROOT}/state/wallet_state.json" ]; then
    WALLET_BALANCE=$("$PYTHON_BIN" -c "
import json
with open('${PROJECT_ROOT}/state/wallet_state.json') as f:
    w = json.load(f)
print(w.get('balance_usdt', 'null'))
" 2>/dev/null) || WALLET_OK=1
    WALLET_POSITIONS=$("$PYTHON_BIN" -c "
import json
with open('${PROJECT_ROOT}/state/wallet_state.json') as f:
    w = json.load(f)
print(len(w.get('open_positions', {})))
" 2>/dev/null) || WALLET_OK=1
else
    WALLET_OK=1
fi

# 4. config.json readable
CONFIG_OK=0
if [ ! -f "${PROJECT_ROOT}/config/config.json" ]; then
    CONFIG_OK=1
fi

# 5. watchlist.json readable + pair count
WATCHLIST_OK=0
PAIR_COUNT=0
if [ -f "${PROJECT_ROOT}/config/watchlist.json" ]; then
    PAIR_COUNT=$("$PYTHON_BIN" -c "
import json
with open('${PROJECT_ROOT}/config/watchlist.json') as f:
    pairs = json.load(f)
print(len(pairs))
" 2>/dev/null) || WATCHLIST_OK=1
else
    WATCHLIST_OK=1
fi

# 6. Master log line count
LOG_LINES=$(wc -l < "${PROJECT_ROOT}/logs/cqd_master_log.csv" 2>/dev/null || echo 0)

# 7. Monitor lock status
LOCK_OK=0
if [ -f "${PROJECT_ROOT}/state/cqd_monitor.lock" ]; then
    LOCK_AGE=$(($(date +%s) - $(stat -c %Y "${PROJECT_ROOT}/state/cqd_monitor.lock" 2>/dev/null || echo 0)))
    if [ "$LOCK_AGE" -gt 600 ]; then
        LOCK_OK=2  # stale lock
    fi
else
    LOCK_OK=1  # no lock file
fi

# ── Overall health ─────────────────────────────────────────────────────────────
FAILS=0
[ "$VENV_OK" -ne 0 ] && FAILS=$((FAILS + 1))
[ "$CCXT_OK" -ne 0 ] && FAILS=$((FAILS + 1))
[ "$WALLET_OK" -ne 0 ] && FAILS=$((FAILS + 1))
[ "$CONFIG_OK" -ne 0 ] && FAILS=$((FAILS + 1))

if [ "$FAILS" -eq 0 ]; then
    STATUS="healthy"
elif [ "$FAILS" -le 2 ]; then
    STATUS="degraded"
else
    STATUS="unhealthy"
fi

# ── JSON output ───────────────────────────────────────────────────────────────
cat <<JSON
{
  "status": "$STATUS",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "checks": {
    "venv": $(json_bool $VENV_OK),
    "ccxt_import": $(json_bool $CCXT_OK),
    "wallet_readable": $(json_bool $WALLET_OK),
    "config_readable": $(json_bool $CONFIG_OK),
    "watchlist_readable": $(json_bool $WATCHLIST_OK),
    "lock_status": $LOCK_OK
  },
  "metrics": {
    "wallet_balance_usdt": $WALLET_BALANCE,
    "open_positions": $WALLET_POSITIONS,
    "watchlist_pairs": $PAIR_COUNT,
    "master_log_lines": $LOG_LINES
  }
}
JSON