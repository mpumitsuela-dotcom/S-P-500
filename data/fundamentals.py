"""
Company fundamentals from FMP, with Finnhub filling the gaps.

FMP's free plan doesn't cover every S&P 500 stock, so a symbol with no FMP
values gets them from Finnhub's free basic-financials endpoint instead
(data/finnhub_data.py fundamentals_row). Each row's `source` says which one
was used, so the reports can say where the numbers came from.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

from data import finnhub_data
from data.cache import is_fresh
from data.fmp_data import get_fundamentals_frame

logger = logging.getLogger(__name__)

FIELDS = ("pe", "pb", "roe", "gross_margin", "debt_to_equity")
# New Finnhub lookups per call (cached ones are free); coverage backfills over a few runs.
MAX_NEW_FINNHUB = int(os.environ.get("FINNHUB_MAX_NEW_FUNDAMENTALS_PER_RUN", "150"))


def _has_values(row: pd.Series) -> bool:
    return any(pd.notna(row.get(f)) for f in FIELDS)


def get_fundamentals(symbols: list[str], max_new_symbols: int | None = None) -> pd.DataFrame:
    fmp = get_fundamentals_frame(symbols, max_new_symbols=max_new_symbols) if max_new_symbols is not None else get_fundamentals_frame(symbols)
    rows: dict[str, dict] = {}
    if fmp is not None and not fmp.empty and "symbol" in fmp.columns:
        for _, r in fmp.iterrows():
            if _has_values(r):
                rows[r["symbol"]] = {**r.to_dict(), "source": "FMP"}

    missing = [s for s in symbols if s not in rows]
    cached = [s for s in missing if is_fresh(finnhub_data.METRIC_NS, s, finnhub_data.METRIC_TTL_SECONDS)]
    budget = max(len(missing), 0) if max_new_symbols is not None else MAX_NEW_FINNHUB
    to_fetch = cached + [s for s in missing if s not in cached][:budget]
    filled = 0
    for sym in to_fetch:
        try:
            row = finnhub_data.fundamentals_row(sym)
        except Exception as exc:  # noqa: BLE001 - one symbol must not stop the run
            logger.warning("Finnhub fundamentals failed for %s: %s", sym, exc)
            continue
        if any(row.get(f) is not None for f in FIELDS):
            rows[sym] = {**row, "source": "Finnhub"}
            filled += 1
    logger.info("Fundamentals: %d from FMP, %d filled from Finnhub, %d still missing",
                sum(r["source"] == "FMP" for r in rows.values()), filled, len(symbols) - len(rows))

    frame = pd.DataFrame(list(rows.values()))
    for col in (*FIELDS, "earnings_growth"):
        if col not in frame.columns:
            frame[col] = pd.Series(dtype=float)
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "symbol" not in frame.columns:
        frame["symbol"] = pd.Series(dtype=str)
    return frame
