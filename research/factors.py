"""
Factor definitions: value, quality, momentum, low-volatility.

Each factor function returns a sector-neutral z-score Series indexed by
symbol, where HIGHER is always better (i.e. "cheap", "high-quality",
"strong-momentum", "low-volatility" all map to positive scores) so they
can be combined with simple weighted addition in portfolio/construction.py.

This is a long-only, unlevered, monthly-rebalance-in-spirit multi-factor
model (see config.StrategyConfig and scheduler/ for how the twice-daily
cadence is layered on top without forcing needless turnover). It follows a
well-documented academic style (Fama-French/AQR-style factor investing),
not a proprietary edge - realistic expectations are index-like returns
with a modest, uncertain premium, not guaranteed outperformance. See
README.md "Honest expectations" section.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.scoring import sector_neutral_zscore


def _combine_legs(legs: list[pd.Series], all_symbols: pd.Index) -> pd.Series:
    if not legs:
        return pd.Series(0.0, index=all_symbols)
    combined = pd.concat(legs, axis=1).mean(axis=1, skipna=True)
    return combined.reindex(all_symbols).fillna(0.0)


def compute_value_factor(fundamentals: pd.DataFrame, sector_map: dict[str, str]) -> pd.Series:
    """Cheaper (lower P/E, lower P/B) = higher score. Missing/negative P/E or P/B excluded from that leg."""
    df = fundamentals.copy()
    df["sector"] = df["symbol"].map(sector_map)
    df = df.dropna(subset=["sector"]).set_index("symbol")

    df["inv_pe"] = np.where((df["pe"].notna()) & (df["pe"] > 0), 1.0 / df["pe"], np.nan)
    df["inv_pb"] = np.where((df["pb"].notna()) & (df["pb"] > 0), 1.0 / df["pb"], np.nan)

    legs = []
    if df["inv_pe"].notna().any():
        legs.append(sector_neutral_zscore(df.dropna(subset=["inv_pe"]), "inv_pe"))
    if df["inv_pb"].notna().any():
        legs.append(sector_neutral_zscore(df.dropna(subset=["inv_pb"]), "inv_pb"))

    return _combine_legs(legs, pd.Index(fundamentals["symbol"].unique()))


def compute_quality_factor(fundamentals: pd.DataFrame, sector_map: dict[str, str]) -> pd.Series:
    """Higher ROE, higher gross margin, lower debt/equity = higher score."""
    df = fundamentals.copy()
    df["sector"] = df["symbol"].map(sector_map)
    df = df.dropna(subset=["sector"]).set_index("symbol")

    legs = []
    if df["roe"].notna().any():
        legs.append(sector_neutral_zscore(df.dropna(subset=["roe"]), "roe"))
    if df["gross_margin"].notna().any():
        legs.append(sector_neutral_zscore(df.dropna(subset=["gross_margin"]), "gross_margin"))
    if "debt_to_equity" in df.columns and df["debt_to_equity"].notna().any():
        d = df.dropna(subset=["debt_to_equity"]).copy()
        d["inv_debt_to_equity"] = -d["debt_to_equity"]  # lower leverage is better quality
        legs.append(sector_neutral_zscore(d, "inv_debt_to_equity"))

    return _combine_legs(legs, pd.Index(fundamentals["symbol"].unique()))


def compute_momentum_factor(
    prices: pd.DataFrame, sector_map: dict[str, str], lookback_days: int = 252, skip_recent_days: int = 21
) -> pd.Series:
    """
    Classic 12-1 month momentum: total return over the lookback window,
    EXCLUDING the most recent month (short-term reversal effect - stocks
    that just spiked tend to partially mean-revert over the next few weeks).
    """
    pivot = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    effective_lookback = lookback_days
    if len(pivot) < lookback_days + skip_recent_days + 1:
        effective_lookback = max(len(pivot) - skip_recent_days - 1, 20)

    end_idx = max(len(pivot) - skip_recent_days - 1, 0)
    start_idx = max(end_idx - effective_lookback, 0)
    end_row = pivot.iloc[end_idx]
    start_row = pivot.iloc[start_idx]

    total_return = (end_row / start_row) - 1.0
    total_return = total_return.replace([np.inf, -np.inf], np.nan)

    df = total_return.rename("momentum_raw").to_frame()
    df.index.name = "symbol"
    df["sector"] = df.index.map(sector_map)
    df = df.dropna(subset=["sector", "momentum_raw"])

    legs = [sector_neutral_zscore(df, "momentum_raw")] if not df.empty else []
    return _combine_legs(legs, total_return.index)


def compute_research_factor(research: pd.DataFrame, sector_map: dict[str, str]) -> pd.Series:
    """
    "What does the latest research say": combines a news-sentiment leg
    (data/finnhub_data.py get_research_frame news_sentiment_score) with an
    analyst-consensus leg (analyst_score, -2..+2, None/excluded when a
    symbol has no analyst coverage). Both legs are sector-neutral z-scored
    like every other factor, so this plugs into
    research/signals.py compute_combined_scores exactly like value/quality.

    A symbol with no news this cycle and no analyst coverage gets a neutral
    0.0 here rather than being penalized - "no research available" is not
    the same signal as "research says sell".
    """
    df = research.copy()
    df["sector"] = df["symbol"].map(sector_map)
    df = df.dropna(subset=["sector"]).set_index("symbol")

    legs = []
    if "news_sentiment_score" in df.columns and df["news_sentiment_score"].notna().any():
        legs.append(sector_neutral_zscore(df.dropna(subset=["news_sentiment_score"]), "news_sentiment_score"))
    if "analyst_score" in df.columns and df["analyst_score"].notna().any():
        legs.append(sector_neutral_zscore(df.dropna(subset=["analyst_score"]), "analyst_score"))

    return _combine_legs(legs, pd.Index(research["symbol"].unique()))


def compute_low_vol_factor(prices: pd.DataFrame, sector_map: dict[str, str], lookback_days: int = 126) -> pd.Series:
    """Lower realized daily-return volatility over the lookback window = higher score."""
    pivot = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    window = pivot.tail(lookback_days)
    returns = window.pct_change().dropna(how="all")
    vol = returns.std()
    inv_vol = 1.0 / vol.replace(0, np.nan)
    inv_vol = inv_vol.replace([np.inf, -np.inf], np.nan)

    df = inv_vol.rename("inv_vol").to_frame()
    df.index.name = "symbol"
    df["sector"] = df.index.map(sector_map)
    df = df.dropna(subset=["sector", "inv_vol"])

    legs = [sector_neutral_zscore(df, "inv_vol")] if not df.empty else []
    return _combine_legs(legs, inv_vol.index)
