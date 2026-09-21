"""
Risk sizing utilities: converts target *weights* (fractions of equity)
into target *dollar amounts* and share counts, applying a simple
volatility-targeting overlay so the whole book scales down in shaky
markets rather than always being 98% invested regardless of conditions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import STRATEGY


def estimate_portfolio_volatility(prices: pd.DataFrame, weights: pd.Series, lookback_days: int = 63) -> float:
    """Annualized volatility of the weighted basket over the trailing window."""
    pivot = prices.pivot(index="date", columns="symbol", values="close").sort_index().tail(lookback_days)
    returns = pivot.pct_change().dropna(how="all")
    aligned_weights = weights.reindex(returns.columns).fillna(0.0)
    portfolio_returns = (returns * aligned_weights).sum(axis=1)
    daily_vol = portfolio_returns.std()
    return float(daily_vol * np.sqrt(252)) if pd.notna(daily_vol) else STRATEGY.target_annual_vol


def apply_vol_target(weights: pd.Series, realized_annual_vol: float) -> pd.Series:
    """
    Scale the whole book up/down (never above 1.0x, i.e. never adds
    leverage) so realized volatility tracks the target. If realized vol
    is already at/below target, weights are left as-is (or floor-scaled
    up to a max of 1.0x, meaning "don't add leverage").
    """
    if realized_annual_vol <= 0:
        return weights
    scale = min(STRATEGY.target_annual_vol / realized_annual_vol, 1.0)
    scale = max(scale, 0.25)  # never scale below 25% invested purely on vol-targeting grounds
    return weights * scale


def weights_to_target_shares(
    target_weights: pd.DataFrame, account_equity: float, latest_prices: pd.Series
) -> pd.DataFrame:
    """
    target_weights: [symbol, target_weight]
    Returns [symbol, target_weight, target_dollars, target_shares] with
    shares floored to whole numbers (Alpaca paper supports fractional
    shares too, but whole shares keep the guard/reconciliation logic simple).
    """
    df = target_weights.copy()
    df["target_dollars"] = df["target_weight"] * account_equity
    df["price"] = df["symbol"].map(latest_prices)
    df = df.dropna(subset=["price"])
    df["target_shares"] = np.floor(df["target_dollars"] / df["price"]).astype(int)
    return df
