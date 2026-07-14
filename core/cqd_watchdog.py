#!/usr/bin/env python3
"""CQD liveness watchdog.

Detects when the engine has gone silent (no recent SCAN/EVALUATOR activity in
the master log) and pages via the CQD's OWN Telegram bot. Inherits the strict
isolation guardrail from cqd_logger: a leaked global TG_BOT_TOKEN halts the
process to prevent routing alerts to the wrong bot.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_FILE = PROJECT_ROOT / "logs" / "cqd_master_log.csv"
TARGET = "https://api.telegram.org/bot{token}/sendMessage"

# ─── Model-independence: scheduler self-heal constants ────────────────────────
# The Hermes scheduler blocks cron jobs whose pinned model drifts from the
# active chat model (RuntimeError "...job is unpinned"). CQD execution is
# 100% script-only and must NEVER depend on the user's rotating chat model.
# So the watchdog re-pins any drift-blocked CQD job to these STABLE values,
# which are independent of the user's chat-model choice and never rotated.
MODEL_DRIFT_PHRASE = "job is unpinned"
STABLE_CRON_MODEL = "tencent/hy3:free"
STABLE_CRON_PROVIDER = "openrouter"
CQD_SCRIPT_PREFIX = "cqd_"


class WatchdogError(Exception):
    """Raised for watchdog-specific failures (missing creds, send errors)."""


# ─── FAIL-FAST SECURITY GUARDRAIL ─────────────────────────────────────────────
if os.getenv("TG_BOT_TOKEN") is not None or os.getenv("TG_CHAT_ID") is not None:
    raise RuntimeError(
        "SECURITY VIOLATION: TG_BOT_TOKEN or TG_CHAT_ID detected at import time. "
        "CQD watchdog requires strict isolation — only CQD_TG_BOT_TOKEN and "
        "CQD_TG_CHAT_ID are permitted. Halted to prevent credential leakage."
    )


def _last_timestamp(log_file: Path) -> float | None:
    """Return epoch seconds of the most recent log row, or None if no data."""
    if not log_file.exists():
        return None
    last = None
    with log_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("Timestamp"):
                continue
            last = line
    if not last:
        return None
    ts = last.split(",", 1)[0].strip().strip('"')
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        return None


def detect_staleness(log_file, max_age_seconds: int) -> bool:
    """True if the newest log row is older than ``max_age_seconds`` (or absent)."""
    last = _last_timestamp(Path(log_file))
    if last is None:
        return True
    return (time.time() - last) > max_age_seconds


def send_alert(message: str, token: str | None = None, chat_id: str | None = None) -> int:
    """Send ``message`` via the CQD Telegram bot. Returns Telegram message_id."""
    token = token or os.getenv("CQD_TG_BOT_TOKEN")
    chat_id = chat_id or os.getenv("CQD_TG_CHAT_ID")
    if not token or not chat_id:
        raise WatchdogError(
            "Missing CQD Telegram credentials (CQD_TG_BOT_TOKEN / CQD_TG_CHAT_ID)."
        )
    url = TARGET.format(token=token)
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "disable_notification": False},
            timeout=10,
        )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - surface network/parse failures
        raise WatchdogError(f"Telegram send failed: {exc}") from exc
    if not data.get("ok"):
        raise WatchdogError(f"Telegram API error: {data}")
    return data.get("result", {}).get("message_id")


def cronjob_list() -> list[dict]:
    """Return the scheduler's cron job list via the `cronjob` CLI (model-free)."""
    import json
    import subprocess

    result = subprocess.run(
        ["cronjob", "action=list"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise WatchdogError(f"cronjob list failed: {result.stderr}")
    # The CLI prints a JSON document to stdout.
    return json.loads(result.stdout)


def cronjob_update(job_id: str, **kwargs) -> dict:
    """Re-pin a cron job via the `cronjob` CLI (no model dependence)."""
    import json
    import subprocess

    cmd = ["cronjob", "action=update", f"job_id={job_id}"]
    for key, value in kwargs.items():
        cmd.append(f"{key}={value}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise WatchdogError(f"cronjob update failed: {result.stderr}")
    return json.loads(result.stdout)


def is_model_drift_block(execution_error: str | None) -> bool:
    """True if the scheduler blocked a job due to active-model drift."""
    if not execution_error:
        return False
    return MODEL_DRIFT_PHRASE in str(execution_error)


def self_heal_cron_pins(scripts_prefix: str = CQD_SCRIPT_PREFIX) -> list[str]:
    """Re-pin any CQD cron job blocked by model drift to stable values.

    Returns the list of job_ids that were healed. This makes CQD execution
    independent of the user's rotating chat model — the watchdog absorbs the
    scheduler's drift guard automatically, so a model switch never silently
    kills a CQD job.

    Only jobs whose script name starts with ``scripts_prefix`` are touched, so
    unrelated cron jobs are never modified.
    """
    jobs = cronjob_list()
    healed: list[str] = []
    for job in jobs:
        if not str(job.get("script", "")).startswith(scripts_prefix):
            continue
        if is_model_drift_block(job.get("execution_error")):
            cronjob_update(
                job["job_id"],
                model=STABLE_CRON_MODEL,
                provider=STABLE_CRON_PROVIDER,
            )
            healed.append(job["job_id"])
    return healed


def _detect_drift_blocks(scripts_prefix: str = CQD_SCRIPT_PREFIX) -> list[str]:
    """Return names of CQD crons currently blocked by the model-drift guard.

    Best-effort: only works when the `cronjob` CLI is available (interactive
    Hermes session). Inside a no_agent cron it returns [] rather than crashing,
    so the watchdog never dies on a missing binary.
    """
    try:
        jobs = cronjob_list()
    except Exception:  # noqa: BLE001 - cronjob CLI absent in no_agent context
        return []
    blocked = []
    for job in jobs:
        if not str(job.get("script", "")).startswith(scripts_prefix):
            continue
        if is_model_drift_block(job.get("execution_error")):
            blocked.append(job.get("name", job.get("job_id", "?")))
    return blocked


def run_once(
    log_file=DEFAULT_LOG_FILE,
    max_age_seconds: int = 600,
    token: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Check liveness once. Returns True if an alert was sent (engine stale)."""
    if os.getenv("TG_BOT_TOKEN") is not None or os.getenv("TG_CHAT_ID") is not None:
        raise RuntimeError("SECURITY VIOLATION: global TG_BOT_TOKEN/TG_CHAT_ID leaked.")
    if detect_staleness(log_file, max_age_seconds):
        send_alert(
            f"⚠️ CQD LIVENESS ALERT: engine STALE (no activity in >{max_age_seconds}s). "
            "Check the CQD cron jobs.",
            token=token,
            chat_id=chat_id,
        )
        return True
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CQD liveness watchdog")
    parser.add_argument(
        "--max-age",
        type=int,
        default=600,
        help="Seconds of inactivity before declaring the engine stale.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help="Path to cqd_master_log.csv",
    )
    parser.add_argument(
        "--no-heal",
        action="store_true",
        help="Skip the scheduler model-drift self-heal step.",
    )
    args = parser.parse_args()

    # 1) Self-heal (session-level): when run from an interactive Hermes session
    #    the cronjob tool is available and self_heal_cron_pins() re-pins any
    #    drift-blocked CQD job. Inside a no_agent cron the cronjob CLI is not on
    #    PATH, so this step is a no-op there — drift blocks surface via the
    #    liveness/Telegram alert instead and are healed from the session.
    if not args.no_heal:
        try:
            healed = self_heal_cron_pins()
            if healed:
                print(f"[cqd_watchdog] self-healed drift-blocked jobs: {healed}")
        except Exception as exc:  # noqa: BLE001 - best-effort; never crash the watchdog
            print(f"[cqd_watchdog] heal skipped (run from session to auto-heal): {exc}",
                  file=sys.stderr)

    # 2) Liveness check + report any scheduler drift blocks via Telegram.
    drift_blocks = _detect_drift_blocks()
    try:
        alerted = run_once(log_file=args.log_file, max_age_seconds=args.max_age)
    except WatchdogError as exc:
        print(f"[cqd_watchdog] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    if drift_blocks:
        try:
            send_alert(
                f"⚠️ CQD SCHEDULER ALERT: {len(drift_blocks)} job(s) blocked by "
                "model-drift guard (model rotation). Heal from a Hermes session: "
                "re-pin these CQD crons. Jobs: " + ", ".join(drift_blocks)
            )
        except WatchdogError:
            pass
    print(f"[cqd_watchdog] alerted={alerted}")
