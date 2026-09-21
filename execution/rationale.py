"""
Builds a plain-English rationale for each buy/sell decision, referencing the
specific factor scores - and, when available, the underlying research detail
(headline counts/sentiment, analyst consensus) - that drove it. Written to
the trade log (execution/trade_log.py) so TRIAL_REPORT.md (execution/
reporting.py), and the user directly, can see WHY a name was bought or sold,
not just that it was - a direct response to being asked for a report of why
the system bought and sold specific stocks.
"""
from __future__ import annotations

import pandas as pd

_LEG_LABELS = {
    "value": "value (cheapness)",
    "quality": "quality (profitability/balance sheet)",
    "momentum": "price momentum",
    "low_vol": "low volatility",
    "research": "research (news + analyst consensus)",
}


def _top_legs(row: pd.Series, n: int = 2) -> list[tuple[str, float]]:
    legs = {name: row.get(name, 0.0) for name in _LEG_LABELS}
    ranked = sorted(legs.items(), key=lambda kv: abs(kv[1]), reverse=True)
    return [(name, val) for name, val in ranked[:n] if abs(val) > 0.05]


def _research_detail(symbol: str, research: pd.DataFrame | None) -> str | None:
    if research is None or research.empty or symbol not in set(research["symbol"]):
        return None
    row = research[research["symbol"] == symbol].iloc[0]
    bits = []

    headlines = int(row.get("headline_count", 0) or 0)
    sentiment = row.get("news_sentiment_score")
    if headlines:
        if sentiment is not None and sentiment != 0:
            tone = "positive" if sentiment > 0 else "negative"
            bits.append(f"{headlines} recent headline(s), net {tone} tone ({sentiment:+.0f})")
        else:
            bits.append(f"{headlines} recent headline(s), neutral/mixed tone")

    analyst_score = row.get("analyst_score")
    breakdown = row.get("analyst_breakdown")
    total_analysts = int(row.get("total_analysts", 0) or 0)
    if analyst_score is not None and total_analysts:
        if isinstance(breakdown, dict):
            bits.append(
                f"analyst consensus {analyst_score:+.2f}/2 "
                f"({breakdown.get('strongBuy', 0)} strong buy, {breakdown.get('buy', 0)} buy, "
                f"{breakdown.get('hold', 0)} hold, {breakdown.get('sell', 0)} sell, "
                f"{breakdown.get('strongSell', 0)} strong sell)"
            )
        else:
            bits.append(f"analyst consensus {analyst_score:+.2f}/2 across {total_analysts} rating(s)")

    return "; ".join(bits) if bits else None


def explain_rebalance_decision(symbol: str, side: str, scores: pd.DataFrame, research: pd.DataFrame | None = None) -> str:
    """side: 'buy' | 'sell'. scores: research/signals.py compute_combined_scores() output."""
    if symbol not in scores.index:
        return f"{side} — symbol not present in this run's ranked universe (data gap)"

    row = scores.loc[symbol]
    rank = int(row["rank"]) if "rank" in scores.columns and pd.notna(row.get("rank")) else None
    total = int(scores["rank"].max()) if "rank" in scores.columns and not scores.empty else None

    parts = [f"combined score {row['combined_score']:+.2f}"]
    if rank and total:
        parts.append(f"ranked #{rank} of {total}")

    top = _top_legs(row)
    if top:
        parts.append("driven mainly by " + ", ".join(f"{_LEG_LABELS[name]} {val:+.2f}" for name, val in top))

    if side == "buy":
        parts.append("entering/adding to the top-ranked target portfolio")
    else:
        parts.append("trimmed toward target weight, or dropped out of the top-ranked names")

    detail = _research_detail(symbol, research)
    if detail:
        parts.append(f"research detail: {detail}")

    return "; ".join(parts)


def explain_trim_decision(symbol: str, intraday_move: float, sentiment_row: pd.Series | None) -> str:
    """Rationale for scheduler/run_afternoon.py's news+price tactical trim - a
    narrower kind of decision than the AM rebalance (see that module's
    docstring: a risk circuit breaker, not a second alpha call)."""
    parts = [f"intraday move {intraday_move:+.1%} on the position since entry"]
    if sentiment_row is not None:
        score = sentiment_row.get("sentiment_score")
        count = sentiment_row.get("headline_count")
        if count:
            if score is not None:
                parts.append(f"{int(count)} recent headline(s), net negative tone ({score:+.0f})")
            else:
                parts.append(f"{int(count)} recent negative-leaning headline(s)")
    parts.append("trimmed 50% as a risk circuit breaker, not a full exit")
    return "; ".join(parts)
