# CQD — Crypto Quant Desk

> **Version `0.1.0`** · *Pre-release. Sandbox-only, unvalidated strategy, tuned to one container.*

**CQD is an automated crypto *practice*-trader.** It scans a list of coins,
scores them with technical-analysis math, and opens **simulated** trades with
*fake money* in a local file. It never touches a real exchange and holds no
trading credentials. It sends you alerts on **its own Telegram bot** and runs
quietly inside the server container you already have.

---

## What this means for you (the short version)

- 🛡️ **It cannot lose real money.** No exchange API keys exist anywhere. "Trading" happens in a JSON file (`state/wallet_state.json`).
- 🤖 **It's your bot, in your chat.** CQD has its *own* Telegram bot, deliberately isolated from any other bot, so trade alerts never leak into the wrong place.
- 📊 **It thinks with math, not AI.** Every decision is deterministic code (indicators + a numeric Fear & Greed reading). There is **no language model** in the trading path.
- 💤 **It runs where your server already runs.** No new infrastructure, no separate deploy. It lives as scripts + scheduled jobs inside your existing container.
- ⚠️ **It's a practice tool, not a proven system.** The strategy has *not* been backtested yet, and it's tuned to this one machine — see [Honest Status](#honest-status).

---

## How it works (plain English)

1. **Watchlist.** CQD looks at ~10 coins (5 permanent "core" pairs + 5 that rotate daily).
2. **Scan.** On a timer it pulls recent prices (read-only, from Binance) plus two free market-mood readings: the **Fear & Greed Index** and **BTC dominance**.
3. **Score.** For each coin it computes standard indicators (RSI, MACD, Bollinger Bands, trend, volatility) and nudges a **conviction score** from 1–10.
   - **7–10** → "long" (bet price rises)
   - **5–6** → neutral (do nothing)
   - **1–4** → short bias
4. **Act.** If a coin scores **≥ 7**, it opens a **fake** position sized at ~2.5% of the paper balance (between $50–$500), with an automatic stop-loss and take-profit based on volatility.
5. **Watch.** Every few minutes it checks the live price and closes the fake position if it hits the stop or target, recording the paper profit/loss.
6. **Alert.** It messages you on its own Telegram bot when positions open/close and if it ever goes quiet.

### A note on the "≥ 7" rule
The intended rule (only trade conviction ≥ 7) is enforced in the trigger
script. The log shows two early sandbox positions opened at lower scores (from
manual/test runs), so treat ≥ 7 as the *current* intended gate, not a guarantee
of every past execution.

---

## Schedule (what runs, and when)

CQD is **cron-driven, not a constantly-running program** — that's normal. Four
core jobs plus a watchdog overseer:

| Job | How often | What it does |
|-----|-----------|--------------|
| **Monitor** | every 5 min | Checks open positions vs live price; closes on stop/target. |
| **Trigger / Evaluator** | every 15 min | Re-scores the watchlist; opens a sim position for any coin ≥ 7. |
| **Rotator** | daily, 04:00 UTC | Re-picks the 5 rotating "satellite" pairs. |
| **Health** | hourly | Emits a JSON health report (venv, wallet, config, locks). |
| **Watchdog** | every 10 min | "Liveness" check — pages you on Telegram if scans go silent >10 min, and self-heals scheduler hiccups. |

> **Why a watchdog?** The scheduler that runs these jobs can silently disable a
> job if the chat model changes. The watchdog detects that and re-pins the jobs
> so CQD keeps running — execution never depends on which AI model you're using.

---

## Telegram (your own bot)

- CQD uses two values in its local `.env`: `CQD_TG_BOT_TOKEN` and `CQD_TG_CHAT_ID`.
- **Hard safety rule:** if CQD ever detects the *global* bot token, it **refuses to run** (fail-fast). This guarantees its alerts stay in its own chat.
- Every successful send is logged to `state/tg_sent_log.csv`.

---

## Honest status

**This is a work in progress, and you should know exactly where it stands:**

- **Sandbox only.** No live exchange keys, no real orders, ever (by design today *and* because the plumbing doesn't exist yet). The "wallet" starts at a fake 10,000 USDT.
- **Not backtested.** The strategy has no historical validation. Issue **#11** (a plan to build a backtesting harness) is still open. Any "win rate" is currently unknown — CQD is a hypothesis-generator, not a proven system.
- **Tuned to one machine.** Paths, the Python environment, schedules, and the data source (Binance) are hard-coded to this operator's container. **Running it elsewhere will take days of path/config tinkering** — this is not a clone-and-go project yet.
- **Small track record.** Only 2 sandbox positions opened so far; none closed yet. The bookkeeping around closed trades is still maturing.
- **Some advertised features aren't built.** `config.json` lists trailing-stop parameters that aren't implemented (only fixed stop-loss/take-profit exists today).

---

## For operators / developers

<details>
<summary>Click for the technical layer (paths, crons, guardrails, architecture)</summary>

### Layout
```
/opt/data/cqd-trading-bot/
├── core/
│   ├── quant_evaluator.py     # scoring engine (deterministic, no LLM)
│   ├── sandbox_engine.py      # position open/close, SL/TP, token guardrail
│   ├── cqd_logger.py          # CSV audit log + Telegram sender (CQD-only creds)
│   ├── cqd_watchdog.py        # liveness + scheduler drift self-heal
│   └── rotate_watchlist.py    # daily satellite-pair selection
├── cron_wrappers/*.sh         # scheduler entrypoints (all unset global TG token)
├── config/                    # watchlist.json, config.json
├── state/                     # wallet_state.json, locks, tg_sent_log.csv
├── logs/cqd_master_log.csv    # master audit trail
└── scripts/cqd_report_data.py # model-free status-report data collector
```

### Exact trigger mechanics
- Conviction base = 5, clamped to [1,10]; **score ≥ 7 ⇒ long** (`core/quant_evaluator.py`).
- SL% = `1.5 × ATR / price`, TP% = `3.0 × ATR / price`; absolute prices via `entry × (1 ± pct)` (`core/sandbox_engine.py`).
- Exit only on SL/TP *touch* — a position between its levels is held correctly, not forgotten.

### Credential isolation (4 layers)
1. Import-time `RuntimeError` if `TG_BOT_TOKEN`/`TG_CHAT_ID` present (`sandbox_engine.py`, `cqd_logger.py`, `cqd_watchdog.py`).
2. Send-time guard in `send_telegram_alert`.
3. Every wrapper does `unset TG_BOT_TOKEN TG_CHAT_ID` before Python.
4. Sender reads **only** `CQD_TG_BOT_TOKEN` / `CQD_TG_CHAT_ID`.

### Scheduler traps (and the fix)
- **Symlink containment:** wrappers resolve their true path with `readlink -f` so they work regardless of how the scheduler launches them.
- **Model-drift block:** the scheduler disables a job whose pinned model drifted from the active chat model. The watchdog re-pins drift-blocked `cqd_*` jobs to stable values automatically.

### Model independence
CQD execution is 100% script-only (`no_agent=true` crons). It must never depend on the user's rotating chat model. "Sentiment" is the numeric Fear & Greed Index + BTC dominance from public REST APIs — **there is no LLM in the decision path.**

### GitHub
Issues & board: `jjroth89/cqd-trading-bot`. Backtest plan: issue **#11** (P0).

</details>

---

*CQD is a sandbox research bot. Nothing here is financial advice, and no real
funds are at risk by design.*
