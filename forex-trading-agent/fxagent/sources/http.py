"""Shared HTTP helper: retries, a browser-like User-Agent, sane timeouts."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (X11; Linux x86_64) forex-trading-agent/1.0"


def get(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: float = 15, tries: int = 3) -> requests.Response:
    hdrs = {"User-Agent": UA}
    hdrs.update(headers or {})
    last: Exception | None = None
    for attempt in range(tries):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{resp.status_code} from {url}", response=resp)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
    assert last is not None
    raise last
