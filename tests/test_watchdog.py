"""CQD liveness watchdog — TDD RED phase.

These tests define the EXPECTED behaviour of ``core/cqd_watchdog.py`` BEFORE the
module exists. They MUST fail (collection error: ModuleNotFoundError) until the
module is implemented, then MUST pass after GREEN.

The watchdog detects when the CQD engine has gone silent (no SCAN/EVALUATOR
activity in the master log within ``max_age_seconds``) and pages via the CQD's
OWN Telegram bot (never the global Hermes token). It inherits the strict
isolation guardrail from cqd_logger: a leaked TG_BOT_TOKEN halts execution.
"""

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from cqd_watchdog import (  # noqa: E402
    WatchdogError,
    detect_staleness,
    run_once,
    send_alert,
)


def _write_log(tmp_path, minutes_ago, component="EVALUATOR", event="SCAN"):
    """Write a one-row master log whose timestamp is ``minutes_ago`` in the past."""
    p = tmp_path / "cqd_master_log.csv"
    ts = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - minutes_ago * 60)
    )
    p.write_text(
        "Timestamp,Component,Event_Type,Pair,Conviction,FGI,BTC_Dom,PnL_USDT,Details\n"
        f"{ts},{component},{event},BTC/USDT,2,22,56.0,,\n"
    )
    return p


def test_fresh_log_not_stale(tmp_path):
    p = _write_log(tmp_path, minutes_ago=1)
    assert detect_staleness(p, max_age_seconds=600) is False


def test_old_log_is_stale(tmp_path):
    p = _write_log(tmp_path, minutes_ago=15)
    assert detect_staleness(p, max_age_seconds=600) is True


def test_send_alert_requires_credentials(monkeypatch):
    monkeypatch.delenv("CQD_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("CQD_TG_CHAT_ID", raising=False)
    with pytest.raises(WatchdogError):
        send_alert("liveness alert", token=None, chat_id=None)


def test_send_alert_success(monkeypatch):
    captured = {}

    class _Resp:
        ok = True

        def json(self):
            return {"ok": True, "result": {"message_id": 1234}}

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _Resp()

    monkeypatch.setattr("cqd_watchdog.requests.post", _fake_post)
    mid = send_alert("liveness alert", token="T", chat_id="C")
    assert mid == 1234
    assert "botT/sendMessage" in captured["url"]
    assert captured["json"]["chat_id"] == "C"
    assert captured["json"]["text"] == "liveness alert"


def test_run_once_alerts_on_stale(tmp_path, monkeypatch):
    p = _write_log(tmp_path, minutes_ago=15)
    sent = []
    monkeypatch.setattr(
        "cqd_watchdog.send_alert", lambda msg, **kw: (sent.append(msg) or 1234)
    )
    alerted = run_once(log_file=p, max_age_seconds=600, token="T", chat_id="C")
    assert alerted is True
    assert any("STALE" in m for m in sent)


def test_run_once_silent_when_fresh(tmp_path, monkeypatch):
    p = _write_log(tmp_path, minutes_ago=1)
    sent = []
    monkeypatch.setattr(
        "cqd_watchdog.send_alert", lambda msg, **kw: (sent.append(msg) or 1234)
    )
    alerted = run_once(log_file=p, max_age_seconds=600, token="T", chat_id="C")
    assert alerted is False
    assert sent == []


def test_fail_fast_on_global_token_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "global-leak")
    with pytest.raises(RuntimeError):
        run_once(
            log_file=_write_log(tmp_path, minutes_ago=15),
            max_age_seconds=600,
            token="T",
            chat_id="C",
        )
