# CQD — Lessons Learned

*A retrospective on building, operating, and ultimately pausing the Crypto Quant Desk.*
*Compiled July 2026. Companion to the README; read that first.*

---

## 0. TL;DR

CQD is a from-scratch, cron-driven, **deterministic** crypto "quant desk" that runs entirely in a sandbox (fake money, no exchange keys). It was built to prove out an operational pattern — observable pipeline, isolated credentials, scheduler resilience — more than to make money. It succeeded at that. We are pausing active development not because it failed, but because the next questions (signal edge, live capital) are better answered by higher-stakes work the author is now doing. The reusable value lives on as skills and as `core/quant_evaluator.py`.

---

## 1. Project Genesis & Intent

- The project began as a vibe-coded knock-off of an unknown reference ("a trading bot skill called mercury, or something") — old Hermes was prompted to "look at this repo and write something similar."
- The original first-iteration code lived at `/opt/data/cqd/` and **no longer exists**; only a read-only migration audit (`/opt/data/cqd-trading-bot-audit/`, dated 2026-07-11) survives. That audit already described the bot as a *"Production-grade Sandbox"* paper trader — i.e., **fake-money by design from day one.**
- Intent was never "build a profitable trader." It was: stand up a deterministic signal pipeline, make it observable and operationally clean, and learn the container/cron/credential patterns the hard way in a zero-risk environment.
- The "mercury" origin repo/skill is **not present anywhere in this environment** (searched skills, repos, disk). There is no gold-standard original to diff against — which reframes the whole project as original work, not a port.

---

## 2. Architecture That Worked

- **Deterministic engine, zero LLM in the path.** Every decision is indicator math + a numeric Fear & Greed / BTC-dominance input pulled from public REST APIs. This was the single best architectural decision: it made the bot immune to model rotation, made every conviction explainable, and removed an entire class of nondeterminism bugs.
- **Cron-driven, not a daemon.** Four execution crons (monitor/trigger/rotator/health) + a watchdog, all `no_agent=true` script jobs. No long-lived process to OOM, restart, or orphan.
- **CSV audit trail.** `logs/cqd_master_log.csv` + `state/tg_sent_log.csv` give a complete, grep-able history of every scan and alert.
- **Isolated Telegram channel.** CQD has its *own* bot, deliberately separate from the global Hermes gateway bot.

---

## 3. Cron & Scheduler Pitfalls (Hermes)

These cost the most time. All are now codified in the `hermes-cron-patterns` skill.

### 3.1 Symlink containment block
The Hermes scheduler canonicalizes a cron script's real path and **rejects anything that resolves outside `/opt/data/scripts/`**. Symlinking `/opt/data/scripts/cqd_monitor.sh` → `/opt/data/cqd-trading-bot/cron_wrappers/cqd_monitor.sh` was silently blocked with `Blocked: script path resolves outside the scripts directory`.
**Fix:** replace symlinks with real in-tree **trampoline** files in `/opt/data/scripts/` that `exec` the canonical wrapper. Single source of truth preserved, containment satisfied.

### 3.2 Model-drift unpinned-job block
The scheduler **disables any unpinned job after the active chat model drifts** (`RuntimeError: Skipped to prevent unintended spend... job is unpinned`). This fires even for `no_agent` script jobs that *never call an LLM* — so it killed the rotator purely because the user switched models (`poolside/laguna-m.1` → `tencent/hy3`).
**Fix:** pin every job once to a stable provider + frozen model string. `provider: none` is invalid; use a real stable string.

### 3.3 `no_agent` jobs still need pinning
A common wrong assumption: "it's a script, it doesn't use the model, so it doesn't need a pin." False. The drift guard is structural, not model-usage-based. **Every** cron job must be pinned or it can die silently on model rotation.

### 3.4 `deliver` semantics
- `deliver: local` → output saved, nothing sent. Use for watchdog/silent-success jobs.
- `deliver: all` / `telegram:...` → pushes to connected channels. Watchdog alerts go via CQD's *own* bot, not the global gateway, to avoid spamming the wrong chat.
- Inside a `no_agent` cron, the `cronjob` CLI is **absent**, so drift surfaces as a scheduler alert, not a self-healable call.

### 3.5 Trampoline pattern (canonical)
```bash
#!/bin/bash
# /opt/data/scripts/cqd_monitor.sh  (real file, passes containment)
exec /opt/data/cqd-trading-bot/cron_wrappers/cqd_monitor.sh "$@"
```

---

## 4. Container Persistence & Runtime

### 4.1 Ephemeral overlay vs `/opt/data/`
The container has an ephemeral overlay; anything written outside `/opt/data/` is **eradicated on restart**. All durable state (repo, venv, `.env`, logs) lives under `/opt/data/`. Writing config to `/root/` or `/tmp/` expecting survival is a silent data-loss bug.

### 4.2 `root` `.bashrc` auto-source
Interactive root shells auto-source `/opt/data/global_config/global_env.sh` via an injected `/root/.bashrc`. **Never manage `/root/.bashrc` from inside the container** — the infra rewrites it on every boot. Put env in `global_env.sh` instead.

### 4.3 S6-overlay supervisor
`/init` (PID 1) is S6-overlay, not systemd. Supervised services inherit env from `/run/s6/container_environment/`. Don't assume `systemctl` or global reload paths work. The Hermes gateway runs as a supervised service (`gateway-default`); restart via the gateway's own mechanism, not `kill -9` of PID 1 children blindly.

### 4.4 Secrets topology
- **Global secrets:** `/opt/data/.env` only.
- **Project secrets:** isolated `.env` in the project dir, created on demand.
- **Redaction:** never log/emit raw values from any `.env`. Output masked checksums only.

---

## 5. Credential Isolation

This was the highest-risk area and the one most likely to cause a silent, embarrassing leak.

### 5.1 CQD's own bot vs the global gateway
CQD uses `CQD_TG_BOT_TOKEN` / `CQD_TG_CHAT_ID` in its local `.env`. The global Hermes bot uses `TG_BOT_TOKEN` / `TG_CHAT_ID`. These must **never** mix.

### 5.2 The token-leak pitfall
Child processes **inherit** the global `TG_BOT_TOKEN` from the environment. If CQD's sender ever read the inherited global token instead of its own, trade alerts would land in the *wrong* (global) Telegram chat. One early wrapper was missing the scrub and would have leaked.
**Fix:** every wrapper does `unset TG_BOT_TOKEN TG_CHAT_ID` before invoking Python.

### 5.3 Four-layer isolation (defense in depth)
1. **Import-time guard** — `RuntimeError` if `TG_BOT_TOKEN`/`TG_CHAT_ID` are present in `sandbox_engine.py`, `cqd_logger.py`, `cqd_watchdog.py`.
2. **Send-time guard** — `send_telegram_alert` refuses to fire if global creds are in scope.
3. **Wrapper scrub** — `unset` before Python in all five wrappers.
4. **Scoped reader** — sender reads *only* `CQD_TG_BOT_TOKEN` / `CQD_TG_CHAT_ID`.

---

## 6. The Sandbox Reality Check

The uncomfortable truths that drove the "running in circles" feeling.

### 6.1 Zero closed trades
Across days of runtime, `grep EXIT|CLOSE` on the master log returned **0**. Exactly one position opened and sat for 15+ hours. The bot never completed a single trade lifecycle. This is *correct* behavior while price ranges inside SL/TP bands — but it means the system had no evidence of working end-to-end on a real close.

### 6.2 No backtest = no edge proof
There is **no backtest code** in the repo. The signal engine produces convictions, but nobody has measured whether those convictions correlate with future price movement. Without that number, the "strategy" is a hypothesis, not a system.

### 6.3 "Production-grade sandbox" is an oxymoron
The migration audit labeled CQD "Production-grade Sandbox." That label flatters a fake-money simulator. A paper trader that can't lose money and has no validated signal is, operationally, **a JSON file with a cron job.** Calling it production-grade obscured the fact that the core value proposition was unproven. We spent sessions on infrastructure for a simulator instead of on the one question that mattered: *does the signal have edge?*

### 6.4 Lesson
Maintain a system only in proportion to the evidence it matters. Fake-money simulators deserve a fraction of the operational polish we lavished on this one — until a backtest earns the investment.

---

## 7. Process & Debugging

### 7.1 TDD for the watchdog
The liveness watchdog (`core/cqd_watchdog.py`) was built test-first: `tests/test_watchdog.py` (7 tests) → RED → implement → GREEN (22 passed). This caught import and logic errors before they reached a cron. **TDD paid off** for a self-contained module.

### 7.2 `systematic-debugging` skill
Loading the systematic-debugging skill forced a root-cause trace instead of patching. It surfaced that the "monitor forgets positions" alarm was *wrong* — the monitor correctly holds a position between its bands. Caught a false diagnosis before touching code.

### 7.3 The "running in circles" diagnosis
The real cause of the循环 feeling: every session went to **infrastructure plumbing** (cron containment, model-drift pinning, Telegram isolation, gateway flaps, README rewrites) while the project's justifying question — signal edge — was never addressed. The fix was not more plumbing; it was **stopping** and recognizing the bot had answered its only useful question (operational patterns) and had nothing left to prove without a backtest.

### 7.4 Don't overwrite the first iteration silently
Iteration 1 (`/opt/data/cqd/`) was lost/overwritten during a migration. Keep the original path or a tagged git branch next time — the audit trail suffered.

---

## 8. The Lost Origin ("Mercury")

- User recalls the first iteration was based on "a trading bot skill called mercury or something" — old Hermes prompted to clone a reference repo's style.
- That reference **does not exist** in this environment. No mercury/mercurius skill or repo is present.
- Implication: there is no canonical original to match against, so CQD is best understood as **original work** inspired by a now-lost prompt, not a port with a spec. This removes any obligation to "fix it to match" and makes the archive decision clean.

---

## 9. What We'd Do Differently

1. **Backtest before polish.** Write a historical-klines backtest harness *first*; earn the operational investment with a measured edge.
2. **One question per project.** Define the single justifying question up front ("does the signal have edge?") and stop the moment it's answered or proven unanswerable without more data.
3. **Don't label sandbox "production-grade."** Accurate framing prevents over-investment in infrastructure for a simulator.
4. **Preserve iteration history.** Tag the first iteration in git before migrating; don't let `/opt/data/cqd/` vanish.
5. **Close the loop on at least one trade.** Force a sandbox close (hit SL/TP or a manual close path) early, so the full lifecycle is proven before building alerting around it.
6. **Separate "infra skill" from "bot value."** The reusable wins here were the *patterns* (cron containment, credential isolation, drift self-heal) — extract those as skills immediately, then evaluate the bot on its own merits.

---

## 10. Reusable Artifacts (Skills Created)

These outlive the project and are the real deliverables:

- **`hermes-cron-patterns`** — symlink containment, model-drift block, trampoline pattern, `deliver` semantics. Includes `cqd_outage.md` incident report.
- **`cqd-ops`** (v2.0.0) — operations runbook; corrected to state *no LLM in the decision path* (sentiment is numeric FNG + BTC dominance).
- **`container-persistence-patterns`** — ephemeral overlay vs `/opt/data/`, root `.bashrc` auto-source, S6-overlay, secrets topology, credential-discovery.
- **`python-debugging-patterns`** — TDD Makefile pattern, sandbox-engine import guard, requirements-txt workflow.

---

## 11. How To Resume

If the author (or a future reader) wants to pick CQD back up:

1. **Resume crons:** unpause `cqd-monitor`, `cqd-trigger`, `cqd-rotator`, `cqd-health`, `cqd-watchdog` via the Hermes scheduler. They are pinned and ready.
2. **Prove edge first:** build the backtest harness over historical Binance klines; measure conviction→forward-return correlation. *This is the gate for any further investment.*
3. **Only then** consider live-exchange integration (issue #11 roadmap), trailing stops (`config.json` already lists them, unimplemented), and portability.
4. **Keep the skills.** They apply to any scheduled, credential-isolated pipeline — not just this bot.

---

*CQD was a clean, well-instrumented sandbox and a genuine engineering exercise. It earned its lessons; it is paused, not failed.*
