# CQD Environment Isolation — proven pattern

## Why
The Hermes container exports a global `TG_BOT_TOKEN` / `TG_CHAT_ID` for the MAIN agent Telegram bot. The CQD bot has its OWN bot (`CQD_TG_BOT_TOKEN` / `CQD_TG_CHAT_ID`). Because child processes inherit the parent shell's environment, any CQD process spawned without scrubbing will silently fall back to the global token and send alerts to the wrong chat.

## Shell wrappers (cqd_monitor.sh, cqd_trigger.sh)
Immediately before invoking the Python binary:

```bash
# strip inherited global creds so CQD can only use its own .env
unset TG_BOT_TOKEN
unset TG_CHAT_ID
python3 /opt/data/cqd-trading-bot/core/sandbox_engine.py "$@"
```

## Python notification engine (core/cqd_logger.py)
- Remove the anonymous-class hack (`type("CqdLogger", ...)`); structure as a real importable module/class.
- Read ONLY the CQD-scoped vars:
  ```python
  token = os.getenv("CQD_TG_BOT_TOKEN")
  chat_id = os.getenv("CQD_TG_CHAT_ID")
  ```
- Fail-fast guardrail at init — halt if a global token is present in memory:
  ```python
  if os.getenv("TG_BOT_TOKEN") or os.getenv("TG_CHAT_ID"):
      raise RuntimeError(
          "Global Hermes TG token detected in CQD process env — "
          "aborting to prevent alert leakage to the wrong chat."
      )
  ```

## Verification
After applying: run the Telegram smoke test from the SKILL.md. Confirm the message arrives in the CQD chat (not the main Hermes chat). Re-run a wrapper script and check no message lands in the main chat.
