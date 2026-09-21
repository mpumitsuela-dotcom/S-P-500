"""
Walk-forward backtest engine.

No-lookahead enforcement: at each rebalance date T, the factor engine only
ever sees price rows with date <= T (enforced by slicing prices before
calling compute_combined_scores - see _prices_as_of). Fundamentals are
deliberately excluded from the backtest (see research/signals.py docstring)
because free-tier point-in-time fundamentals aren't available.

Rebalance cadence: monthly by default (SP500_REBALANCE_FREQ env var can
change this), which is standard for a factor strategy - the twice-daily
cadence the user asked for is a LIVE-TRADING operational cadence (checking
guards / reacting to news twice a day), not a backtestable "alpha refresh
twice a day" claim, because there's no reason to think re-scoring value/
momentum/quality factors intraday produces new information. This
distinction is called out explicitly in the guide.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import STRATEGY
from execution.paper_broker import SimulationBroker
from execution.rebalancer import compute_rebalance_orders, execute_orders
from portfolio.construction import build_target_portfolio
from portfolio.risk import apply_vol_target, estimate_portfolio_volatility
from research.signals import compute_combined_scores

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    benchmark_curve: pd.Series
    rebalance_log: list[dict]


def _prices_as_of(prices: pd.DataFrame, as_of: pd.Timestamp, min_history_days: int = 260) -> pd.DataFrame:
    window = prices[prices["date"] <= as_of]
    return window


def _month_end_dates(all_dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(all_dates, index=all_dates)
    return list(s.groupby([all_dates.year, all_dates.month]).max())


def run_backtest(
    prices: pd.DataFrame,
    sector_map: dict[str, str],
    benchmark_prices: pd.Series,
    starting_cash: float = 100_000.0,
    warmup_days: int = 260,
) -> BacktestResult:
    """
    prices: long frame [symbol, date, open, high, low, close, volume] for the whole backtest period
    benchmark_prices: Series indexed by date, e.g. SPY close, for comparison
    """
    all_dates = pd.DatetimeIndex(sorted(prices["date"].unique()))
    if len(all_dates) <= warmup_days:
        raise ValueError(f"Not enough price history ({len(all_dates)} days) for a {warmup_days}-day warmup")

    tradeable_dates = all_dates[warmup_days:]
    rebalance_dates = [d for d in _month_end_dates(all_dates) if d in set(tradeable_dates)]

    broker = SimulationBroker(starting_cash)
    equity_points: dict[pd.Timestamp, float] = {}
    rebalance_log: list[dict] = []

    daily_close = prices.pivot(index="date", columns="symbol", values="close").sort_index()

    for current_date in tradeable_dates:
        day_prices = daily_close.loc[current_date].dropna()
        broker.mark_to_market(day_prices.to_dict())

        if current_date in rebalance_dates:
            hist = _prices_as_of(prices, current_date)
            scores = compute_combined_scores(hist, sector_map, fundamentals=None)
            scores = scores[scores.index.isin(day_prices.index)]  # only names with a tradable price today
            target = build_target_portfolio(scores, sector_map)

            weights_series = target.set_index("symbol")["target_weight"]
            realized_vol = estimate_portfolio_volatility(hist, weights_series)
            scaled_weights = apply_vol_target(weights_series, realized_vol)
            target = target.assign(target_weight=target["symbol"].map(scaled_weights))

            equity = broker.get_account_equity()
            orders = compute_rebalance_orders(target, broker.get_positions(), equity, day_prices)
            results = execute_orders(broker, orders, latest_prices=day_prices)
            rebalance_log.append({"date": str(current_date.date()), "orders": results, "realized_vol": realized_vol})
            logger.info("Rebalanced on %s: %d orders", current_date.date(), len(orders))

        equity_points[current_date] = broker.get_account_equity()

    equity_curve = pd.Series(equity_points).sort_index()
    benchmark_curve = benchmark_prices.reindex(equity_curve.index).ffill()
    return BacktestResult(equity_curve=equity_curve, benchmark_curve=benchmark_curve, rebalance_log=rebalance_log)
