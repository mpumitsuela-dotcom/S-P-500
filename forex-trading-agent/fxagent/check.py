"""Check every connection without placing any trade:  python -m fxagent.check"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from . import config, sentiment
from .alpaca import Alpaca
from .sources import calendar, fx_prices, macro, news
from .universe import ALL_ETFS, CURRENCIES, USD_FRED_RATE


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    s = config.load()
    ok = True

    def line(name, good, detail=""):
        nonlocal ok
        ok &= bool(good) or name.startswith("(optional)")
        print(f"{'OK  ' if good else 'FAIL'} {name}{': ' + detail if detail else ''}")

    try:
        a = Alpaca(s)
        acct = a.account()
        line("Alpaca paper account", True, f"equity ${float(acct['equity']):,.2f}, market {'open' if a.clock()['is_open'] else 'closed'}")
        q = a.quotes(ALL_ETFS)
        line("Alpaca ETF quotes", len(q) > 0, f"{len(q)}/{len(ALL_ETFS)} ETFs quoted")
    except Exception as exc:  # noqa: BLE001
        line("Alpaca paper account", False, str(exc)[:200])
    eur = fx_prices.fetch(CURRENCIES["EUR"].yahoo, "15m", "5d")
    line("FX prices (Yahoo)", len(eur) > 30, f"{len(eur)} EUR/USD 15-min bars")
    hs = news.fetch_all(s.finnhub_key, now=datetime.now(timezone.utc))
    line("News headlines", len(hs) > 0, f"{len(hs)} in the last 12h")
    line("(optional) Finnhub key", bool(s.finnhub_key))
    evs = calendar.fetch()
    line("Economic calendar", len(evs) > 0, f"{len(evs)} events this week")
    r = macro.latest_rate(USD_FRED_RATE, s.fred_key)
    line("(optional) FRED interest rates", r is not None, f"Fed funds {r}%" if r is not None else "no key")
    if s.anthropic_key:
        res = sentiment.claude_scores(s.anthropic_key, hs[:20], [])
        line("(optional) Claude news analysis", res is not None)
    else:
        line("(optional) Claude news analysis", False, "no ANTHROPIC_API_KEY - keyword scorer will be used")
    print("\nAll required connections work." if ok else "\nSome required connections failed - see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
