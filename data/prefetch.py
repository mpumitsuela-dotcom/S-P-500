"""
Evening warm-up of the slow-changing research, so the next morning's session
only has to fetch the news.

The morning ranking needs, for every S&P 500 stock, the news (changes daily),
the analyst ratings (refreshed every 3 days) and the company financials
(weekly). At Finnhub's free rate limit, fetching all three for ~500 stocks in
the morning takes ~25 minutes. Fetching the ratings and financials after the
close (scheduler/run_shared.py, after the report) leaves the morning with
~500 news calls (~10 minutes), so every stock gets researched every day.

Only symbols whose cached copy is stale are fetched, and the whole thing
stops at a deadline so it never holds up the runner.
"""
from __future__ import annotations

import logging
import time

from data import finnhub_data
from data.cache import _key_to_path, is_fresh

logger = logging.getLogger(__name__)


def warm_slow_research(symbols: list[str], deadline_seconds: float = 20 * 60) -> dict[str, int]:
    start = time.monotonic()
    done = {"analysts": 0, "financials": 0, "failed": 0, "left": 0}
    jobs = [
        ("analysts", "finnhub_recommendation", finnhub_data.RECOMMENDATION_TTL_SECONDS, finnhub_data.get_analyst_recommendation),
        ("financials", finnhub_data.METRIC_NS, finnhub_data.METRIC_TTL_SECONDS, finnhub_data.get_basic_financials),
    ]
    for label, ns, ttl, fetch in jobs:
        # Refresh anything that would expire before tomorrow's session, not just what's already stale.
        stale = [s for s in symbols if not is_fresh(ns, s, ttl - 18 * 3600)]
        for i, sym in enumerate(stale):
            if time.monotonic() - start > deadline_seconds:
                done["left"] += len(stale) - i
                break
            try:
                _key_to_path(ns, sym).unlink(missing_ok=True)  # force a refresh of an about-to-expire copy
                fetch(sym)
                done[label] += 1
            except Exception as exc:  # noqa: BLE001 - one symbol must not stop the warm-up
                done["failed"] += 1
                logger.warning("Research warm-up failed for %s (%s): %s", sym, label, exc)
    logger.info("Research warm-up: %s", done)
    return done
