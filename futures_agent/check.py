#!/usr/bin/env python3
"""Check every connection the futures agent needs, without trading:
`python -m futures_agent.check`. Prints OK/FAIL per service; exits 1 on a failure."""
from __future__ import annotations

import os
import sys
from datetime import date

from futures_agent import research, tradovate
from futures_agent.settings import INSTRUMENTS, SETTINGS
from options_agent import gemini_research


def main() -> int:
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            print(f"OK    {name}: {fn()}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"FAIL  {name}: {exc}")

    if not tradovate.configured():
        print("FAIL  Tradovate: TRADOVATE_USERNAME, TRADOVATE_PASSWORD, TRADOVATE_CID and TRADOVATE_SECRET secrets are not all set")
        return 1
    check("Tradovate demo login", lambda: (tradovate.ensure_demo(), tradovate._authenticate(), "logged in")[2])
    check("Tradovate account", lambda: "{name} (id {id})".format(**tradovate.account()))
    check("Tradovate account value", lambda: f"${tradovate.get_equity():,.2f}")
    check("Tradovate positions", lambda: f"{len(tradovate.get_positions())} open")
    for root in SETTINGS.instruments:
        check(f"Tradovate contract {root}", lambda root=root: "{name}, expires {expiration}".format(
            **tradovate.front_contract(root, date.today(), SETTINGS.min_days_to_expiry + 3)))
        check(f"Reference price {root}", lambda root=root: research.futures_price(root) or (_ for _ in ()).throw(RuntimeError("none")))
    check("Alpaca price history (read-only)", lambda: f"{len(research.fetch_closes([i.proxy for i in INSTRUMENTS.values()], date.today()))} markets")

    def finnhub():
        from data import finnhub_data

        return f"{finnhub_data.get_news_sentiment('AAPL')['headline_count']} AAPL headlines"

    check("Finnhub news", finnhub) if os.environ.get("FINNHUB_API_KEY") else print("FAIL  Finnhub: FINNHUB_API_KEY not set")
    if gemini_research.api_key():
        check("Gemini research", lambda: "{model} answered ({direction})".format(
            **research.research_market(INSTRUMENTS["MES"], date.today(), {"note": "connection test"})))
    else:
        ok = False
        print("FAIL  Gemini: GEMINI_API_KEY not set (free key: https://aistudio.google.com/apikey)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
