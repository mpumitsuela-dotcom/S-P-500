"""
Alpaca Market Data client - free with any Alpaca account (paper or live),
covers IEX-feed OHLCV bars and latest quotes, which is enough for a daily
factor strategy (no need for a paid SIP real-time feed).

Docs: https://docs.alpaca.markets/reference/stockbars
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import API_KEYS
from data.cache import cached_call

logger = logging.getLogger(__name__)

BARS_TTL_SECONDS = 3600 * 4  # historical daily bars don't change intraday except today's bar


def _headers() -> dict:
    return {
        "APCA-API-KEY-ID": API_KEYS.alpaca_key_id,
        "APCA-API-SECRET-KEY": API_KEYS.alpaca_secret_key,
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def _get(url: str, params: dict) -> dict:
    resp = requests.get(url, headers=_headers(), params=params, timeout=20)
    if resp.status_code == 429:
        raise RuntimeError("Alpaca data API rate-limited (429)")
    resp.raise_for_status()
    return resp.json()


def get_daily_bars(symbols: list[str], start: date, end: date, feed: str = "iex") -> pd.DataFrame:
    """
    Fetch daily OHLCV bars for a list of symbols between [start, end].
    Returns a long DataFrame: columns [symbol, date, open, high, low, close, volume].
    Batches symbols (Alpaca allows multi-symbol requests) and paginates via next_page_token.
    """
    if not API_KEYS.alpaca_key_id:
        raise RuntimeError("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set - see .env.example")

    all_rows: list[dict] = []
    batch_size = 200  # Alpaca supports many symbols per request; keep batches modest for URL length
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        cache_key = f"{','.join(sorted(batch))}|{start}|{end}|{feed}"

        def fetch(batch=batch):
            rows: list[dict] = []
            page_token = None
            while True:
                params = {
                    "symbols": ",".join(batch),
                    "timeframe": "1Day",
                    "start": f"{start}T00:00:00Z",
                    "end": f"{end}T00:00:00Z",
                    "feed": feed,
                    "limit": 10000,
                }
                if page_token:
                    params["page_token"] = page_token
                payload = _get(f"{API_KEYS.alpaca_data_url}/v2/stocks/bars", params)
                bars_by_symbol = payload.get("bars", {})
                for sym, bars in bars_by_symbol.items():
                    for b in bars:
                        rows.append(
                            {
                                "symbol": sym,
                                "date": b["t"][:10],
                                "open": b["o"],
                                "high": b["h"],
                                "low": b["l"],
                                "close": b["c"],
                                "volume": b["v"],
                            }
                        )
                page_token = payload.get("next_page_token")
                if not page_token:
                    break
            return rows

        all_rows.extend(cached_call("alpaca_bars", cache_key, BARS_TTL_SECONDS, fetch))

    if not all_rows:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)


def get_latest_quotes(symbols: list[str]) -> pd.DataFrame:
    """Latest trade price per symbol - used for the pre-flight price-sanity guard."""
    if not API_KEYS.alpaca_key_id:
        raise RuntimeError("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set - see .env.example")
    rows = []
    batch_size = 200
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        payload = _get(
            f"{API_KEYS.alpaca_data_url}/v2/stocks/trades/latest",
            {"symbols": ",".join(batch), "feed": "iex"},
        )
        for sym, trade in payload.get("trades", {}).items():
            rows.append({"symbol": sym, "price": trade["p"], "timestamp": trade["t"]})
    return pd.DataFrame(rows)


def default_lookback_start(as_of: date, trading_days: int = 400) -> date:
    """~400 calendar days covers >252 trading days for a 12-month momentum/vol lookback."""
    return as_of - timedelta(days=trading_days)
