# CQD status report (proven recipe, 2026-07-15)

A recurring, human-readable trading + dev status report. Built to be honest: it
reports REAL wallet/trade data and explicitly states when there are zero closed
trades (CQD is sandbox-only). The user may adopt this as a permanent report.

## Architecture — keep it model-free where it can be
- **Data collection is a script, not AI.** `scripts/cqd_report_data.py` gathers
  wallet state, open positions, log stats, and fetches the LIVE price via `ccxt`
  (model-free). Output is JSON. This honors the model-independence rule (§1.2):
  math/data = scripts; AI is reserved for rendering/summarizing.
- **Rendering is an agent (`no_agent=false`).** The cron prompt reads the JSON,
  calls `cronjob action=list` for scheduler health, `git status` for dev state,
  and renders a trading-dashboard-style markdown report. Summarization is exactly
  the AI-reserved zone, so this is correct — NOT a violation of §1.2.

## Data collector contract
`scripts/cqd_report_data.py` prints JSON with:
- `wallet`: `balance_usdt`, `open_position_notional`, `total_value_usdt`
  (= balance + open notionals, PAPER capital), `closed_trades`,
  `unrealized_total_usdt`.
- `open_positions[]`: symbol, direction, entry, SL, TP, live_price, unrealized %.
- `log`: row count, first/last timestamp, event_type histogram, pairs_seen.
- `mode`: always "SANDBOX (simulation only — no live exchange keys)".

It `pop`s `TG_BOT_TOKEN`/`TG_CHAT_ID` at import (token-isolation guardrail) and
returns `price: null` on ccxt failure rather than fabricating.

## Cron registration (fires 10:00 America/Sao_Paulo = 13:00Z)
```bash
cronjob action=create name='cqd-daily-status-report' \
  script='' no_agent=false schedule='0 13 * * *' deliver='all' \
  skills='["cqd-ops"]' \
  enabled_toolsets='["terminal","file","web","delegation"]'
```
Prompt: run the collector, `cronjob action=list`, `git status --short`, check
`docs/README_*.md`; render WALLET / OPEN POSITIONS table / CLOSED TRADES
(prominently "0 closed trades" if empty) / SCHEDULER HEALTH / DEV STATUS.

## HARD RULES for the rendered report (encode in the prompt)
- Use ONLY real numbers. NEVER invent trades, gains, or losses.
- State SANDBOX/simulation-only at the top — total value is paper capital.
- Be frank about limitations (runs in operator's Docker container; may need
  tinkering to run elsewhere).
- Readable for a regular person; short dev/technical section at the end.

## Verify
1. Run the collector by hand: `/opt/data/cqd_venv/bin/python scripts/cqd_report_data.py`
   → expect valid JSON with a live price (not null) and `closed_trades` count.
2. `cronjob action=run job_id=<id>` → report delivered to the messenger.
