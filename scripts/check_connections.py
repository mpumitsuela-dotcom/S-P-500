#!/usr/bin/env python3
"""
Check that every data and research API the agent uses is reachable and
accepts the configured key. Run it before trusting the scheduler:

    python3 scripts/check_connections.py

It makes one small request per endpoint (about 7 in total, well inside every
free tier), bypasses the disk cache, and places no orders. Exits 0 only when
every check passes.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import API_KEYS  # noqa: E402

PROBE_SYMBOL = "AAPL"
TIMEOUT = 20


def _alpaca_headers() -> dict:
    return {"APCA-API-KEY-ID": API_KEYS.alpaca_key_id, "APCA-API-SECRET-KEY": API_KEYS.alpaca_secret_key}


def _check(name: str, fn) -> bool:
    try:
        detail = fn()
        print(f"  OK    {name}: {detail}")
        return True
    except Exception as exc:  # noqa: BLE001 - report every failure, keep checking
        print(f"  FAIL  {name}: {exc}")
        return False


def _get_json(url: str, **kwargs):
    resp = requests.get(url, timeout=TIMEOUT, **kwargs)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def alpaca_account():
    acct = _get_json(f"{API_KEYS.alpaca_base_url}/v2/account", headers=_alpaca_headers())
    mode = "PAPER" if "paper" in API_KEYS.alpaca_base_url else "LIVE"
    return f"{mode} account, equity ${float(acct['equity']):,.2f}, status {acct.get('status')}"


def alpaca_clock():
    clock = _get_json(f"{API_KEYS.alpaca_base_url}/v2/clock", headers=_alpaca_headers())
    return f"market {'open' if clock['is_open'] else 'closed'}, next open {clock['next_open']}"


def alpaca_bars():
    end = date.today()
    payload = _get_json(
        f"{API_KEYS.alpaca_data_url}/v2/stocks/bars",
        headers=_alpaca_headers(),
        params={"symbols": PROBE_SYMBOL, "timeframe": "1Day", "start": (end - timedelta(days=10)).isoformat(), "feed": "iex"},
    )
    bars = payload.get("bars", {}).get(PROBE_SYMBOL, [])
    if not bars:
        raise RuntimeError("no bars returned")
    return f"{len(bars)} daily bars for {PROBE_SYMBOL}, last close {bars[-1]['c']}"


def fmp_fundamentals():
    data = _get_json(
        "https://financialmodelingprep.com/stable/ratios-ttm",
        params={"symbol": PROBE_SYMBOL, "apikey": API_KEYS.fmp_key},
    )
    if not data:
        raise RuntimeError("empty response")
    return f"TTM ratios for {PROBE_SYMBOL} returned"


def finnhub_news():
    today = date.today()
    news = _get_json(
        "https://finnhub.io/api/v1/company-news",
        params={"symbol": PROBE_SYMBOL, "from": (today - timedelta(days=3)).isoformat(), "to": today.isoformat(), "token": API_KEYS.finnhub_key},
    )
    return f"{len(news)} recent {PROBE_SYMBOL} headlines"


def finnhub_analysts():
    recs = _get_json(
        "https://finnhub.io/api/v1/stock/recommendation",
        params={"symbol": PROBE_SYMBOL, "token": API_KEYS.finnhub_key},
    )
    if not recs:
        raise RuntimeError("no analyst recommendation data")
    return f"analyst consensus for {recs[0].get('period')}"


def wikipedia_universe():
    from data.universe import _fetch_from_wikipedia

    rows = _fetch_from_wikipedia()
    if len(rows) < 450:
        raise RuntimeError(f"only {len(rows)} constituents parsed")
    return f"{len(rows)} S&P 500 constituents"


def main() -> int:
    missing = API_KEYS.missing()
    if missing:
        print(f"Missing keys: {', '.join(missing)} - see .env.example")

    checks = [
        ("Alpaca trading (account)", alpaca_account),
        ("Alpaca trading (market clock)", alpaca_clock),
        ("Alpaca market data (price bars)", alpaca_bars),
        ("FMP fundamentals (research: value/quality)", fmp_fundamentals),
        ("Finnhub company news (research: sentiment)", finnhub_news),
        ("Finnhub analyst ratings (research: consensus)", finnhub_analysts),
        ("Wikipedia S&P 500 list (universe)", wikipedia_universe),
    ]
    print("Checking connections:")
    results = [_check(name, fn) for name, fn in checks]
    passed = sum(results)
    print(f"\n{passed}/{len(results)} connections OK")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
