#!/usr/bin/env python3
"""
Run the twice-daily schedule from more than one machine without ever trading
the same session twice.

Both GitHub Actions and your PC call this script every ~15 minutes. Each
firing:

  1. Pulls the shared state from the `agent-state` branch
     (scheduler/shared_state.py).
  2. Decides, from real New York time and the shared "already ran today"
     markers, whether the AM or PM session is due (scheduler/dispatcher.py).
  3. Backup runners wait BACKUP_DELAY_MINUTES into each window first, so the
     primary gets the first chance. If the primary already ran, the backup
     sees its marker and does nothing.
  4. Claims the session by pushing the marker BEFORE trading. If the push is
     rejected, another runner changed the state first: re-pull and decide
     again (it will usually find the session already claimed).
  5. Runs the session, then pushes the updated trade log and state.

Role and name come from the environment:
  SP500_RUNNER_ROLE  primary (default) or backup
  SP500_RUNNER_NAME  shown in the state commits and markers (default: hostname)
"""
from __future__ import annotations

import os
import socket
import sys
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import PROJECT_ROOT  # noqa: E402
from scheduler import shared_state  # noqa: E402
from scheduler.dispatcher import AM_WINDOW, NY_TZ, PM_WINDOW, _market_open_per_alpaca, decide  # noqa: E402

# Pauses only the machine it's on. The shared .state/KILL_SWITCH (on the
# agent-state branch) pauses every runner; a local copy of it would be
# overwritten by the next sync, so machine-level pausing uses this file instead.
LOCAL_KILL_SWITCH_NAME = "KILL_SWITCH"
BACKUP_DELAY_MINUTES = int(os.environ.get("SP500_BACKUP_DELAY_MINUTES", "25"))
MAX_CLAIM_ATTEMPTS = 3

# After the close: the daily report (and every fifth trading day, the 5-day
# report) - see execution/daily_report.py. Ends by 17:00 so the workflow's
# last cron firing (21:52 UTC = 16:52 EST) still lands in it in winter.
REPORT_WINDOW = (dtime(16, 10), dtime(17, 0))

SESSIONS = {
    # session -> (marker file, window)
    "morning": ("last_morning_run_date", AM_WINDOW),
    "afternoon": ("last_afternoon_run_date", PM_WINDOW),
    "report": ("last_report_run_date", REPORT_WINDOW),
}
TRADING_SESSIONS = ("morning", "afternoon")


def _log(msg: str) -> None:
    print(f"[run_shared] {msg}", flush=True)


def _ran_today(root: Path, marker: str, today: str) -> bool:
    p = root / ".state" / marker
    return p.exists() and p.read_text().strip() == today


def _in_backup_hold(now_ny: datetime, window) -> bool:
    start = datetime.combine(now_ny.date(), window[0], tzinfo=NY_TZ)
    return now_ny < start + timedelta(minutes=BACKUP_DELAY_MINUTES)


def due_sessions(root: Path, now_ny: datetime, role: str, force: str = "") -> list[str]:
    """force: run this session now even outside its window (owner-requested
    catch-up via the workflow's "force_session" input). It still runs at most
    once a day and still passes every guard, including market hours."""
    today = now_ny.date().isoformat()
    d = decide(
        now_ny,
        already_ran_morning_today=_ran_today(root, SESSIONS["morning"][0], today),
        already_ran_afternoon_today=_ran_today(root, SESSIONS["afternoon"][0], today),
    )
    due = [s for s, flag in (("morning", d.run_morning), ("afternoon", d.run_afternoon)) if flag]
    # Report only on days a trading session actually ran (skips weekends and holidays).
    traded_today = any(_ran_today(root, SESSIONS[s][0], today) for s in TRADING_SESSIONS)
    if (
        now_ny.weekday() < 5
        and REPORT_WINDOW[0] <= now_ny.time() <= REPORT_WINDOW[1]
        and traded_today
        and not _ran_today(root, SESSIONS["report"][0], today)
    ):
        due.append("report")
    if role == "backup":
        held = [s for s in due if _in_backup_hold(now_ny, SESSIONS[s][1])]
        if held:
            _log(f"backup runner: giving the primary until +{BACKUP_DELAY_MINUTES} min for {', '.join(held)}")
        due = [s for s in due if s not in held]
    if force in SESSIONS and force not in due and now_ny.weekday() < 5 and not _ran_today(root, SESSIONS[force][0], today):
        _log(f"forced run of the {force} session (outside its window, on request)")
        due.append(force)
    return due


def _run_session(name: str) -> int:
    if name == "report":
        from execution import daily_report

        return daily_report.run_end_of_day(datetime.now(NY_TZ).date())
    if name == "morning":
        import scheduler.run_morning as mod
    else:
        import scheduler.run_afternoon as mod
    return mod.main() or 0


def _alert(day, runner: str, sessions: list[str], rc: int, note: str = "", since: datetime | None = None) -> None:
    from execution import alerts

    alerts.alert_if_needed(day, runner, sessions, rc, note, since)


def main(
    root: Path = PROJECT_ROOT,
    now_ny: datetime | None = None,
    run_session=_run_session,
    market_open=_market_open_per_alpaca,
    alert=_alert,
) -> int:
    role = os.environ.get("SP500_RUNNER_ROLE", "primary").strip().lower()
    runner = os.environ.get("SP500_RUNNER_NAME") or socket.gethostname()
    if role not in ("primary", "backup"):
        _log(f"SP500_RUNNER_ROLE must be 'primary' or 'backup', got {role!r}")
        return 1
    if root == PROJECT_ROOT and not shared_state.state_dir_is_shared():
        _log("SP500_AGENT_STATE_DIR points outside the project; shared state needs the default .state/")
        return 1

    if (root / LOCAL_KILL_SWITCH_NAME).exists():
        _log(f"{root / LOCAL_KILL_SWITCH_NAME} exists - this machine is paused")
        return 0

    now_ny = now_ny or datetime.now(NY_TZ)
    today = now_ny.date().isoformat()
    _log(f"{runner} ({role}) at NY {now_ny:%Y-%m-%d %H:%M %A}")

    claimed: list[str] = []
    parent = None
    for _ in range(MAX_CLAIM_ATTEMPTS):
        try:
            parent = shared_state.pull(root)
        except shared_state.SharedStateError as exc:
            # Without the shared state we can't know whether the other runner
            # already traded, so never trade blind.
            _log(f"cannot load shared state, not trading: {exc}")
            return 1

        due = due_sessions(root, now_ny, role, os.environ.get("SP500_FORCE_SESSION", "").strip().lower())
        if not due:
            _log("nothing due (outside windows, or already ran today)")
            return 0
        if any(s in TRADING_SESSIONS for s in due) and not market_open():
            _log("Alpaca clock says the market is closed (holiday?) - skipping trading")
            due = [s for s in due if s not in TRADING_SESSIONS]
            if not due:
                return 0

        for s in due:
            (root / ".state" / SESSIONS[s][0]).write_text(today)
            (root / ".state" / f"{SESSIONS[s][0].replace('_date', '_by')}").write_text(f"{runner} ({role})\n")
        new_parent = shared_state.push(f"{runner}: claim {', '.join(due)} {today}", parent, root)
        if new_parent:
            parent, claimed = new_parent, due
            break
        _log("another runner updated the shared state first - re-checking")
    else:
        _log("could not claim after several attempts - not trading")
        return 1

    rc = 0
    notes = []
    started = datetime.now(NY_TZ)  # session records are stamped with the real clock
    for s in claimed:
        _log(f"running {s} session")
        try:
            rc = max(rc, run_session(s))
        except Exception as exc:  # noqa: BLE001 - still publish whatever state exists
            _log(f"{s} session raised: {exc!r}")
            notes.append(f"The {s} step raised {exc!r}.")
            rc = max(rc, 1)

    # Publish the trade log and state. Only the claimant writes during a
    # session, so if the push races anything, our files win.
    for _ in range(MAX_CLAIM_ATTEMPTS):
        pushed = shared_state.push(f"{runner}: {', '.join(claimed)} done {today} (exit {rc})", parent, root)
        if pushed:
            break
        parent = _refetch_parent(root)
    else:
        _log("WARNING: could not publish the session's state")
        notes.append("The trade log and state could not be saved to the agent-state branch.")
        rc = max(rc, 1)

    # Tell the owner now (GitHub issue -> email) rather than at the end of the day.
    alert(now_ny.date(), runner, claimed, rc, " ".join(notes), since=started)
    return rc


def _refetch_parent(root: Path) -> str | None:
    shared_state._git(root, "fetch", "--quiet", "--depth=1", "origin", shared_state.STATE_BRANCH)
    return shared_state._git(root, "rev-parse", "FETCH_HEAD").stdout.decode().strip()


if __name__ == "__main__":
    sys.exit(main())
