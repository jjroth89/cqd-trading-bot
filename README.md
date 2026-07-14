# CQD — Crypto Quant Desk

> **Version `0.1.0`** · *Sandbox validation phase. Deterministic engine, live signal pipeline, backtest harness in planning.*

CQD is an automated crypto *quant desk*: a cron-driven pipeline that ingests market data, scores a rotating watchlist with a deterministic technical-analysis engine, and manages simulated positions against live prices. Execution is fully scripted (`no_agent` crons) — there is **no LLM in the decision path**. The sandbox layer is the deliberate first stage: validate signal quality and position lifecycle against real price action before any exchange integration.

---

## Pipeline

```
[BINANCE OHLCV + FNG/BTC-DOM]  ──▶  quant_evaluator  ──▶  conviction[1..10]
                                            │                        │
                                            ▼                        ▼
                                  sandbox_engine.open()      monitor (5m poll)
                                            │                        │
                                            ▼                        ▼
                                  wallet_state.json      SL/TP touch ──▶ close() ──▶ trade_history
                                            │
                                            ▼
                                  cqd_logger ──▶ CQD Telegram bot (isolated creds)
```

1. **Ingest** — read-only Binance klines (15m/1h) + numeric macro inputs: Crypto Fear & Greed Index (alternative.me) and BTC dominance (CoinGecko global).
2. **Score** — per-symbol indicator stack → base conviction 5, clamped to `[1,10]`.
3. **Act** — `score ≥ 7` opens a simulated position; size = `2.5% × balance`, clamped `[$50, $500]`.
4. **Manage** — monitor polls every 5 min; closes on SL/TP *touch*, records P/L.
5. **Alert** — entry/exit events pushed to CQD's own Telegram bot (credential-isolated).

---

## Scoring engine

`core/quant_evaluator.py` — base conviction 5, adjusted by a weighted indicator vote:

| Signal | Effect |
|--------|--------|
| RSI near oversold (<35) / overbought (>70) | ± |
| MACD histogram sign + crossover | ± |
| Bollinger Band touch (lower/upper) | ± |
| EMA bullish/bearish alignment (ema20 >/< ema50) | ± |
| Price vs ema50 | ± |
| CVD (cumulative volume delta) sign | ± |
| CMF / MFI overbought-oversold | ± |
| Volatility expansion / squeeze-breakout setup | ± |

- **Conviction 7–10** → long bias
- **5–6** → neutral (no action)
- **1–4** → short bias

Score ≥ 7 is the enforced entry gate in the trigger. Early manual/test runs opened a couple of positions below that gate; treat ≥ 7 as the current production rule.

### Macro inputs
`macro_cache.json` is refreshed each evaluator cycle: `fear_greed_index`, `fear_greed_label`, `btc_dominance`, `total_market_cap`. These feed the conviction nudge and are surfaced in the status report.

---

## Execution model

`core/sandbox_engine.py`:

- **SL%** = `1.5 × ATR / price`  ·  **TP%** = `3.0 × ATR / price`
- Absolute levels: `sl_price = entry × (1 − sl_pct)`, `tp_price = entry × (1 + tp_pct)`
- **Exit semantics:** close only on SL/TP *touch* (price crossing the level). A position trading between its bands is held intentionally — this is correct risk-managed behavior, not a stalled trade.
- Wallet starts at 10,000 USDT paper; realized P/L accumulates in `trade_history`.

---

## Schedule

CQD is **cron-driven** (S6 + Hermes scheduler), not a long-lived daemon — by design.

| Job | Interval | Responsibility |
|-----|----------|----------------|
| `cqd-monitor` | `*/5 * * * *` | Position lifecycle vs live price; SL/TP close. |
| `cqd-trigger` | `*/15 * * * *` | Re-score watchlist; open sim position on score ≥ 7. |
| `cqd-rotator` | `0 4 * * *` | Refresh the 5 rotating satellite pairs. |
| `cqd-health` | hourly | JSON health report (venv, wallet, config, locks). |
| `cqd-watchdog` | `*/10 * * * *` | Liveness probe + scheduler-drift self-heal. |

**Watchdog rationale:** the scheduler disables an unpinned job after an active-model drift. The watchdog detects drift-blocked `cqd_*` jobs and re-pins them, so execution continuity never depends on which chat model is active.

---

## Credential isolation (4 layers)

CQD runs its **own** Telegram bot, deliberately isolated from the global/Hermes bot:

1. **Import-time guard** — `RuntimeError` if `TG_BOT_TOKEN`/`TG_CHAT_ID` present (`sandbox_engine.py`, `cqd_logger.py`, `cqd_watchdog.py`).
2. **Send-time guard** — `send_telegram_alert` refuses to fire if global creds are in scope.
3. **Wrapper scrub** — every cron wrapper runs `unset TG_BOT_TOKEN TG_CHAT_ID` before invoking Python.
4. **Scoped reader** — sender reads **only** `CQD_TG_BOT_TOKEN` / `CQD_TG_CHAT_ID` from the local `.env`.

Every successful send is logged to `state/tg_sent_log.csv`.

---

## Scheduler resilience

- **Symlink containment** — wrappers resolve their canonical path via `readlink -f`, so scheduler launches from any cwd succeed.
- **Model-drift block** — jobs are pinned to a stable provider/model; the watchdog re-pins drift-blocked jobs automatically.
- **Liveness** — watchdog pages (CQD's own bot) if evaluator scans go silent >10 min.

---

## Status

**Engine:** fully deterministic, zero LLM dependencies, green across all four execution crons. The signal pipeline is producing consistent, explainable convictions every 15 minutes and managing the open position through its full SL/TP lifecycle correctly.

**Validation path:** currently sandbox-first — no exchange keys, no live orders. This is the intentional pre-live stage: prove signal quality and position management on real price action before wiring capital. A backtesting harness is the next milestone on the roadmap.

**Track record:** small but clean. Positions open and are managed to their risk levels; closed-trade bookkeeping is maturing alongside the backtest tooling.

**Known gaps (planned):**
- Trailing-stop params exist in `config.json` but only fixed SL/TP are implemented today.
- Hard-coded to this operator's container (paths, venv, Binance source) — portability work is deferred until the strategy is validated.

---

## Layout

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
├── state/                     # wallet_state.json, locks, tg_sent_log.csv, macro_cache.json
├── logs/cqd_master_log.csv    # master audit trail
└── scripts/cqd_report_data.py # model-free status-report data collector
```

## GitHub

Issues & project board: `jjroth89/cqd-trading-bot`.

---

*CQD is a sandbox research bot. Nothing here is financial advice, and no real funds are at risk by design.*

---

## Project Status — Prototype Completed, Focus Shifted

CQD was built as a deliberate engineering exercise: stand up a fully deterministic, cron-driven quant pipeline from nothing, prove the operational discipline end-to-end (credential isolation, scheduler resilience, complete audit trail), and validate a technical-analysis signal engine against live market data — all without risking a cent.

That objective is met. The engine runs, the pipeline is observable from ingest to alert, and the operational patterns extracted here have already been codified into reusable skills for the wider stack.

As of July 2026, **active development on CQD is paused.** The author has moved on to higher-stakes, production-trading work where these exact lessons apply directly. CQD stays in the repo as a reference implementation and a clean sandbox for strategy experimentation.

**What CQD proved out:**
- A deterministic scoring engine (`core/quant_evaluator.py`) emitting consistent, fully explainable convictions every 15 minutes.
- Bulletproof credential isolation between the bot's own Telegram channel and the global gateway — no cross-talk, ever.
- Scheduler-resilience patterns (drift self-healing, symlink-containment workarounds) that survive model rotation and container restarts.

**Deliberately left open (for the future or the curious):**
- A backtesting harness to measure signal edge over historical data.
- Live-exchange integration — gated behind backtest validation.

The bot is currently **paused** (all crons disabled) but trivially resumable. The code is documented, committed, and ready to pick back up. See [`docs/LESSONS_LEARNED.md`](docs/LESSONS_LEARNED.md) for the full retrospective.
