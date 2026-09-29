"""Forex news headlines from several free sources.

- Finnhub "forex" news category (free key, optional)
- Public RSS feeds from forex news sites (no key)
Every source is optional; failures are logged and skipped.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import http

log = logging.getLogger(__name__)

RSS_FEEDS = {
    "FXStreet": "https://www.fxstreet.com/rss/news",
    "Investing.com FX": "https://www.investing.com/rss/news_1.rss",
    "investingLive": "https://investinglive.com/feed/news",
    "FXEmpire": "https://www.fxempire.com/api/v1/en/articles/rss/news",
}


@dataclass
class Headline:
    source: str
    title: str
    summary: str
    published: datetime

    @property
    def text(self) -> str:
        return f"{self.title}. {self.summary}".strip()


def _finnhub(key: str) -> list[Headline]:
    if not key:
        return []
    resp = http.get("https://finnhub.io/api/v1/news", params={"category": "forex", "token": key})
    out = []
    for item in resp.json() or []:
        try:
            out.append(
                Headline(
                    source=f"Finnhub/{item.get('source', '')}",
                    title=(item.get("headline") or "").strip(),
                    summary=(item.get("summary") or "").strip()[:400],
                    published=datetime.fromtimestamp(int(item["datetime"]), tz=timezone.utc),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out


def parse_rss(xml_text: str, source: str) -> list[Headline]:
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub = item.findtext("pubDate")
        try:
            published = parsedate_to_datetime(pub) if pub else None
        except (TypeError, ValueError):
            published = None
        if published is None:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        # strip any HTML in the description
        if "<" in desc:
            desc = re.sub(r"<[^>]+>", " ", desc)
        out.append(Headline(source=source, title=title, summary=" ".join(desc.split())[:400], published=published))
    return out


def fetch_all(finnhub_key: str, hours: int = 12, now: datetime | None = None) -> list[Headline]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    items: list[Headline] = []
    try:
        items += _finnhub(finnhub_key)
    except Exception as exc:  # noqa: BLE001
        log.warning("Finnhub news unavailable: %s", exc)
    for name, url in RSS_FEEDS.items():
        try:
            items += parse_rss(http.get(url, tries=2).text, name)
        except Exception as exc:  # noqa: BLE001
            log.warning("News feed %s unavailable: %s", name, exc)
    seen, unique = set(), []
    for h in sorted(items, key=lambda h: h.published, reverse=True):
        key = h.title.lower()[:80]
        if h.published >= cutoff and h.title and key not in seen:
            seen.add(key)
            unique.append(h)
    log.info("News: %d recent headlines from %d sources", len(unique), len({h.source.split('/')[0] for h in unique}))
    return unique
