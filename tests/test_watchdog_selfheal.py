"""Self-heal for scheduler model-drift blocks — TDD RED.

When the user rotates their chat model, the Hermes scheduler blocks UNPINNED
(or stale-pinned) cron jobs with a RuntimeError containing the drift phrase.
CQD execution is 100% script-only and must NEVER depend on the active chat
model, so the watchdog must detect that block and re-pin the CQD jobs to a
stable value automatically — no human intervention, no breakage.

These tests define the EXPECTED behaviour of the self-heal helper in
``core/cqd_watchdog.py`` BEFORE it exists. They MUST fail (AttributeError /
missing function) until implemented, then MUST pass after GREEN.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from cqd_watchdog import (  # noqa: E402
    MODEL_DRIFT_PHRASE,
    STABLE_CRON_MODEL,
    STABLE_CRON_PROVIDER,
    is_model_drift_block,
    self_heal_cron_pins,
)


def test_drift_phrase_constant_defined():
    assert isinstance(MODEL_DRIFT_PHRASE, str)
    assert "job is unpinned" in MODEL_DRIFT_PHRASE


def test_stable_pin_constants_defined():
    # Stable = must NOT equal a rotating chat model; provider must be valid.
    assert STABLE_CRON_PROVIDER == "openrouter"
    assert isinstance(STABLE_CRON_MODEL, str) and STABLE_CRON_MODEL


def test_is_model_drift_block_true_for_drift_error():
    err = (
        "RuntimeError: Skipped to prevent unintended spend: global inference "
        "config drifted since this job was created (model 'poolside/laguna-m.1:free' "
        "-> 'tencent/hy3:free'), and this job is unpinned. No inference call was made."
    )
    assert is_model_drift_block(err) is True


def test_is_model_drift_block_false_for_other_error():
    assert is_model_drift_block("RuntimeError: Unknown provider 'none'.") is False
    assert is_model_drift_block("") is False
    assert is_model_drift_block(None) is False


def test_self_heal_repins_only_drift_blocked_jobs(monkeypatch, tmp_path):
    """self_heal_cron_pins must re-pin jobs whose execution_error matches drift."""
    jobs = [
        {"job_id": "aaa", "name": "cqd-monitor", "script": "cqd_monitor.sh",
         "execution_error": None, "last_status": "ok"},
        {"job_id": "bbb", "name": "cqd-rotator", "script": "cqd_rotator.sh",
         "execution_error": "RuntimeError: Skipped to prevent unintended spend: "
                            "...job is unpinned...", "last_status": "error"},
    ]
    repinned = []

    def fake_list():
        return jobs

    def fake_update(job_id, **kwargs):
        repinned.append((job_id, kwargs))
        for j in jobs:
            if j["job_id"] == job_id:
                j.update(kwargs)
        return {"success": True}

    monkeypatch.setattr("cqd_watchdog.cronjob_list", fake_list)
    monkeypatch.setattr("cqd_watchdog.cronjob_update", fake_update)

    healed = self_heal_cron_pins(scripts_prefix="cqd_")
    assert healed == ["bbb"]
    assert repinned == [("bbb", {
        "model": STABLE_CRON_MODEL,
        "provider": STABLE_CRON_PROVIDER,
    })]


def test_self_heal_skips_non_cqd_jobs(monkeypatch):
    jobs = [
        {"job_id": "zzz", "name": "backup-db", "script": "backup.sh",
         "execution_error": "RuntimeError: Skipped to prevent unintended spend: "
                            "job is unpinned.", "last_status": "error"},
    ]
    repinned = []
    monkeypatch.setattr("cqd_watchdog.cronjob_list", lambda: jobs)
    monkeypatch.setattr("cqd_watchdog.cronjob_update",
                        lambda jid, **kw: repinned.append((jid, kw)))
    healed = self_heal_cron_pins(scripts_prefix="cqd_")
    assert healed == []  # not a CQD job → untouched
