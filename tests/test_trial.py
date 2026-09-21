from datetime import date

from execution.trial import (
    get_or_init_trial_start,
    get_or_init_trial_start_equity,
    reset_trial,
    trial_active,
    trial_days_elapsed,
)


def test_get_or_init_trial_start_sets_once(tmp_path, monkeypatch):
    marker = tmp_path / "trial_start_date.txt"
    monkeypatch.setattr("execution.trial.TRIAL_START_DATE_FILE", marker)

    first = get_or_init_trial_start(date(2026, 1, 1))
    assert first == date(2026, 1, 1)

    # A later call, even with a different "today", must return the ORIGINAL start date.
    second = get_or_init_trial_start(date(2026, 1, 15))
    assert second == date(2026, 1, 1)


def test_get_or_init_trial_start_equity_sets_once(tmp_path, monkeypatch):
    marker = tmp_path / "trial_start_equity.txt"
    monkeypatch.setattr("execution.trial.TRIAL_START_EQUITY_FILE", marker)

    first = get_or_init_trial_start_equity(100_000.0)
    assert first == 100_000.0

    second = get_or_init_trial_start_equity(103_500.0)
    assert second == 100_000.0  # baseline doesn't move just because equity changed


def test_trial_days_elapsed():
    assert trial_days_elapsed(date(2026, 1, 1), date(2026, 1, 1)) == 0
    assert trial_days_elapsed(date(2026, 1, 1), date(2026, 1, 31)) == 30


def test_trial_active_within_window():
    assert trial_active(date(2026, 1, 1), date(2026, 1, 29), length_days=30) is True


def test_trial_active_false_once_elapsed():
    assert trial_active(date(2026, 1, 1), date(2026, 1, 31), length_days=30) is False


def test_reset_trial_clears_markers(tmp_path, monkeypatch):
    date_marker = tmp_path / "trial_start_date.txt"
    equity_marker = tmp_path / "trial_start_equity.txt"
    monkeypatch.setattr("execution.trial.TRIAL_START_DATE_FILE", date_marker)
    monkeypatch.setattr("execution.trial.TRIAL_START_EQUITY_FILE", equity_marker)

    get_or_init_trial_start(date(2026, 1, 1))
    get_or_init_trial_start_equity(100_000.0)
    assert date_marker.exists() and equity_marker.exists()

    reset_trial()
    assert not date_marker.exists()
    assert not equity_marker.exists()
