"""
S&P 500 constituent list, sourced from Wikipedia (free, no API key).

IMPORTANT limitation (stated plainly rather than hidden): this only gives
the *current* constituent list, not point-in-time history. Backtests run
against today's constituents applied to historical prices, which is a
survivorship-bias risk (companies that were removed from the index - often
because they performed badly - are invisible to the backtest). The
tearsheet in backtest/metrics.py prints an explicit warning about this on
every run. A production-grade fix would need a point-in-time membership
history (e.g. from a paid provider); that is out of scope for this build.
"""
from __future__ import annotations

import io
import logging

import pandas as pd
import requests

from data.cache import cached_call

logger = logging.getLogger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UNIVERSE_TTL_SECONDS = 24 * 3600  # constituents rarely change intraday/day-to-day


def _fetch_from_wikipedia() -> list[dict]:
    resp = requests.get(
        WIKI_URL,
        headers={"User-Agent": "sp500-agent-research/1.0 (educational use)"},
        timeout=20,
    )
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    df = df.rename(
        columns={
            "Symbol": "symbol",
            "Security": "name",
            "GICS Sector": "sector",
            "GICS Sub-Industry": "sub_industry",
        }
    )
    # Keep Wikipedia's native "BRK.B" / "BF.B" dot notation as-is - confirmed against a real
    # Alpaca account (both /v2/stocks/bars and /v2/assets) that Alpaca wants the dot, not a
    # dash. An earlier version of this file converted "." to "-", which is wrong and makes
    # Alpaca return 400 "invalid symbol" for every dash-notation ticker in a batch request -
    # confirmed 2026-09-21 against a live account.
    keep = df[["symbol", "name", "sector", "sub_industry"]].dropna(subset=["symbol"])
    return keep.to_dict(orient="records")


def get_sp500_constituents() -> pd.DataFrame:
    """Return a DataFrame of symbol, name, sector, sub_industry for the current S&P 500."""
    records = cached_call("universe", WIKI_URL, UNIVERSE_TTL_SECONDS, _fetch_from_wikipedia)
    df = pd.DataFrame(records)
    if df.empty:
        raise RuntimeError("S&P 500 constituent fetch returned no rows - Wikipedia table layout may have changed")
    return df.reset_index(drop=True)


def get_sector_map() -> dict[str, str]:
    df = get_sp500_constituents()
    return dict(zip(df["symbol"], df["sector"]))
