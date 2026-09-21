from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from execution.guards import (
    GuardFailure,
    check_data_freshness,
    check_drawdown_halt,
    check_kill_switch,
    check_market_hours,
    check_price_sanity,
    check_trial_period,
    check_universe_coverage,
    run_all_guards,
)
from config import KILL_SWITCH_FILE

NY_TZ = ZoneInfo("America/New_York")


def test_market_hours_weekday_open():
    dt = datetime(2024, 6, 11, 10, 0, tzinfo=NY_TZ)  # a Tuesday, 10am
    assert check_market_hours(dt).passed


def test_market_hours_weekend_fails():
    dt = datetime(2024, 6, 8, 10, 0, tzinfo=NY_TZ)  # a Saturday
    assert not check_market_hours(dt).passed


def test_market_hours_before_open_fails():
    dt = datetime(2024, 6, 11, 8, 0, tzinfo=NY_TZ)
    assert not check_market_hours(dt).passed


def test_data_freshness_stale_fails():
    prices = pd.DataFrame({"date": pd.to_datetime(["2024-01-01", "2024-01-02"])})
    result = check_data_freshness(prices, pd.Timestamp("2024-01-20"))
    assert not result.passed


def test_data_freshness_fresh_passes():
    prices = pd.DataFrame({"date": pd.to_datetime(["2024-01-19", "2024-01-20"])})
    result = check_data_freshness(prices, pd.Timestamp("2024-01-20"))
    assert result.passed


def test_price_sanity_flags_implausible_move():
    new = pd.Series({"AAA": 200.0})
    ref = pd.Series({"AAA": 100.0})  # +100% - way over the default 35% threshold
    result = check_price_sanity(new, ref)
    assert not result.passed


def test_price_sanity_passes_normal_move():
    new = pd.Series({"AAA": 102.0})
    ref = pd.Series({"AAA": 100.0})
    result = check_price_sanity(new, ref)
    assert result.passed


def test_drawdown_halt_triggers_on_large_drop():
    curve = pd.Series([100, 110, 120, 95])  # ~21% off peak of 120
    result = check_drawdown_halt(curve)
    assert not result.passed


def test_drawdown_halt_ok_on_small_drop():
    curve = pd.Series([100, 105, 103])
    result = check_drawdown_halt(curve)
    assert result.passed


def test_universe_coverage_fails_when_too_sparse():
    scores = pd.DataFrame(index=["AAA"])
    sector_map = {f"S{i}": "Tech" for i in range(100)}
    result = check_universe_coverage(scores, sector_map, min_coverage=0.8)
    assert not result.passed


def test_trial_period_active_within_window():
    result = check_trial_period(date(2026, 1, 1), date(2026, 1, 15), length_days=30)
    assert result.passed
    assert "day 15 of 30" in result.message


def test_trial_period_fails_once_elapsed():
    result = check_trial_period(date(2026, 1, 1), date(2026, 1, 31), length_days=30)
    assert not result.passed


def test_trial_period_passes_on_last_day():
    # day 30 of a 30-day trial (elapsed == 29) should still be active
    result = check_trial_period(date(2026, 1, 1), date(2026, 1, 30), length_days=30)
    assert result.passed


def test_kill_switch_file_halts(tmp_path, monkeypatch):
    monkeypatch.setattr("execution.guards.KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH")
    assert check_kill_switch().passed
    (tmp_path / "KILL_SWITCH").write_text("halt")
    assert not check_kill_switch().passed


def test_run_all_guards_raises_on_any_failure():
    results = [check_market_hours(datetime(2024, 6, 8, 10, 0, tzinfo=NY_TZ))]  # weekend -> fails
    with pytest.raises(GuardFailure):
        run_all_guards(results)


def test_run_all_guards_passes_when_all_ok():
    results = [check_market_hours(datetime(2024, 6, 11, 10, 0, tzinfo=NY_TZ))]
    run_all_guards(results)  # should not raise
