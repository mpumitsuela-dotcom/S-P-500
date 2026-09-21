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
