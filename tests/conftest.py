"""Shared synthetic fixtures - no network calls anywhere in the test suite."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sector_map():
    return {
        "AAA": "Technology", "BBB": "Technology", "CCC": "Technology",
        "DDD": "Financials", "EEE": "Financials", "FFF": "Financials",
        "GGG": "Energy", "HHH": "Energy", "III": "Energy",
        "JJJ": "Utilities", "KKK": "Utilities",
    }


@pytest.fixture
def synthetic_prices(sector_map):
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2023-01-01", periods=300)
    rows = []
    for i, sym in enumerate(sector_map):
        drift = 0.0003 * (1 + i % 3)  # give symbols different momentum profiles
        vol = 0.01 + 0.002 * (i % 4)
        returns = rng.normal(drift, vol, len(dates))
        prices = 100 * np.cumprod(1 + returns)
        for d, p in zip(dates, prices):
            rows.append({"symbol": sym, "date": d, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1_000_000})
    return pd.DataFrame(rows)


@pytest.fixture
def synthetic_research(sector_map):
    rng = np.random.default_rng(11)
    rows = []
    for sym in sector_map:
        rows.append(
            {
                "symbol": sym,
                "news_sentiment_score": float(rng.integers(-5, 6)),
                "headline_count": int(rng.integers(0, 8)),
                "analyst_score": float(rng.uniform(-2, 2)),
                "total_analysts": int(rng.integers(1, 20)),
                "analyst_breakdown": {"strongBuy": 1, "buy": 2, "hold": 1, "sell": 0, "strongSell": 0},
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def synthetic_fundamentals(sector_map):
    rng = np.random.default_rng(7)
    rows = []
    for sym in sector_map:
        rows.append(
            {
                "symbol": sym,
                "pe": float(rng.uniform(8, 35)),
                "pb": float(rng.uniform(1, 8)),
                "roe": float(rng.uniform(0.02, 0.35)),
                "gross_margin": float(rng.uniform(0.2, 0.7)),
                "debt_to_equity": float(rng.uniform(0.1, 2.5)),
                "earnings_growth": float(rng.uniform(-0.1, 0.3)),
            }
        )
    return pd.DataFrame(rows)
