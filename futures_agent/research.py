"""
Research for the futures agent.

Data (free):
  - Daily prices of the ETF that tracks each market (SPY for the S&P 500,
    QQQ for the Nasdaq, GLD for gold...) and of the index's biggest companies,
    from Alpaca's market-data API (the S&P 500 agent's keys; read-only, no
    trading on that account).
  - Company news and analyst ratings from Finnhub, scored exactly as the
    options agent does (options_agent/signals.py).
  - A reference futures price (last trade) from Yahoo Finance's public chart
    feed, used only to size a trade before it's placed; the stop and target
    are then set from the actual fill price.

Deep research: Google Gemini with live Google Search, asked about the market
as a whole - the biggest companies' results and guidance, the Federal Reserve,
inflation and jobs data, and for oil and gold, supply and geopolitics - plus
any major scheduled event in the next two days.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, timedelta

import pandas as pd
import requests

from futures_agent.settings import Instrument
from options_agent import gemini_research, signals

logger = logging.getLogger("futures_agent.research")


def fetch_closes(symbols: list[str], today: date) -> dict[str, pd.Series]:
    from data.alpaca_data import get_daily_bars

    bars = get_daily_bars(sorted(set(symbols)), today - timedelta(days=150), today + timedelta(days=1))
    return {sym: g.sort_values("date").set_index("date")["close"].astype(float) for sym, g in bars.groupby("symbol")}


def company_scores(companies: list[str], closes: dict[str, pd.Series]) -> dict[str, dict]:
    """Per company: {score, parts, headlines} from price, Finnhub news and analysts."""
    out = {}
    for sym in companies:
        feats = signals.price_features(closes.get(sym, pd.Series(dtype=float)))
        if not feats:
            continue
        try:
            res = signals.fetch_research(sym)
        except Exception as exc:  # noqa: BLE001 - one company's research gap isn't fatal
            logger.warning("Finnhub research failed for %s: %s", sym, exc)
            res = {"news_score": None, "headline_count": 0, "headlines": [], "analyst_score": None}
        sc = signals.direction_score(feats, res["news_score"], res["headline_count"], res["analyst_score"])
        out[sym] = {"score": sc["score"], "return_20d_pct": feats["return_20d_pct"],
                    "headlines": res["headlines"][:2], "researched": bool(res["headline_count"] or res["analyst_score"] is not None)}
    return out


def futures_price(root: str) -> float | None:
    """Last futures price from Yahoo Finance (e.g. MES=F), or None."""
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{root}=F",
            params={"interval": "1d", "range": "5d"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=20,
        )
        resp.raise_for_status()
        meta = resp.json()["chart"]["result"][0]["meta"]
        price = float(meta.get("regularMarketPrice") or 0)
        return price or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("No reference price for %s: %s", root, exc)
        return None


def build_prompt(inst: Instrument, today: date, facts: dict) -> str:
    return f"""You are a futures strategist at a macro hedge fund. Today is {today:%A %d %B %Y}.
Research the outlook for the {inst.name} futures contract ({inst.root}) over the next 1-2 weeks, using
Google Search for the latest information: for stock indexes, the biggest companies' recent results,
guidance and news, and market breadth; for all markets, the Federal Reserve and interest rates,
inflation and jobs data, the US dollar, and geopolitics; for oil and gold, supply, demand and inventories.
Also find the economic calendar: is a major scheduled event (Fed decision, CPI, jobs report, or for
oil an OPEC meeting) due within the next 2 days?

A trading agent is deciding whether to go LONG (expects a rise) or SHORT (expects a fall). Its own
market data says:
{json.dumps(facts, indent=1, default=str)}

Weigh the evidence honestly. If it is mixed or thin, say "neutral" - a missed trade costs nothing,
a wrong one loses money. Check the data above against the news rather than repeating it.

Reply with ONLY one JSON object, no other text:
{{"direction": "bullish" | "bearish" | "neutral",
  "conviction": <integer 0-100, how confident you are it moves that way within 2 weeks>,
  "expected_move_pct": <number, expected % move over 2 weeks, negative for down>,
  "thesis": "<2-3 sentences: why, citing the specific facts>",
  "catalysts": ["<upcoming event with date>", ...],
  "risks": ["<what would make this wrong>", ...],
  "key_events": ["<scheduled market-moving event in the next 7 days, with date>", ...],
  "major_event_within_2_days": true | false}}"""


def research_market(inst: Instrument, today: date, facts: dict) -> dict:
    return gemini_research.ask(build_prompt(inst, today, facts), inst.root)


def market_facts(inst: Instrument, feats: dict, companies: dict[str, dict], score: dict) -> dict:
    facts = {
        "tracking_etf": inst.proxy,
        "vs_50_day_average_pct": feats["vs_sma50_pct"],
        "return_5_days_pct": feats["return_5d_pct"],
        "return_20_days_pct": feats["return_20d_pct"],
        "annual_volatility_pct": feats["annual_vol_pct"],
        "rsi14": feats["rsi14"],
        "agent_data_score_-1_to_+1": score["score"],
    }
    if companies:
        facts["biggest_companies"] = {s: {"score": c["score"], "return_20d_pct": c["return_20d_pct"], "headlines": c["headlines"]}
                                      for s, c in companies.items()}
    return facts


def daily_vol(feats: dict) -> float:
    """Typical daily move as a fraction."""
    return float(feats["_vol20"] or feats["annual_vol_pct"] / 100 / math.sqrt(252))
