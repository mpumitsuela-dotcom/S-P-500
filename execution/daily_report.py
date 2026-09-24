"""
End-of-day and 5-day reports: why each stock was bought or sold, and the
research behind each decision (from execution/decisions.py records).

- Daily: reports/daily/YYYY-MM-DD.md, written after the close every trading day.
- 5-day: reports/weekly/5-day-ending-YYYY-MM-DD.md, written after every fifth
  trading day's daily report.

Both are saved with the shared state on the agent-state branch and, when a
GitHub token is available (GitHub Actions), also posted as a GitHub issue so
GitHub emails them to the repo owner. scheduler/run_shared.py runs this in
its after-close "report" slot.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import date, datetime, timedelta

import requests

from execution import decisions
from execution.decisions import NY_TZ, REPORTS_DIR

logger = logging.getLogger("sp500_agent.reports")

DAILY_DIR = REPORTS_DIR / "daily"
WEEKLY_DIR = REPORTS_DIR / "weekly"
EQUITY_FILE = REPORTS_DIR / "data" / "equity.jsonl"
DAYS_PER_PERIOD_REPORT = 5
ISSUE_BODY_LIMIT = 60000

_FACTOR_LABELS = {
    "value": ("Value", "cheap vs. sector peers on P/E and P/B"),
    "quality": ("Quality", "profitability (ROE, margins) and balance-sheet strength"),
    "momentum": ("Momentum", "12-month price trend, excluding the last month"),
    "low_vol": ("Low volatility", "steadier price than sector peers"),
    "research": ("Research", "recent news tone + analyst consensus"),
}

HONEST_NOTE = (
    "_Scores are sector-neutral z-scores: 0 is average for the stock's sector, +1 is one standard "
    "deviation better. News tone is a keyword count over recent headlines, not a deep read. None of "
    "this guarantees a stock will rise; a few days of results can't show whether the strategy has "
    "a real edge over the S&P 500._"
)


# ---------- formatting helpers ----------

def _pct(v, signed=True):
    if v is None:
        return "n/a"
    return f"{v:+.1%}" if signed else f"{v:.1%}"


def _num(v, fmt="{:.2f}"):
    return "n/a" if v is None else fmt.format(v)


def _money(v, signed=False):
    if v is None:
        return "n/a"
    return f"{'+' if signed and v >= 0 else ''}{'-' if v < 0 else ''}${abs(v):,.0f}"


def _long_date(d: date) -> str:
    return d.strftime("%A %d %B %Y")


def _driver(factors: dict) -> str | None:
    legs = {k: factors.get(k) for k in _FACTOR_LABELS if isinstance(factors.get(k), (int, float))}
    if not legs:
        return None
    return max(legs, key=lambda k: abs(legs[k]))


def render_trade(rec: dict) -> list[str]:
    """One trade with the research behind it, as markdown lines."""
    research = rec.get("research") or {}
    factors = research.get("factors") or {}
    side = rec["side"]
    price = rec.get("price")
    head = f"### {rec['symbol']} — {'bought' if side == 'buy' else 'sold'} {rec['qty']} shares"
    if price:
        head += f" at ~${price:,.2f} ({_money(rec.get('value'))})"
    status = rec.get("status", "")
    lines = [head, ""]
    if status and not str(status).startswith(("new", "accepted", "filled", "pending", "partially")):
        lines += [f"**Order status: {status}**", ""]

    lines += [f"**Why:** {rec.get('reason', '').strip() or 'n/a'}", ""]

    if rec.get("session") == "PM" and side == "sell":
        move = rec.get("move_since_entry")
        lines += [
            "**What triggered it:** the afternoon risk check trims a holding only when BOTH its recent news "
            f"turns clearly negative AND its price is down 3%+ since purchase (it was {_pct(move)}).",
            "",
        ]

    if factors:
        rank = factors.get("rank")
        lines += [
            f"**Research scores** (combined {_num(factors.get('combined_score'), '{:+.2f}')}"
            + (f", ranked #{int(rank)} in the S&P 500" if rank else "")
            + (f", sector: {factors['sector']}" if factors.get("sector") else "")
            + "):",
            "",
            "| Factor | Score | Measures |",
            "|---|---|---|",
        ]
        for key, (label, meaning) in _FACTOR_LABELS.items():
            if key in factors:
                lines.append(f"| {label} | {_num(factors.get(key), '{:+.2f}')} | {meaning} |")
        lines.append("")

    f = research.get("fundamentals") or {}
    if f:
        bits = []
        if "pe" in f:
            bits.append(f"P/E {_num(f['pe'], '{:.1f}')}")
        if "pb" in f:
            bits.append(f"P/B {_num(f['pb'], '{:.1f}')}")
        if "roe" in f:
            bits.append(f"return on equity {_pct(f['roe'], signed=False)}")
        if "gross_margin" in f:
            bits.append(f"gross margin {_pct(f['gross_margin'], signed=False)}")
        if "debt_to_equity" in f:
            bits.append(f"debt/equity {_num(f['debt_to_equity'], '{:.2f}')}")
        lines += [f"**Company financials (FMP, trailing 12 months):** {', '.join(bits)}", ""]

    p = research.get("price") or {}
    if p:
        bits = []
        if "return_12m_ex_1m" in p:
            bits.append(f"12-month return (excl. last month) {_pct(p['return_12m_ex_1m'])}")
        if "return_1m" in p:
            bits.append(f"last month {_pct(p['return_1m'])}")
        if "volatility_60d_annualized" in p:
            bits.append(f"volatility {_pct(p['volatility_60d_annualized'], signed=False)}/yr")
        lines += [f"**Price trend (Alpaca):** {', '.join(bits)}", ""]

    news = research.get("news") or {}
    count = news.get("headline_count")
    if count:
        tone = news.get("sentiment_score")
        lines += [f"**News (Finnhub): {count} headline(s) in the last 2 days, net tone {_num(tone, '{:+.0f}')}.** Most influential:"]
        for h in news.get("top_headlines") or []:
            when = ""
            if h.get("datetime"):
                when = datetime.fromtimestamp(h["datetime"], NY_TZ).strftime("%d %b") + ", "
            title = h.get("headline", "").strip().replace("|", "/")
            link = f"[{title}]({h['url']})" if h.get("url") else title
            lines.append(f"- {link} — {when}{h.get('source', '')} (tone {h.get('score', 0):+d})")
        lines.append("")
    elif count == 0:
        lines += ["**News (Finnhub):** no headlines in the last 2 days.", ""]
    else:
        lines += ["**News (Finnhub):** not collected for this stock on this run (free-tier limit), so research scored it neutral.", ""]

    a = research.get("analysts") or {}
    b = a.get("breakdown") or {}
    if a.get("total"):
        lines += [
            f"**Analysts (Finnhub{', ' + a['period'] if a.get('period') else ''}):** "
            f"{b.get('strongBuy', 0)} strong buy, {b.get('buy', 0)} buy, {b.get('hold', 0)} hold, "
            f"{b.get('sell', 0)} sell, {b.get('strongSell', 0)} strong sell — consensus "
            f"{_num(a.get('score'), '{:+.2f}')} on a -2 (strong sell) to +2 (strong buy) scale.",
            "",
        ]
    return lines


# ---------- data ----------

def _read_equity() -> list[dict]:
    if not EQUITY_FILE.exists():
        return []
    rows = []
    for line in EQUITY_FILE.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _record_equity(day: date, equity: float) -> None:
    rows = [r for r in _read_equity() if r.get("date") != day.isoformat()]
    rows.append({"date": day.isoformat(), "equity": equity})
    rows.sort(key=lambda r: r["date"])
    EQUITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    EQUITY_FILE.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _trial_start_equity() -> float | None:
    from execution.trial import TRIAL_START_EQUITY_FILE

    try:
        return float(TRIAL_START_EQUITY_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _spy_closes(start: date, end: date) -> dict[str, float]:
    try:
        from data.alpaca_data import get_daily_bars

        # get_daily_bars treats `end` as exclusive (it asks up to end 00:00 UTC),
        # so ask one day further to include `end`'s own closing bar.
        bars = get_daily_bars(["SPY"], start, end + timedelta(days=1))
    except Exception:  # noqa: BLE001
        logger.warning("Could not fetch SPY bars", exc_info=True)
        return {}
    if bars.empty:
        return {}
    return {str(d)[:10]: float(c) for d, c in zip(bars["date"], bars["close"])}


def _spy_return(closes: dict[str, float], start_excl: date, end: date) -> float | None:
    """SPY return from the close before start_excl's day... to end's close."""
    before = [d for d in closes if d < start_excl.isoformat()]
    if not before or end.isoformat() not in closes:
        return None
    return closes[end.isoformat()] / closes[max(before)] - 1


def _holdings_lines(positions: dict) -> list[str]:
    if not positions:
        return ["- (no open positions)"]
    lines = ["| Stock | Shares | Avg cost | Price | Value | Gain/loss |", "|---|---|---|---|---|---|"]
    for sym in sorted(positions, key=lambda s: -positions[s].market_value):
        p = positions[sym]
        gain = p.current_price / p.avg_entry_price - 1 if p.avg_entry_price else None
        lines.append(
            f"| {sym} | {p.qty} | ${p.avg_entry_price:,.2f} | ${p.current_price:,.2f} | "
            f"{_money(p.market_value)} | {_pct(gain)} |"
        )
    return lines


def _session_lines(records: list[dict]) -> list[str]:
    names = {"AM": "Morning rebalance", "PM": "Afternoon risk check"}
    labels = {"completed": "traded", "no_trades": "no trades needed", "halted": "HALTED", "crashed": "CRASHED"}
    out = []
    for r in records:
        if r.get("type") == "session":
            detail = (r.get("detail") or "").strip()
            if r.get("outcome") == "crashed" and detail:
                # A crash record holds a traceback; the owner only needs the error line.
                detail = f"{detail.splitlines()[-1].strip()} (a technical fault; it was fixed and the session re-ran if a later line says so)"
            line = f"- **{r['date']} {names.get(r['session'], r['session'])}:** {labels.get(r['outcome'], r['outcome'])} — {detail}"
            excluded = r.get("excluded_unresearched") or []
            if excluded:
                shown = ", ".join(excluded[:12]) + (f" and {len(excluded) - 12} more" if len(excluded) > 12 else "")
                line += f" Not bought because no research could be fetched for them: {shown}."
            out.append(line)
    return out


def _trade_sections(trades: list[dict]) -> list[str]:
    buys = [t for t in trades if t["side"] == "buy"]
    sells = [t for t in trades if t["side"] == "sell"]
    lines = ["## Why I bought", ""]
    if buys:
        for t in sorted(buys, key=lambda t: -(t.get("value") or 0)):
            lines += render_trade(t)
    else:
        lines += ["No stocks were bought.", ""]
    lines += ["## Why I sold", ""]
    if sells:
        for t in sorted(sells, key=lambda t: -(t.get("value") or 0)):
            lines += render_trade(t)
    else:
        lines += ["No stocks were sold.", ""]
    return lines


# ---------- reports ----------

def build_daily_report(day: date, equity: float | None, positions: dict, prev_equity: float | None, spy_day: float | None) -> str:
    records = decisions.read_records(day, day)
    trades = [r for r in records if r.get("type") == "trade"]
    buys = [t for t in trades if t["side"] == "buy"]
    sells = [t for t in trades if t["side"] == "sell"]

    lines = [f"# Daily trading report — {_long_date(day)}", "", "## Summary", ""]
    if equity is not None:
        day_ret = equity / prev_equity - 1 if prev_equity else None
        lines.append(
            f"- **Account value:** {_money(equity)}"
            + (f" ({_money(equity - prev_equity, signed=True)}, {_pct(day_ret)} today)" if prev_equity else "")
        )
        if day_ret is not None and spy_day is not None:
            verdict = "ahead of" if day_ret > spy_day else "behind"
            lines.append(f"- **S&P 500 (SPY) today:** {_pct(spy_day)} — the agent was {verdict} the index by {abs(day_ret - spy_day):.2%}")
        elif spy_day is not None:
            lines.append(f"- **S&P 500 (SPY) today:** {_pct(spy_day)}")
    lines.append(
        f"- **Trades:** {len(buys)} buy(s) ({_money(sum(t.get('value') or 0 for t in buys))}), "
        f"{len(sells)} sell(s) ({_money(sum(t.get('value') or 0 for t in sells))})"
    )
    lines += _session_lines(records) or ["- No session records for today."]
    lines.append("")

    lines += _trade_sections(trades)
    lines += ["## Holdings at the close", ""] + _holdings_lines(positions) + ["", HONEST_NOTE, ""]
    return "\n".join(lines)


def build_period_report(days: list[date], equity_by_day: dict[str, float], start_equity: float | None, positions: dict, spy_closes: dict[str, float], day_number: int) -> str:
    start, end = days[0], days[-1]
    records = decisions.read_records(start, end)
    trades = [r for r in records if r.get("type") == "trade"]
    buys = [t for t in trades if t["side"] == "buy"]
    sells = [t for t in trades if t["side"] == "sell"]

    lines = [
        f"# 5-day trading report — {start:%d %b} to {end:%d %b %Y}",
        f"_Trading days {day_number - len(days) + 1}–{day_number} of the trial._",
        "",
        "## Performance",
        "",
    ]
    end_equity = equity_by_day.get(end.isoformat())
    agent_ret = end_equity / start_equity - 1 if end_equity and start_equity else None
    spy_ret = _spy_return(spy_closes, start, end)
    lines.append(f"- **Account value:** {_money(start_equity)} → {_money(end_equity)} ({_pct(agent_ret)})")
    lines.append(f"- **S&P 500 (SPY) over the same days:** {_pct(spy_ret)}")
    if agent_ret is not None and spy_ret is not None:
        lines.append(f"- **Agent vs S&P 500:** {agent_ret - spy_ret:+.2%} ({'ahead' if agent_ret > spy_ret else 'behind'})")
    lines += ["", "| Day | Account value | Day change | SPY | Buys | Sells |", "|---|---|---|---|---|---|"]
    prev = start_equity
    for d in days:
        eq = equity_by_day.get(d.isoformat())
        day_trades = [t for t in trades if t["date"] == d.isoformat()]
        spy_d = _spy_return(spy_closes, d, d)
        lines.append(
            f"| {d:%a %d %b} | {_money(eq)} | {_pct(eq / prev - 1 if eq and prev else None)} | {_pct(spy_d)} | "
            f"{sum(t['side'] == 'buy' for t in day_trades)} | {sum(t['side'] == 'sell' for t in day_trades)} |"
        )
        prev = eq or prev
    lines.append("")

    lines += ["## What the research favoured", ""]
    if trades:
        drivers = Counter(_FACTOR_LABELS[k][0] for k in (_driver((t.get("research") or {}).get("factors") or {}) for t in buys) if k)
        if drivers:
            lines.append("- **Main reason behind buys:** " + ", ".join(f"{name} ({n})" for name, n in drivers.most_common()))
        sectors = Counter(((t.get("research") or {}).get("factors") or {}).get("sector") for t in buys)
        sectors.pop(None, None)
        if sectors:
            lines.append("- **Sectors bought:** " + ", ".join(f"{s} ({n})" for s, n in sectors.most_common()))

        def avg(items, getter):
            vals = [v for v in (getter(t) for t in items) if isinstance(v, (int, float))]
            return sum(vals) / len(vals) if vals else None

        for label, getter, fmt in (
            ("analyst consensus (-2..+2)", lambda t: ((t.get("research") or {}).get("analysts") or {}).get("score"), "{:+.2f}"),
            ("news tone", lambda t: ((t.get("research") or {}).get("news") or {}).get("sentiment_score"), "{:+.1f}"),
            ("combined research score", lambda t: ((t.get("research") or {}).get("factors") or {}).get("combined_score"), "{:+.2f}"),
        ):
            b, s = avg(buys, getter), avg(sells, getter)
            if b is not None or s is not None:
                lines.append(f"- **Average {label}:** buys {_num(b, fmt)}, sells {_num(s, fmt)}")
    else:
        lines.append("- No trades in these five days.")
    lines.append("")

    lines += ["## Sessions", ""] + (_session_lines(records) or ["- No session records."]) + [""]
    lines += ["# Key decisions and the research behind them", ""]
    lines += _trade_sections(trades)
    lines += ["## Holdings at the end of the period", ""] + _holdings_lines(positions) + ["", HONEST_NOTE, ""]
    return "\n".join(lines)


# ---------- publishing ----------

def publish_issue(title: str, body: str, rel_path: str, label: str) -> None:
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        logger.info("No GITHUB_TOKEN/GITHUB_REPOSITORY - report saved to %s only", rel_path)
        return
    from scheduler.shared_state import STATE_BRANCH

    if rel_path:
        link = f"https://github.com/{repo}/blob/{STATE_BRANCH}/{rel_path}"
        if len(body) > ISSUE_BODY_LIMIT:
            body = body[:ISSUE_BODY_LIMIT] + f"\n\n…(truncated — full report: {link})"
        body += f"\n\n---\nSaved at [{rel_path}]({link})."
    elif len(body) > ISSUE_BODY_LIMIT:
        body = body[:ISSUE_BODY_LIMIT] + "\n\n…(truncated)"
    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    payload = {"title": title, "body": body, "labels": [label]}
    resp = requests.post(url, headers=headers, json=payload, timeout=20)
    if resp.status_code == 422:  # label problems: post without it
        payload.pop("labels")
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    logger.info("Posted report as issue %s", resp.json().get("html_url"))


def run_end_of_day(day: date, broker=None) -> int:
    """Write (and publish) today's daily report, plus the 5-day report every fifth trading day."""
    if broker is None:
        from execution.alpaca_broker import AlpacaBroker

        broker = AlpacaBroker()
    equity = broker.get_account_equity()
    positions = broker.get_positions()

    history = [r for r in _read_equity() if r["date"] < day.isoformat()]
    prev_equity = history[-1]["equity"] if history else _trial_start_equity()
    _record_equity(day, equity)

    spy = _spy_closes(day - timedelta(days=21), day)
    daily = build_daily_report(day, equity, positions, prev_equity, _spy_return(spy, day, day))
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    daily_path = DAILY_DIR / f"{day.isoformat()}.md"
    daily_path.write_text(daily, encoding="utf-8")

    rc = 0
    try:
        publish_issue(f"Daily trading report — {day:%a %d %b %Y}", daily, f"reports/daily/{daily_path.name}", "daily-report")
    except Exception:  # noqa: BLE001 - the saved file is the report of record
        logger.warning("Could not post the daily report issue", exc_info=True)
        rc = 1

    trading_days = sorted(p.stem for p in DAILY_DIR.glob("*.md"))
    if len(trading_days) % DAYS_PER_PERIOD_REPORT == 0:
        days = [date.fromisoformat(d) for d in trading_days[-DAYS_PER_PERIOD_REPORT:]]
        equity_by_day = {r["date"]: r["equity"] for r in _read_equity()}
        earlier = [r for r in _read_equity() if r["date"] < days[0].isoformat()]
        start_equity = earlier[-1]["equity"] if earlier else _trial_start_equity()
        spy = _spy_closes(days[0] - timedelta(days=10), day)
        period = build_period_report(days, equity_by_day, start_equity, positions, spy, len(trading_days))
        WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
        period_path = WEEKLY_DIR / f"5-day-ending-{day.isoformat()}.md"
        period_path.write_text(period, encoding="utf-8")
        try:
            publish_issue(
                f"5-day trading report — {days[0]:%d %b} to {day:%d %b %Y}", period,
                f"reports/weekly/{period_path.name}", "5-day-report",
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not post the 5-day report issue", exc_info=True)
            rc = 1
    return rc


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return run_end_of_day(datetime.now(NY_TZ).date())
