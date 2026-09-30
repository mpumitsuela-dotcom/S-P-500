"""
The agent's own data vote: a direction score in [-1, +1] per company built
from market data and free research feeds.

  trend     (35%)  price vs its 50-day average, and 20-day vs 50-day average
  momentum  (30%)  20-day return relative to the stock's own volatility
  news      (20%)  Finnhub headlines about the company, last 2 days
  analysts  (15%)  Finnhub analyst buy/sell consensus, relative to the usual
                   bullish tilt (most stocks are rated "buy" on balance)

+1 means everything points up (a call), -1 everything points down (a put).
Timing data: the next earnings date (Finnhub calendar) - the agent does not
open a position within a week of earnings, when prices jump unpredictably and
option prices collapse right after the report.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta

import pandas as pd

logger = logging.getLogger("options_agent.signals")

WEIGHTS = {"trend": 0.35, "momentum": 0.30, "news": 0.20, "analysts": 0.15}
# Typical analyst consensus on the (2*strongBuy + buy - sell - 2*strongSell)/n
# scale for large caps is about +0.8; above that is genuinely positive.
ANALYST_NEUTRAL = 0.8


def _clip(x: float) -> float:
    return float(max(-1.0, min(1.0, x)))


def rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(period).mean().iloc[-1]
    if loss == 0 or math.isnan(loss):
        return 100.0
    return float(100 - 100 / (1 + gain / loss))


def price_features(closes: pd.Series) -> dict | None:
    """closes: daily closes, oldest first. None when there's too little history."""
    closes = closes.dropna()
    if len(closes) < 60:
        return None
    last = float(closes.iloc[-1])
    sma20, sma50 = float(closes.tail(20).mean()), float(closes.tail(50).mean())
    daily = closes.pct_change().dropna()
    vol20 = float(daily.tail(20).std())
    ret20 = float(closes.iloc[-1] / closes.iloc[-21] - 1)
    ret5 = float(closes.iloc[-1] / closes.iloc[-6] - 1)
    return {
        "price": round(last, 2),
        "vs_sma50_pct": round((last / sma50 - 1) * 100, 2),
        "sma20_vs_sma50_pct": round((sma20 / sma50 - 1) * 100, 2),
        "return_5d_pct": round(ret5 * 100, 2),
        "return_20d_pct": round(ret20 * 100, 2),
        "annual_vol_pct": round(vol20 * math.sqrt(252) * 100, 1),
        "rsi14": round(rsi(closes), 1),
        "_vol20": vol20,
        "_ret20": ret20,
    }


def direction_score(features: dict, news_score: float | None, headline_count: int, analyst_score: float | None) -> dict:
    """Combine the pieces into one score. Missing research legs are left out
    and the remaining weights rescaled; the result records which were used."""
    parts: dict[str, float] = {}
    trend = 0.6 * _clip(features["vs_sma50_pct"] / 6) + 0.4 * _clip(features["sma20_vs_sma50_pct"] / 3)
    parts["trend"] = trend
    vol = features["_vol20"] or 0.02
    parts["momentum"] = _clip(features["_ret20"] / (vol * math.sqrt(20)) / 2)
    if news_score is not None and headline_count > 0:
        parts["news"] = _clip(news_score / 5)
    if analyst_score is not None:
        parts["analysts"] = _clip((analyst_score - ANALYST_NEUTRAL) / 1.0)
    total_w = sum(WEIGHTS[k] for k in parts)
    score = sum(WEIGHTS[k] * v for k, v in parts.items()) / total_w
    return {"score": round(score, 3), "parts": {k: round(v, 2) for k, v in parts.items()}}


def market_regime(spy_closes: pd.Series) -> dict:
    f = price_features(spy_closes)
    if not f:
        return {"trend": "unknown"}
    return {
        "trend": "up" if f["vs_sma50_pct"] > 0 else "down",
        "spy_vs_sma50_pct": f["vs_sma50_pct"],
        "spy_return_20d_pct": f["return_20d_pct"],
        "spy_annual_vol_pct": f["annual_vol_pct"],
    }


def days_to_earnings(earnings_date: str | None, today: date) -> int | None:
    if not earnings_date:
        return None
    try:
        d = date.fromisoformat(earnings_date)
    except ValueError:
        return None
    return (d - today).days if d >= today else None


# --- data fetching (network) ---------------------------------------------

def fetch_closes(symbols: list[str], today: date) -> dict[str, pd.Series]:
    from options_agent import broker

    out = {}
    for sym in symbols:
        try:
            out[sym] = broker.daily_closes(sym, today - timedelta(days=150), today)
        except Exception as exc:  # noqa: BLE001 - one missing history skips that company only
            logger.warning("Price history failed for %s: %s", sym, exc)
    return out


def fetch_earnings_dates(symbols: list[str], today: date) -> dict[str, str]:
    """Next earnings date per symbol within ~70 days (Finnhub free calendar)."""
    from data import finnhub_data

    try:
        payload = finnhub_data._get(
            "/calendar/earnings", {"from": today.isoformat(), "to": (today + timedelta(days=70)).isoformat()}
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Earnings calendar unavailable: %s", exc)
        return {}
    wanted, out = set(symbols), {}
    for row in (payload or {}).get("earningsCalendar") or []:
        sym, d = row.get("symbol"), row.get("date")
        if sym in wanted and d and (sym not in out or d < out[sym]):
            out[sym] = d
    return out


def _headline_text(h: dict) -> str:
    """'Headline (Source, 2026-09-29)' so Gemini can tell how recent and from where."""
    when = datetime.utcfromtimestamp(h["datetime"]).strftime("%Y-%m-%d") if h.get("datetime") else ""
    extra = ", ".join(x for x in (h.get("source", ""), when) if x)
    return f"{h.get('headline', '')} ({extra})" if extra else h.get("headline", "")


# Company fundamentals passed to Gemini (Finnhub /stock/metric, cached a week).
FINANCIAL_FIELDS = {
    "marketCapitalization": "market_cap_millions", "peTTM": "pe_ratio", "psTTM": "price_to_sales",
    "revenueGrowthTTMYoy": "revenue_growth_pct_yoy", "epsGrowthTTMYoy": "eps_growth_pct_yoy",
    "grossMarginTTM": "gross_margin_pct", "netProfitMarginTTM": "net_margin_pct", "roeTTM": "return_on_equity_pct",
    "totalDebt/totalEquityQuarterly": "debt_to_equity", "beta": "beta",
    "52WeekHigh": "high_52_weeks", "52WeekLow": "low_52_weeks",
}


def company_financials(symbol: str) -> dict:
    from data import finnhub_data

    try:
        m = finnhub_data.get_basic_financials(symbol)
    except Exception as exc:  # noqa: BLE001 - financials are extra context, not required
        logger.warning("Finnhub financials failed for %s: %s", symbol, exc)
        return {}
    return {name: round(float(m[k]), 2) for k, name in FINANCIAL_FIELDS.items() if isinstance(m.get(k), (int, float))}


def fetch_research(symbol: str) -> dict:
    """Finnhub news sentiment + analyst consensus for one company."""
    from data import finnhub_data

    news = finnhub_data.get_news_sentiment(symbol)
    rec = finnhub_data.get_analyst_recommendation(symbol) or {}
    return {
        "news_score": news.get("sentiment_score"),
        "headline_count": news.get("headline_count", 0),
        "headlines": [_headline_text(h) for h in news.get("top_headlines", [])][:5],
        "analyst_score": finnhub_data._analyst_score(rec),
        "analysts": {k: rec.get(k) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")} if rec else {},
    }


def sector_map() -> dict[str, str]:
    """Symbol -> GICS sector (S&P 500 list), or {} if it can't be loaded."""
    try:
        from data.universe import get_sector_map

        return get_sector_map()
    except Exception as exc:  # noqa: BLE001 - the sector limit is skipped, not the trading day
        logger.warning("Sector list unavailable, sector limit not applied: %s", exc)
        return {}


def company_names() -> dict[str, str]:
    try:
        from data import finnhub_data

        return finnhub_data._company_names()
    except Exception:  # noqa: BLE001
        return {}


def score_universe(symbols: list[str], today: date) -> tuple[pd.DataFrame, dict]:
    """Score every watchlist company. Returns (table, market regime)."""
    closes = fetch_closes(sorted(set(symbols) | {"SPY"}), today)
    regime = market_regime(closes.get("SPY", pd.Series(dtype=float)))
    earnings = fetch_earnings_dates(symbols, today)
    rows = []
    for sym in symbols:
        feats = price_features(closes.get(sym, pd.Series(dtype=float)))
        if not feats:
            logger.warning("Not enough price history for %s", sym)
            continue
        try:
            res = fetch_research(sym)
        except Exception as exc:  # noqa: BLE001 - research gap noted, not fatal
            logger.warning("Finnhub research failed for %s: %s", sym, exc)
            res = {"news_score": None, "headline_count": 0, "headlines": [], "analyst_score": None, "analysts": {}}
        sc = direction_score(feats, res["news_score"], res["headline_count"], res["analyst_score"])
        rows.append({
            "symbol": sym,
            **{k: v for k, v in feats.items() if not k.startswith("_")},
            **res,
            "score": sc["score"],
            "score_parts": sc["parts"],
            "earnings_date": earnings.get(sym),
            "days_to_earnings": days_to_earnings(earnings.get(sym), today),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.reindex(df["score"].abs().sort_values(ascending=False).index).reset_index(drop=True)
    return df, regime
