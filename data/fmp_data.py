"""
Financial Modeling Prep client - free tier (250 requests/day as of this
build; verify current limits at https://site.financialmodelingprep.com/developer/docs).

Used for fundamental "value" and "quality" factor inputs: trailing P/E,
P/B, ROE, gross margin, debt/equity.

IMPORTANT: FMP retired all `/api/v3/` endpoints on August 31, 2025 for
accounts without a legacy subscription predating that date - they now
403 with "Legacy Endpoint" even with a valid free-tier key. This module
targets the current `/stable/` API (query-param style: `?symbol=X`,
not path style `/X`), confirmed working against a real free-tier key
on 2026-09-21. If FMP changes their API again, `_get` will surface a
clear HTTPError with FMP's own message rather than failing silently.

THREE real constraints measured against a live free-tier key on
2026-09-21, all handled deliberately rather than silently:
  1. Latency: each call took ~4 seconds round-trip in testing. Fetching
     500 symbols x 2 endpoints sequentially would take over an hour, so
     fetches run concurrently (bounded thread pool).
  2. Daily quota: the free tier allows 250 requests/day, but a full
     S&P 500 universe needs up to 1000 (500 symbols x 2 endpoints) for a
     cold cache. get_fundamentals_frame() will NOT try to blow through
     the quota - it fetches already-cached symbols for free, then spends
     a capped request budget on new ones, and leaves the rest for a
     future run. Combined with the 7-day cache TTL, a cold universe
     backfills itself over roughly a week of daily runs rather than
     failing outright. Symbols not yet fetched simply get a
     value/quality score of 0 for that run (research/signals.py already
     handles missing fundamentals gracefully) - not a crash, not a
     guard failure.
  3. Retrying a quota error makes it worse, fast: a naive retry-on-any-
     exception policy was tested live and confirmed to burn through the
     daily quota roughly 3x faster than necessary (every quota hit got
     retried 3 times for a guaranteed-repeat failure), exhausting a
     250/day budget in well under 100 real symbols. `_get` now raises
     FMPQuotaExceeded for 402/429 specifically, excluded from tenacity's
     retry, and get_fundamentals_frame trips a circuit breaker on the
     first confirmed quota hit so it stops spending the budget on
     symbols that are certain to fail for the rest of the run.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from config import API_KEYS
from data.cache import cached_call, is_fresh

logger = logging.getLogger(__name__)

BASE_URL = "https://financialmodelingprep.com/stable"
FUNDAMENTALS_TTL_SECONDS = 24 * 3600 * 7  # weekly refresh is plenty for quarterly fundamentals

# Stay comfortably under the 250 req/day free-tier cap even if other jobs
# (e.g. a manual test run) also hit FMP the same day. 2 endpoint calls per
# symbol, so this fetches at most ~55 NEW symbols per run.
DEFAULT_MAX_NEW_SYMBOLS_PER_RUN = int(os.environ.get("FMP_MAX_NEW_SYMBOLS_PER_RUN", "55"))
MAX_WORKERS = int(os.environ.get("FMP_MAX_WORKERS", "8"))


class FMPQuotaExceeded(Exception):
    """
    Raised for 402 (plan doesn't cover this endpoint/symbol) and 429 (rate
    or daily-quota limit hit). Both are PERMANENT for the remainder of the
    quota window, not transient - retrying them wastes the request budget
    even faster (confirmed live: retrying 3x on a quota error burns 3x the
    requests for zero benefit and can exhaust an otherwise-sufficient daily
    quota in minutes). _get excludes this from tenacity's retry so a quota
    hit fails once and moves on, instead of amplifying itself.
    """


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_not_exception_type(FMPQuotaExceeded),
)
def _get(path: str, symbol: str, params: dict | None = None) -> list | dict:
    params = {**(params or {}), "symbol": symbol, "apikey": API_KEYS.fmp_key}
    resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=20)
    if resp.status_code in (402, 429):
        raise FMPQuotaExceeded(f"{resp.status_code}: {resp.text[:200]}")
    resp.raise_for_status()  # a genuine 4xx/5xx here still retries (transient network/server issues)
    return resp.json()


def get_key_metrics_ttm(symbol: str) -> dict:
    """Trailing-twelve-month key metrics: ROE, enterprise value multiples, etc."""
    if not API_KEYS.fmp_key:
        raise RuntimeError("FMP_API_KEY not set - see .env.example")

    def fetch():
        data = _get("/key-metrics-ttm", symbol)
        return data[0] if isinstance(data, list) and data else {}

    return cached_call("fmp_key_metrics", symbol, FUNDAMENTALS_TTL_SECONDS, fetch)


def get_ratios_ttm(symbol: str) -> dict:
    """Trailing-twelve-month ratios: P/E, P/B, gross margin, debt/equity, etc."""
    if not API_KEYS.fmp_key:
        raise RuntimeError("FMP_API_KEY not set - see .env.example")

    def fetch():
        data = _get("/ratios-ttm", symbol)
        return data[0] if isinstance(data, list) and data else {}

    return cached_call("fmp_ratios", symbol, FUNDAMENTALS_TTL_SECONDS, fetch)


def _row_from(symbol: str, km: dict, ratios: dict) -> dict:
    return {
        "symbol": symbol,
        "pe": ratios.get("priceToEarningsRatioTTM"),
        "pb": ratios.get("priceToBookRatioTTM"),
        "roe": km.get("returnOnEquityTTM"),
        "gross_margin": ratios.get("grossProfitMarginTTM"),
        "debt_to_equity": ratios.get("debtToEquityRatioTTM"),
        # earnings_growth: FMP's current stable TTM endpoints don't carry a trailing growth
        # figure (a separate /stable/financial-growth call would be needed). No factor
        # currently reads this field (see research/factors.py compute_quality_factor), so
        # nothing downstream is affected - left in the schema for a future extension.
        "earnings_growth": None,
    }


def get_fundamentals_frame(symbols: list[str], max_new_symbols: int | None = None) -> pd.DataFrame:
    """
    One row per symbol with the raw fields the factor engine needs:
    pe, pb, roe, gross_margin, debt_to_equity, earnings_growth.

    Already-cached symbols (fresh within FUNDAMENTALS_TTL_SECONDS) are read
    for free with no network call and no budget impact. New symbols are
    fetched concurrently, capped at `max_new_symbols` (default
    DEFAULT_MAX_NEW_SYMBOLS_PER_RUN) to respect FMP's daily quota. Symbols
    beyond the cap are simply left out of the returned frame for this run -
    see module docstring for why that's a safe degradation, not a bug.
    """
    max_new_symbols = max_new_symbols if max_new_symbols is not None else DEFAULT_MAX_NEW_SYMBOLS_PER_RUN

    cached_symbols = [
        s for s in symbols
        if is_fresh("fmp_key_metrics", s, FUNDAMENTALS_TTL_SECONDS) and is_fresh("fmp_ratios", s, FUNDAMENTALS_TTL_SECONDS)
    ]
    new_symbols = [s for s in symbols if s not in cached_symbols][:max_new_symbols]
    skipped = len(symbols) - len(cached_symbols) - len(new_symbols)

    logger.info(
        "FMP fundamentals: %d symbols from cache (free), fetching %d new (budget cap %d), %d skipped this run",
        len(cached_symbols), len(new_symbols), max_new_symbols, max(skipped, 0),
    )

    rows: list[dict] = []
    quota_exhausted = threading.Event()  # circuit breaker: trip once, stop spending the budget

    def fetch_one(sym: str) -> dict | None:
        if quota_exhausted.is_set():
            return None  # don't even attempt - a sibling worker already confirmed the quota is gone
        try:
            return _row_from(sym, get_key_metrics_ttm(sym), get_ratios_ttm(sym))
        except FMPQuotaExceeded as exc:
            quota_exhausted.set()
            logger.warning("FMP quota/plan limit hit on %s - stopping further fetches this run: %s", sym, exc)
            return None
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not abort the run
            logger.warning("FMP fundamentals fetch failed for %s: %s", sym, exc)
            return None

    # Cached reads are local disk I/O - fine sequentially, no need for a thread pool.
    for sym in cached_symbols:
        row = fetch_one(sym)
        if row:
            rows.append(row)

    # New fetches hit the network (~4s/call observed) - run them concurrently to keep
    # wall-clock time reasonable for a pre-market job. quota_exhausted stops this early
    # if FMP confirms the daily/plan quota is gone, rather than burning through every
    # remaining queued symbol for a guaranteed failure.
    if new_symbols and not quota_exhausted.is_set():
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(fetch_one, sym): sym for sym in new_symbols}
            for future in as_completed(futures):
                row = future.result()
                if row:
                    rows.append(row)

    if quota_exhausted.is_set():
        logger.warning(
            "FMP quota exhausted this run - %d/%d requested symbols got fundamentals; "
            "the rest default to a neutral value/quality score of 0 (momentum/low-vol still apply). "
            "This resets on FMP's normal daily/plan cycle - see README.md.",
            len(rows), len(cached_symbols) + len(new_symbols),
        )

    return pd.DataFrame(rows)
