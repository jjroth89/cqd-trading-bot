# Incident: CQD engine silently dead ~3 hours (2026-07-14)

## Symptom
- `logs/cqd_master_log.csv` stops at `00:26Z`; current time `03:32Z` — ~3h silence.
- `ps` shows no CQD process (expected: cron-driven, not a daemon — not in itself a fault).
- `cronjob action=list`: `cqd-monitor` (5m) and `cqd-trigger` (15m) both `last_status: "error"`; `cqd-rotator` showed stale `ok` (last real run 19:46 previous day).
- An OPEN position (OPN/USDT long, entered 00:26) was unmonitored the entire window.

## Root cause
The four CQD wrappers were **symlinked** into `/opt/data/scripts/`:
```
/opt/data/scripts/cqd_monitor.sh -> /opt/data/cqd-trading-bot/cron_wrappers/cqd_monitor.sh
```
The scheduler canonicalizes the symlink and rejects any real path outside
`/opt/data/scripts/`, returning:
`Blocked: script path resolves outside the scripts directory (/opt/data/scripts): 'cqd_monitor.sh'`
Every scheduled tick was aborted before Python ran. No user-facing alert (cron `deliver: local`).

## Fix
Replaced each symlink with a real in-tree **trampoline** in `/opt/data/scripts/` that
`exec`s the repo wrapper (see `templates/cron_trampoline.sh`). Logic stays single-sourced.

## Verification
- `cronjob action=run job_id=cd62ada9f940` (monitor) -> `execution_success: true`, `last_status: ok`
- `cronjob action=run job_id=beabda3c125e` (trigger) -> same
- Log resumes streaming (new SCAN lines at 03:32-03:38Z). Open position again policed.

## Takeaway
For script crons in this environment, never symlink the real script into /opt/data/scripts/.
Use an in-tree trampoline. A silent `last_status: error` with a manually-working script
== containment block until proven otherwise.

## Prevention added afterward (the real lesson)
The trampoline fixed the block, but the outage went UNNOTICED for 3h because the
cron was `deliver: local` — no output reached the user. Two defenses now applied:
1. **`deliver='telegram'`** on the critical CQD crons, so an `error` tick pages
   immediately instead of sitting silently in `cronjob action=list`.
2. **Independent liveness canary** (`cqd-watchdog`, every 10m, `deliver: telegram`)
   that reads the master log's newest timestamp and pages if it is older than
   `MAX_AGE_SECONDS` (600). It does NOT share the worker process, so a single
   failure can't kill both the engine and the alert. Watch the *output*
   (log timestamp), not the cron `last_status` — status can report `ok` while
   writing nothing. Pages via the CQD's OWN `CQD_TG_BOT_TOKEN` (separate from the
   global Hermes token), so a global-credential leak can't also blind the alert.

See `cqd-ops` skill `references/watchdog_recipe.md` for the concrete CQD canary.
