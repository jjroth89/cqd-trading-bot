# CQD README — Critique & Migration Map

**Purpose:** The repository currently ships **no README.md** (the old one was
never carried into `/opt/data/cqd-trading-bot/`; the only surviving copy lives in
the sibling audit folder `/opt/data/cqd-trading-bot-audit/README.md`). That old
document is **obsolete** on multiple fronts after the recent changes:

- new **watchdog cron** (liveness self-heal),
- **model-independence** rule (scheduler no longer tied to the chat model),
- **scheduler traps** fixed (model-drift block + auto re-pin),
- **sandbox-only** status confirmed and hardened,
- **runs-in-the-operator's-container** reality (no Coolify/standalone deploy),
- Telegram **own-bot isolation** promoted to a hard fail-fast guardrail.

This file is a **critique + migration map only**. It does NOT write the new
README. It tells the next author exactly what to delete, what to add, and how to
sound.

---

## 1. Stale / Wrong Claims in the OLD README

Each row: where the claim appears → why it's now wrong → what the new README must say instead.

| # | Old claim (location) | Why stale / wrong | Required fix |
|---|----------------------|-------------------|--------------|
| 1 | Project root is `/opt/data/cqd/` everywhere (lines 24–25, 35–38, 53–75, 108–110, 153–162) | Repo actually lives at `/opt/data/cqd-trading-bot/`. `/opt/data/cqd` does **not exist**. Every `cp`/`cat`/`python` example in the doc fails. | Replace every `/opt/data/cqd` with `/opt/data/cqd-trading-bot`. |
| 2 | "`uv venv /opt/data/.venv/cqd`" + "`/opt/data/.venv/cqd/bin/python`" (lines 102–105, 153–159) | Canonical venv is `/opt/data/cqd_venv` (see `Makefile` `VENV := /opt/data/cqd_venv`). The `.venv/cqd` path was removed in commit `8253cee`. | Use `/opt/data/cqd_venv/bin/python`; reference `make install-deps`. |
| 3 | Credentials at `/opt/data/cqd/config/credentials.env` (lines 25, 57, 108–110) | Live config is a `.env` at the repo root: `/opt/data/cqd-trading-bot/.env` (see `.env.template`, all `*_cron.sh` wrappers `source` it). `credentials.env` does not exist. | Document the root `.env` + `.env.template`; drop `credentials.env`. |
| 4 | "Hermes Admin: `/opt/data/.hermes/credentials.env`" (line 24) | Misleading framing. The real threat model is a **leaked global `TG_BOT_TOKEN`/`TG_CHAT_ID`**; the system now **actively refuses** to run if those globals are present (fail-fast in `cqd_watchdog.py`, `cqd_logger.py`, and `unset` in every wrapper). | Reframe credential isolation around the own-bot rule + the fail-fast guardrail, not a static file map. |
| 5 | "When deploying via Coolify … persistent volume mounts" (lines 29–38, 113–124) | The bot runs **inside the operator's existing container**, not as a Coolify-deployed service. There is no separate deploy target. Volume-mount guidance is fiction for this deployment. | Remove Coolify guidance; add the honest "runs in operator's container" caveat (see §2.6). |
| 6 | Cron table lists exactly **5** jobs (lines 79–87) and omits the watchdog (lines 63–68) | A **6th** automation exists: `cqd-watchdog` (`*/10 * * * *`, `cqd_watchdog.sh`, `no_agent=true`) — liveness + drift self-heal. The architecture must be described as the new **4-cron core + watchdog overseer** model (see §2.2). | Add the watchdog to the automation map; reframe as 4-cron architecture + watchdog. |
| 7 | Cron "Safety Header" column implies `set -euo pipefail` + fcntl lock is the complete safety story (lines 81–87) | Real-world runtime risk is the **Hermes scheduler model-drift trap** (`RuntimeError: "…job is unpinned"`), which silently disables crons when the chat model rotates. The old table never mentions it. | Add a scheduler-trap row / note + the watchdog self-heal that mitigates it (see §2.5). |
| 8 | Prerequisites: "SSH key configured for git operations" (line 96) | Setup is now `make install-deps` + root `.env`; SSH/git is incidental, not a prerequisite for running. | Drop SSH from prerequisites; list `make`, `uv`, Python 3.13, `.env`. |
| 9 | Architecture Map omits `core/cqd_watchdog.py` and `cron_wrappers/cqd_watchdog.sh` (lines 58–68) | Those files exist and are load-bearing. | Add both to the tree diagram. |
| 10 | "Review `/opt/data/cqd/DEVELOPMENT_MANIFEST.md` before committing" (line 6) | That path is dead; the manifest is not in the repo. | Remove the pointer, or point to the real dev doc if one is added. |
| 11 | Badge: "Production-grade Sandbox" (line 3) | Still roughly true, but "sandbox" is now a **hard, enforced** guarantee (`sandbox_rules` in `config.json`, no exchange keys). Understated. | Keep badge but back it with the enforced sandbox section (§2.1). |
| 12 | "No exchange API keys configured — paper trading only" (line 141) | True and now **confirmed/hardened**, but buried. The single most important safety fact should lead, not trail. | Elevate sandbox-only to a top-level section + lead sentence. |
| 13 | Telegram described as "main Telegram gateway for mobile notifications" shared with admin (lines 24, 135) | CQD uses its **own** bot (`CQD_TG_BOT_TOKEN`/`CQD_TG_CHAT_ID`), deliberately isolated from the global Hermes bot. Old phrasing implies shared routing. | State explicitly: CQD owns its bot; global tokens are forbidden. |

---

## 2. Required NEW Sections (in the rewritten README)

### 2.1 Sandbox-Only Status (elevate to top)
- Lead with it: **CQD is paper-trading only. No exchange API keys exist anywhere. `sandbox_rules` in `config.json` enforce `global_max_open_positions`, `max_position_size_usdt`, etc.**
- Explain in plain words what "sandbox" means to a non-dev: it simulates trades with fake money, never touches a real exchange, and the code refuses to run if real keys appear.

### 2.2 4-Cron Architecture
- Document the four **core** crons (health, evaluator/trigger, monitor, rotator) **plus the watchdog overseer** (`cqd-watchdog`, `*/10`).
- Give the actual `cronjob action=create … no_agent=true` invocation for each (drawn from the `*.sh` headers), since these are Hermes-scheduler crons, not system cron.
- Note the watchdog's dual job: liveness paging **and** drift self-heal (see §2.5).

### 2.3 Telegram Own-Bot Isolation (hard rule)
- CQD talks only to its **own** Telegram bot via `CQD_TG_BOT_TOKEN`/`CQD_TG_CHAT_ID` from the root `.env`.
- **Fail-fast guardrail:** `core/cqd_watchdog.py` and `core/cqd_logger.py` raise `RuntimeError` if a global `TG_BOT_TOKEN`/`TG_CHAT_ID` is present; every `*_cron.sh` does `unset TG_BOT_TOKEN TG_CHAT_ID` before invoking Python.
- Plain-language: "CQD has its own private bot so its trade alerts can never leak into the operator's personal Telegram."

### 2.4 Model Independence
- CQD execution is **100% script-only** (`no_agent=true`); it must never depend on the user's rotating chat model.
- The watchdog pins CQD crons to stable `STABLE_CRON_MODEL = "tencent/hy3:free"` / `STABLE_CRON_PROVIDER = "openrouter"` so a model switch can't kill a job.

### 2.5 Scheduler Traps (and the fix)
- **The trap:** the Hermes scheduler blocks a cron whose pinned model drifts from the active chat model (`RuntimeError: "…job is unpinned"`), silently disabling it.
- **The fix:** `cqd_watchdog.py` detects `"job is unpinned"` blocks via `self_heal_cron_pins()` and re-pins affected `cqd_*` jobs to the stable values; inside a `no_agent` cron the `cronjob` CLI is absent, so it degrades to a Telegram alert instead of crashing.
- This is why "set -euo pipefail" alone (the old table) is not the real safety story.

### 2.6 Honest "Runs in Operator's Container" Caveat
- CQD is **not** a standalone service or Coolify app. It runs as scripts/crons **inside the operator's existing container** (the same machine running Hermes).
- Be explicit that state, logs, and `.env` live on that container's filesystem (`/opt/data/cqd-trading-bot/…`); there is no separate host, VM, or cloud deploy.
- Drop all Coolify/volume-mount prose (see critique #5).

---

## 3. Tone Guidance

The new README must be **understandable by regular people first**, with developer
vocabulary as a clearly-marked secondary layer.

**Do:**
- Open with a one-paragraph, no-jargon summary: "CQD is an automated crypto
  practice-trader that runs on fake money, sends you alerts on its own Telegram
  bot, and lives inside your server container."
- Put the human-relevant facts up front: it's safe (sandbox), it's yours (own
  bot), it runs where your server already runs (no new infra).
- Use a "What this means for you" gloss next to each technical claim.
- Reserve exact cron invocations, `config.json` keys, and the fail-fast/RuntimeError
  mechanics for a clearly separated "For operators / developers" block or appendix.

**Don't:**
- Lead with badges, manifests, or credential file maps (that's how the old one started).
- Use "production-grade", "dual-credential isolation", or "ephemeral vs persistent
  mount strategy" without a plain-English translation right beside it.
- Imply a deploy/devops step (Coolify, Docker volumes) that doesn't exist.
- Bury the sandbox-only guarantee at line 141.

**Voice target:** a competent non-programmer operator should finish the top half
knowing *what CQD is, that it can't lose real money, that it alerts via its own
bot, and that it already runs on their machine* — without reading a single code
snippet. Devs can scroll down for the exact commands.

---

## 4. Quick Reference — Path/Identity Corrections

| Old (wrong) | New (correct) |
|-------------|---------------|
| `/opt/data/cqd/` (project root) | `/opt/data/cqd-trading-bot/` |
| `/opt/data/.venv/cqd` (venv) | `/opt/data/cqd_venv` |
| `/opt/data/cqd/config/credentials.env` | `/opt/data/cqd-trading-bot/.env` (+ `.env.template`) |
| Coolify deploy target | runs in operator's container (no separate deploy) |
| 5 crons, no watchdog | 4-core crons + `cqd-watchdog` overseer |
| "shared Telegram gateway" | CQD's own bot + global-token fail-fast |
