#!/bin/bash
# verify_sandbox_only.sh — Prove CQD is simulation-only (no live trading).
# Exit 0 = SAFE (no order-placement code / no exchange keys found).
# Exit 1 = REGRESSION: found live-order code or exchange credentials.
# Run from the repo root: bash scripts/verify_sandbox_only.sh [REPO_ROOT]
set -uo pipefail

ROOT="${1:-/opt/data/cqd-trading-bot}"
cd "$ROOT" || { echo "ERROR: cannot cd to $ROOT"; exit 2; }

FOUND=0

echo "==> Scanning for live-order code in core/*.py =="
if grep -rEn "create_order|place_order|exchange\.create_|create_market|create_limit" core/ 2>/dev/null; then
  echo "!! FOUND order-placement code — REGRESSION from sandbox contract"
  FOUND=1
fi

echo "==> Scanning for exchange API keys / secrets =="
if grep -rEn "apiKey|api_secret|secret\s*=\s*[\"'][A-Za-z0-9]|'secret'|\"secret\"" core/ config/ 2>/dev/null; then
  echo "!! FOUND exchange credentials — REGRESSION"
  FOUND=1
fi

echo "==> Confirming exchange use is read-only (fetch_ohlcv / fetch_ticker) =="
RO_COUNT=$(grep -rEoh "fetch_ohlcv|fetch_ticker|fetch_tickers" core/ 2>/dev/null | wc -l)
echo "    read-only fetches found: $RO_COUNT"

if [ "$FOUND" -eq 0 ]; then
  echo "OK: no live-order code and no exchange keys. CQD is simulation-only."
  exit 0
else
  echo "FAIL: live-trading artifacts present. HALT and report."
  exit 1
fi
