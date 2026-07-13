#!/bin/bash
# =============================================================================
# crypto-quant-desk — Cron Monitor Script
# =============================================================================
# Launches the sandbox engine in monitor mode to check open positions against
# live prices and close any that hit SL/TP.
#
# Dynamic paths (container-native):
#   Sandbox Engine: PROJECT_ROOT/core/sandbox_engine.py
#   State:          PROJECT_ROOT/state/wallet_state.json
#   Log:            PROJECT_ROOT/logs/cqd_master_log.csv
#
# Usage with Hermes cron:
#   cronjob action=create schedule='*/5 * * * *'
#     script=cqd_monitor.sh
#     no_agent=true
#     name='cqd-monitor'
# =============================================================================

set -euo pipefail

# ── Dynamic Project Root ─────────────────────────────────────────────────────
# Resolve symlink to get actual project location when run via /opt/data/scripts/
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_PATH")")"

# ── Canonical Paths ───────────────────────────────────────────────────────
PYTHON_BIN="/opt/data/cqd_venv/bin/python"
SANDBOX_SCRIPT="/opt/data/cqd-trading-bot/core/sandbox_engine.py"

# ── Load Environment Variables ─────────────────────────────────────────────
if [ -f "/opt/data/cqd-trading-bot/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "/opt/data/cqd-trading-bot/.env"
    set +a
fi

# ── Hard Environment Isolation ─────────────────────────────────────────────
# Scrub any inherited global Hermes Telegram credentials before invoking
# Python to prevent CQD alerts from leaking to the global bot channel.
unset TG_BOT_TOKEN TG_CHAT_ID

# ── Execute monitor mode ─────────────────────────────────────────────────
echo "[CQD-MONITOR] Checking open positions..."
"${PYTHON_BIN}" "${SANDBOX_SCRIPT}" --monitor
echo "[CQD-MONITOR] Monitor cycle complete."