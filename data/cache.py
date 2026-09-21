"""
Lightweight disk cache for API responses.

Free-tier APIs (FMP: 250 req/day, Finnhub: 60 req/min) are the binding
constraint on how often this system can refresh fundamentals, so every
provider call goes through this cache. Prices/bars are cached with a short
TTL (they change every session); fundamentals are cached with a long TTL
(quarterly data doesn't need refetching every run).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from config import CACHE_DIR


def _key_to_path(namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    ns_dir = CACHE_DIR / namespace
    ns_dir.mkdir(parents=True, exist_ok=True)
    return ns_dir / f"{digest}.json"


def cached_call(namespace: str, key: str, ttl_seconds: float, fetch_fn: Callable[[], Any]) -> Any:
    """Return cached JSON-serializable data for (namespace, key) if fresh, else call fetch_fn."""
    path = _key_to_path(namespace, key)
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            if time.time() - payload["_cached_at"] < ttl_seconds:
                return payload["data"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # fall through and refetch

    data = fetch_fn()
    try:
        path.write_text(json.dumps({"_cached_at": time.time(), "data": data}))
    except (TypeError, OSError):
        pass  # non-serializable or disk issue - still return the fresh data
    return data


def is_fresh(namespace: str, key: str, ttl_seconds: float) -> bool:
    """Check whether a fresh cache entry exists WITHOUT triggering a fetch. Used to plan
    request budgets (e.g. data/fmp_data.py) before spending any network calls."""
    age = cache_age_seconds(namespace, key)
    return age is not None and age < ttl_seconds


def cache_age_seconds(namespace: str, key: str) -> float | None:
    path = _key_to_path(namespace, key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        return time.time() - payload["_cached_at"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None
