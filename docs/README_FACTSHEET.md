# CQD Trading Bot — Plain‑English Fact Sheet

> **What this document is:** A non‑technical, evidence‑based description of what the
> CQD ("crypto‑quant‑desk") bot in `/opt/data/cqd-trading-bot` actually does,
> based on a direct read of its source code, config, logs, and the GitHub issue
> board. It is a **fact sheet**, not the final README. Every claim below is backed
> by code I read; anything I could not verify is flagged in ⚠️ boxes.

---

## 1. One‑paragraph summary — what CQD is

CQD is a **crypto trading research bot that runs entirely in simulation ("paper
trading") — it does not trade real money and holds no live exchange account
credentials.** On a fixed schedule it scans a list of ~10 cryptocurrencies, pulls
their recent price history and a couple of market‑sentiment readings, runs them
through a set of technical‑analysis formulas, and produces a 1‑to‑10 "conviction"
score for each coin. When a coin scores high enough (≥ 7), the bot opens a
**fake** position in a local file (a "sandbox wallet"), then continuously watches
the live price and closes that fake position if it hits a pre‑set profit target or
loss limit. It sends Telegram messages about what it's doing. It is a
**quantitative analysis + paper‑trading sandbox**, not a money‑making machine, and
its trading logic has **not yet been validated by backtesting** (see §5 and Issue #11).

---

## 2. What it trades and how decisions are made (everyday language)

**What it watches**
- A **watchlist** of 10 trading pairs (e.g. `BTC/USDT` = Bitcoin priced in
  USDT, a US‑dollar‑pegged token).
- **5 "core" pairs are fixed:** `BTC/USDT`, `ETH/USDT` (Ethereum), `BNB/USDT`
  (Binance Coin), `SOL/USDT` (Solana), `ADA/USDT` (Cardano).
- **5 "satellite" pairs rotate daily** — each day a script ranks all Binance
  USDT pairs by liquidity, volatility, and recent momentum and picks the top 5 to
  join the core 5. Current satellites in the file: `UTK`, `OPN`, `ZEC`, `XRP`,
  `ALLO` (this list changes daily, so it is only a snapshot).

**Where the data comes from**
- Price candles (open/high/low/close/volume) are fetched **read‑only** from the
  Binance public API via the `ccxt` library (`fetch_ohlcv` / `fetch_ticker`).
- Market mood is pulled from two free public APIs: the **Fear & Greed Index**
  (alternative.me) and **BTC dominance / total market cap** (CoinGecko).
- These mood readings are cached for 2 hours to avoid hammering the APIs.

**How it decides (the "scoring")**
- For each coin, `quant_evaluator.py` computes standard trading indicators:
  RSI (is it overbought/oversold?), MACD (momentum), Bollinger Bands (is price
  at the top or bottom of its recent range?), EMAs (trend direction), ATR
  (volatility), plus volume and money‑flow metrics.
- Each indicator nudges a running **conviction score** up or down (e.g. oversold +
  2, overbought − 2, bullish money flow + 1). The score is clamped to **1–10**.
- The score maps to a direction: **7–10 = "long" (bet price rises)**, 5–6 =
  neutral, 3–4 = slight short bias, 1–2 = "short" (bet price falls).

**How it acts**
- `cqd_trigger.sh` runs the evaluator on every watchlist pair. **If a coin's
  conviction ≥ 7, it hands the result to `sandbox_engine.py --execute`.**
- `sandbox_engine.py` opens a **simulated** position: it sizes the bet at
  **2.5% of the sandbox balance** (clamped between **$50 and $500**), and sets a
  **stop‑loss and take‑profit based on ATR** (volatility × 1.5 for the stop,
  × 3.0 for the profit target). It writes this to `state/wallet_state.json`.
- `cqd_monitor.sh` later checks the live price against those SL/TP levels and
  closes the simulated position when one is hit, recording the fake profit/loss.

**⚠️ Important honesty note on the decision gate:** The "conviction ≥ 7" rule is
enforced only in the cron **wrapper script** (`cqd_trigger.sh`), *not* inside
`sandbox_engine.py` itself. The sandbox engine will blindly open whatever payload
it is handed. The master log shows two `SANDBOX EXECUTE` events — one BTC
short at conviction **2** and one OPN long at conviction **2.5** — both **below**
the 7 threshold. This means at least some executions came from manual/test runs or
an earlier configuration, not the normal gate. I could not fully reconcile this
from the code alone; treat the ≥7 gate as the *intended* rule, with past
executions partly driven by manual triggers.

---

## 3. The cron job schedule (explained simply)

Four jobs are designed to run on a timer (the cron `schedule='…'` strings in the
wrapper headers). All four call an isolated Python environment at
`/opt/data/cqd_venv/bin/python` and strip out any global Telegram credentials
before running.

| Job (wrapper script)            | Intended schedule | What it does, in plain words                                                          |
| ------------------------------- | ---------------- | -------------------------------------------------------------------------------------- |
| `cqd_monitor.sh`               | **every 5 min** (`*/5 * * * *`)   | Checks each open simulated position against the live price; closes it if SL/TP is hit. |
| `cqd_trigger.sh` (evaluator)  | **every 15 min** (`*/15 * * * *`)  | Re‑scores the whole watchlist; opens a simulated position for any coin scoring ≥ 7.     |
| `cqd_rotator.sh` → `rotate_watchlist.py` | **daily 04:00 UTC** | Re‑picks the 5 rotating satellite pairs from Binance's top‑ranked assets.               |
| `cqd_watchdog.sh` → `cqd_watchdog.py`   | **every 10 min** (`*/10 * * * *`) | "Liveness" check: if no scan activity in >600s, pages the operator via Telegram.        |

Supporting (not a trade loop): `cqd_health.sh` (hourly health JSON) and
`cqd_telegram_verification.sh` (reconciles Telegram sends vs. log rows).

**Self‑healing nuance:** `cqd_watchdog.py` also tries to **re‑pin** any CQD cron
job that the scheduler blocked for "model drift" back to a fixed model
`tencent/hy3:free` / provider `openrouter` (hardcoded constants). This is
specifically so the bot keeps running even if the operator switches chat models.

---

## 4. Sandbox vs. real trading — status

**STATUS: SIMULATION‑ONLY. No live exchange keys. No real orders are ever placed.**

I searched the entire codebase for order‑placement code (`create_order`,
`place_order`, `apiKey`, `secret`, `exchange.create_*`) and found **none**. The
only exchange use is **read‑only** price fetching (`ccxt.binance` +
`fetch_ticker`/`fetch_ohlcv`). There are **no API key / secret files** anywhere
in the repo — only Telegram credentials (`CQD_TG_BOT_TOKEN`,
`CQD_TG_CHAT_ID`, see §6).

The sandbox "portfolio" lives in `state/wallet_state.json`, starting at a fake
**10,000 USDT** balance (currently ~9,749.56 in the file). "Opening a trade"
means subtracting a simulated amount from that JSON balance; "closing" means
adding it back with the simulated PnL. **No funds move. No broker is contacted.**

There is a deliberate **security guardrail**: every core script aborts at import
if it detects the *global* Hermes Telegram token (`TG_BOT_TOKEN` /
`TG_CHAT_ID`), so CQD can never accidentally route alerts to the wrong bot.

---

## 5. Known limitations / "where this honestly stands"

- **Not backtested.** The trading strategy has **no historical validation**.
  Issue **#11** (open, labeled **P0 / Critical**, "Remote Backtest Worker on
  Home PC") is literally a plan to *build* a backtesting/Optuna harness. Until
  that exists, any "win rate" is unknown — the bot is a hypothesis‑generator,
  not a proven system.
- **Tuned to one machine.** Paths are hard‑coded to this operator's environment:
  `/opt/data/cqd-trading-bot/`, Python at `/opt/data/cqd_venv/bin/python`,
  cron schedules, a fixed model pin (`tencent/hy3:free`), and Binance‑only data.
  **Running it elsewhere will need days of path/config tinkering** — this is not a
  portable, "clone‑and‑go" project.
- **Tiny track record.** The master log contains only **2 sandbox executions**
  (one BTC short, one OPN long). The `trade_history` array in `wallet_state.json`
  is **empty**, and currently only the OPN position is recorded as open — the BTC
  position is absent with no recorded close, so the bookkeeping around closed
  trades is incomplete/inconsistent in the current snapshot.
- **Feature gaps vs. config.** `config.json` advertises trailing‑stop parameters
  (`trailing_stop_activation_multiplier`, `trailing_stop_distance_multiplier`),
  but **no trailing‑stop logic is implemented** in the engine — only fixed ATR
  SL/TP. The "conviction ≥ 7" gate lives in the shell wrapper, not the engine.
- **Single exchange / single direction model.** All data is Binance‑only; the
  watchlist rotator is hard‑wired to Binance `fetch_tickers()`.
- **No live capital path at all** — by design today, but also because none of the
  plumbing (keys, order execution, exchange‑auth sandbox) exists yet.

---

## 6. Telegram alerting setup

- CQD has its **own dedicated Telegram bot**, configured via two variables in a
  local `.env` file: `CQD_TG_BOT_TOKEN` and `CQD_TG_CHAT_ID` (a template
  exists at `.env.template`; the real `.env` is git‑ignored and not committed).
- It sends two kinds of trade tickets:
  - **"QUANT DESK POSITION OPENED"** — asset, long/short, entry price, allocated
    USDT, target take‑profit and stop‑loss prices/percentages.
  - **"QUANT DESK POSITION CLOSED"** — same fields plus realized PnL.
- The **watchdog** sends liveness/scheduler‑drift alerts ("engine STALE", "job(s)
  blocked by model‑drift guard") to the same channel.
- Every successful send is logged to `state/tg_sent_log.csv`. A verification
  script (`cqd_telegram_verification.sh`) cross‑checks that log against the
  master log and the wallet to flag any missed deliveries.
- **Isolation:** the bot deliberately refuses to use the operator's *global*
  Hermes Telegram credentials (fail‑fast guardrail in `cqd_logger.py`,
  `sandbox_engine.py`, `cqd_watchdog.py`), so its alerts stay in its own chat.

---

## 7. Glossary — terms a normal person needs

- **Conviction** — CQD's 1–10 confidence score for a trade idea. Roughly: ≥7 =
  confident enough to open a simulated long; ≤2 = strong short bias; 5–6 =
  sit still.
- **SL / TP (Stop‑Loss / Take‑Profit)** — pre‑set price lines. SL = "get me out
  if it drops this far" (limits losses); TP = "take my winnings if it rises this
  far." CQD sets both automatically from volatility (ATR).
- **Watchlist** — the rotating list of ~10 coins CQD actually looks at each cycle
  (5 fixed "core" + 5 daily‑rotated "satellites").
- **Sandbox / Paper trading** — a risk‑free simulation. CQD "trades" with fake
  money in a JSON file; nothing is bought or sold on any real exchange.
- **FGI (Fear & Greed Index)** — a 0–100 public sentiment gauge for crypto
  (0 = extreme fear, 100 = extreme greed). CQD uses it as a contrarian nudge in
  its scoring.
- **ATR (Average True Range)** — a volatility measure. CQD uses it to size
  stop‑loss and take‑profit distances so they widen when a coin is jumpy and
  tighten when it's calm.
- **Satellite pairs** — the 5 non‑core coins auto‑selected each day by the
  rotator for their liquidity/volatility/momentum; the other 5 are permanent
  "core" pairs.
- **Watchdog** — a background checker that pages the operator on Telegram if the
  bot goes quiet (no scans for >10 minutes) or if the scheduler silently
  disables a job.

---

### Gaps I could not fully verify (flagged for the parent agent)
1. **No `README.md` exists** in the repo, despite the task referencing a
   "current/old README" — this fact sheet was built from scratch against the code.
2. **`core/cqd_position_tracker.py` is NOT present** (the task listed it as
   optional "if present"; it is absent). Position state lives entirely in
   `sandbox_engine.py` + `state/wallet_state.json`.
3. **Execution‑vs‑gate discrepancy** (conviction 2 / 2.5 executions below the
   intended ≥7 threshold) — likely manual/test runs, but not provable from code
   alone.
4. **Git remotes confirm** origin = `jjroth89/cqd-trading-bot` (matches Issue
   #11 board); last commit `8253cee` ("backtest issue prep") lines up with the
   P0 backtest‑worker issue being freshly opened.
5. I read the live source on disk; I did **not** execute the bot (no network
   trading, and running it would mutate `wallet_state.json` / hit external APIs).
