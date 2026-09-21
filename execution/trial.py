"""
30-day trial tracking.

The user asked for a bounded trial: run for 30 days, then stop and let them
decide whether to continue, adjust, or turn it off - not run indefinitely by
default. Enforced as a guard (execution/guards.py check_trial_period) so it
fails safe exactly like every other guard: once the window is up, no further
orders are placed, automatically, without anyone needing to remember to turn
it off.

Two marker files, in the same simple idempotent-marker-file style already
used by scheduler/dispatcher.py:
  - trial_start_date.txt   : NY calendar date of the first run ever, set once.
  - trial_start_equity.txt : paper account equity the first time it's read
                              after the trial starts, set once. Used as the
                              baseline for the vs-S&P-500 performance report.
"""
from __future__ import annotations

from datetime import date

from config import STATE_DIR

TRIAL_START_DATE_FILE = STATE_DIR / "trial_start_date.txt"
TRIAL_START_EQUITY_FILE = STATE_DIR / "trial_start_equity.txt"


def get_or_init_trial_start(today: date) -> date:
    """First call ever starts the clock at `today` and persists it; every later
    call (any day, any session) just reads that same start date back."""
    if TRIAL_START_DATE_FILE.exists():
        raw = TRIAL_START_DATE_FILE.read_text().strip()
        return date.fromisoformat(raw)
    TRIAL_START_DATE_FILE.write_text(today.isoformat())
    return today


def get_or_init_trial_start_equity(current_equity: float) -> float:
    """First call ever records `current_equity` as the trial's starting baseline
    and persists it; every later call just reads that same baseline back."""
    if TRIAL_START_EQUITY_FILE.exists():
        try:
            return float(TRIAL_START_EQUITY_FILE.read_text().strip())
        except ValueError:
            pass  # corrupt marker - fall through and reset it below
    TRIAL_START_EQUITY_FILE.write_text(str(current_equity))
    return current_equity


def trial_days_elapsed(start: date, today: date) -> int:
    return (today - start).days


def trial_active(start: date, today: date, length_days: int) -> bool:
    return trial_days_elapsed(start, today) < length_days


def reset_trial(today: date | None = None) -> None:
    """Clears both markers so a fresh 30-day window starts on the next run.
    Not called automatically anywhere - this is for a human to invoke
    deliberately (see README.md) after reviewing TRIAL_REPORT.md and deciding
    to continue."""
    for f in (TRIAL_START_DATE_FILE, TRIAL_START_EQUITY_FILE):
        if f.exists():
            f.unlink()
