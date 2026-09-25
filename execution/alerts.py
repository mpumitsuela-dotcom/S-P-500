"""
"Needs attention" alerts: when a session is halted by a safety check, crashes,
or the runner itself fails, open a GitHub issue right away (GitHub emails it
to the repo owner) instead of waiting for the end-of-day report.

Each alert says whether the problem is technical (Claude's scheduled
check-ins diagnose and fix those) or needs the owner's decision about the
trading itself. Posting reuses execution/daily_report.publish_issue, so it
only happens where a GitHub token is available (GitHub Actions).
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime

from execution import daily_report, decisions

logger = logging.getLogger("sp500_agent.alerts")

SESSION_CODES = {"morning": "AM", "afternoon": "PM", "report": None}
SESSION_NAMES = {"AM": "morning rebalance", "PM": "afternoon risk check"}

# Guard halts that are a decision about the money, not a malfunction.
OWNER_DECISIONS = {
    "trial_period": (
        "The 30-day trial has ended, so the agent has stopped trading. TRIAL_REPORT.md on the "
        "agent-state branch compares its return with the S&P 500. Decide whether to extend the "
        "trial (raise SP500_TRIAL_DAYS), start a fresh one, or stop."
    ),
    "drawdown_halt": (
        "The account has fallen more than the drawdown limit from its recent peak, so the agent "
        "has stopped buying. Decide whether to keep trading as-is, raise the limit "
        "(SP500_MAX_DAILY_DD_HALT), or pause (create .state/KILL_SWITCH on the agent-state branch)."
    ),
}


def _run_link() -> str:
    server, repo, run_id = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""


def build_alert(
    day: date, runner: str, sessions: list[str], rc: int, note: str = "", since: datetime | None = None
) -> tuple[str, str] | None:
    """Returns (title, body) when this run's sessions need attention, else None.
    since: only session records written at/after this time count, so an
    earlier failure the same day (already alerted, since fixed) isn't
    re-reported by a later successful run."""
    codes = [SESSION_CODES[s] for s in sessions if SESSION_CODES.get(s)]
    records = [
        r for r in decisions.read_records(day, day)
        if r.get("type") == "session" and r.get("session") in codes and r.get("outcome") in ("halted", "crashed")
        and (since is None or datetime.fromisoformat(r["timestamp"]) >= since.replace(microsecond=0))
    ]
    since_ok = lambda r: since is None or datetime.fromisoformat(r["timestamp"]) >= since.replace(microsecond=0)  # noqa: E731
    foreign = [r for r in decisions.read_records(day, day) if r.get("type") == "foreign_orders" and since_ok(r)]
    if not records and rc == 0 and not note and not foreign:
        return None

    owner_items, technical_items = [], []
    for r in records:
        what = f"The {SESSION_NAMES.get(r['session'], r['session'])} {r['outcome']}: {r.get('detail', '').strip()}"
        decision = next((text for guard, text in OWNER_DECISIONS.items() if f"{guard}:" in r.get("detail", "")), None)
        (owner_items if decision else technical_items).append((what, decision))
    if rc and not records:
        technical_items.append((f"The {', '.join(sessions)} step exited with code {rc}. {note}".strip(), None))
    elif note:
        technical_items.append((note, None))

    for r in foreign:
        listed = "; ".join(r.get("orders", [])[:15])
        more = len(r.get("orders", [])) - 15
        owner_items.append((
            f"{len(r.get('orders', []))} order(s) on the Alpaca account were NOT placed by this agent: {listed}"
            + (f" and {more} more" if more > 0 else ""),
            "Something else is trading this account, most likely an older copy of the agent (for example a "
            "scheduled task on your computer). Turn it off so only this agent trades; otherwise the two "
            "fight each other's positions and the trial's comparison with the S&P 500 is meaningless.",
        ))
    needs_owner = bool(owner_items)
    title = (
        f"{'🟠 Decision needed' if needs_owner else '⚠️ Trading agent needs attention'} — "
        f"{day:%a %d %b} ({', '.join(sessions)})"
    )
    lines = [f"**Runner:** {runner}", ""]
    if owner_items:
        lines += ["## Needs your decision", ""]
        for what, decision in owner_items:
            lines += [f"- {what}", f"  - **What to decide:** {decision}"]
        lines.append("")
    if technical_items:
        lines += [
            "## Technical problem",
            "",
            "No trades were placed by the affected session. Claude's scheduled check-in will diagnose "
            "and fix technical problems and report back; nothing is needed from you unless asked.",
            "",
        ] + [f"- {what}" for what, _ in technical_items] + [""]
    if any("kill switch" in what.lower() or "crashed" in what for what, _ in technical_items + owner_items):
        lines += [
            "A crash sets the kill switch (`.state/KILL_SWITCH` on the agent-state branch), which pauses "
            "all trading until it is cleared after the fix.",
            "",
        ]
    link = _run_link()
    if link:
        lines.append(f"Run logs: {link}")
    return title, "\n".join(lines)


def alert_if_needed(day: date, runner: str, sessions: list[str], rc: int, note: str = "", since: datetime | None = None) -> None:
    """Never raises: an alert failure must not change the run's outcome."""
    try:
        alert = build_alert(day, runner, sessions, rc, note, since)
        if alert:
            title, body = alert
            logger.warning(title)
            daily_report.publish_issue(title, body, "", "needs-attention")
    except Exception:  # noqa: BLE001
        logger.warning("Could not post the needs-attention alert", exc_info=True)
