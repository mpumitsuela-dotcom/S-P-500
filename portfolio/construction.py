"""
Turns combined factor scores into a risk-constrained target portfolio.

Approach: pick the top N names by combined_score, weight them
proportional to (score - min_score) so higher-conviction names get more
weight, then iteratively cap any position or sector that breaches its
limit and redistribute the excess to uncapped names. This iterative
capping is the same style of hard-constraint approach used in the prior
build - it's simple, deterministic, and easy to unit test, as opposed to
a full mean-variance optimizer (which would need a covariance estimate
and a solver dependency this build deliberately avoids for transparency).
"""
from __future__ import annotations

import pandas as pd

from config import STRATEGY


def select_top_names(scores: pd.DataFrame, num_positions: int | None = None) -> pd.DataFrame:
    n = num_positions or STRATEGY.num_positions
    return scores.head(n).copy()


def _initial_weights(selected: pd.DataFrame) -> pd.Series:
    raw = selected["combined_score"] - selected["combined_score"].min() + 0.01  # keep strictly positive
    return raw / raw.sum()


def cap_and_redistribute(
    weights: pd.Series,
    sector_map: dict[str, str],
    max_position_weight: float | None = None,
    max_sector_weight: float | None = None,
    max_iterations: int = 50,
) -> pd.Series:
    """
    Iteratively enforce per-position and per-sector caps, redistributing
    any excess weight pro-rata across uncapped names, until stable or
    max_iterations is hit (guards against pathological inputs where caps
    can't all be satisfied, e.g. one sector with too few eligible names -
    in that case the loop exits and the (still-capped-at-position-level)
    result is returned rather than looping forever).
    """
    max_pos = max_position_weight if max_position_weight is not None else STRATEGY.max_position_weight
    max_sec = max_sector_weight if max_sector_weight is not None else STRATEGY.max_sector_weight

    w = weights.copy()
    sectors = pd.Series({s: sector_map.get(s) for s in w.index})

    for _ in range(max_iterations):
        changed = False

        over_position = w[w > max_pos]
        if not over_position.empty:
            excess = (over_position - max_pos).sum()
            w.loc[over_position.index] = max_pos
            uncapped = w.index.difference(over_position.index)
            if len(uncapped) > 0 and excess > 1e-9:
                w.loc[uncapped] += excess * (w.loc[uncapped] / w.loc[uncapped].sum())
            changed = True

        sector_totals = w.groupby(sectors).sum()
        over_sectors = sector_totals[sector_totals > max_sec]
        if not over_sectors.empty:
            for sector, total in over_sectors.items():
                members = sectors[sectors == sector].index
                scale = max_sec / total
                excess = (w.loc[members] * (1 - scale)).sum()
                w.loc[members] *= scale
                other = w.index.difference(members)
                if len(other) > 0 and excess > 1e-9:
                    w.loc[other] += excess * (w.loc[other] / w.loc[other].sum())
            changed = True

        if not changed:
            break

    return w / w.sum()  # renormalize for float drift


def build_target_portfolio(
    scores: pd.DataFrame, sector_map: dict[str, str], num_positions: int | None = None
) -> pd.DataFrame:
    """
    Returns a DataFrame [symbol, sector, combined_score, target_weight]
    for the selected names, respecting position/sector caps and leaving
    STRATEGY.cash_buffer uninvested.
    """
    selected = select_top_names(scores, num_positions)
    raw_weights = _initial_weights(selected)
    capped = cap_and_redistribute(raw_weights, sector_map)
    investable = 1.0 - STRATEGY.cash_buffer
    final_weights = capped * investable

    out = selected.copy()
    out.index = out.index.rename("symbol")  # scores may arrive with an unnamed or differently-named index
    out["target_weight"] = final_weights
    return out.reset_index()[["symbol", "sector", "combined_score", "target_weight"]]
