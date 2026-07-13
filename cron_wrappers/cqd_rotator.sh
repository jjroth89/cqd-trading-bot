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

# ── Dynamic Project Root ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# ── Canonical Paths ─────────────────────────────────────────────────────────
ROTATOR_SCRIPT="${PROJECT_ROOT}/core/rotate_watchlist.py"
WATCHLIST_JSON="${PROJECT_ROOT}/config/watchlist.json"
PYTHON_BIN="${PROJECT_ROOT}/../cqd_venv/bin/python"

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