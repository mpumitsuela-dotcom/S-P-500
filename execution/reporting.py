"""
Builds TRIAL_REPORT.md: a plain-language summary of the 30-day trial -
performance vs the S&P 500 (via SPY) and every buy/sell decision made, with
its rationale (see execution/rationale.py, execution/trade_log.py). Written
once the trial-period guard trips (execution/guards.py check_trial_period)
so the user has something concrete to decide "how do I want to proceed"
from - an honest account of what happened and why, not a promise that the
strategy beat the index (see the "Honest context" section it writes - no
legitimate system can guarantee that over a fixed 30-day window).
"""
from __future__ import annotations

from datetime import date

from config import PROJECT_ROOT
from data.alpaca_data import get_daily_bars
from execution.trade_log import read_all_entries

REPORT_FILE = PROJECT_ROOT / "TRIAL_REPORT.md"


def _spy_return(start: date, end: date) -> float | None:
    try:
        bars = get_daily_bars(["SPY"], start, end)
    except Exception:
        return None
    if bars.empty:
        return None
    bars = bars.sort_values("date")
    first, last = bars["close"].iloc[0], bars["close"].iloc[-1]
    if not first:
        return None
    return (last / first) - 1.0


def build_trial_report(
    trial_start: date,
    today: date,
    starting_equity: float,
    current_equity: float,
    current_positions: dict,
) -> str:
    agent_return = (current_equity / starting_equity) - 1.0 if starting_equity else 0.0
    spy_return = _spy_return(trial_start, today)

    lines = [
        f"# 30-day trial report — {trial_start} to {today}",
        "",
        "## Performance",
        "",
        f"- Starting paper equity ({trial_start}): ${starting_equity:,.2f}",
        f"- Current paper equity ({today}): ${current_equity:,.2f}",
        f"- Agent return: {agent_return:+.2%}",
    ]
    if spy_return is not None:
        lines.append(f"- S&P 500 (SPY) return over the same period: {spy_return:+.2%}")
        lines.append(f"- Agent vs S&P 500: {agent_return - spy_return:+.2%}")
    else:
        lines.append("- S&P 500 (SPY) return over the same period: unavailable this run (will fill in on a later run)")

    lines += ["", "## Current holdings", ""]
    if current_positions:
        for sym in sorted(current_positions):
            pos = current_positions[sym]
            unrealized = (pos.current_price / pos.avg_entry_price - 1.0) if pos.avg_entry_price else 0.0
            lines.append(
                f"- {sym}: {pos.qty} shares, avg entry ${pos.avg_entry_price:,.2f}, "
                f"current ${pos.current_price:,.2f} ({unrealized:+.2%} unrealized)"
            )
    else:
        lines.append("- (no open positions)")

    lines += ["", "## Every buy/sell decision this trial, with why", ""]
    entries = [e for e in read_all_entries() if trial_start.isoformat() <= e["timestamp"][:10] <= today.isoformat()]
    if entries:
        for e in entries:
            lines.append(f"- **{e['timestamp']} ({e['session']})** {e['side'].upper()} {e['qty']} {e['symbol']} [{e['status']}] — {e['reason']}")
    else:
        lines.append("- (no trades were recorded this trial)")

    lines += [
        "",
        "## Honest context",
        "",
        "This is a single ~30-day sample from one specific starting date - far too short "
        "and too path-dependent to be a statistically meaningful test of whether this "
        "strategy actually beats the index, in either direction. No legitimate trading "
        "system can guarantee outperformance over a fixed short window; this report is an "
        "honest account of what the system decided and why, not a verdict on the strategy's "
        "edge. Treat it primarily as a functional check - did it run safely, unattended, "
        "twice a day, for a month, and make explainable decisions - alongside whatever the "
        "return numbers above show.",
        "",
        "The system will not place any further trades until you clear the trial marker "
        "(see README.md \"Extending or restarting the trial\") or the kill switch, "
        "whichever applies.",
    ]
    return "\n".join(lines)


def write_trial_report(
    trial_start: date,
    today: date,
    starting_equity: float,
    current_equity: float,
    current_positions: dict,
) -> str:
    content = build_trial_report(trial_start, today, starting_equity, current_equity, current_positions)
    REPORT_FILE.write_text(content)
    return content
