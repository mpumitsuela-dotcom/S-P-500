#!/usr/bin/env python3
"""
Backtest the agent's settings on real Alpaca history and compare variants.

    python scripts/run_backtest.py --years 6 --freq W

Downloads daily bars for today's S&P 500 constituents plus SPY once, then runs
each variant (a set of SP500_* environment overrides) in its own process so the
frozen STRATEGY config really changes. Writes results/backtest/summary.md,
results.json and one tearsheet per variant.

What this CAN tell you: how the price-based part of the strategy (momentum and
low volatility, re-weighted to sum to 1) behaves across several market regimes,
and how much each variant trades.
What it CANNOT tell you (stated plainly in the output as well):
  - value, quality and research/news factors are not tested: free data has no
    point-in-time history for them;
  - survivorship bias: today's constituents only, so past losers that left the
    index are missing and every variant looks better than it was;
  - the stop-loss, the drawdown halt and intraday behaviour are not simulated;
  - rebalancing is weekly or monthly, not the live agent's daily checks.
A backtest is evidence about the design, never a promise about future returns.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "results" / "backtest"

# Price-only factors: momentum and low-vol are renormalised to sum to 1, so the
# variants differ in the momentum / low-vol mix, number of names, caps and band.
VARIANTS: dict[str, dict[str, str]] = {
    "A_old_live_30pos": {"SP500_NUM_POSITIONS": "30", "SP500_MAX_POSITION_WEIGHT": "0.06",
                         "SP500_WEIGHT_MOMENTUM": "0.25", "SP500_WEIGHT_LOW_VOL": "0.15", "SP500_RANK_BUFFER": "0"},
    "B_higher_risk_20pos": {"SP500_NUM_POSITIONS": "20", "SP500_MAX_POSITION_WEIGHT": "0.10",
                            "SP500_WEIGHT_MOMENTUM": "0.35", "SP500_WEIGHT_LOW_VOL": "0.05", "SP500_RANK_BUFFER": "0"},
    "C_higher_risk_plus_band": {"SP500_NUM_POSITIONS": "20", "SP500_MAX_POSITION_WEIGHT": "0.10",
                                "SP500_WEIGHT_MOMENTUM": "0.35", "SP500_WEIGHT_LOW_VOL": "0.05", "SP500_RANK_BUFFER": "10"},
    "D_momentum_only_20pos_band": {"SP500_NUM_POSITIONS": "20", "SP500_MAX_POSITION_WEIGHT": "0.10",
                                   "SP500_WEIGHT_MOMENTUM": "0.35", "SP500_WEIGHT_LOW_VOL": "0.0", "SP500_RANK_BUFFER": "10"},
    "E_defensive_30pos": {"SP500_NUM_POSITIONS": "30", "SP500_MAX_POSITION_WEIGHT": "0.06",
                          "SP500_WEIGHT_MOMENTUM": "0.10", "SP500_WEIGHT_LOW_VOL": "0.30", "SP500_RANK_BUFFER": "0"},
}


def load_or_fetch(years: int, cache: Path):
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    from data.alpaca_data import get_daily_bars
    from data.universe import get_sector_map

    sector_map = get_sector_map()
    symbols = sorted(sector_map)
    end = date.today()
    start = end - timedelta(days=int(years * 365.25) + 5)
    print(f"Downloading {len(symbols)} symbols, {start} -> {end} ...", flush=True)
    prices = get_daily_bars(symbols, start, end)
    spy = get_daily_bars(["SPY"], start, end).set_index("date")["close"]
    data = {"prices": prices, "sector_map": sector_map, "benchmark": spy}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(data))
    return data


def run_one(name: str, data_path: Path, freq: str, max_turnover: float) -> None:
    """Runs inside a subprocess whose environment already holds the variant's overrides."""
    from backtest.engine import run_backtest
    from backtest.metrics import compute_stats, render_tearsheet_html, significance_verdict

    data = pickle.loads(data_path.read_bytes())
    result = run_backtest(data["prices"], data["sector_map"], data["benchmark"], starting_cash=100_000,
                          freq=freq, max_turnover=max_turnover)
    stats = compute_stats(result.equity_curve, result.benchmark_curve)
    years = max(stats.n_days / 252.0, 1e-6)
    traded = sum(r.get("traded_dollars", 0.0) for r in result.rebalance_log)
    avg_equity = float(result.equity_curve.mean())
    eq = result.equity_curve
    yearly = {}
    for y, grp in eq.groupby(eq.index.year):
        bm = result.benchmark_curve.reindex(grp.index).ffill()
        yearly[str(y)] = {"strategy": float(grp.iloc[-1] / grp.iloc[0] - 1), "spy": float(bm.iloc[-1] / bm.iloc[0] - 1)}
    out = {
        "variant": name, "from": str(eq.index[0].date()), "to": str(eq.index[-1].date()), "days": stats.n_days,
        "total_return": stats.total_return, "spy_total_return": stats.benchmark_total_return,
        "cagr": stats.annualized_return, "spy_cagr": stats.benchmark_annualized_return,
        "vol": stats.annualized_vol, "sharpe": stats.sharpe, "max_drawdown": stats.max_drawdown,
        "alpha": stats.alpha_annualized, "beta": stats.beta, "t_alpha": stats.t_stat_alpha,
        "annual_turnover": traded / avg_equity / years if avg_equity else 0.0,
        "rebalances": len(result.rebalance_log), "by_year": yearly,
        "verdict": significance_verdict(stats),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{name}.json").write_text(json.dumps(out, indent=2))
    (OUT_DIR / f"{name}_tearsheet.html").write_text(
        render_tearsheet_html(stats, result.equity_curve, result.benchmark_curve, title=f"sp500_agent backtest - {name}"))
    print(f"{name}: total {stats.total_return:+.1%} vs SPY {stats.benchmark_total_return:+.1%}", flush=True)


def summarize() -> str:
    rows = [json.loads(p.read_text()) for p in sorted(OUT_DIR.glob("*.json")) if p.name != "results.json"]
    if not rows:
        return "No results."
    lines = [
        f"# Backtest summary ({rows[0]['from']} to {rows[0]['to']}, {rows[0]['days']} trading days)",
        "",
        "| Variant | Total return | SPY | CAGR | Vol | Sharpe | Max drawdown | Beta | Alpha/yr | t(alpha) | Turnover/yr |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['variant']} | {r['total_return']:+.1%} | {r['spy_total_return']:+.1%} | {r['cagr']:+.1%} | {r['vol']:.1%} | "
            f"{r['sharpe']:.2f} | {r['max_drawdown']:.1%} | {r['beta']:.2f} | {r['alpha']:+.1%} | {r['t_alpha']:.2f} | {r['annual_turnover']:.0%} |")
    years = sorted({y for r in rows for y in r["by_year"]})
    lines += ["", "## Return by calendar year (strategy / SPY)", "", "| Variant | " + " | ".join(years) + " |",
              "|---|" + "---|" * len(years)]
    for r in rows:
        cells = [f"{r['by_year'][y]['strategy']:+.1%} / {r['by_year'][y]['spy']:+.1%}" if y in r["by_year"] else "-" for y in years]
        lines.append(f"| {r['variant']} | " + " | ".join(cells) + " |")
    lines += [
        "", "## Read this before trusting any number", "",
        "- Only the price factors (momentum, low volatility) are tested. Value, quality and news/analyst research are NOT: free data has no history for them.",
        "- Survivorship bias: today's S&P 500 members only. Companies that dropped out are missing, so every variant looks better than it really was.",
        "- No stop-loss, no drawdown halt, no intraday behaviour; weekly or monthly rebalancing instead of the live agent's daily checks.",
        "- t(alpha) below about 2 means the outperformance is not distinguishable from luck.",
        "- A backtest is evidence about the design, not a promise about future returns.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=6)
    ap.add_argument("--freq", choices=["W", "M"], default="W")
    ap.add_argument("--max-turnover", type=float, default=1.0, help="per-rebalance cap (1.0 = full rebalance)")
    ap.add_argument("--variant", help="(internal) run one variant in this process")
    ap.add_argument("--data", default=str(ROOT / ".cache" / "backtest_data.pkl"))
    args = ap.parse_args()
    data_path = Path(args.data)

    if args.variant:
        run_one(args.variant, data_path, args.freq, args.max_turnover)
        return 0

    for old in OUT_DIR.glob("*") if OUT_DIR.exists() else []:
        old.unlink()
    load_or_fetch(args.years, data_path)
    failed = []
    for name, overrides in VARIANTS.items():
        env = {**os.environ, **overrides, "SP500_TARGET_VOL": os.environ.get("SP500_TARGET_VOL", "0.20")}
        cmd = [sys.executable, __file__, "--variant", name, "--freq", args.freq, "--max-turnover", str(args.max_turnover),
               "--data", str(data_path)]
        if subprocess.run(cmd, env=env).returncode != 0:
            failed.append(name)
    summary = summarize()
    (OUT_DIR / "summary.md").write_text(summary)
    print(summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
