#!/bin/bash
# =============================================================================
# crypto-quant-desk — Daily Watchlist Rotator Wrapper
# =============================================================================
# Runs daily at 04:00 UTC under the cron system.
# Outputs dynamic 10-pair watchlist to PROJECT_ROOT/config/watchlist.json
# (5 core pairs + 5 satellite pairs selected by score).
#
# Dynamic paths (container-native):
#   Rotator:  PROJECT_ROOT/core/rotate_watchlist.py
#   Output:   PROJECT_ROOT/config/watchlist.json
# =============================================================================

set -euo pipefail

# ── Security: never inherit the global Hermes Telegram token ──────────────────
# CQD uses its OWN bot (CQD_TG_BOT_TOKEN). If the global TG_BOT_TOKEN/TG_CHAT_ID
# leaked in, alerts could route to the wrong bot. Strip them before any Python run.
unset TG_BOT_TOKEN TG_CHAT_ID

# ── Dynamic Project Root ─────────────────────────────────────────────────────
# When run via symlink in /opt/data/scripts/, resolve to actual project location
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_PATH")")"

# ── Canonical Paths ─────────────────────────────────────────────────────────
ROTATOR_SCRIPT="${PROJECT_ROOT}/core/rotate_watchlist.py"
WATCHLIST_JSON="${PROJECT_ROOT}/config/watchlist.json"
PYTHON_BIN="/opt/data/cqd_venv/bin/python"

# Load environment if .env exists
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${PROJECT_ROOT}/.env"
    set +a
fi

echo "[CQD-CRON] Launching Daily Watchlist Rotation..."
if [ ! -f "${ROTATOR_SCRIPT}" ]; then
    echo "[CQD-CRON] FATAL: Rotator script not found at ${ROTATOR_SCRIPT}"
    exit 1
fi

"${PYTHON_BIN}" "${ROTATOR_SCRIPT}" --output "${WATCHLIST_JSON}"
echo "[CQD-CRON] Daily Rotation completed successfully."