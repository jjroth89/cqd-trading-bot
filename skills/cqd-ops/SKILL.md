---
name: cqd-ops
description: "Operate and maintain the CQD trading bot: environment isolation (prevent global Hermes TG token leak), model-independent execution (AI-free math/data path), Telegram smoke tests, scheduler/cron traps, GitHub issue-board management for jjroth89/cqd-trading-bot, and remote backtest worker coordination."
version: 2.0.0
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
2. **AI is reserved for things scripts can't do well** — market sentiment
   analysis, news fetching/summarization, qualitative research. NOT for
   execution, math, or data retrieval.
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

### 2.1 Health check (verdict-first)
CQD is **cron-driven, not a daemon** — `ps` showing no long-lived process is
NORMAL and not a fault. Verify liveness instead:

1. `cronjob action=list` → expect FOUR enabled jobs all `last_status: "ok"`:
   - `cqd-monitor` (every 5m) — polls open positions vs live price, closes on SL/TP.
   - `cqd-trigger` (every 15m) — evaluates watchlist, executes sandbox for conviction >= 7.
   - `cqd-rotator` (daily 04:00 UTC) — rewrites the 10-pair watchlist.
   - `cqd-watchdog` (every 10m, `deliver: local`) — pages via the CQD bot's OWN
     token if the master log is stale; the canary that prevents silent death.
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
| 1 | **`plan`** | Multi-file features (e.g. issue #11 remote worker) | Write atomic TDD task list to `.hermes/plans/` before code |
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
Architecture + acceptance criteria are fully specified in **issue #11**
(Cloudflare Tunnel → Tailscale → reverse SSH → ngrok fallback). Read the issue
before implementing submission/runner code; do not re-derive from scratch. The
procedure is speculative until #11 is implemented — capture proven commands
there only after they run successfully.

## 6. Pitfalls (quick reference)
- Never `cat`/log the CQD token or chat ID. Mask if verification is needed.
- Do not assume the bot's `.env` IS the global one — separate files, separate TG bots.
- `gh issue edit` replaces the whole body — assemble full text first.
- The liveness canary (`cqd-watchdog`, `core/cqd_watchdog.py`, TDD-built) is the
  only thing that pages on engine silence — `ps` empty is NORMAL (cron-driven).
  Recipe + cron registration: `references/watchdog_recipe.md`.
- Model-drift and symlink blocks are **scheduler traps**, not CQD bugs — see §2.3.

## 7. References
- `references/env-isolation.md` — token-leak guardrail proof + code.
- `references/watchdog_recipe.md` — liveness canary build + cron registration.
- `references/issue_11_spec.md` — remote backtest worker architecture.
