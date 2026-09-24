"""
Finnhub client - free tier (60 requests/minute as of this build; verify
current limits at https://finnhub.io/pricing).

Used for three things:
  1. Company news headlines, feeding a simple keyword/lexicon sentiment
     score. Used two ways: get_news_sentiment/get_news_sentiment_frame (the
     original path) for the PM (afternoon) tactical trim check on currently
     held names; get_research_frame (below) folds a daily-refreshed version
     of the same signal into the AM session's actual buy/sell ranking.
  2. Analyst recommendation trends (get_analyst_recommendation) - real
     "what do covering analysts currently think" research, not just a
     headline word count. Also feeds get_research_frame / the AM ranking.
  3. Basic financials as a secondary source/cross-check for fundamentals.

This is intentionally a *simple* sentiment approach (word-list scoring),
not an ML/NLP model, and the analyst leg is a raw consensus count, not a
proprietary rating - both are described honestly in the guide as coarse
signals, not a source of guaranteed edge on their own. See
research/factors.py compute_research_factor for how they're combined and
weighted alongside the value/quality/momentum/low-vol factors.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import API_KEYS
from data.cache import cached_call, is_fresh

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
NEWS_TTL_SECONDS = 3600  # news changes fast - short TTL, and it's cheap on the free tier

# Separate, longer-lived cache used specifically by get_research_frame (the AM
# ranking input) - keyed by symbol alone (not a rolling date range like
# get_news_sentiment's cache key) so is_fresh() can actually tell whether a
# symbol was already covered today, and so the free-tier request budget below
# means something. ~20h so it effectively refreshes once/trading day without
# being so tight that a slightly-late run refetches everything.
RESEARCH_NEWS_TTL_SECONDS = 20 * 3600
RECOMMENDATION_TTL_SECONDS = 24 * 3600 * 3  # analyst recommendation trends update roughly monthly

# Fetching fresh research for the full ~500-symbol universe every run would
# be slow (2 calls/symbol at a 60 req/min free-tier limit) and isn't needed -
# same reasoning and pattern as data/fmp_data.py's fundamentals budget cap.
# Coverage backfills across a handful of runs, then mostly just refreshes.
DEFAULT_MAX_NEW_RESEARCH_SYMBOLS_PER_RUN = int(os.environ.get("FINNHUB_MAX_NEW_SYMBOLS_PER_RUN", "120"))
RESEARCH_MAX_WORKERS = int(os.environ.get("FINNHUB_MAX_WORKERS", "6"))

_POSITIVE_WORDS = {
    "beat", "beats", "upgrade", "upgraded", "surge", "surges", "record",
    "strong", "growth", "raises", "raised", "outperform", "buyback",
    "expansion", "profit", "profits", "rally", "bullish", "exceeds",
}
_NEGATIVE_WORDS = {
    "miss", "misses", "downgrade", "downgraded", "plunge", "plunges",
    "weak", "cuts", "cut", "lawsuit", "investigation", "recall", "fraud",
    "bankruptcy", "layoffs", "warns", "warning", "slump", "bearish",
    "probe", "halted", "delisted",
}
_WORD_RE = re.compile(r"[a-z]+")


# Every Finnhub call in the process goes through one shared limiter, so the
# thread pools below can't burst past the free tier's 60 requests/minute.
# Without it, 6 workers x 2 calls/symbol hit the limit within seconds and
# almost every research fetch failed (confirmed live on 2026-09-24).
CALLS_PER_MINUTE = float(os.environ.get("FINNHUB_CALLS_PER_MINUTE", "50"))
_rate_lock = threading.Lock()
_next_call_at = 0.0


def _throttle() -> None:
    global _next_call_at
    if CALLS_PER_MINUTE <= 0:
        return
    with _rate_lock:
        now = time.monotonic()
        wait = _next_call_at - now
        _next_call_at = max(now, _next_call_at) + 60.0 / CALLS_PER_MINUTE
    if wait > 0:
        time.sleep(wait)


class FinnhubRateLimited(RuntimeError):
    pass


@retry(
    stop=stop_after_attempt(4),
    # A 429 means the minute's budget is spent: wait it out rather than
    # retrying within a second or two, which only burns more of it.
    wait=wait_exponential(multiplier=5, min=10, max=60),
    retry=retry_if_exception_type((FinnhubRateLimited, requests.ConnectionError, requests.Timeout)),
    reraise=True,
)
def _get(path: str, params: dict) -> list | dict:
    params = {**params, "token": API_KEYS.finnhub_key}
    _throttle()
    resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=20)
    if resp.status_code == 429:
        raise FinnhubRateLimited("Finnhub rate-limited (429) - free tier is 60 req/min")
    resp.raise_for_status()
    return resp.json()


TOP_HEADLINES_KEPT = 5


def _score_headline(headline: str, summary: str) -> int:
    text = f"{headline} {summary}".lower()
    words = set(_WORD_RE.findall(text))
    return len(words & _POSITIVE_WORDS) - len(words & _NEGATIVE_WORDS)


def get_news_sentiment(symbol: str, lookback_days: int = 2) -> dict:
    """
    Returns {"symbol", "headline_count", "sentiment_score", "flagged_negative"}.
    flagged_negative=True means the net score was materially negative and the
    afternoon guard session should consider it (see execution/guards.py and
    scheduler/run_afternoon.py) - it does NOT by itself trigger a sale;
    it's one input into the tactical check.
    """
    if not API_KEYS.finnhub_key:
        raise RuntimeError("FINNHUB_API_KEY not set - see .env.example")

    end = date.today()
    start = end - timedelta(days=lookback_days)
    cache_key = f"{symbol}|{start}|{end}"

    def fetch():
        return _get(
            "/company-news",
            {"symbol": symbol, "from": str(start), "to": str(end)},
        )

    articles = cached_call("finnhub_news", cache_key, NEWS_TTL_SECONDS, fetch)
    if not isinstance(articles, list):
        articles = []

    total_score = 0
    scored = []
    for a in articles:
        score = _score_headline(a.get("headline", ""), a.get("summary", ""))
        total_score += score
        scored.append((score, a))

    # The headlines that moved the score most (then the newest), kept so the
    # daily/weekly reports can show the actual news behind a decision.
    scored.sort(key=lambda sa: (abs(sa[0]), sa[1].get("datetime") or 0), reverse=True)
    top_headlines = [
        {
            "headline": a.get("headline", ""),
            "source": a.get("source", ""),
            "url": a.get("url", ""),
            "datetime": a.get("datetime"),
            "score": score,
        }
        for score, a in scored[:TOP_HEADLINES_KEPT]
    ]

    return {
        "symbol": symbol,
        "headline_count": len(articles),
        "sentiment_score": total_score,
        "flagged_negative": total_score <= -3 and len(articles) >= 2,
        "top_headlines": top_headlines,
    }


def get_news_sentiment_frame(symbols: list[str]) -> pd.DataFrame:
    rows = []
    for sym in symbols:
        try:
            rows.append(get_news_sentiment(sym))
        except Exception as exc:  # noqa: BLE001 - one symbol's news failure shouldn't abort the run
            logger.warning("Finnhub news fetch failed for %s: %s", sym, exc)
    return pd.DataFrame(rows)


def get_analyst_recommendation(symbol: str) -> dict:
    """
    Latest monthly analyst recommendation trend: {symbol, period, strongBuy,
    buy, hold, sell, strongSell}. Returns {} (not a crash) for a symbol with
    no Finnhub analyst coverage - some smaller S&P 500 names have thin
    coverage even on paid tiers, let alone free.
    """
    if not API_KEYS.finnhub_key:
        raise RuntimeError("FINNHUB_API_KEY not set - see .env.example")

    def fetch():
        data = _get("/stock/recommendation", {"symbol": symbol})
        return data[0] if isinstance(data, list) and data else {}

    return cached_call("finnhub_recommendation", symbol, RECOMMENDATION_TTL_SECONDS, fetch)


def _research_news_cached(symbol: str) -> dict:
    """Same underlying company-news + lexicon scoring as get_news_sentiment, but cached
    under a symbol-only key (see RESEARCH_NEWS_TTL_SECONDS) so get_research_frame's
    budget cap can tell a genuinely-covered-today symbol from a stale one."""

    def fetch():
        return get_news_sentiment(symbol)

    return cached_call("finnhub_research_news", symbol, RESEARCH_NEWS_TTL_SECONDS, fetch)


def _analyst_score(rec: dict) -> float | None:
    """Consensus score in [-2, +2]: (2*strongBuy + buy - sell - 2*strongSell) / total
    ratings. None (no leg contribution) when there's no analyst coverage at all."""
    strong_buy = rec.get("strongBuy") or 0
    buy = rec.get("buy") or 0
    hold = rec.get("hold") or 0
    sell = rec.get("sell") or 0
    strong_sell = rec.get("strongSell") or 0
    total = strong_buy + buy + hold + sell + strong_sell
    if total == 0:
        return None
    return (2 * strong_buy + buy - sell - 2 * strong_sell) / total


def _research_row(symbol: str) -> dict:
    news = _research_news_cached(symbol)
    rec = get_analyst_recommendation(symbol)
    return {
        "symbol": symbol,
        "news_sentiment_score": news.get("sentiment_score", 0),
        "headline_count": news.get("headline_count", 0),
        "top_headlines": news.get("top_headlines", []),
        "analyst_period": rec.get("period"),
        "analyst_score": _analyst_score(rec),
        "total_analysts": (rec.get("strongBuy") or 0) + (rec.get("buy") or 0) + (rec.get("hold") or 0)
        + (rec.get("sell") or 0) + (rec.get("strongSell") or 0),
        "analyst_breakdown": {
            "strongBuy": rec.get("strongBuy") or 0,
            "buy": rec.get("buy") or 0,
            "hold": rec.get("hold") or 0,
            "sell": rec.get("sell") or 0,
            "strongSell": rec.get("strongSell") or 0,
        },
    }


def get_research_frame(symbols: list[str], max_new_symbols: int | None = None) -> pd.DataFrame:
    """
    One row per symbol: news_sentiment_score, headline_count, analyst_score
    (-2..+2 consensus, or None with no coverage), total_analysts,
    analyst_breakdown. This is the "latest research" input into the AM
    session's buy/sell ranking (research/factors.py compute_research_factor)
    - a step further than the PM-only news trim check: here it actually
    competes with value/quality/momentum/low-vol for portfolio weight, both
    up (research-favored names get bought/held) and down (research-unfavored
    names get sold/excluded).

    Budget-capped and concurrent, same pattern as
    data/fmp_data.py get_fundamentals_frame, for the same reason (free-tier
    rate limit, ~500-symbol universe) - coverage backfills over a handful of
    runs rather than blocking on a cold cache.
    """
    max_new_symbols = max_new_symbols if max_new_symbols is not None else DEFAULT_MAX_NEW_RESEARCH_SYMBOLS_PER_RUN

    cached_symbols = [
        s for s in symbols
        if is_fresh("finnhub_research_news", s, RESEARCH_NEWS_TTL_SECONDS)
        and is_fresh("finnhub_recommendation", s, RECOMMENDATION_TTL_SECONDS)
    ]
    new_symbols = [s for s in symbols if s not in cached_symbols][:max_new_symbols]
    skipped = len(symbols) - len(cached_symbols) - len(new_symbols)

    logger.info(
        "Finnhub research: %d symbols from cache (free), fetching %d new (budget cap %d), %d skipped this run",
        len(cached_symbols), len(new_symbols), max_new_symbols, max(skipped, 0),
    )

    rows: list[dict] = []
    lock = threading.Lock()

    def fetch_one(sym: str) -> dict | None:
        try:
            return _research_row(sym)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not abort the run
            logger.warning("Finnhub research fetch failed for %s: %s", sym, exc)
            return None

    for sym in cached_symbols:
        row = fetch_one(sym)
        if row:
            rows.append(row)

    if new_symbols:
        with ThreadPoolExecutor(max_workers=RESEARCH_MAX_WORKERS) as pool:
            futures = {pool.submit(fetch_one, sym): sym for sym in new_symbols}
            for future in as_completed(futures):
                row = future.result()
                if row:
                    with lock:
                        rows.append(row)

    return pd.DataFrame(rows)
