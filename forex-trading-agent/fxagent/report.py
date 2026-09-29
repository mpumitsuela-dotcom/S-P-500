"""End-of-day report: what it traded, why, and how the day went. Saved to the
state branch and posted as a GitHub issue (GitHub emails it to the owner)."""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime

import requests

log = logging.getLogger(__name__)


def realized_by_symbol(fills: list[dict]) -> dict[str, float]:
    """Match sells against earlier buys (first in, first out)."""
    lots: dict[str, list[list[float]]] = defaultdict(list)
    pnl: dict[str, float] = defaultdict(float)
    for f in fills:
        sym, qty, price = f["symbol"], float(f["qty"]), float(f["price"])
        if f["side"] == "buy":
            lots[sym].append([qty, price])
            continue
        while qty > 1e-9 and lots[sym]:
            lot = lots[sym][0]
            used = min(qty, lot[0])
            pnl[sym] += used * (price - lot[1])
            lot[0] -= used
            qty -= used
            if lot[0] <= 1e-9:
                lots[sym].pop(0)
    return dict(pnl)


def build(date: str, equity: float, start_equity: float, fills: list[dict], decisions: list[dict], research: dict | None) -> str:
    change = equity - start_equity
    pct = change / start_equity * 100 if start_equity else 0.0
    lines = [
        f"# Forex agent daily report — {date}",
        "",
        f"**Account:** ${equity:,.2f} at the close, {'up' if change >= 0 else 'down'} ${abs(change):,.2f} ({pct:+.2f}%) on the day.",
        "",
    ]
    buys = [d for d in decisions if d.get("action") == "buy"]
    sells = [d for d in decisions if d.get("action") == "sell"]
    problems = [d for d in decisions if d.get("action") in ("order_rejected", "sell_failed")]
    skips = [d for d in decisions if d.get("action") == "skip"]

    pnl = realized_by_symbol(fills)
    lines.append("## Trades")
    if not buys and not fills:
        lines.append("No trades today — no currency signal was strong enough, or the safety rules said wait.")
    for b in buys:
        lines.append(
            f"- **{b['time']} ET — bought {b['qty']} {b['symbol']}** ({b.get('currency')} {b.get('view')}, strength {b.get('score')}) "
            f"at up to ${b['limit']}, stop-loss ${b['stop']}, take-profit ${b['target']}, most it could lose ${b['risk_dollars']}.  \n"
            f"  Why: {b.get('reason', '')}"
        )
    for s in sells:
        lines.append(f"- **{s['time']} ET — sold {s['symbol']}**: {s.get('reason', '')}")
    if pnl:
        lines += ["", "## Result by ETF (closed trades, includes stop-loss / take-profit exits)"]
        for sym, p in sorted(pnl.items()):
            lines.append(f"- {sym}: {'+' if p >= 0 else '-'}${abs(p):,.2f}")
    if problems:
        lines += ["", "## Problems"] + [f"- {p['time']} {p['symbol']}: {p['reason']}" for p in problems]
    if skips:
        lines += ["", f"## Trades it chose not to make ({len(skips)})"]
        seen = set()
        for s in skips:
            key = (s["symbol"], s["reason"][:40])
            if key not in seen:
                seen.add(key)
                lines.append(f"- {s['time']} {s['symbol']}: {s['reason']}")
    if research and research.get("summary"):
        lines += ["", "## Latest market research", research["summary"]]
    lines += ["", "_Paper account — no real money. Past results don't predict future ones._"]
    return "\n".join(lines) + "\n"


def post_issue(title: str, body: str) -> None:
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        log.info("Not on GitHub Actions - report saved locally only")
        return
    try:
        r = requests.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"title": title, "body": body, "labels": ["daily-report"]},
            timeout=20,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Could not post the report issue: %s", exc)


def end_of_day(broker, state, now_ny: datetime) -> None:
    account = broker.account()
    fills = broker.fills_since(now_ny.replace(hour=0, minute=0, second=0, microsecond=0))
    decisions = []
    if state.decisions.exists():
        decisions = [json.loads(line) for line in state.decisions.read_text().splitlines() if line.strip()]
    research = json.loads(state.research.read_text()) if state.research.exists() else None
    body = build(state.today, float(account["equity"]), float(account.get("last_equity") or account["equity"]), fills, decisions, research)
    state.report.parent.mkdir(parents=True, exist_ok=True)
    state.report.write_text(body)
    post_issue(f"Forex daily report {state.today}", body)
    log.info("Daily report written")
