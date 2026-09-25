"""Detecting orders on the account that this agent didn't place."""
from datetime import datetime

import pytest

from execution import alerts, decisions, order_audit
from execution.alpaca_broker import AGENT_ORDER_PREFIX
from execution.decisions import NY_TZ

NOW = datetime(2026, 9, 25, 9, 40, tzinfo=NY_TZ)


def order(oid, symbol, side, qty, submitted, client=None):
    return {"id": oid, "symbol": symbol, "side": side, "qty": str(qty), "status": "filled",
            "submitted_at": submitted, "filled_avg_price": "10.0", "client_order_id": client or f"uuid-{oid}"}


@pytest.fixture(autouse=True)
def tmp_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "DECISIONS_FILE", tmp_path / "d.jsonl")


class Broker:
    def __init__(self, orders):
        self.orders = orders

    def get_orders(self, after):
        return self.orders


def test_tagged_and_recorded_orders_are_ours_the_rest_are_foreign():
    trades = [{"symbol": "BAC", "side": "buy", "qty": 67, "date": "2026-09-24"}]
    orders = [
        order("1", "BAC", "buy", 67, "2026-09-24T15:37:43Z"),                         # matches a recorded trade
        order("2", "MU", "buy", 1, "2026-09-25T13:45:00Z", AGENT_ORDER_PREFIX + "x"),  # tagged
        order("3", "KO", "buy", 34, "2026-09-24T14:10:00Z"),                          # neither
        order("4", "BAC", "buy", 67, "2026-09-24T18:00:00Z"),                         # a second identical order: only one recorded
    ]
    assert [o["id"] for o in order_audit.foreign_orders(orders, trades)] == ["3", "4"]


def test_foreign_orders_are_recorded_once_and_alert_the_owner():
    broker = Broker([order("3", "KO", "buy", 34, "2026-09-24T14:10:00Z")])
    assert len(order_audit.check_and_record(broker, now=NOW)) == 1
    assert order_audit.check_and_record(broker, now=NOW) == []  # not re-reported

    title, body = alerts.build_alert(NOW.date(), "github-actions", ["morning"], 0)
    assert title.startswith("🟠 Decision needed")
    assert "NOT placed by this agent" in body and "KO" in body


def test_agent_orders_are_tagged(monkeypatch):
    from execution import alpaca_broker

    sent = {}
    broker = alpaca_broker.AlpacaBroker.__new__(alpaca_broker.AlpacaBroker)
    monkeypatch.setattr(broker, "_request", lambda method, path, **kw: sent.update(kw["json"]) or {"id": "1", "status": "accepted"})
    broker.submit_order("AAPL", 1, "buy")
    assert sent["client_order_id"].startswith(AGENT_ORDER_PREFIX) and len(sent["client_order_id"]) <= 48
