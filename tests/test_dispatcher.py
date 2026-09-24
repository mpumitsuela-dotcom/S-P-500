from datetime import datetime
from zoneinfo import ZoneInfo

from scheduler.dispatcher import decide

NY_TZ = ZoneInfo("America/New_York")


def ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NY_TZ)


def test_inside_am_window_first_time_triggers_morning():
    # Monday 2026-09-21, 9:45am NY
    decision = decide(ny(2026, 9, 21, 9, 45), already_ran_morning_today=False, already_ran_afternoon_today=False)
    assert decision.run_morning is True
    assert decision.run_afternoon is False


def test_inside_am_window_but_already_ran_skips():
    decision = decide(ny(2026, 9, 21, 9, 45), already_ran_morning_today=True, already_ran_afternoon_today=False)
    assert decision.run_morning is False


def test_inside_pm_window_first_time_triggers_afternoon():
    decision = decide(ny(2026, 9, 21, 15, 30), already_ran_morning_today=True, already_ran_afternoon_today=False)
    assert decision.run_afternoon is True


def test_inside_pm_window_but_already_ran_skips():
    decision = decide(ny(2026, 9, 21, 15, 30), already_ran_morning_today=True, already_ran_afternoon_today=True)
    assert decision.run_afternoon is False


def test_outside_both_windows_does_nothing():
    decision = decide(ny(2026, 9, 21, 12, 0), already_ran_morning_today=False, already_ran_afternoon_today=False)
    assert decision.run_morning is False
    assert decision.run_afternoon is False


def test_before_market_open_does_nothing():
    decision = decide(ny(2026, 9, 21, 6, 0), already_ran_morning_today=False, already_ran_afternoon_today=False)
    assert decision.run_morning is False
    assert decision.run_afternoon is False


def test_weekend_does_nothing_even_inside_window():
    # 2026-09-19 is a Saturday
    decision = decide(ny(2026, 9, 19, 9, 45), already_ran_morning_today=False, already_ran_afternoon_today=False)
    assert decision.run_morning is False
    assert decision.reason == "weekend"


def test_windows_do_not_overlap():
    from scheduler.dispatcher import AM_WINDOW, PM_WINDOW
    assert AM_WINDOW[1] < PM_WINDOW[0]


def _patch_main(monkeypatch, tmp_path, run_morning, market_open, morning_rc=0):
    import sys
    import types

    import scheduler
    import scheduler.dispatcher as dispatcher
    from scheduler.dispatcher import DispatchDecision

    monkeypatch.setattr(dispatcher, "STATE_DIR", tmp_path)
    monkeypatch.setattr(dispatcher, "decide", lambda *a, **k: DispatchDecision(run_morning, False, "test"))
    monkeypatch.setattr(dispatcher, "_market_open_per_alpaca", lambda: market_open)
    calls = []
    fake = types.ModuleType("scheduler.run_morning")
    fake.main = lambda: calls.append("am") or morning_rc
    monkeypatch.setitem(sys.modules, "scheduler.run_morning", fake)
    monkeypatch.setattr(scheduler, "run_morning", fake, raising=False)
    return dispatcher, calls


def test_main_skips_on_market_holiday_without_marking(monkeypatch, tmp_path):
    dispatcher, calls = _patch_main(monkeypatch, tmp_path, run_morning=True, market_open=False)
    assert dispatcher.main() == 0
    assert calls == []
    assert not (tmp_path / "last_morning_run_date").exists()


def test_main_runs_session_and_propagates_guard_halt(monkeypatch, tmp_path):
    dispatcher, calls = _patch_main(monkeypatch, tmp_path, run_morning=True, market_open=True, morning_rc=2)
    assert dispatcher.main() == 2
    assert calls == ["am"]
    assert (tmp_path / "last_morning_run_date").exists()


def test_main_outside_windows_does_not_query_clock(monkeypatch, tmp_path):
    dispatcher, calls = _patch_main(monkeypatch, tmp_path, run_morning=False, market_open=True)
    monkeypatch.setattr(dispatcher, "_market_open_per_alpaca", lambda: (_ for _ in ()).throw(AssertionError("called")))
    assert dispatcher.main() == 0
    assert calls == []
