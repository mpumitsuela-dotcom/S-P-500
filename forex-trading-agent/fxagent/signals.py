"""Combine price action, news and interest rates into one score per currency,
then turn scores into trade ideas on the matching ETFs.

Each component is squashed to -1..+1, then blended with fixed weights. A
component with no data is left out and the remaining weights are rescaled.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import atr, ema, pct_return, rsi, zclip
from .sentiment import CurrencyView
from .universe import CURRENCIES, USD_BEAR_ETF, USD_BULL_ETF

WEIGHTS = {
    "intraday_trend": 0.25,   # fast vs slow average on 15-minute bars
    "intraday_momentum": 0.20,  # move over the last ~4 hours, vs normal volatility
    "daily_trend": 0.20,      # 20-day move, vs normal volatility
    "news": 0.25,             # headline sentiment (Claude or keywords)
    "carry": 0.10,            # interest-rate gap vs the dollar
}
OVERBOUGHT_RSI = 75
OVERSOLD_RSI = 25


@dataclass
class CurrencySignal:
    code: str
    score: float
    components: dict[str, float] = field(default_factory=dict)
    rsi: float | None = None
    atr_pct: float | None = None   # typical 15-min range as a fraction of price
    notes: list[str] = field(default_factory=list)


@dataclass
class TradeIdea:
    etf: str
    currency: str
    direction: str       # "bullish" or "bearish" (only for USD via UDN)
    strength: float      # 0..1
    signal: CurrencySignal
    reason: str = ""


def price_components(intraday: pd.DataFrame, daily: pd.DataFrame) -> tuple[dict[str, float], float | None, float | None]:
    comps: dict[str, float] = {}
    rsi_val = atr_pct = None
    if len(intraday) >= 30:
        close = intraday["close"]
        a = atr(intraday).iloc[-1]
        if a > 0:
            comps["intraday_trend"] = zclip(float(ema(close, 8).iloc[-1] - ema(close, 21).iloc[-1]) / a, 1.0)
            atr_pct = float(a / close.iloc[-1])
        rets = close.pct_change().dropna()
        vol = float(rets.tail(200).std()) * np.sqrt(16)
        if vol > 0:
            comps["intraday_momentum"] = zclip(pct_return(close, 16) / vol, 1.5)
        rsi_val = float(rsi(close).iloc[-1])
    if len(daily) >= 25:
        close = daily["close"]
        vol = float(close.pct_change().dropna().tail(120).std()) * np.sqrt(20)
        if vol > 0:
            comps["daily_trend"] = zclip(pct_return(close, 20) / vol, 1.5)
    return comps, rsi_val, atr_pct


def blend(comps: dict[str, float]) -> float:
    used = {k: w for k, w in WEIGHTS.items() if k in comps}
    total = sum(used.values())
    if total == 0:
        return 0.0
    return float(sum(comps[k] * w for k, w in used.items()) / total)


def build_signals(
    prices: dict[str, tuple[pd.DataFrame, pd.DataFrame]],   # code -> (intraday, daily), USD = dollar index
    news: dict[str, CurrencyView],
    rates: dict[str, float | None],
) -> dict[str, CurrencySignal]:
    codes = ["USD"] + list(CURRENCIES)
    news_score = {c: (news.get(c).score if news.get(c) else 0.0) for c in codes}
    others = [c for c in codes if c != "USD"]
    known_rates = {c: r for c, r in rates.items() if r is not None}

    out = {}
    for code in codes:
        intraday, daily = prices.get(code, (pd.DataFrame(), pd.DataFrame()))
        comps, rsi_val, atr_pct = price_components(intraday, daily)
        notes = []
        # News is relative: good euro news only helps EUR/USD if dollar news isn't better.
        if code == "USD":
            rel_news = news_score["USD"] - float(np.mean([news_score[c] for c in others]))
        else:
            rel_news = news_score[code] - news_score["USD"]
        if any(news.get(c) and (news[c].headlines or news[c].reason not in ("", "no clear news", "not scored")) for c in (code, "USD")):
            comps["news"] = max(-1.0, min(1.0, rel_news))
        # Carry: rate gap vs the dollar (for USD: vs the average of the others), in percentage points.
        if code == "USD" and "USD" in known_rates and len(known_rates) > 1:
            comps["carry"] = zclip(known_rates["USD"] - float(np.mean([r for c, r in known_rates.items() if c != "USD"])), 2.0)
        elif code != "USD" and code in known_rates and "USD" in known_rates:
            comps["carry"] = zclip(known_rates[code] - known_rates["USD"], 2.0)

        score = blend(comps)
        if rsi_val is not None and score > 0 and rsi_val > OVERBOUGHT_RSI:
            score *= 0.5
            notes.append(f"overbought (RSI {rsi_val:.0f}) - signal halved")
        if rsi_val is not None and score < 0 and rsi_val < OVERSOLD_RSI:
            score *= 0.5
            notes.append(f"oversold (RSI {rsi_val:.0f}) - signal halved")
        if "intraday_trend" not in comps:
            notes.append("no intraday price data")
        out[code] = CurrencySignal(code, score, comps, rsi_val, atr_pct, notes)
    return out


def _describe(sig: CurrencySignal, news: dict[str, CurrencyView]) -> str:
    names = {"intraday_trend": "15-min trend", "intraday_momentum": "4-hour momentum", "daily_trend": "20-day trend", "news": "news", "carry": "interest rates"}
    parts = [f"{names[k]} {'+' if v >= 0 else ''}{v:.2f}" for k, v in sorted(sig.components.items(), key=lambda kv: -abs(kv[1]))]
    reason = ", ".join(parts)
    view = news.get(sig.code)
    if view and view.reason and view.reason not in ("no clear news", "not scored"):
        reason += f". News: {view.reason}"
    if sig.notes:
        reason += ". " + "; ".join(sig.notes)
    return reason


def trade_ideas(signals: dict[str, CurrencySignal], news: dict[str, CurrencyView], threshold: float) -> list[TradeIdea]:
    ideas = []
    for code, sig in signals.items():
        if code == "USD":
            if sig.score >= threshold:
                ideas.append(TradeIdea(USD_BULL_ETF, "USD", "bullish", sig.score, sig, _describe(sig, news)))
            elif sig.score <= -threshold:
                ideas.append(TradeIdea(USD_BEAR_ETF, "USD", "bearish", -sig.score, sig, _describe(sig, news)))
        elif sig.score >= threshold:
            ideas.append(TradeIdea(CURRENCIES[code].etf, code, "bullish", sig.score, sig, _describe(sig, news)))
    return sorted(ideas, key=lambda i: -i.strength)


def held_view(etf: str, signals: dict[str, CurrencySignal]) -> float:
    """Current score for a held ETF, signed so that >0 still supports holding it."""
    if etf == USD_BULL_ETF:
        return signals["USD"].score
    if etf == USD_BEAR_ETF:
        return -signals["USD"].score
    for code, c in CURRENCIES.items():
        if c.etf == etf and code in signals:
            return signals[code].score
    return 0.0
