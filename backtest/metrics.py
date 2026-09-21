"""
Performance metrics and an honest, self-critical HTML tearsheet.

Deliberately mirrors the design principle from the prior build: the
tearsheet states plainly when a result is NOT statistically distinguishable
from noise, and prints a survivorship-bias warning every time (this build's
universe is sourced from Wikipedia's *current* S&P 500 list - see
data/universe.py docstring).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PerformanceStats:
    total_return: float
    annualized_return: float
    annualized_vol: float
    sharpe: float
    max_drawdown: float
    benchmark_total_return: float
    benchmark_annualized_return: float
    alpha_annualized: float
    beta: float
    t_stat_alpha: float
    n_days: int


def compute_stats(equity_curve: pd.Series, benchmark_curve: pd.Series, risk_free_rate: float = 0.0) -> PerformanceStats:
    equity_curve = equity_curve.dropna()
    benchmark_curve = benchmark_curve.reindex(equity_curve.index).ffill().dropna()
    common_idx = equity_curve.index.intersection(benchmark_curve.index)
    equity_curve = equity_curve.loc[common_idx]
    benchmark_curve = benchmark_curve.loc[common_idx]

    strat_returns = equity_curve.pct_change().dropna()
    bench_returns = benchmark_curve.pct_change().dropna()
    n_days = len(strat_returns)

    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0] - 1.0
    years = max(n_days / 252.0, 1e-6)
    annualized_return = (1 + total_return) ** (1 / years) - 1
    annualized_vol = strat_returns.std() * np.sqrt(252)
    sharpe = (annualized_return - risk_free_rate) / annualized_vol if annualized_vol > 0 else 0.0

    running_max = equity_curve.cummax()
    drawdown = equity_curve / running_max - 1.0
    max_drawdown = drawdown.min()

    bench_total_return = benchmark_curve.iloc[-1] / benchmark_curve.iloc[0] - 1.0
    bench_annualized = (1 + bench_total_return) ** (1 / years) - 1

    aligned = pd.concat({"strat": strat_returns, "bench": bench_returns}, axis=1).dropna()
    if len(aligned) > 10 and aligned["bench"].var() > 0:
        cov = aligned["strat"].cov(aligned["bench"])
        beta = cov / aligned["bench"].var()
        alpha_daily = aligned["strat"].mean() - beta * aligned["bench"].mean()
        alpha_annualized = alpha_daily * 252
        residuals = aligned["strat"] - (beta * aligned["bench"] + alpha_daily)
        se = residuals.std() / np.sqrt(len(aligned))
        t_stat_alpha = alpha_daily / se if se > 0 else 0.0
    else:
        beta, alpha_annualized, t_stat_alpha = 0.0, 0.0, 0.0

    return PerformanceStats(
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_vol=annualized_vol,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        benchmark_total_return=bench_total_return,
        benchmark_annualized_return=bench_annualized,
        alpha_annualized=alpha_annualized,
        beta=beta,
        t_stat_alpha=t_stat_alpha,
        n_days=n_days,
    )


def significance_verdict(stats: PerformanceStats) -> str:
    if stats.n_days < 60:
        return "Too little data to say anything meaningful (need months, ideally years, of history)."
    if abs(stats.t_stat_alpha) < 2.0:
        return (
            f"t-stat on alpha is {stats.t_stat_alpha:.2f} - NOT statistically distinguishable from zero. "
            "Any outperformance shown here could plausibly be luck, not skill."
        )
    direction = "outperformance" if stats.t_stat_alpha > 0 else "underperformance"
    return f"t-stat on alpha is {stats.t_stat_alpha:.2f} - {direction} is statistically significant at ~95% confidence."


def render_tearsheet_html(
    stats: PerformanceStats,
    equity_curve: pd.Series,
    benchmark_curve: pd.Series,
    title: str = "sp500_agent backtest tearsheet",
) -> str:
    common_idx = equity_curve.index.intersection(benchmark_curve.index)
    eq = (equity_curve.loc[common_idx] / equity_curve.loc[common_idx].iloc[0] * 100).round(2)
    bm = (benchmark_curve.loc[common_idx] / benchmark_curve.loc[common_idx].iloc[0] * 100).round(2)

    dates = [d.strftime("%Y-%m-%d") for d in common_idx]
    verdict = significance_verdict(stats)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; background:#0b0e14; color:#e6e6e6; }}
.card {{ background:#151a24; border-radius:10px; padding:1.25rem 1.5rem; margin-bottom:1rem; }}
.grid {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:1rem; }}
.stat-label {{ font-size:0.8rem; color:#9aa4b2; }}
.stat-value {{ font-size:1.4rem; font-weight:600; }}
.warn {{ background:#3a2a10; border-left:4px solid #e0a030; padding:0.75rem 1rem; border-radius:6px; }}
.verdict {{ background:#1a2a3a; border-left:4px solid #4a90e2; padding:0.75rem 1rem; border-radius:6px; }}
h1 {{ font-size:1.4rem; }}
canvas {{ max-height: 360px; }}
</style></head>
<body>
<h1>{title}</h1>
<div class="card warn">
  <strong>Survivorship-bias warning:</strong> the universe used in this backtest is the
  <em>current</em> S&amp;P 500 constituent list, applied historically. Companies removed from
  the index over the backtest period (often the worst performers) are invisible to this
  test, which biases results upward. Treat these numbers as an optimistic upper bound.
</div>
<div class="card verdict"><strong>Statistical significance:</strong> {verdict}</div>
<div class="card grid">
  <div><div class="stat-label">Total return</div><div class="stat-value">{stats.total_return:.1%}</div></div>
  <div><div class="stat-label">Annualized return</div><div class="stat-value">{stats.annualized_return:.1%}</div></div>
  <div><div class="stat-label">Annualized vol</div><div class="stat-value">{stats.annualized_vol:.1%}</div></div>
  <div><div class="stat-label">Sharpe</div><div class="stat-value">{stats.sharpe:.2f}</div></div>
  <div><div class="stat-label">Max drawdown</div><div class="stat-value">{stats.max_drawdown:.1%}</div></div>
  <div><div class="stat-label">Benchmark total return</div><div class="stat-value">{stats.benchmark_total_return:.1%}</div></div>
  <div><div class="stat-label">Alpha (annualized)</div><div class="stat-value">{stats.alpha_annualized:.1%}</div></div>
  <div><div class="stat-label">Beta</div><div class="stat-value">{stats.beta:.2f}</div></div>
</div>
<div class="card"><canvas id="chart"></canvas></div>
<script>
const ctx = document.getElementById('chart');
new Chart(ctx, {{
  type: 'line',
  data: {{
    labels: {dates},
    datasets: [
      {{ label: 'Strategy', data: {eq.tolist()}, borderColor: '#4ade80', borderWidth: 2, pointRadius: 0 }},
      {{ label: 'S&P 500', data: {bm.tolist()}, borderColor: '#9aa4b2', borderWidth: 2, pointRadius: 0 }}
    ]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ ticks: {{ color: '#9aa4b2', maxTicksLimit: 10 }}, grid: {{ color: '#232838' }} }},
      y: {{ ticks: {{ color: '#9aa4b2' }}, grid: {{ color: '#232838' }}, title: {{ display:true, text:'Growth of 100', color:'#9aa4b2' }} }}
    }},
    plugins: {{ legend: {{ labels: {{ color: '#e6e6e6' }} }} }}
  }}
}});
</script>
<p style="color:#6b7280; font-size:0.8rem; margin-top:2rem;">
Educational software. Not investment advice. Past backtested performance, especially with
survivorship bias present, does not predict future results.
</p>
</body></html>"""
