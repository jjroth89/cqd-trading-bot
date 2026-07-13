#!/bin/bash
# =============================================================================
# crypto-quant-desk — Sandbox Monitor Wrapper
# =============================================================================
# Runs every 5 minutes under cron to check open positions against live prices.
# Closes any positions that hit SL or TP.
#
# Dynamic paths (container-native):
#   Monitor: PROJECT_ROOT/core/sandbox_engine.py --monitor
# =============================================================================

set -euo pipefail

# ── Dynamic Project Root ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# ── Canonical Paths ─────────────────────────────────────────────────────────
SANDBOX_SCRIPT="${PROJECT_ROOT}/core/sandbox_engine.py"
PYTHON_BIN="${PROJECT_ROOT}/../cqd_venv/bin/python"

# Load environment if .env exists
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${PROJECT_ROOT}/.env"
    set +a
fi

echo "[CQD-CRON] Launching Sandbox Position Monitor..."
if [ ! -f "${SANDBOX_SCRIPT}" ]; then
    echo "[CQD-CRON] FATAL: Sandbox script not found at ${SANDBOX_SCRIPT}"
    exit 1
fi

"${PYTHON_BIN}" "${SANDBOX_SCRIPT}" --monitor
echo "[CQD-CRON] Monitor cycle completed."