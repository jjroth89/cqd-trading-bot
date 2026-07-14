# CQD liveness watchdog canary (proven recipe, 2026-07-14)

A cron-driven CQD bot can die SILENTLY: `ps` empty is normal (it's not a daemon),
and a `deliver: local` cron shows only `last_status: "error"` with no page. The
canary below detects engine silence and pages via the CQD's OWN Telegram bot.

## Why this shape
- It is a SEPARATE cron from the worker, so one failure can't kill both.
- It watches the *output* (`logs/cqd_master_log.csv` newest timestamp), not the
  cron `last_status` (which can report `ok` while writing nothing).
- It pages via `CQD_TG_BOT_TOKEN` / `CQD_TG_CHAT_ID` — distinct from the global
  Hermes token, so a global-credential leak can't blind the alert.

## Files (all TDD-built: test written RED before `core/cqd_watchdog.py` existed)
- `core/cqd_watchdog.py` — `detect_staleness(log, max_age_seconds)`,
  `send_alert(msg, token, chat_id)`, `run_once(...)`. Inherits the fail-fast
  guardrail: raises `RuntimeError` if `TG_BOT_TOKEN`/`TG_CHAT_ID` leaked in.
- `tests/test_watchdog.py` — 7 tests (fresh/stale detection, alert requires
  creds, alert success, alerts-on-stale, silent-when-fresh, fail-fast on leak).
- `cron_wrappers/cqd_watchdog.sh` — sources `/opt/data/cqd-trading-bot/.env`,
  `unset TG_BOT_TOKEN TG_CHAT_ID`, runs `core/cqd_watchdog.py --max-age <N>`.
- `/opt/data/scripts/cqd_watchdog.sh` — in-tree **trampoline** (NOT a symlink)
  that `exec`s the repo wrapper. Required: see `hermes-cron-patterns`.

## Register the cron
```bash
cronjob action=create name='cqd-watchdog' script=cqd_watchdog.sh \
  no_agent=true schedule='*/10 * * * *' deliver='telegram' \
  workdir='/opt/data/cqd-trading-bot'
```
`deliver='telegram'` is the key — without it the watchdog's own failure is silent too.

## Run the suite
```bash
/opt/data/cqd_venv/bin/python -m pytest tests/ -q
```
(pytest had to be installed into `cqd_venv` first: `uv pip install pytest --python /opt/data/cqd_venv/bin/python`.)

## Tuning
- `MAX_AGE_SECONDS` default 600 (10m). Set `CQD_WATCHDOG_MAX_AGE_SECONDS` in
  `/opt/data/cqd-trading-bot/.env` to override.
- Keep the watchdog interval (<10m) shorter than `MAX_AGE_SECONDS` so a single
  missed tick still alerts.
