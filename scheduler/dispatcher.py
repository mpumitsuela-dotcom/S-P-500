#!/usr/bin/env python3
"""
Timezone-safe dispatcher for the twice-daily schedule.

Rather than relying on a single precisely-timed local-time trigger to land
at exactly the right moment (fragile: depends on the triggering machine's
configured timezone matching an assumption made once, and breaks across US
daylight-saving transitions), this script is designed to be invoked
REPEATEDLY - on an hourly cadence is enough - all day, every day, by
whatever fires it (in this build: .github/workflows/trading-agent.yml,
GitHub Actions on a 15-minute cron during market hours). It does its own
timezone-aware check against real NY market time (via zoneinfo, which handles DST automatically) and only
actually runs the AM or PM job once each, on days the market is open,
inside a target window - regardless of what timezone/UTC-offset the
triggering schedule itself was set up in.

Window width vs. trigger cadence: the trigger may fire as rarely as hourly
(see project docs / the scheduled task's cron expression). A once-per-hour
cadence lands somewhere different within each clock hour depending on the
cron's exact minute, and a fixed UTC cron drifts by a full hour relative to
NY local time across a DST transition until someone updates it. AM_WINDOW
and PM_WINDOW are therefore deliberately ~65-70 minutes wide - wider than
the trigger's own period - so at least one firing is guaranteed to land
inside each window even with an hour of slack in either direction, without
needing sub-hourly triggering (which isn't always available) or a
DST-aware cron.

Idempotency: a marker file per session (.state/last_morning_run_date,
.state/last_afternoon_run_date) records the NY calendar date it last ran
on, so firing this dispatcher many times an hour - or many times within a
wide window - doesn't re-run the same session multiple times.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import STATE_DIR  # noqa: E402

NY_TZ = ZoneInfo("America/New_York")

# ~65-70 minutes wide - see module docstring for why: it needs to be wider
# than the (roughly hourly) trigger cadence, with margin for DST drift.
AM_WINDOW = (dtime(9, 30), dtime(10, 35))   # after the opening-auction volatility settles
PM_WINDOW = (dtime(14, 55), dtime(16, 0))   # ahead of / at the close


@dataclass
class DispatchDecision:
    run_morning: bool
    run_afternoon: bool
    reason: str


def decide(now_ny: datetime, already_ran_morning_today: bool, already_ran_afternoon_today: bool) -> DispatchDecision:
    """Pure decision function - takes NY-local time and prior-run state, returns what to do.
    Kept separate from main() so it's testable without mocking datetime.now()."""
    if now_ny.weekday() >= 5:
        return DispatchDecision(False, False, "weekend")

    run_morning = (
        AM_WINDOW[0] <= now_ny.time() <= AM_WINDOW[1] and not already_ran_morning_today
    )
    run_afternoon = (
        PM_WINDOW[0] <= now_ny.time() <= PM_WINDOW[1] and not already_ran_afternoon_today
    )

    if run_morning and run_afternoon:
        reason = "inside both windows (unexpected - windows shouldn't overlap)"
    elif run_morning:
        reason = "inside AM window, not yet run today"
    elif run_afternoon:
        reason = "inside PM window, not yet run today"
    else:
        reason = "outside both windows, or already ran today"

    return DispatchDecision(run_morning, run_afternoon, reason)


def _already_ran_today(marker_name: str, today_str: str) -> bool:
    marker = STATE_DIR / marker_name
    return marker.exists() and marker.read_text().strip() == today_str


def _mark_ran(marker_name: str, today_str: str) -> None:
    (STATE_DIR / marker_name).write_text(today_str)


def _market_open_per_alpaca() -> bool:
    """Ask Alpaca's clock whether the market is open right now. Catches exchange
    holidays and early closes, which decide() (weekday + time only) can't see.
    If the clock can't be reached, assume open and let the per-session guards
    decide - they fail closed on their own."""
    try:
        from execution.alpaca_broker import AlpacaBroker
        return AlpacaBroker().is_market_open()
    except Exception as exc:  # noqa: BLE001 - never let the pre-check crash dispatch
        print(f"[dispatcher] could not reach Alpaca clock ({exc}); deferring to session guards")
        return True


def main() -> int:
    now_ny = datetime.now(NY_TZ)
    today_str = now_ny.date().isoformat()

    print(f"[dispatcher] NY time now: {now_ny.strftime('%Y-%m-%d %H:%M:%S %A')}")

    decision = decide(
        now_ny,
        already_ran_morning_today=_already_ran_today("last_morning_run_date", today_str),
        already_ran_afternoon_today=_already_ran_today("last_afternoon_run_date", today_str),
    )
    print(f"[dispatcher] {decision.reason}")

    if (decision.run_morning or decision.run_afternoon) and not _market_open_per_alpaca():
        # Not marked as ran: nothing happened, and a holiday needs no retry anyway.
        print("[dispatcher] Alpaca clock says the market is closed (holiday?) - skipping")
        return 0

    # Worst exit code of the sessions that ran (0 ok, 2 guard halt, 1 crash), so
    # the scheduler that fired us shows a failure instead of a silent green run.
    rc = 0
    if decision.run_morning:
        import scheduler.run_morning as run_morning
        rc = max(rc, run_morning.main() or 0)
        _mark_ran("last_morning_run_date", today_str)

    if decision.run_afternoon:
        import scheduler.run_afternoon as run_afternoon
        rc = max(rc, run_afternoon.main() or 0)
        _mark_ran("last_afternoon_run_date", today_str)

    return rc


if __name__ == "__main__":
    sys.exit(main())
