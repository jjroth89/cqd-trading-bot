#!/bin/bash
# =============================================================================
# crypto-quant-desk — Cron Trigger Script (no_agent mode)
# =============================================================================
# Dynamically scans the full daily watchlist from PROJECT_ROOT/config/watchlist.json.
# Each pair is evaluated independently; any pair scoring conviction >= 7
# writes a trigger payload and immediately executes the sandbox engine.
#
# Dynamic paths (container-native):
#   Evaluator: PROJECT_ROOT/core/quant_evaluator.py
#   Sandbox:   PROJECT_ROOT/core/sandbox_engine.py
#   Watchlist: PROJECT_ROOT/config/watchlist.json
#   State:     PROJECT_ROOT/state/
#
# Usage with Hermes cron:
#   cronjob action=create schedule='*/15 * * * *'
#     script=cqd_trigger.sh
#     no_agent=true
#     name='cqd-evaluator'
# =============================================================================

set -euo pipefail

# ── Dynamic Project Root ─────────────────────────────────────────────────────
# Resolve symlink to get actual project location when run via /opt/data/scripts/
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_PATH")")"

# ── Canonical Paths (relative to PROJECT_ROOT) ──────────────────────────────
WATCHLIST_FILE="${PROJECT_ROOT}/config/watchlist.json"
PYTHON_SCRIPT="${PROJECT_ROOT}/core/quant_evaluator.py"
SANDBOX_SCRIPT="${PROJECT_ROOT}/core/sandbox_engine.py"
PYTHON_BIN="/opt/data/cqd_venv/bin/python"
EXCHANGE="${2:-binance}"

# ── Load Environment Variables ───────────────────────────────────────────────
# .env file is loaded by Python scripts via python-dotenv, but shell scripts need it too
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${PROJECT_ROOT}/.env"
    set +a
fi

# ── Parse watchlist JSON ─────────────────────────────────────────────────────
PAIRS=$("${PYTHON_BIN}" -c "
import json, sys
with open('${WATCHLIST_FILE}') as f:
    data = json.load(f)
print(' '.join(data))
")

if [ -z "${PAIRS}" ]; then
    echo "[CQD-TRIGGER] No pairs in watchlist. Exiting."
    exit 0
fi

echo "[CQD-TRIGGER] Processing watchlist pairs: ${PAIRS}"

# Evaluate each pair
for PAIR in ${PAIRS}; do
    echo "[CQD-TRIGGER] Evaluating ${PAIR}..."
    
    PAYLOAD="/tmp/cqd_trigger_${PAIR//\//_}.json"
    
    # Run evaluator
    "${PYTHON_BIN}" "${PYTHON_SCRIPT}" \
        --pair "${PAIR}" \
        --exchange "${EXCHANGE}" \
        --output "${PAYLOAD}" \
        --timeframe 1h \
        --limit 200
    
    # Check conviction score
    CONVICTION=$("${PYTHON_BIN}" -c "
import json
with open('${PAYLOAD}') as f:
    data = json.load(f)
print(data.get('conviction_score', 0))
")
    
    # ── Hard Environment Isolation ─────────────────────────────────────────
    # Scrub any inherited global Hermes Telegram credentials before invoking
    # Python to prevent CQD alerts from leaking to the global bot channel.
    unset TG_BOT_TOKEN TG_CHAT_ID

    if [ "${CONVICTION}" -ge 7 ]; then
        echo "[CQD-TRIGGER] Conviction ${CONVICTION} >= 7 for ${PAIR}. Executing sandbox..."
        "${PYTHON_BIN}" "${SANDBOX_SCRIPT}" --execute "${PAYLOAD}"
    else
        echo "[CQD-TRIGGER] Conviction ${CONVICTION} < 7 for ${PAIR}. Skipping."
    fi
    
    # Clean up payload
    rm -f "${PAYLOAD}"
done

echo "[CQD-TRIGGER] Watchlist evaluation complete."