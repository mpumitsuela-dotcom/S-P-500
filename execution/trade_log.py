"""
Append-only log of every order this system places, with the plain-English
rationale from execution/rationale.py - answers "why did it buy/sell X" for
each decision, not just what it did.

Two files, both under PROJECT_ROOT so they sit alongside the code and travel
with it:
  - trade_log.csv : machine-readable (timestamp, session, symbol, side, qty,
    status, reason) - what execution/reporting.py reads to build
    TRIAL_REPORT.md.
  - TRADE_LOG.md  : the same information as a running human-readable log,
    for someone to just open and read without any tooling.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime

from config import PROJECT_ROOT

CSV_PATH = PROJECT_ROOT / "trade_log.csv"
MD_PATH = PROJECT_ROOT / "TRADE_LOG.md"

_CSV_FIELDS = ["timestamp", "session", "symbol", "side", "qty", "status", "reason"]


@dataclass
class TradeLogEntry:
    session: str  # "AM" | "PM"
    symbol: str
    side: str
    qty: int
    status: str
    reason: str


def record_trades(entries: list[TradeLogEntry], now: datetime | None = None) -> None:
    if not entries:
        return
    now = now or datetime.now()
    is_new = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        if is_new:
            writer.writeheader()
        for e in entries:
            writer.writerow(
                {
                    "timestamp": now.isoformat(timespec="seconds"),
                    "session": e.session,
                    "symbol": e.symbol,
                    "side": e.side,
                    "qty": e.qty,
                    "status": e.status,
                    "reason": e.reason,
                }
            )

    with MD_PATH.open("a") as f:
        for e in entries:
            f.write(f"- **{now.strftime('%Y-%m-%d %H:%M')} ({e.session})** {e.side.upper()} {e.qty} {e.symbol} [{e.status}] — {e.reason}\n")


def read_all_entries() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open() as f:
        return list(csv.DictReader(f))
