"""
Charts and position statistics for the daily and 5-day reports, laid out like
the Alpaca dashboard:

  1. Account value since the trial started, against the same money in the
     S&P 500 (SPY) - one dollar axis, so the two lines compare directly.
  2. Where the money is: each holding's share of the whole account, plus cash.
  3. Gain or loss on each holding since it was bought.

plus a statistics table (shares, average cost, price, value, % of account,
today's move, unrealised gain/loss).

Charts are PNGs saved under reports/charts/ (shared state on the agent-state
branch) and linked from the report markdown. Colours follow the dataviz
reference palette: one blue series for magnitude, blue/red for gain/loss
(always with a +/- label, never colour alone), grey for cash and the
benchmark's neutral role. Everything here is best-effort: a chart failure
never stops the report.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from execution.broker_base import format_qty

logger = logging.getLogger("sp500_agent.reports")

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e6e5e0"
BLUE = "#2a78d6"      # categorical slot 1 / sequential blue
ORANGE = "#eb6834"    # categorical slot 2 (the benchmark line)
RED = "#e34948"       # diverging negative pole
CASH = "#b9b8b2"      # neutral


def position_rows(positions: dict, equity: float) -> list[dict]:
    rows = []
    for sym, p in positions.items():
        value = p.qty * p.current_price
        cost = p.qty * p.avg_entry_price
        rows.append({
            "symbol": sym,
            "qty": p.qty,
            "avg_cost": p.avg_entry_price,
            "price": p.current_price,
            "value": value,
            "weight": value / equity if equity else None,
            "today": (p.current_price / p.lastday_price - 1) if getattr(p, "lastday_price", None) else None,
            "pl": value - cost,
            "pl_pct": (p.current_price / p.avg_entry_price - 1) if p.avg_entry_price else None,
        })
    return sorted(rows, key=lambda r: -r["value"])


def stats_table(rows: list[dict], equity: float) -> list[str]:
    def pct(v):
        return "–" if v is None else f"{v:+.2%}"

    invested = sum(r["value"] for r in rows)
    lines = [
        "| Stock | Shares | Avg cost | Price | Market value | % of account | Today | Gain/loss $ | Gain/loss % |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['symbol']} | {format_qty(r['qty'])} | ${r['avg_cost']:,.2f} | ${r['price']:,.2f} | ${r['value']:,.0f} | "
            f"{r['weight']:.1%} | {pct(r['today'])} | {'+' if r['pl'] >= 0 else '−'}${abs(r['pl']):,.0f} | {pct(r['pl_pct'])} |"
        )
    total_pl = sum(r["pl"] for r in rows)
    cash = equity - invested
    lines.append(
        f"| **Invested** | | | | **${invested:,.0f}** | **{invested / equity:.1%}** | | "
        f"**{'+' if total_pl >= 0 else '−'}${abs(total_pl):,.0f}** | |"
    )
    lines.append(f"| **Cash** | | | | ${cash:,.0f} | {cash / equity:.1%} | | | |")
    lines.append(f"| **Total account** | | | | **${equity:,.0f}** | 100% | | | |")
    return lines


def _style(ax, plt):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _figure(plt, height):
    fig, ax = plt.subplots(figsize=(9, height), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style(ax, plt)
    return fig, ax


def _title(ax, title, subtitle):
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold", color=INK, pad=22)
    # Offset in points, not a fraction of the axes: on tall charts a fraction
    # pushed the subtitle up into the title.
    ax.annotate(subtitle, xy=(0, 1), xycoords="axes fraction", xytext=(0, 6), textcoords="offset points",
                fontsize=9, color=INK_2, va="bottom")


def equity_chart(path: Path, points: list[tuple[date, float]], spy_points: list[tuple[date, float]]) -> None:
    """points: account value by date (trial start first). spy_points: the same
    starting money invested in SPY, by date."""
    import matplotlib.pyplot as plt

    fig, ax = _figure(plt, 3.8)
    # Trading days are evenly spaced (no gaps for weekends and holidays).
    days = sorted({d for d, _ in points} | {d for d, _ in spy_points})
    pos = {d: i for i, d in enumerate(days)}
    xs, ys = [pos[d] for d, _ in points], [v for _, v in points]
    ax.plot(xs, ys, color=BLUE, linewidth=2, marker="o", markersize=5, label="Agent's account", zorder=3)
    if spy_points:
        sx, sy = [pos[d] for d, _ in spy_points], [v for _, v in spy_points]
        ax.plot(sx, sy, color=ORANGE, linewidth=2, marker="o", markersize=5, label="Same money in the S&P 500 (SPY)", zorder=2)
    start = ys[0]
    ax.axhline(start, color=INK_2, linewidth=0.8, linestyle=(0, (3, 3)), zorder=1, label=f"Starting value ${start:,.0f}")
    # Direct labels at the line ends (value and return), text in ink, not series colour.
    ax.annotate(f"${ys[-1]:,.0f} ({ys[-1] / start - 1:+.2%})", (xs[-1], ys[-1]), xytext=(8, 0),
                textcoords="offset points", color=INK, fontsize=9, va="center")
    if spy_points:
        ax.annotate(f"${sy[-1]:,.0f} ({sy[-1] / start - 1:+.2%})", (sx[-1], sy[-1]), xytext=(8, 0),
                    textcoords="offset points", color=INK_2, fontsize=9, va="center")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_xticks(range(len(days)))
    ax.set_xticklabels([f"{d:%a %d %b}" if i else "Start" for i, d in enumerate(days)])
    ax.margins(x=0.12, y=0.25)
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK_2)
    _title(ax, "Account value vs the S&P 500", "Since the trial started · end-of-day values")
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def allocation_chart(path: Path, rows: list[dict], equity: float) -> None:
    import matplotlib.pyplot as plt

    invested = sum(r["value"] for r in rows)
    cash = equity - invested
    items = [(r["symbol"], r["value"], BLUE) for r in rows] + [("Cash", max(cash, 0), CASH)]
    fig, ax = _figure(plt, 0.9 + 0.28 * len(items))
    labels = [i[0] for i in items][::-1]
    values = [i[1] / equity * 100 for i in items][::-1]
    colors = [i[2] for i in items][::-1]
    bars = ax.barh(labels, values, color=colors, height=0.72)
    for bar, (_, dollars, _) in zip(bars, items[::-1]):
        ax.annotate(f"{bar.get_width():.1f}%  ·  ${dollars:,.0f}", (bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(4, 0), textcoords="offset points", va="center", fontsize=8, color=INK_2)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_xlim(0, max(values) * 1.3)
    ax.grid(axis="y", visible=False)
    _title(ax, "Where the money is", f"Each holding's share of the whole account (${equity:,.0f}) · "
                                      f"invested {invested / equity:.0%}, cash {cash / equity:.0%}")
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def gain_loss_chart(path: Path, rows: list[dict]) -> None:
    import matplotlib.pyplot as plt

    rows = sorted([r for r in rows if r["pl_pct"] is not None], key=lambda r: r["pl_pct"])
    fig, ax = _figure(plt, 0.9 + 0.28 * len(rows))
    vals = [r["pl_pct"] * 100 for r in rows]
    bars = ax.barh([r["symbol"] for r in rows], vals, color=[BLUE if v >= 0 else RED for v in vals], height=0.72)
    span = max(abs(v) for v in vals) if vals else 1
    for bar, r in zip(bars, rows):
        v = bar.get_width()
        pct = "0.0%" if abs(v) < 0.05 else f"{'+' if v > 0 else '−'}{abs(v):.1f}%"
        dollars = "$0" if abs(r["pl"]) < 0.5 else f"{'+' if r['pl'] > 0 else '−'}${abs(r['pl']):,.0f}"
        ax.annotate(f"{pct}  ({dollars})", (v, bar.get_y() + bar.get_height() / 2), xytext=(4 if v >= 0 else -4, 0),
                    textcoords="offset points", va="center", ha="left" if v >= 0 else "right", fontsize=8, color=INK_2)
    ax.axvline(0, color=INK_2, linewidth=0.8)
    ax.set_xlim(-span * 1.6, span * 1.6)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: "0%" if abs(v) < 1e-9 else f"{'+' if v > 0 else '−'}{abs(v):.0f}%"))
    ax.grid(axis="y", visible=False)
    _title(ax, "Gain or loss on each holding", "Since purchase, at today's close · blue = gain (+), red = loss (−)")
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def build(day: date, charts_dir: Path, positions: dict, equity: float, equity_points: list[tuple[date, float]],
          spy_points: list[tuple[date, float]]) -> tuple[list[str], list[str]]:
    """Returns (chart markdown lines, statistics table lines). Never raises."""
    rows = position_rows(positions, equity)
    table = stats_table(rows, equity) if rows and equity else []
    chart_lines: list[str] = []
    try:
        import matplotlib

        matplotlib.use("Agg")
        charts_dir.mkdir(parents=True, exist_ok=True)
        made = []
        if len(equity_points) >= 2:
            p = charts_dir / f"{day.isoformat()}-account-vs-sp500.png"
            equity_chart(p, equity_points, spy_points)
            made.append(("Account value vs the S&P 500", p))
        if rows and equity:
            p = charts_dir / f"{day.isoformat()}-allocation.png"
            allocation_chart(p, rows, equity)
            made.append(("Where the money is", p))
            p = charts_dir / f"{day.isoformat()}-gain-loss.png"
            gain_loss_chart(p, rows)
            made.append(("Gain or loss on each holding", p))
        chart_lines = [f"![{title}](../charts/{p.name})" for title, p in made]
    except Exception:  # noqa: BLE001 - charts are a bonus; the report must still go out
        logger.warning("Could not draw report charts", exc_info=True)
    return chart_lines, table
