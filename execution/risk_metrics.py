"""
Risk scorecard for the 5-day report: is the agent beating the S&P 500
through skill, or just by taking more risk?

Built from the end-of-day account values the reports already record
(reports/data/equity.jsonl) and SPY's daily closes, over the whole trial so
far. Formulas follow the python-trading-toolkit skill (Sharpe with a 0%
risk-free rate, drawdown from the running peak, beta = cov / var).
"""
from __future__ import annotations

import math
from datetime import date

MIN_DAYS_FOR_RELIABLE = 20  # below this, say plainly that the numbers are noise-prone
# Volatility, Sharpe, beta and alpha from a handful of daily returns are
# meaningless (2 days gave a Sharpe of 65), so they're only shown from here on.
MIN_DAYS_FOR_RATIOS = 5


def _std(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def compute(equity_rows: list[dict], start_equity: float | None, spy_day_return) -> dict:
    """equity_rows: [{"date": "YYYY-MM-DD", "equity": float}, ...] sorted by date.
    spy_day_return(day) -> SPY's return on that trading day, or None."""
    rows = sorted(equity_rows, key=lambda r: r["date"])
    if not rows:
        return {"days": 0}
    curve = ([start_equity] if start_equity else []) + [r["equity"] for r in rows]
    agent, paired_agent, paired_spy = [], [], []
    prev = start_equity
    for r in rows:
        if prev:
            ra = r["equity"] / prev - 1
            agent.append(ra)
            rs = spy_day_return(date.fromisoformat(r["date"]))
            if rs is not None:
                paired_agent.append(ra)
                paired_spy.append(rs)
        prev = r["equity"]

    out: dict = {"days": len(agent)}
    if start_equity:
        out["agent_return"] = rows[-1]["equity"] / start_equity - 1
    if paired_spy:
        out["spy_return"] = math.prod(1 + x for x in paired_spy) - 1

    peak, max_dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)
    out["max_drawdown"] = max_dd

    sd = _std(agent)
    if sd:
        out["volatility"] = sd * math.sqrt(252)
        out["sharpe"] = (sum(agent) / len(agent)) / sd * math.sqrt(252)
    spy_sd = _std(paired_spy)
    if len(paired_spy) >= 3 and spy_sd:
        ma, ms = sum(paired_agent) / len(paired_agent), sum(paired_spy) / len(paired_spy)
        cov = sum((a - ma) * (s - ms) for a, s in zip(paired_agent, paired_spy)) / (len(paired_spy) - 1)
        out["beta"] = cov / spy_sd ** 2
        if "agent_return" in out and "spy_return" in out:
            out["alpha"] = out["agent_return"] - out["beta"] * out["spy_return"]
    return out


def render(m: dict) -> list[str]:
    lines = ["## Risk scorecard (trial to date)", ""]
    if not m.get("days"):
        return lines + ["- Not enough trading days recorded yet.", ""]

    def pct(v):
        return "n/a" if v is None else f"{v:+.2%}"

    lines.append(f"- **Return:** agent {pct(m.get('agent_return'))} vs S&P 500 {pct(m.get('spy_return'))} over {m['days']} trading day(s)")
    dd = m.get("max_drawdown")
    lines.append(
        f"- **Largest fall from a peak (max drawdown):** {'none so far' if not dd else f'{dd:.2%}'} "
        "— the worst the account has been below its own high"
    )
    if m["days"] < MIN_DAYS_FOR_RATIOS:
        return lines + [
            f"- *Volatility, Sharpe ratio, beta and alpha appear from trading day {MIN_DAYS_FOR_RATIOS}; "
            f"with {m['days']} day(s) they would be meaningless.*",
            "",
        ]
    if "volatility" in m:
        lines.append(f"- **Volatility:** {m['volatility']:.1%} a year — how much the account value swings day to day")
    if "sharpe" in m:
        lines.append(f"- **Sharpe ratio:** {m['sharpe']:.2f} — return per unit of risk, yearly rate (above 1 is good, below 0 is losing)")
    if "beta" in m:
        b = m["beta"]
        feel = "moves more than" if b > 1.1 else "moves less than" if b < 0.9 else "moves about in line with"
        lines.append(f"- **Beta:** {b:.2f} — the account {feel} the S&P 500 (1.00 = exactly with it)")
    if "alpha" in m:
        verdict = "added" if m["alpha"] > 0 else "lost"
        lines.append(f"- **Alpha:** {pct(m['alpha'])} — return {verdict} beyond what the market's own move explains, given the beta above")
    if m["days"] < MIN_DAYS_FOR_RELIABLE:
        lines.append(
            f"- *Only {m['days']} trading day(s) so far: these figures swing a lot and can't yet show whether the "
            f"agent has a real edge. They become meaningful after about {MIN_DAYS_FOR_RELIABLE} days.*"
        )
    return lines + [""]
