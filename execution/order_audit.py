"""
Find orders on the Alpaca account that this agent did not place.

On 2026-09-24 the account held stocks this agent never ordered, bought at
that day's prices - a sign that something else (e.g. an older copy of the
agent running on the owner's computer) is trading the same account. Two
traders on one account fight each other's positions and make the trial's
comparison with the S&P 500 meaningless, so the owner needs to know.

An order counts as this agent's when:
  - its client_order_id starts with AGENT_ORDER_PREFIX (every order placed
    since this tagging was added), or
  - it matches one of the agent's recorded trades (symbol, side, qty, NY
    date) - for orders placed before tagging existed.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from execution.alpaca_broker import AGENT_ORDER_PREFIX

NY_TZ = ZoneInfo("America/New_York")


def _ny_date(ts: str) -> str:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(NY_TZ).date().isoformat()


def foreign_orders(orders: list[dict], our_trades: list[dict]) -> list[dict]:
    """orders: Alpaca /v2/orders objects. our_trades: decision records of type 'trade'."""
    ours = Counter((t["symbol"], t["side"], int(t["qty"]), t["date"]) for t in our_trades)
    out = []
    for o in sorted(orders, key=lambda o: o.get("submitted_at") or ""):
        if (o.get("client_order_id") or "").startswith(AGENT_ORDER_PREFIX):
            continue
        key = (o["symbol"], o["side"], int(float(o.get("qty") or 0)), _ny_date(o["submitted_at"]))
        if ours[key] > 0:
            ours[key] -= 1  # each recorded trade accounts for one order
            continue
        out.append(o)
    return out


def summarize(order: dict) -> str:
    when = datetime.fromisoformat(order["submitted_at"].replace("Z", "+00:00")).astimezone(NY_TZ)
    price = order.get("filled_avg_price")
    return (
        f"{when:%a %d %b %H:%M} ET  {order['side'].upper():4} {order.get('qty')} {order['symbol']}"
        f"  [{order.get('status')}]" + (f" @ ${float(price):,.2f}" if price else "")
    )


def check_and_record(broker, days: int = 3, now: datetime | None = None) -> list[dict]:
    """Record orders from the last `days` days that this agent didn't place and
    haven't been reported before. Returns the newly found ones (which
    execution/alerts.py turns into a needs-attention issue)."""
    from datetime import timedelta

    from execution import decisions

    now = now or datetime.now(NY_TZ)
    since = now - timedelta(days=days)
    earlier = decisions.read_records(since.date() - timedelta(days=30), now.date())
    trades = [r for r in earlier if r.get("type") == "trade"]
    reported = {oid for r in earlier if r.get("type") == "foreign_orders" for oid in r.get("order_ids", [])}
    new = [o for o in foreign_orders(broker.get_orders(after=since), trades) if o["id"] not in reported]
    if new:
        decisions._append(
            {
                "type": "foreign_orders",
                "timestamp": now.isoformat(timespec="seconds"),
                "date": now.date().isoformat(),
                "order_ids": [o["id"] for o in new],
                "orders": [summarize(o) for o in new],
            }
        )
    return new
