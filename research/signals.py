"""
Combines factor scores into a single ranking signal per the weights in
config.StrategyConfig.

IMPORTANT lookahead-bias note: value, quality, and research all need
point-in-time data (what P/E ratio, what analyst consensus, was known *as
of that historical date*). The free-tier sources used here (FMP
key-metrics-ttm/ratios-ttm, Finnhub news/recommendation) only return
CURRENT snapshots, not history. Using "current" fundamentals/research to
make decisions on past dates in a backtest would leak future information
into the past - a classic, serious backtest bug.

So: fundamentals and research are both OPTIONAL here. When either is None
(or empty), only the remaining active factors are used and their weights
are renormalized to sum to 1.0. The backtester (backtest/engine.py) always
calls this in price-only mode (fundamentals=None, research=None) for
exactly this reason. The live/paper scheduler (scheduler/run_morning.py)
calls it WITH both, which is safe there because "today's real current
fundamentals/research" is exactly the correct information set for a
decision made today.
"""
from __future__ import annotations

import pandas as pd

from config import STRATEGY
from research.factors import (
    compute_low_vol_factor,
    compute_momentum_factor,
    compute_quality_factor,
    compute_research_factor,
    compute_value_factor,
)


def compute_combined_scores(
    prices: pd.DataFrame,
    sector_map: dict[str, str],
    fundamentals: pd.DataFrame | None = None,
    research: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    prices: long frame [symbol, date, open, high, low, close, volume]
    fundamentals: frame [symbol, pe, pb, roe, gross_margin, debt_to_equity, earnings_growth] or None
    research: frame [symbol, news_sentiment_score, analyst_score, ...] (data/finnhub_data.py
        get_research_frame) or None
    sector_map: {symbol: sector}

    Returns a DataFrame indexed by symbol with columns:
    value, quality, momentum, low_vol, research, combined_score, rank, sector
    (value/quality are all-zero when fundamentals is None; research is
    all-zero when research is None - see lookahead-bias note above)
    """
    momentum = compute_momentum_factor(prices, sector_map)
    low_vol = compute_low_vol_factor(prices, sector_map)

    weights = {"momentum": STRATEGY.weight_momentum, "low_vol": STRATEGY.weight_low_vol}
    factor_series = {"momentum": momentum, "low_vol": low_vol}

    if fundamentals is not None and not fundamentals.empty:
        factor_series["value"] = compute_value_factor(fundamentals, sector_map)
        factor_series["quality"] = compute_quality_factor(fundamentals, sector_map)
        weights["value"] = STRATEGY.weight_value
        weights["quality"] = STRATEGY.weight_quality
    else:
        factor_series["value"] = pd.Series(0.0, index=momentum.index)
        factor_series["quality"] = pd.Series(0.0, index=momentum.index)

    if research is not None and not research.empty:
        factor_series["research"] = compute_research_factor(research, sector_map)
        weights["research"] = STRATEGY.weight_research
    else:
        factor_series["research"] = pd.Series(0.0, index=momentum.index)

    weight_total = sum(weights.values())
    if weight_total <= 0:
        raise ValueError("Active factor weights sum to zero - check config.StrategyConfig")
    weights = {k: v / weight_total for k, v in weights.items()}

    out = pd.concat(factor_series, axis=1).fillna(0.0)
    out["combined_score"] = sum(
        weights.get(name, 0.0) * out[name] for name in ("value", "quality", "momentum", "low_vol", "research")
    )
    out["sector"] = out.index.map(sector_map)
    out.index.name = "symbol"
    out = out.sort_values("combined_score", ascending=False)
    out["rank"] = range(1, len(out) + 1)
    return out
