"""Currency exchange-rate prices (no API key): Yahoo Finance chart endpoint.

Returns rates expressed as "USD per 1 unit of the currency", so a rising
series always means the currency is getting stronger against the dollar.
For the dollar itself we use the US Dollar Index (DXY).
"""
from __future__ import annotations

import logging

import pandas as pd

from . import http

log = logging.getLogger(__name__)
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def parse_chart(payload: dict, invert: bool = False) -> pd.DataFrame:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    r = result[0]
    ts = r.get("timestamp") or []
    quote = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    df = pd.DataFrame(
        {k: quote.get(k) or [None] * len(ts) for k in ("open", "high", "low", "close")},
        index=pd.to_datetime(ts, unit="s", utc=True),
    ).astype(float).dropna()
    if invert and not df.empty:
        inv = 1.0 / df
        # inverting swaps which side is the high and which is the low
        df = pd.DataFrame({"open": inv["open"], "high": inv["low"], "low": inv["high"], "close": inv["close"]})
    return df


def fetch(symbol: str, interval: str, range_: str, invert: bool = False) -> pd.DataFrame:
    try:
        resp = http.get(CHART_URL.format(symbol=symbol), params={"interval": interval, "range": range_})
        return parse_chart(resp.json(), invert=invert)
    except Exception as exc:  # noqa: BLE001 - one bad source must not stop the run
        log.warning("FX prices unavailable for %s (%s %s): %s", symbol, interval, range_, exc)
        return pd.DataFrame(columns=["open", "high", "low", "close"])
