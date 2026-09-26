"""
Structured record of every decision, with the research behind it, for the
daily and 5-day reports (execution/daily_report.py).

execution/trade_log.py keeps a one-line reason per order. This keeps the
evidence behind it: factor scores and rank, the company's fundamentals, its
price trend and volatility, the actual news headlines with their tone, and
the analyst ratings, as they stood when the decision was made. It also
records how each session ended (traded, nothing to do, halted by a guard),
so a report can explain a day with no trades.

Stored as JSON lines in reports/data/decisions.jsonl, which is part of the
shared state on the agent-state branch (scheduler/shared_state.py).
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from config import PROJECT_ROOT

NY_TZ = ZoneInfo("America/New_York")
REPORTS_DIR = PROJECT_ROOT / "reports"
DECISIONS_FILE = REPORTS_DIR / "data" / "decisions.jsonl"

_FUNDAMENTAL_FIELDS = ("pe", "pb", "roe", "gross_margin", "debt_to_equity")
_FACTOR_FIELDS = ("combined_score", "rank", "value", "quality", "momentum", "low_vol", "research", "sector")


def _clean(v):
    """JSON-safe: numpy scalars -> python, NaN/inf -> None, containers recursively."""
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if v is pd.NA or v is pd.NaT:
        return None
    return v


def _row(frame: pd.DataFrame | None, symbol: str) -> dict:
    """One symbol's row as a dict, whether `symbol` is the index or a column."""
    if frame is None or frame.empty:
        return {}
    if "symbol" in frame.columns:
        match = frame[frame["symbol"] == symbol]
        return match.iloc[0].to_dict() if not match.empty else {}
    return frame.loc[symbol].to_dict() if symbol in frame.index else {}


def price_stats(prices: pd.DataFrame | None, symbol: str) -> dict:
    """Plain-number price context: 12-1 month return (what the momentum factor
    ranks on), last-month return, and 60-day annualized volatility."""
    if prices is None or prices.empty:
        return {}
    closes = prices[prices["symbol"] == symbol].sort_values("date")["close"].reset_index(drop=True)
    if len(closes) < 30:
        return {}
    out = {"last_close": float(closes.iloc[-1])}
    end = len(closes) - 22
    start = max(end - 252, 0)
    if end > start and closes.iloc[start]:
        out["return_12m_ex_1m"] = float(closes.iloc[end] / closes.iloc[start] - 1)
    out["return_1m"] = float(closes.iloc[-1] / closes.iloc[-22] - 1)
    daily = closes.pct_change().dropna().tail(60)
    if len(daily) >= 20:
        out["volatility_60d_annualized"] = float(daily.std() * math.sqrt(252))
    return out


def research_snapshot(
    symbol: str,
    scores: pd.DataFrame | None = None,
    fundamentals: pd.DataFrame | None = None,
    research: pd.DataFrame | None = None,
    prices: pd.DataFrame | None = None,
) -> dict:
    s = _row(scores, symbol)
    f = _row(fundamentals, symbol)
    r = _row(research, symbol)
    snap = {
        "factors": {k: s.get(k) for k in _FACTOR_FIELDS if k in s},
        "fundamentals": {k: f.get(k) for k in _FUNDAMENTAL_FIELDS if f.get(k) is not None and f.get(k) == f.get(k)},
        "fundamentals_source": f.get("source"),
        "price": price_stats(prices, symbol),
        "news": {
            "headline_count": r.get("headline_count"),
            "sentiment_score": r.get("news_sentiment_score", r.get("sentiment_score")),
            "top_headlines": r.get("top_headlines") or [],
        },
        "analysts": {
            "score": r.get("analyst_score"),
            "total": r.get("total_analysts"),
            "breakdown": r.get("analyst_breakdown"),
            "period": r.get("analyst_period"),
        },
    }
    return _clean(snap)


def _append(record: dict) -> None:
    DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DECISIONS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_clean(record), ensure_ascii=False) + "\n")


def record_trades(session: str, results: list[dict], snapshots: dict[str, dict], prices: pd.Series | None = None, now: datetime | None = None, extra: dict[str, dict] | None = None) -> None:
    """results: execution/rebalancer.py execute_orders() output."""
    now = now or datetime.now(NY_TZ)
    for r in results:
        sym = r["symbol"]
        price = float(prices[sym]) if prices is not None and sym in prices.index else None
        _append(
            {
                "type": "trade",
                "timestamp": now.isoformat(timespec="seconds"),
                "date": now.date().isoformat(),
                "session": session,
                "symbol": sym,
                "side": r["side"],
                "qty": r["qty"],
                "status": r["status"],
                "price": price,
                "value": price * r["qty"] if price else None,
                "reason": r.get("reason", ""),
                "research": snapshots.get(sym, {}),
                **(extra or {}).get(sym, {}),
            }
        )


def record_session(session: str, outcome: str, detail: str = "", now: datetime | None = None, **fields) -> None:
    """outcome: completed | no_trades | halted | crashed"""
    now = now or datetime.now(NY_TZ)
    _append(
        {
            "type": "session",
            "timestamp": now.isoformat(timespec="seconds"),
            "date": now.date().isoformat(),
            "session": session,
            "outcome": outcome,
            "detail": detail,
            **fields,
        }
    )


def read_records(start: date, end: date) -> list[dict]:
    if not DECISIONS_FILE.exists():
        return []
    out = []
    for line in DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if start.isoformat() <= rec.get("date", "") <= end.isoformat():
            out.append(rec)
    return out
