"""
Generic scoring utilities shared by every factor: winsorization, z-scoring,
and sector-neutralization.

Sector-neutralization matters because raw factor values differ by sector
for structural reasons (utilities trade at low P/E for regulatory reasons,
not because they're "cheap" in a stock-picking sense) - comparing a stock
only to its own sector peers removes that bias.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def winsorize(series: pd.Series, lower_pct: float = 0.01, upper_pct: float = 0.99) -> pd.Series:
    """Clip extreme values so a handful of outliers can't dominate a z-score."""
    if series.dropna().empty:
        return series
    lo, hi = series.quantile([lower_pct, upper_pct])
    return series.clip(lower=lo, upper=hi)


def zscore(series: pd.Series) -> pd.Series:
    mean, std = series.mean(), series.std()
    if std is None or std == 0 or np.isnan(std):
        return pd.Series(0.0, index=series.index)
    return (series - mean) / std


def sector_neutral_zscore(df: pd.DataFrame, value_col: str, sector_col: str = "sector") -> pd.Series:
    """
    Winsorize + z-score within each sector group, so a stock is only ranked
    against same-sector peers. Sectors with too few names (<3) fall back to
    the whole-universe z-score to avoid a noisy per-sector mean/std.
    """
    # Missing values (e.g. a stock FMP's plan doesn't cover) arrive as None,
    # which makes the column object-dtype; newer pandas then refuses to write
    # the float z-scores back. Score on a numeric copy.
    df = df.assign(**{value_col: pd.to_numeric(df[value_col], errors="coerce")})
    out = pd.Series(index=df.index, dtype=float)
    for sector, group in df.groupby(sector_col):
        if len(group) < 3:
            continue
        w = winsorize(group[value_col])
        out.loc[group.index] = zscore(w)
    missing = out.isna()
    if missing.any():
        w_all = winsorize(df.loc[missing, value_col])
        out.loc[missing] = zscore(w_all)
    return out.fillna(0.0)
