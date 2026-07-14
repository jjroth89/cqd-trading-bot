#!/bin/bash
# =============================================================================
# crypto-quant-desk — Liveness Watchdog Wrapper (no_agent cron mode)
# =============================================================================
# Pings the master log and pages via the CQD's OWN Telegram bot if the engine
# has gone silent (no SCAN/EVALUATOR activity within MAX_AGE_SECONDS).
#
# Usage with Hermes cron:
#   cronjob action=create schedule='*/10 * * * *' \
#     script=cqd_watchdog.sh no_agent=true name='cqd-watchdog'
# =============================================================================

set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_PATH")")"

PYTHON_BIN="/opt/data/cqd_venv/bin/python"
WATCHDOG_SCRIPT="/opt/data/cqd-trading-bot/core/cqd_watchdog.py"

# ── Load CQD .env (its own Telegram creds) ────────────────────────────────
if [ -f "/opt/data/cqd-trading-bot/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "/opt/data/cqd-trading-bot/.env"
    set +a
fi

# ── Hard Environment Isolation (same guardrail as monitor/trigger) ──────────
unset TG_BOT_TOKEN TG_CHAT_ID

MAX_AGE_SECONDS="${CQD_WATCHDOG_MAX_AGE_SECONDS:-600}"

# Run quietly. On success the CQD bot already received the alert (if stale),
# so we stay silent to avoid spamming the operator channel every cycle.
# On failure we surface the error so the scheduler can record it.
OUT="$("${PYTHON_BIN}" "${WATCHDOG_SCRIPT}" --max-age "${MAX_AGE_SECONDS}" 2>&1)"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "[CQD-WATCHDOG] ERROR:" >&2
    echo "$OUT" >&2
    exit "$RC"
fi

echo "[CQD-WATCHDOG] Watchdog cycle complete (silent on success)."
