#!/usr/bin/env python3
"""Check every connection the options agent needs, without trading:
`python -m options_agent.check`. Prints OK/FAIL per service; exits 1 on a failure."""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

from options_agent import broker, gemini_research


def main() -> int:
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            print(f"OK    {name}: {fn()}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"FAIL  {name}: {exc}")

    if not broker.configured():
        print("FAIL  Alpaca: OPT_ALPACA_API_KEY_ID and OPT_ALPACA_API_SECRET_KEY secrets are not set")
        return 1
    check("Alpaca paper endpoint", lambda: (broker.ensure_paper(), broker.base_url())[1])
    check("Alpaca market clock", lambda: broker.get_clock()["description"])
    check("Alpaca account", lambda: "equity ${equity:,.2f}, options level {options_level}".format(**broker.get_account()))
    check("Alpaca option positions", lambda: f"{len(broker.get_option_positions())} held")
    check("Alpaca price history", lambda: f"{len(broker.daily_closes('SPY', date.today() - timedelta(days=10), date.today()))} SPY days")
    check("Alpaca option chain", lambda: f"{len(broker.option_chain('AAPL', 'call', date.today() + timedelta(days=30), date.today() + timedelta(days=60), 0, 1e6))} AAPL calls")

    def finnhub():
        from data import finnhub_data

        return f"{finnhub_data.get_news_sentiment('AAPL')['headline_count']} AAPL headlines"

    check("Finnhub news", finnhub) if os.environ.get("FINNHUB_API_KEY") else print("FAIL  Finnhub: FINNHUB_API_KEY not set")

    def gemini():
        v = gemini_research.research("AAPL", "Apple", date.today(), {"note": "connection test"})
        return f"{v['model']} answered ({v['direction']}, {len(v['sources'])} sources)"

    if gemini_research.api_key():
        check("Gemini models offered to this key", lambda: ", ".join(gemini_research.discover_models()[:8]) or "none listed")
        check("Gemini research", gemini)

        def plain():
            # Tells a quota problem with Google Search grounding apart from one with the key itself.
            model = (gemini_research.discover_models() or gemini_research.MODELS)[0]
            gemini_research._call(model, "Reply with the word OK.", grounded=False)
            return f"{model} answers without Google Search"

        check("Gemini without Google Search (diagnostic)", plain)
    else:
        ok = False
        print("FAIL  Gemini: GEMINI_API_KEY not set (free key: https://aistudio.google.com/apikey)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
