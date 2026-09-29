"""Plain technical indicators on pandas Series / OHLC DataFrames."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    out = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    out = out.where(loss != 0, 100.0)          # only gains -> 100
    return out.where((gain != 0) | (loss != 0), 50.0)  # flat -> neutral


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average true range. `bars` needs high, low, close columns."""
    prev_close = bars["close"].shift(1)
    tr = pd.concat(
        [bars["high"] - bars["low"], (bars["high"] - prev_close).abs(), (bars["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def pct_return(series: pd.Series, lookback: int) -> float:
    s = series.dropna()
    if len(s) <= lookback:
        return 0.0
    return float(s.iloc[-1] / s.iloc[-1 - lookback] - 1)


def zclip(x: float, scale: float) -> float:
    """Squash x/scale into -1..1 smoothly."""
    if scale <= 0:
        return 0.0
    return float(np.tanh(x / scale))
