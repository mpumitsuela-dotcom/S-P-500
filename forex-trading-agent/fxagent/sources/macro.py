"""Central-bank / short-term interest rates from FRED (free key, optional).

Higher rates tend to support a currency (the "carry" effect). This is a
slow-moving tilt, not an intraday signal, so it gets a small weight.
"""
from __future__ import annotations

import logging

from . import http

log = logging.getLogger(__name__)
URL = "https://api.stlouisfed.org/fred/series/observations"


def latest_rate(series_id: str, key: str) -> float | None:
    if not key:
        return None
    try:
        resp = http.get(URL, params={"series_id": series_id, "api_key": key, "file_type": "json", "sort_order": "desc", "limit": 10})
        for obs in resp.json().get("observations", []):
            if obs.get("value") not in (None, ".", ""):
                return float(obs["value"])
    except Exception as exc:  # noqa: BLE001
        log.warning("FRED rate %s unavailable: %s", series_id, exc)
    return None
