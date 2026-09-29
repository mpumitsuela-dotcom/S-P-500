"""
The daily report for the options agent, in plain words: money, open
contracts, what it bought and sold today and why, and the research behind
each decision (with Gemini's sources). Saved on the options-agent-state
branch under reports/options/daily/ and posted as a GitHub issue (GitHub
emails it to the owner).
"""
from __future__ import annotations

from datetime import date


def _money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.1%}"


def build(
    day: date, run: dict, equity: float, last_equity: float, positions: list[dict], trades: list[dict],
    research: list[dict], spy_since_start: float | None, final: bool = False,
) -> tuple[str, str]:
    start_eq = float(run.get("start_equity") or equity)
    since = equity - start_eq
    since_pct = since / start_eq if start_eq else 0.0
    today_chg = equity - last_equity
    end = date.fromisoformat(run["end_date"]) if run.get("end_date") else None
    days_left = (end - day).days if end else None

    title = f"{'🏁 Final report' if final else '📈 Options agent'} — {day:%a %d %b %Y}: {_money(equity)} ({_pct(since_pct)} since start)"
    lines = [
        f"# Options agent — {day:%A %d %B %Y}",
        "",
        f"- **Account value:** {_money(equity)} ({_money(today_chg)} today)",
        f"- **Since the start on {run.get('start_date')}:** {_money(since)} ({_pct(since_pct)})",
        f"- **S&P 500 (SPY) over the same time:** {_pct(spy_since_start)}",
    ]
    if days_left is not None and not final:
        lines.append(f"- **Days left in the two-month run:** {max(days_left, 0)} (ends {run['end_date']})")
    if run.get("entries_halted"):
        lines.append(f"- **New trades paused:** {run['entries_halted']}")
    lines += ["", "## Contracts held", ""]
    if positions:
        lines += ["| Contract | Bet | Qty | Paid | Now | Gain | Days left | Why it was bought |", "|---|---|---|---|---|---|---|---|"]
        for p in positions:
            lines.append(
                f"| {p['underlying']} {p['kind']} ${p['strike']:g} exp {p['expiration']} | "
                f"{'up' if p['kind'] == 'call' else 'down'} | {p['qty']} | ${p['entry_price']:.2f} | ${p['current_price']:.2f} | "
                f"{_pct(p['gain'])} | {p['dte']} | {p.get('why', '')[:160]} |"
            )
    else:
        lines.append("None: all money is in cash.")

    lines += ["", "## Trades today", ""]
    if trades:
        for t in trades:
            verb = "Bought" if t["side"] == "buy" else "Sold"
            if t.get("filled_qty"):
                lines.append(
                    f"- **{verb} {t['filled_qty']} × {t['label']}** at ${t['fill_price']:.2f} "
                    f"({_money(t['filled_qty'] * t['fill_price'] * 100)}). {t['reason']}"
                )
            else:
                lines.append(f"- {verb[:-2] if verb == 'Bought' else 'Sell'} order for {t['label']} did not fill ({t.get('status')}). {t['reason']}")
    else:
        lines.append("No trades today.")

    verdicts = [r for r in research if r.get("type") == "verdict"]
    passes = [r for r in research if r.get("type") == "pass"]
    lines += ["", "## Research today", ""]
    if not verdicts and not passes:
        lines.append("No new research today (research runs once each trading day around 11:00 New York time).")
    for v in verdicts:
        g = v.get("gemini") or {}
        lines += [
            f"### {v['symbol']}: {v.get('decision', '')}",
            f"- **Data score:** {v.get('score', 0):+.2f} ({v.get('score_explained', '')})",
            f"- **Gemini ({g.get('model', '?')}):** {g.get('direction', '?')}, conviction {g.get('conviction', '?')}/100. {g.get('thesis', '')}",
        ]
        if g.get("risks"):
            lines.append(f"- **Risks:** {'; '.join(g['risks'][:3])}")
        if g.get("sources"):
            lines.append("- **Sources:** " + ", ".join(f"[{s['title'] or 'link'}]({s['url']})" for s in g["sources"][:5]))
        lines.append("")
    if passes:
        lines += ["**Passed on:**", ""] + [f"- {p['symbol']}: {p['reason']}" for p in passes[:15]]

    lines += [
        "",
        "---",
        "This is a paper (simulated money) account. The agent only buys options, so the most it can lose on any "
        "contract is what it paid for it. Past results don't promise future ones.",
    ]
    return title, "\n".join(lines)
