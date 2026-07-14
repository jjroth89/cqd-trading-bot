---
name: cqd-ops
description: "Operate and maintain the CQD trading bot: environment isolation (prevent global Hermes TG token leak), model-independent execution (AI-free math/data path), Telegram smoke tests, scheduler/cron traps, GitHub issue-board management for jjroth89/cqd-trading-bot, and remote backtest worker coordination."
version: 2.3.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [CQD, trading-bot, devops, github-issues, telegram, cron, scheduler]
    related_skills:
      - spike
      - test-driven-development
      - systematic-debugging
      - requesting-code-review
      - plan
      - github-issues
      - hermes-cron-patterns
---

# CQD Trading Bot Operations

Recurring operational procedures for the CQD trading bot. The bot is a project
the user runs; it is **NOT** the same process as the main Hermes Agent and must
**NOT** inherit Hermes' global credentials.

> ## ⚠️ STATUS (as of Jul 2026): PAUSED / STASHED
> CQD is **intentionally paused** — a deliberate stash, not an outage. All five
> crons are `state: paused` (via `cronjob action=pause`). The code, `README.md`,
> and `docs/LESSONS_LEARNED.md` remain the reference implementation. See §2.0 for
> pause/resume. The reusable value (operational patterns) now lives in
> `hermes-cron-patterns`, `container-persistence-patterns`, and
> `hermes-gateway-restart`. Treat CQD as **archived reference**, not live ops,
> unless the user explicitly asks to revive it.

This skill is the single source of truth for CQD operations. Follow it
verbatim. The three sections below are mandatory gates, not suggestions:

1. **RULES** — non-negotiable invariants (token isolation, model independence, verify-don't-assert).
2. **OPERATE** — health checks, Telegram smoke test, scheduler traps.
3. **DEVELOP** — the required skill-driven workflow before any code or env change.

---

## 1. RULES (hard invariants — violate and you will break CQD)

### 1.1 Token isolation — no global Hermes credential leakage
Child processes launched from the container inherit the global `TG_BOT_TOKEN`
/ `TG_CHAT_ID` exported into the Hermes runtime. If the CQD bot ever falls back
to those env vars, its alerts **LEAK to the MAIN Hermes Telegram bot** instead
of the user's CQD chat.

- Bot's own creds: `CQD_TG_BOT_TOKEN` + `CQD_TG_CHAT_ID` (its OWN bot, distinct from the main Hermes TG bot).
- Loaded via `load_dotenv(PROJECT_ROOT / ".env")` in `sandbox_engine.py` / `quant_evaluator.py`.
- Guardrails (proven — see `references/env-isolation.md`):
  1. In any shell wrapper (`cqd_monitor.sh`, `cqd_trigger.sh`, `cqd_rotator.sh`, `cqd_watchdog.sh`) that invokes Python, `unset TG_BOT_TOKEN TG_CHAT_ID` immediately before the python invocation.
  2. In `core/cqd_logger.py` / notification engine, strictly read `os.getenv("CQD_TG_BOT_TOKEN")` / `os.getenv("CQD_TG_CHAT_ID")`.
  3. Fail-fast at init: if `os.getenv("TG_BOT_TOKEN")` is present in memory, raise `RuntimeError` and halt — never send.

### 1.2 Model independence & AI boundary
CQD execution is **100% script-only and must never depend on the active chat
model.** The user rotates models constantly (free/cheap tiers, no paid plan),
so ANY execution-path coupling to a model name is guaranteed to silently break.

1. **Math & market data are scripts, not AI.** All indicator math, candle/OHLCV
   fetching (`ccxt`), wallet/SL/TP computation, and Telegram alerts run in plain
   Python via cron (`no_agent=true`, `script=...`). They NEVER call an LLM.
   Verified: `core/` + `scripts/` have zero AI/LLM imports (no openai, anthropic,
   langchain, litellm, hermes, gpt, claude, gemini).
2. **No AI/LLM anywhere — "sentiment" is numeric, not a model.** All indicator
   math, candle/OHLCV fetching (`ccxt`), wallet/SL/TP computation, and Telegram
   alerts run in plain Python via cron (`no_agent=true`, `script=...`). The only
   "market mood" input is the numeric **Fear & Greed Index** + **BTC dominance**
   pulled from public REST APIs (alternative.me, CoinGecko) — there is NO language
   model in the decision path at all. Verified: `core/` + `scripts/` have zero
   AI/LLM imports (no openai, anthropic, langchain, litellm, hermes, gpt, claude,
   gemini). The engine is 100% deterministic script code.
3. **Cron jobs must NOT be pinned to a rotating chat model.** The Hermes
   scheduler blocks UNPINNED jobs after an active-model drift
   (`RuntimeError: Skipped to prevent unintended spend... job is unpinned`).
   Because CQD crons are `no_agent` script jobs that never call the LLM, pin them
   ONCE to a STABLE provider/model (`openrouter` + a frozen model string) and
   leave them. The watchdog (`core/cqd_watchdog.py`) provides `self_heal_cron_pins()`
   + `_detect_drift_blocks()`: when run from an interactive Hermes session the
   `cronjob` tool is available, so a model rotation is auto-healed; inside the
   `no_agent` cron the `cronjob` CLI is NOT on PATH, so drift blocks instead
   surface as a Telegram SCHEDULER ALERT and are healed from the session. Net:
   execution is independent of your chat model — a rotation pages you, it does
   not silently kill CQD.
4. If a future feature needs AI *inside* a job, that code must call a model
   explicitly via its own API key/credentials inside the script — never via the
   scheduler's `model`/`provider` field.

### 1.3 Verify, don't assert (THE babysitter rule)
Most CQD outages came from claiming something worked ("symlinks will resolve",
"`provider: none` will be accepted", "`cronjob` will be on PATH") **without
proving it.** For anything that touches an environment assumption, scheduler
behavior, or external boundary:

- **SPIKE FIRST.** Load the `spike` skill and run a throwaway experiment with a
  Given/When/Then verdict that *observes actual output* before committing code
  or config. No assertion of success without a red/green artifact behind it.
- **Never say "it works" without a real run or a failing-then-passing test.**
- When asked "is it running?", answer YES/NO up front, name the single broken
  thing, THEN show evidence (see §2.1). No narrative essay.

---

## 2. OPERATE

### 2.0 Pause / Resume (current lifecycle state)

CQD is **cron-driven, not a daemon** — `ps` showing no long-lived process is
NORMAL. As of Jul 2026 the crons are **paused** (stashed), so `cronjob
action=list` shows `state: paused` / `enabled: false` for all five — that is
EXPECTED, not a fault.

- **Job IDs:** `cd62ada9f940` (monitor), `beabda3c125e` (trigger),
  `715e283a5c94` (rotator), `68010b398ba2` (watchdog),
  `76a749c93ccd` (daily-status-report).
- **Pause (done this session):** `cronjob action=pause job_id=<id>` for each.
- **Resume:** `cronjob action=resume job_id=<id>` — already pinned, no re-pin.
- **Do NOT "fix" a paused bot** by restarting crons unless the user explicitly
  wants CQD live again. A paused bot is the correct end-state of a stash.
- Why it was paused + full retrospective: `docs/LESSONS_LEARNED.md` (repo) and
  the "Project Status" section of `README.md`.

### 2.1 Health check (verdict-first)
CQD is **cron-driven, not a daemon** — `ps` showing no long-lived process is
NORMAL and not a fault. Verify liveness instead:

1. `cronjob action=list` → expect FOUR enabled jobs all `last_status: "ok"`:
   - `cqd-monitor` (every 5m) — polls open positions vs live price, closes on SL/TP.
   - `cqd-trigger` (every 15m) — evaluates watchlist, executes sandbox for conviction >= 7.
   - `cqd-rotator` (daily 04:00 UTC) — rewrites the 10-pair watchlist.
   - `cqd-watchdog` (every 10m, `deliver: local`) — pages via the CQD bot's OWN
     token if the master log is stale; the canary that prevents silent death.
   NOTE: a 5th job (`cqd-health`, hourly, and `cqd-daily-status-report`, a
   one-shot) may also exist; for health gating, the FOUR core jobs above are the
   liveness baseline.
2. Tail `logs/cqd_master_log.csv` — last timestamp must be within the schedule window (NOT hours stale).
3. Inspect `state/wallet_state.json` `open_positions` — the monitor must be policing any open position's SL/TP.
   - IMPORTANT: the monitor only exits on a SL/TP *touch*. A position sitting
     between its SL and TP is HELD CORRECTLY, NOT "forgotten". Read the code
     before claiming neglect — an open position in-range is expected, not a bug.
4. If a cron shows `last_status: "error"` while the script runs fine by hand →
   it is a **scheduler trap** (§2.3), not a CQD code bug. Diagnose before patching.
5. `deliver: local` cron output is NOT delivered to the TUI — inspect via
   `cronjob action=list` / `action=run`, never assume you'll "see" it.

### 2.2 Telegram smoke test (proven)
Load the bot's `.env` directly — do NOT rely on the inherited global env:

```python
import os, json, urllib.request
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path("/opt/data/cqd-trading-bot/.env"))
token = os.getenv("CQD_TG_BOT_TOKEN"); chat_id = os.getenv("CQD_TG_CHAT_ID")
url = f"https://api.telegram.org/bot{token}/sendMessage"
payload = json.dumps({"chat_id": int(chat_id), "text": "✅ CQD Telegram Bot — test ping successful"}).encode()
req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as r:
    res = json.loads(r.read()); print("OK", res["result"]["message_id"])
```
Success = a `message_id` is returned. Never log the token.

### 2.3 Scheduler / cron traps (consolidated)
Both are **scheduler-side**, not CQD code bugs. Full detail in `hermes-cron-patterns`.

- **Symlink containment block** — the scheduler rejects any script whose
  real (symlink-resolved) path escapes `/opt/data/scripts/`. FIX: never symlink
  CQD wrappers into `/opt/data/scripts/`; use a real in-tree trampoline that
  `exec`s the repo wrapper at `/opt/data/cqd-trading-bot/cron_wrappers/`.
- **Model-drift block** — UNPINNED jobs error after the active chat model
  changes. FIX: pin once to stable `openrouter` + frozen model (§1.2 rule 3).
  `provider: none` is INVALID.

---

## 3. DEVELOP (mandatory skill-driven workflow)

Before ANY code change, config change, or environment assumption, follow this
gate. Skipping any step for "just this once" is not permitted for CQD work.

| Step | Skill | When | Why |
|------|-------|------|-----|
| 0 | **`spike`** | Any change touching an env/scheduler/external boundary (symlinks, cron pins, PATH, API shapes) | Proves the assumption with observed output before you commit — kills the "I asserted it worked" failure mode |
| 1 | **`plan`** | Multi-file features (e.g. remote backtest worker) | Write atomic TDD task list to `.hermes/plans/` before code |
| 2 | **`test-driven-development`** | Every new feature / bugfix | Failing test FIRST (RED), minimal code (GREEN), refactor. No prod code without a test that failed first |
| 3 | **`systematic-debugging`** | Structural / cron / intermittent faults | 4-phase root-cause loop (reproduce → rank → isolate → fix) before patching |
| 4 | **`requesting-code-review`** | Any commit / push / "ship" | Independent reviewer scans for token leakage, lint, regressions |

Example proven flow (the liveness watchdog): `spike` the staleness detector →
`test-driven-development` wrote `tests/test_watchdog.py` RED before
`core/cqd_watchdog.py` existed → `systematic-debugging` isolated the symlink
containment block → `requesting-code-review` before commit.

---

## 4. GitHub issue-board workflow
- Repo: `jjroth89/cqd-trading-bot`. `gh` is authed as `jjroth89` (token scopes include `repo`, `workflow`).
- Label set: `P0` (red/critical) · `P1` · `P2` · `P3` · `P4` (green/backlog) · plus `enhancement`, `infra`, `backtesting`, `needs-review`, `bug`, `documentation`.
- For long / markdown-heavy bodies, write the body to a temp `.md` file and use
  `gh issue create --body-file <file>` — avoids heredoc truncation and quoting
  hell. `rm` the temp file after. (User pastes of long bodies get truncated
  mid-stream; `--body-file` is immune.)
- Apply the correct priority label (`P0`–`P4`) plus topical labels.
- To edit an existing issue body, `gh issue edit N --body-file <file>` (replaces
  ENTIRE body — assemble the full intended text first, not a delta) and adjust
  labels with `--add-label` / `--remove-label`.
- Backtest job descriptor shape (remote worker) is JSON: `job_id`, `pairs`, `from`, `to`, `config_path`, `random_seed`, `optuna`.

## 5. Remote backtest worker
Architecture + acceptance criteria are fully specified in **the backtest-worker
tracking issue** (Cloudflare Tunnel → Tailscale → reverse SSH → ngrok fallback).
Read that issue before implementing submission/runner code; do not re-derive from
scratch. The procedure is speculative until it is implemented — capture proven
commands there only after they run successfully.

## 6. Pitfalls (quick reference)
- Never `cat`/log the CQD token or chat ID. Mask if verification is needed.
- Do not assume the bot's `.env` IS the global one — separate files, separate TG bots.
- `gh issue edit` replaces the whole body — assemble full text first.
- The liveness canary (`cqd-watchdog`, `core/cqd_watchdog.py`, TDD-built) is the
  only thing that pages on engine silence — `ps` empty is NORMAL (cron-driven).
  Recipe + cron registration: `references/watchdog_recipe.md`.
- **Documentation tone (USER PREFERENCE — verified this session):** the CQD
  README must be TECHNICAL + OPTIMISTIC, not "too human." No emoji-heavy intros,
  no "what this means for you" sections, no "not a proven system" apologies.
  Factsheet stays plain-English for non-technical readers ONLY. When writing
  user-facing docs, do NOT single out any one issue (e.g. #11) as "the most
  crucial" — backtesting is a roadmap milestone, not a singular blocker.
 - **General doc-style guidance (applies beyond CQD) now lives in the
 `documentation-writing` skill** — technical over hand-holding, optimistic-but-
 honest, post-mortem reads as a deliberate pivot not a failure. Follow that for
 any user-facing writing; this bullet is the CQD-specific instance.
- **Git push discipline:** when the user says "push/commit," the job is NOT done
  at `git commit`. CQD's `main` often has **NO upstream configured**, so a bare
  `git push` errors with "no upstream." Always `git push -u origin main` (sets
  tracking). Dropping the push step after committing is how the README stayed
  local for a whole session.
- Model-drift and symlink blocks are **scheduler traps**, not CQD bugs — see §2.3.

## 7. Document / Audit CQD (fact-sheet protocol)

When asked to document, review, or produce an "audit" / "factsheet" / "what does
this bot actually do" artifact for CQD, distinguish the two deliverables:

- **FACT SHEET** (default for "audit" / "what does this bot do"): plain-English,
  non-technical audience, everyday glossary, blunt honesty boxes. Use
  `templates/factsheet.md`.
- **README** (when the user explicitly wants the repo README — they do): write it
  **TECHNICAL and OPTIMISTIC-but-honest**, for an operator/developer audience. NO
  emoji bullet lists, NO "what this means for you" handholding, NO "practice tool,
  not a proven system" soft-pedaling — the user rejected that as "too human."
  Lead with what works (deterministic engine, green crons, consistent convictions),
  frame limitations as roadmap milestones, keep real gaps without apology. Do NOT
  anchor the roadmap to a specific issue number or call any single issue "the most
  crucial" — backtesting is a next-step milestone, not a singular blocker.

Protocol (applies to both):

1. **Read the actual code, don't infer from names.** A README now exists
   (v0.1.0) but the source IS still the spec — verify every claim in it against
   code before publishing. Read at minimum: `core/quant_evaluator.py`,
   `core/sandbox_engine.py`, `core/cqd_logger.py`, `core/cqd_watchdog.py`,
   `core/rotate_watchlist.py`, every `cron_wrappers/*.sh`, `config/*.json`,
   `state/wallet_state.json`, `logs/cqd_master_log.csv`, `state/tg_sent_log.csv`,
   and `state/macro_cache.json`.
2. **Separate verified facts from gaps.** Every claim must trace to code you
   read. Put anything you could NOT verify in a clearly-marked ⚠️ box or a
   "Gaps I could not fully verify" list at the bottom — do not paper over
   uncertainty. A fact sheet that hides unverified assumptions is worse than none.
3. **Prove the "no live trading" claim with a probe**, never assert it. Run
   `scripts/verify_sandbox_only.sh` (or the equivalent grep) to show there is no
   `create_order` / `apiKey` / `secret` / `exchange.create_*` anywhere, and that
   exchange use is read-only (`fetch_ticker` / `fetch_ohlcv`).
4. **Capture the honest limitations** the user cares about (see checklist
   below). Stakeholders repeatedly want to know: is it real-money? is it
   backtested? is it portable? Be blunt.
5. Include a **everyday glossary** (conviction, SL/TP, ATR, FGI, sandbox,
   watchlist, satellite pairs, watchdog) — the audience is often non-technical.
6. Use the starter scaffold at `templates/factsheet.md`; fill the sections, keep
   the ⚠️ honesty boxes. For a technical/optimistic README, copy
   `templates/cqd_readme.md` and fill the live numbers (conviction cadence,
   open positions, version) — do NOT inject emoji or "what this means for you".

### Honest-limitations checklist (verify each against current code before writing)
- **Simulation-only:** no live exchange keys, no order placement. Confirm via the
  sandbox-only probe. If the probe ever finds `create_order`/keys, STOP and flag
  it — that would be a regression from the sandbox contract.
- **Not backtested:** the strategy has NO historical validation. The backtest/
  Optuna harness is a roadmap milestone (tracked in the issue board) — do NOT name
  a single issue as "the most crucial." State this explicitly; "win rate" is unknown.
- **Conviction gate lives in the wrapper, not the engine.** The "conviction >= 7 →
  open" rule is enforced only in `cron_wrappers/cqd_trigger.sh`, NOT inside
  `sandbox_engine.py` (which opens whatever payload it's handed). The master log
  may therefore show EXECUTE rows at conviction < 7 (manual/test runs, or a prior
  config). Don't claim ">=7 is enforced" without this caveat.
- **Trailing stops advertised but NOT implemented.** `config.json` declares
  `trailing_stop_activation_multiplier` / `trailing_stop_distance_multiplier`, but
  only fixed ATR SL/TP exist in the engine. Flag as a gap when documenting config.
- **Position state is NOT in a separate tracker.** There is no
  `cqd_position_tracker.py` — open/closed positions live entirely in
  `core/sandbox_engine.py` + `state/wallet_state.json`. Don't reference a missing
  module.
- **Tuned to one machine.** Paths (`/opt/data/cqd-trading-bot`,
  `/opt/data/cqd_venv/bin/python`), cron schedules, the watchdog's hardcoded model
  pin (`tencent/hy3:free` / `openrouter`), and Binance-only data are hard-wired.
  Not portable; relocating needs path/config rework.
- **Tiny track record.** Few sandbox executions; `trade_history` may be empty and
  an `open_positions` entry may have no recorded close — bookkeeping around closed
  trades can be incomplete. Don't over-claim a clean audit trail.

### Reusable verification probes
- `scripts/verify_sandbox_only.sh` — proves no live-order code / exchange keys.
- Grep `core/*.py` for `cqd_position_tracker` to confirm the module is absent.
- `wc -l state/tg_sent_log.csv` matched against EXECUTE/EXIT rows in the master
  log = Telegram delivery reconciliation (same logic as `cqd_telegram_verification.sh`).

## 8. References
- `references/env-isolation.md` — token-leak guardrail proof + code.
- `references/watchdog_recipe.md` — liveness canary build + cron registration.
- `references/issue_11_spec.md` — remote backtest worker architecture.
- `references/cqd_architecture.md` — verified trading-logic / code-flow + current
  honest-limitations baseline (knowledge bank for audits/doc tasks).
- `references/status_report.md` — model-free data collector + agent-rendered
  daily status report (sandbox-honest, templated trading-dashboard layout).
- `templates/factsheet.md` — starter fact-sheet scaffold for CQD doc tasks.
- `scripts/verify_sandbox_only.sh` — probe proving CQD is simulation-only.
