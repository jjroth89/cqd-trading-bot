# CQD Trading Bot — Technical Brief

> **Purpose:** Extract the *exact* operational/technical mechanics from source code so a
> writer can turn this into a human-facing README. Every claim below carries a
> `file:line` citation. All references are relative to repo root `/opt/data/cqd-trading-bot`.

---

## 1. Architecture at a Glance (model-independent by design)

The bot is a **fully deterministic, script-only pipeline**. There is **no LLM / AI in the
runtime decision path at all** — every indicator, score, and trade parameter is computed by
pure Python/NumPy code in `core/quant_evaluator.py` and `core/sandbox_engine.py`.

- `core/quant_evaluator.py` opens with: *"Heavy-lifting engine. Zero LLM dependencies."*
  (`core/quant_evaluator.py:5`). The module docstring also states all math (technical
  indicators, volatility clustering, cash-flow metrics) is script-computed
  (`core/quant_evaluator.py:2-7`).
- `core/cqd_logger.py` opens with: *"Zero-token, script-intensive CSV appender …
  no LLM involvement ever."* (`core/cqd_logger.py:5-6`).
- The only "external intelligence" is **market-data API sentiment** — Fear & Greed Index and
  global BTC dominance — fetched from `alternative.me` and CoinGecko, **not** an LLM
  (`core/quant_evaluator.py:311-363`, `fetch_sentiment`). These are plain HTTP calls with a
  2-hour cache (`core/quant_evaluator.py:79`, `MACRO_CACHE_TTL_SECONDS`).

**Correction to the brief's premise:** the parent task framed this as *"math is scripts, AI
reserved for sentiment."* The code does **not** use an AI/LLM for sentiment — it pulls
numeric sentiment indices from public APIs. The whole engine is "model-independent" in the
strongest sense: it runs with `no_agent=true` cron jobs and never calls a language model.

---

## 2. Precise Trigger Logic — What Score Opens a Position

There are **two independent gates**, both must pass:

### Gate A — Conviction score generation (evaluator)
- Base score starts at 5 and is adjusted by rule-based signals
  (`core/quant_evaluator.py:570`).
- Scoring inputs: RSI, MACD histogram, Bollinger position, volume spike, EMA alignment,
  volatility regime, CVD/CMF/MFI cash-flow, and Fear & Greed contrarian signal
  (`core/quant_evaluator.py:574-692`).
- Score is clamped to `[1, 10]` (`core/quant_evaluator.py:695`).
- **Direction decision:** `score >= 7` ⇒ `long`; `score >= 5` ⇒ `neutral`; `score >= 3` ⇒
  `neutral_short_bias`; else `short` (`core/quant_evaluator.py:696-703`).
  → *A position is only possible when the conviction score is **≥ 7**, which is the sole
  condition that yields a `long` direction.*

### Gate B — Shell trigger execution
- `cron_wrappers/cqd_trigger.sh` reads `conviction_score` from the evaluator payload and
  executes the sandbox **only if `CONVICTION -ge 7`**
  (`cron_wrappers/cqd_trigger.sh:87-89`).
- The sandbox engine additionally vetoes non-tradeable payloads:
  - ignores anything whose `action != "EXECUTE"` (`core/sandbox_engine.py:345-347`);
  - ignores `direction == "neutral"` (`core/sandbox_engine.py:363-365`);
  - skips if `global_max_open_positions` is reached (default 5)
    (`core/sandbox_engine.py:373-381`, `:467-469`);
  - skips if a position for that pair is already open
    (`core/sandbox_engine.py:392-394`, `:480-481`).

**Net rule:** *Score ≥ 7 (long) AND action==EXECUTE AND not neutral AND under the open-position
cap AND pair not already held ⇒ a position is opened.*

---

## 3. Exact SL / TP Computation Method

### 3a. Percentage levels (evaluator → payload)
Computed from live ATR in `compute_trade_parameters`:

```
stop_loss_pct   = (atr_stop_loss_multiplier * atr_abs) / last_price * 100
take_profit_pct = (atr_take_profit_multiplier * atr_abs) / last_price * 100
```

- `core/quant_evaluator.py:520-521` (the formula).
- Default multipliers: `atr_stop_loss_multiplier = 1.5`,
  `atr_take_profit_multiplier = 3.0` (`core/quant_evaluator.py:460-461`,
  `core/quant_evaluator.py:518-519`).
- `atr_abs` is the current bar's ATR in price units; `last_price` is the last close
  (`core/quant_evaluator.py:501-502`). If `last_price <= 0` or `atr_abs <= 0`, the evaluator
  returns `neutral` with `None` SL/TP (`core/quant_evaluator.py:504-515`).

### 3b. Absolute price levels (sandbox engine)
The sandbox converts the percentages into absolute price levels at execution time:

```
long:  sl_price = entry * (1 - sl_pct/100);  tp_price = entry * (1 + tp_pct/100)
short: sl_price = entry * (1 + sl_pct/100);  tp_price = entry * (1 - tp_pct/100)
```

- `core/sandbox_engine.py:403-408` (in `execute_trade`) and the identical copy in
  `_execute_trade_into_wallet` (`core/sandbox_engine.py:488-493`).
- **Fallback defaults** if the payload omits the percentages: `sl_pct = 2.0`,
  `tp_pct = 5.0` (`core/sandbox_engine.py:399-402`, `:484-487`).

### 3c. Exit evaluation (monitor)
`monitor_positions` checks live price against the stored levels every tick:

```
long:  close if price <= sl_price (STOP_LOSS) or price >= tp_price (TAKE_PROFIT)
short: close if price >= sl_price (STOP_LOSS) or price <= tp_price (TAKE_PROFIT)
```

- `core/sandbox_engine.py:571-580`.
- PnL is `size_usdt * price_diff_pct`, with `price_diff_pct` sign-flipped for shorts
  (`core/sandbox_engine.py:583-587`); wallet balance is credited `size + pnl`
  (`core/sandbox_engine.py:589`).

### Position sizing (for completeness)
- Evaluator: `raw_size = balance * (default_position_size_pct/100)`, clamped to
  `[min_position_size_usdt, max_position_size_usdt]` = `[50, 500]`
  (`core/quant_evaluator.py:524-531`, defaults at `:457-459`).
- Sandbox applies the same clamp if it sizes independently
  (`core/sandbox_engine.py:383-390`, `:471-478`).

---

## 4. The Cron Schedules

Four wrappers carry an explicit `cronjob action=create schedule=…` header. A fifth
(rotator) and a sixth (telegram verification) also exist — see §4.5/§7 discrepancies.

| # | Wrapper | Schedule | Cron name | What it does |
|---|---------|----------|-----------|--------------|
| 1 | `cron_wrappers/cqd_trigger.sh` | `*/15 * * * *` (every 15 min) | `cqd-evaluator` | Loops the watchlist, runs `quant_evaluator.py` per pair, and if `conviction >= 7` runs `sandbox_engine.py --execute` (`cron_wrappers/cqd_trigger.sh:16`, `:61-96`). |
| 2 | `cron_wrappers/cqd_monitor.sh` | `*/5 * * * *` (every 5 min) | `cqd-monitor` | Runs `sandbox_engine.py --monitor` to close any position hitting SL/TP (`cron_wrappers/cqd_monitor.sh:14`, `:46`). |
| 3 | `cron_wrappers/cqd_health.sh` | `0 * * * *` (hourly) | `cqd-health` | Emits a JSON health report (venv, ccxt, wallet, config, lock age); rates status healthy/degraded/unhealthy (`cron_wrappers/cqd_health.sh:9`, `:99-105`). |
| 4 | `cron_wrappers/cqd_watchdog.sh` | `*/10 * * * *` (every 10 min) | `cqd-watchdog` | Runs `cqd_watchdog.py` liveness check + scheduler self-heal (`cron_wrappers/cqd_watchdog.sh:9`, `:37`). |

All four are configured with `no_agent=true` (no LLM), e.g.
`cron_wrappers/cqd_trigger.sh:18`, `cron_wrappers/cqd_monitor.sh:16`,
`cron_wrappers/cqd_watchdog.sh:10`.

### How the trigger chain actually executes
1. `cqd_trigger.sh` resolves the project root via `readlink -f` (symlink-safe,
   `cron_wrappers/cqd_trigger.sh:26`), sources `.env`, scrubs global TG creds
   (`:85`), evaluates each pair (`:67-72`), checks conviction (`:75-80`), and on
   `>= 7` calls `sandbox_engine.py --execute <payload>` (`:89`).
2. The monitor cron (every 5 min) then watches open positions and closes on SL/TP.

---

## 5. Token-Isolation Guardrail (credential leak prevention)

The CQD bot uses its **own** Telegram credentials (`CQD_TG_BOT_TOKEN`, `CQD_TG_CHAT_ID`) and
**must never** see the global Hermes bot token (`TG_BOT_TOKEN` / `TG_CHAT_ID`). The guardrail
is enforced in **four** layers:

1. **Import-time fail-fast (Python).** Each core module raises `RuntimeError` if either
   global var is set:
   - `core/sandbox_engine.py:64-69`
   - `core/cqd_watchdog.py:40-45`
   - `core/cqd_logger.py:38-43`
2. **Send-time guard (sandbox).** `send_telegram_alert` raises `RuntimeError` if
   `TG_BOT_TOKEN` is present, before any network call
   (`core/sandbox_engine.py:238-243`).
3. **Cron shell scrub.** Every wrapper `unset TG_BOT_TOKEN TG_CHAT_ID` before invoking
   Python:
   - `cron_wrappers/cqd_trigger.sh:85`
   - `cron_wrappers/cqd_monitor.sh:42`
   - `cron_wrappers/cqd_watchdog.sh:30`
   (note: `cqd_health.sh` and `cqd_rotator.sh` do **not** currently call `unset` — see §7).
4. **Credential loader only reads CQD vars.** `send_telegram_alert` reads exclusively
   `CQD_TG_BOT_TOKEN` / `CQD_TG_CHAT_ID` (`core/sandbox_engine.py:217-221`);
   `cqd_watchdog.py` does the same (`core/cqd_watchdog.py:80-81`).

---

## 6. The Two Scheduler Traps

### Trap 1 — Symlink containment (PROJECT_ROOT resolution)
The cron system may invoke the wrappers through a **symlink** (e.g. `/opt/data/scripts/…`).
Each wrapper resolves its true location with `readlink -f "${BASH_SOURCE[0]}"` and derives
`PROJECT_ROOT` from it, so paths stay correct regardless of how the script was launched:

- `cron_wrappers/cqd_trigger.sh:26-27`
- `cron_wrappers/cqd_monitor.sh:24-25`
- `cron_wrappers/cqd_health.sh:17-18`
- `cron_wrappers/cqd_rotator.sh:18-19`
- `cron_wrappers/cqd_watchdog.sh:15-16`

Python modules do the equivalent at import: `PROJECT_ROOT = SCRIPT_DIR.parent`
(`core/sandbox_engine.py:37-38`, `core/quant_evaluator.py:28-29`,
`core/cqd_watchdog.py:19`).

### Trap 2 — Model-drift pin (scheduler self-heal)
The Hermes scheduler blocks cron jobs whose pinned model has drifted from the active chat
model (error phrase *"…job is unpinned"*). Because CQD runs `no_agent=true`, it must never
depend on the user's rotating chat model. The watchdog therefore **re-pins** any drift-blocked
CQD job to stable, never-rotated values:

- Drift phrase constant: `MODEL_DRIFT_PHRASE = "job is unpinned"`
  (`core/cqd_watchdog.py:29`).
- Stable pin values: `STABLE_CRON_MODEL = "tencent/hy3:free"`,
  `STABLE_CRON_PROVIDER = "openrouter"` (`core/cqd_watchdog.py:30-31`).
- Only jobs whose script name starts with `cqd_` are touched
  (`CQD_SCRIPT_PREFIX = "cqd_"`, `core/cqd_watchdog.py:32`; filter at `:153`, `:178`).
- `self_heal_cron_pins()` lists jobs via the `cronjob` CLI, detects drift blocks, and
  re-pins them (`core/cqd_watchdog.py:139-162`). It is invoked on every watchdog run
  (`core/cqd_watchdog.py:231-235`).
- Detection helper `is_model_drift_block()` (`core/cqd_watchdog.py:132-136`) and
  `_detect_drift_blocks()` (`core/cqd_watchdog.py:165-182`) surface blocked jobs to Telegram
  when the `cronjob` CLI is unavailable (i.e. inside a `no_agent` cron).

---

## 7. Discrepancies / Things the Writer Should Know

1. **No "old README" exists.** A repo-wide file search for `README*` returned **0 results**,
   and there is no `docs/README.md` prior to this brief. Nothing to diff against — this file
   *is* the first docs artifact. (Flagged per the task's "discrepancies vs the old README"
   request.)

2. **"4 cron schedules" — actually 5 registered wrappers + 1 un-scheduled helper.**
   - The four with explicit `schedule=` headers are trigger/monitor/health/watchdog (§4).
   - **`cqd_rotator.sh` is a 5th cron** ("Runs daily at 04:00 UTC",
     `cron_wrappers/cqd_rotator.sh:5`) that rewrites `config/watchlist.json` with a
     screen-ranked 10-pair list (5 core + 5 satellite pairs,
     `core/rotate_watchlist.py:44`, `:6-13`). It has a stated time but **no
     `cronjob action=create schedule=` line** in its header.
   - **`cqd_telegram_verification.sh`** exists but has **no schedule documented at all**
     (it cross-checks `tg_sent_log.csv` vs the master log, `cron_wrappers/cqd_telegram_verification.sh:1-71`).
   - ⇒ The "4 schedules" in the brief maps to the four explicitly-scheduled wrappers;
     mention rotator (daily 04:00) and the verification script as additional automation.

3. **Token scrub gap.** `cqd_health.sh` and `cqd_rotator.sh` source `.env` but do **not**
   call `unset TG_BOT_TOKEN TG_CHAT_ID` (unlike trigger/monitor/watchdog). Low risk (they
   don't send Telegram messages), but inconsistent with the guardrail in §5.

4. **"AI reserved for sentiment" is inaccurate.** There is no LLM anywhere in the pipeline.
   Sentiment = numeric Fear & Greed + BTC dominance from public REST APIs
   (`core/quant_evaluator.py:311-363`). The engine is 100% deterministic script code.

5. **Watchlist content vs. rotator.** `config/watchlist.json` currently holds 10 pairs
   (`config/watchlist.json:1-11`: BTC, ETH, BNB, SOL, ADA, UTK, OPN, ZEC, XRP, ALLO), but
   the rotator's documented core set is BTC/ETH/BNB/SOL/ADA (`core/rotate_watchlist.py:44`)
   — the satellite half (UTK/OPN/ZEC/XRP/ALLO) is the dynamic portion. The static file is a
   snapshot; the rotator overwrites it daily.

---

## 8. Master Log — Event Types Present

Central audit trail: `logs/cqd_master_log.csv`, columns
`Timestamp, Component, Event_Type, Pair, Conviction, FGI, BTC_Dom, PnL_USDT, Details`
(`core/cqd_logger.py:9`, `:45-56`).

Event types emitted by the code (wrapper functions in `core/cqd_logger.py`):

| Event_Type | Component | Source | Meaning |
|------------|-----------|--------|---------|
| `SCAN` | EVALUATOR | `log_scan` (`core/cqd_logger.py:158-160`) | Per-pair evaluation tick (167 rows in current log). |
| `EXECUTE` | SANDBOX | `log_execute` (`core/cqd_logger.py:163-169`) | Position opened (2 rows). |
| `EXIT` | SANDBOX | `log_exit` (`core/cqd_logger.py:172-177`) | Position closed on SL/TP (0 rows — no exits yet). |
| `ERROR` | varies | `log_error` (`core/cqd_logger.py:180-188`) | Exception captured (1 row). |
| `TG_ERROR` | TG | `log_tg_error` (`core/cqd_logger.py:191-193`) | Telegram send failure (2 rows). |
| `INFO` / `TEST` / `SMOKE` / `INSTANCE` | TEST/SYSTEM | generic `log_event` (`core/cqd_logger.py:130-153`) | Test/smoke/self-test rows only (e.g. `logs/cqd_master_log.csv:2,25,27,30`). |

**Current log summary (176 lines, header + 175 data rows):** dominant event is `SCAN`
(167), with `EXECUTE` (2), `ERROR` (1), `TG_ERROR` (2), and a handful of `TEST`/`INFO`/
`SMOKE`/`INSTANCE` rows. **No `EXIT` events have occurred yet** — consistent with the bot
having opened positions but not yet hit a stop/target.

---

## 9. Quick File Map

| Concern | File:line |
|---------|-----------|
| Conviction scoring (≥7 ⇒ long) | `core/quant_evaluator.py:570`, `:695-703` |
| Trigger gate (shell) | `cron_wrappers/cqd_trigger.sh:87-89` |
| SL/TP % from ATR | `core/quant_evaluator.py:520-521` |
| SL/TP absolute price | `core/sandbox_engine.py:403-408`, `:488-493` |
| Exit logic | `core/sandbox_engine.py:571-580` |
| Position size clamp | `core/quant_evaluator.py:524-531`; `core/sandbox_engine.py:383-390` |
| Cron schedules (×4) | `cqd_trigger.sh:16`, `cqd_monitor.sh:14`, `cqd_health.sh:9`, `cqd_watchdog.sh:9` |
| Rotator (5th) | `cron_wrappers/cqd_rotator.sh:5`; `core/rotate_watchlist.py:44` |
| Credential guardrail (import) | `sandbox_engine.py:64-69`, `cqd_watchdog.py:40-45`, `cqd_logger.py:38-43` |
| Credential guardrail (send) | `core/sandbox_engine.py:238-243` |
| Credential guardrail (shell) | `cqd_trigger.sh:85`, `cqd_monitor.sh:42`, `cqd_watchdog.sh:30` |
| Symlink containment | `cqd_trigger.sh:26-27`, `cqd_monitor.sh:24-25`, `cqd_rotator.sh:18-19`, `cqd_watchdog.sh:15-16` |
| Model-drift pin | `cqd_watchdog.py:29-32`, `:139-162` |
| Log event types | `core/cqd_logger.py:158-193` |
