#!/usr/bin/env python3
"""
List recent orders on the Alpaca account and flag any this agent didn't place.

    python3 scripts/order_audit.py [--days 5]

Run it from a checkout whose shared state has been pulled (the workflow runs
it after the agent step), so the agent's own trade records are available.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution import decisions  # noqa: E402
from execution.alpaca_broker import AlpacaBroker  # noqa: E402
from execution.order_audit import NY_TZ, foreign_orders, summarize  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    days = ap.parse_args().days
    since = datetime.now(NY_TZ) - timedelta(days=days)

    orders = AlpacaBroker().get_orders(after=since)
    trades = [r for r in decisions.read_records(since.date(), datetime.now(NY_TZ).date()) if r.get("type") == "trade"]
    foreign = foreign_orders(orders, trades)
    foreign_ids = {o["id"] for o in foreign}

    print(f"Orders on the account in the last {days} days: {len(orders)} "
          f"({len(orders) - len(foreign)} by this agent, {len(foreign)} NOT by this agent)\n")
    for o in sorted(orders, key=lambda o: o.get("submitted_at") or ""):
        tag = "OTHER " if o["id"] in foreign_ids else "agent "
        print(f"  {tag} {summarize(o)}  client_order_id={o.get('client_order_id')}")
    if foreign:
        print("\nOrders NOT placed by this agent - something else is trading this account.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
