"""Economic calendar (no key): this week's scheduled releases from the
ForexFactory public JSON feed. Used to avoid opening trades right around a
high-impact release (rate decisions, jobs reports, inflation) and to tell the
research step what's coming."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import http

log = logging.getLogger(__name__)
FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


@dataclass
class Event:
    currency: str
    title: str
    impact: str   # "High", "Medium", "Low", "Holiday"
    when: datetime
    forecast: str = ""
    previous: str = ""


def parse(items: list[dict]) -> list[Event]:
    out = []
    for it in items or []:
        try:
            when = datetime.fromisoformat(str(it["date"]))
        except (KeyError, ValueError):
            continue
        if when.tzinfo is None:
            continue
        out.append(
            Event(
                currency=str(it.get("country", "")).upper(),
                title=str(it.get("title", "")),
                impact=str(it.get("impact", "")),
                when=when,
                forecast=str(it.get("forecast", "") or ""),
                previous=str(it.get("previous", "") or ""),
            )
        )
    return out


def fetch() -> list[Event]:
    try:
        return parse(http.get(FEED, tries=2).json())
    except Exception as exc:  # noqa: BLE001
        log.warning("Economic calendar unavailable: %s", exc)
        return []


def high_impact_near(events: list[Event], currency: str, now: datetime, minutes: int) -> Event | None:
    window = timedelta(minutes=minutes)
    for e in events:
        if e.currency == currency and e.impact == "High" and abs(e.when - now) <= window:
            return e
    return None


def upcoming(events: list[Event], now: datetime, hours: int = 24) -> list[Event]:
    return [e for e in events if now <= e.when <= now + timedelta(hours=hours) and e.impact in ("High", "Medium")]
