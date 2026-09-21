#!/usr/bin/env python3
"""
Runs the full backtest pipeline against SYNTHETIC price data (no network
calls) so anyone can validate the wiring - factors -> portfolio
construction -> risk sizing -> simulated execution -> tearsheet - before
ever touching a real API key. Swap in data.alpaca_data.get_daily_bars() and
data.universe.get_sp500_constituents() for real data once your keys are set.

Usage: python scripts/demo_backtest.py
Output: demo_tearsheet.html in the project root.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow running as `python scripts/demo_backtest.py`

import numpy as np
import pandas as pd

from backtest.engine import run_backtest
from backtest.metrics import compute_stats, render_tearsheet_html, significance_verdict


def make_synthetic_universe(n_symbols: int = 60, n_days: int = 750, seed: int = 11):
    rng = np.random.default_rng(seed)
    sectors = ["Technology", "Financials", "Energy", "Healthcare", "Industrials", "Utilities", "Consumer"]
    symbols = [f"SYN{i:03d}" for i in range(n_symbols)]
    sector_map = {s: sectors[i % len(sectors)] for i, s in enumerate(symbols)}

    dates = pd.bdate_range("2022-01-03", periods=n_days)
    market_factor = rng.normal(0.00035, 0.009, n_days)  # a shared market-wide return series

    rows = []
    betas = {}
    for i, sym in enumerate(symbols):
        beta = 0.6 + 1.4 * rng.random()
        betas[sym] = beta
        idio_drift = rng.normal(0.0, 0.0004)
        idio_vol = 0.006 + 0.01 * rng.random()
        idio = rng.normal(idio_drift, idio_vol, n_days)
        returns = beta * market_factor + idio
        prices = 50 * np.cumprod(1 + returns)
        for d, p in zip(dates, prices):
            rows.append({"symbol": sym, "date": d, "open": p, "high": p * 1.005, "low": p * 0.995, "close": p, "volume": 500_000})

    prices = pd.DataFrame(rows)
    benchmark = pd.Series(50 * np.cumprod(1 + market_factor), index=dates)
    return prices, sector_map, benchmark


def main():
    print("Generating synthetic universe (60 symbols, ~3 years of daily bars)...")
    prices, sector_map, benchmark = make_synthetic_universe()

    print("Running walk-forward backtest (monthly rebalance, no-lookahead price-only factors)...")
    result = run_backtest(prices, sector_map, benchmark, starting_cash=100_000)

    stats = compute_stats(result.equity_curve, result.benchmark_curve)
    print(f"\nTotal return:        strategy {stats.total_return:+.1%}  |  benchmark {stats.benchmark_total_return:+.1%}")
    print(f"Annualized return:   strategy {stats.annualized_return:+.1%}  |  benchmark {stats.benchmark_annualized_return:+.1%}")
    print(f"Annualized vol:      {stats.annualized_vol:.1%}")
    print(f"Sharpe:              {stats.sharpe:.2f}")
    print(f"Max drawdown:        {stats.max_drawdown:.1%}")
    print(f"Alpha (annualized):  {stats.alpha_annualized:+.1%}   Beta: {stats.beta:.2f}")
    print(f"Rebalances executed: {len(result.rebalance_log)}")
    print(f"\nSignificance verdict: {significance_verdict(stats)}")

    html = render_tearsheet_html(stats, result.equity_curve, result.benchmark_curve, title="sp500_agent - synthetic demo backtest")
    out_path = "demo_tearsheet.html"
    with open(out_path, "w") as f:
        f.write(html)
    print(f"\nTearsheet written to {out_path}")


if __name__ == "__main__":
    main()
