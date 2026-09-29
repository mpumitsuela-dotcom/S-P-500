"""What the agent watches and what it trades.

Alpaca cannot trade currency pairs, so each currency is traded through an
exchange-traded fund that follows it against the US dollar. The agent is
long-only: a bullish view on a currency buys its ETF; a bullish view on the
dollar buys UUP, and a bearish view on the dollar buys UDN.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Currency:
    code: str          # ISO code, e.g. "EUR"
    name: str
    etf: str           # ETF bought when this currency is expected to rise vs USD
    yahoo: str         # Yahoo Finance symbol for the FX rate
    invert: bool       # True when the Yahoo quote is USD per-unit reversed (USD/JPY etc.)
    fred_rate: str     # FRED series id for the policy / short-term rate


CURRENCIES: dict[str, Currency] = {
    "EUR": Currency("EUR", "euro", "FXE", "EURUSD=X", False, "ECBDFR"),
    "JPY": Currency("JPY", "Japanese yen", "FXY", "JPY=X", True, "IRSTCI01JPM156N"),
    "GBP": Currency("GBP", "British pound", "FXB", "GBPUSD=X", False, "IUDSOIA"),
    "AUD": Currency("AUD", "Australian dollar", "FXA", "AUDUSD=X", False, "IRSTCI01AUM156N"),
    "CAD": Currency("CAD", "Canadian dollar", "FXC", "CAD=X", True, "IRSTCI01CAM156N"),
    "CHF": Currency("CHF", "Swiss franc", "FXF", "CHF=X", True, "IRSTCI01CHM156N"),
}

USD_BULL_ETF = "UUP"
USD_BEAR_ETF = "UDN"
USD_INDEX_YAHOO = "DX-Y.NYB"
USD_FRED_RATE = "DFF"

ALL_ETFS = [c.etf for c in CURRENCIES.values()] + [USD_BULL_ETF, USD_BEAR_ETF]

# Words that tie a headline to a currency, used for news matching.
KEYWORDS: dict[str, list[str]] = {
    "USD": ["dollar", "usd", "fed ", "federal reserve", "powell", "fomc", "us cpi", "nonfarm", "payrolls", "treasury yields", "u.s. economy", "us economy"],
    "EUR": ["euro", "eur/", "eurusd", "ecb", "lagarde", "eurozone", "euro area", "germany", "german"],
    "JPY": ["yen", "jpy", "boj", "bank of japan", "ueda", "japan"],
    "GBP": ["pound", "sterling", "gbp", "cable", "bank of england", "boe", "bailey", "uk ", "britain", "british"],
    "AUD": ["aussie", "aud", "rba", "reserve bank of australia", "australia"],
    "CAD": ["loonie", "cad", "bank of canada", "boc", "canada", "canadian", "crude oil", "oil prices"],
    "CHF": ["franc", "chf", "snb", "swiss national bank", "switzerland", "swiss"],
}
