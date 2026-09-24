"""Needs-attention alerts: technical problems vs. decisions for the owner."""
from datetime import date, datetime

import pytest

from execution import alerts, daily_report, decisions
from execution.decisions import NY_TZ

DAY = date(2026, 9, 24)
AT = datetime(2026, 9, 24, 9, 45, tzinfo=NY_TZ)


@pytest.fixture(autouse=True)
def decisions_file(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "DECISIONS_FILE", tmp_path / "decisions.jsonl")
    for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def test_clean_run_sends_nothing():
    decisions.record_session("AM", "completed", "12 order(s) placed", now=AT)
    assert alerts.build_alert(DAY, "github-actions", ["morning"], 0) is None


def test_drawdown_halt_asks_the_owner():
    decisions.record_session("AM", "halted", "Safety check stopped the session: drawdown_halt: drawdown -7.2% exceeds halt threshold", now=AT)
    title, body = alerts.build_alert(DAY, "github-actions", ["morning"], 2)
    assert title.startswith("🟠 Decision needed — Thu 24 Sep")
    assert "## Needs your decision" in body and "SP500_MAX_DAILY_DD_HALT" in body
    assert "## Technical problem" not in body


def test_trial_end_asks_the_owner():
    decisions.record_session("PM", "halted", "Safety check stopped the session: trial_period: trial ended", now=AT)
    title, body = alerts.build_alert(DAY, "pc-HOME", ["afternoon"], 2)
    assert "Decision needed" in title and "TRIAL_REPORT.md" in body


def test_stale_data_is_technical():
    decisions.record_session("AM", "halted", "Safety check stopped the session: data_freshness: latest price data is 6 days old", now=AT)
    title, body = alerts.build_alert(DAY, "github-actions", ["morning"], 2)
    assert title.startswith("⚠️ Trading agent needs attention")
    assert "## Technical problem" in body and "Claude's scheduled check-in" in body


def test_crash_mentions_kill_switch_and_run_link(monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    decisions.record_session("AM", "crashed", "KeyError: 'close'", now=AT)
    _, body = alerts.build_alert(DAY, "github-actions", ["morning"], 1)
    assert "kill switch" in body
    assert "https://github.com/o/r/actions/runs/42" in body


def test_runner_failure_without_session_record():
    _, body = alerts.build_alert(DAY, "github-actions", ["report"], 1, "The trade log and state could not be saved to the agent-state branch.")
    assert "could not be saved" in body


def test_alert_posts_issue_and_never_raises(monkeypatch):
    posted = []
    monkeypatch.setattr(daily_report, "publish_issue", lambda *a: posted.append(a))
    decisions.record_session("AM", "halted", "Safety check stopped the session: price_sanity: AAPL moved 40%", now=AT)
    alerts.alert_if_needed(DAY, "github-actions", ["morning"], 2)
    assert posted[0][3] == "needs-attention"

    monkeypatch.setattr(daily_report, "publish_issue", lambda *a: (_ for _ in ()).throw(RuntimeError("403")))
    alerts.alert_if_needed(DAY, "github-actions", ["morning"], 2)  # must not raise


def test_earlier_failure_is_not_re_alerted_by_a_later_good_run():
    from datetime import timedelta

    decisions.record_session("AM", "crashed", "TypeError: boom", now=AT)
    later = AT + timedelta(minutes=10)
    decisions.record_session("AM", "completed", "6 order(s) placed", now=later)
    assert alerts.build_alert(DAY, "github-actions", ["morning"], 0, since=later - timedelta(seconds=5)) is None
    assert alerts.build_alert(DAY, "github-actions", ["morning"], 0) is not None  # without since it would
