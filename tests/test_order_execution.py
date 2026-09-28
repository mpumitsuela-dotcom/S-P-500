"""Fractional sizing, marketable limit orders, and confirming sells before buying."""
import pandas as pd

from execution.broker_base import Order, Position, format_qty
from execution.rebalancer import PlannedOrder, compute_rebalance_orders, execute_orders, share_qty


def test_share_qty_and_format():
    assert share_qty(3300, 1800, fractional=True) == 1.833
    assert share_qty(3300, 1800, fractional=False) == 1
    assert format_qty(10.0) == "10" and format_qty(1.8330) == "1.833"


def test_expensive_stock_sized_in_fractions_only_when_allowed():
    target = pd.DataFrame({"symbol": ["BIG", "SMALL"], "target_weight": [0.033, 0.033]})
    prices = pd.Series({"BIG": 1800.0, "SMALL": 50.0})
    frac = {o.symbol: o.qty for o in compute_rebalance_orders(target, {}, 100_000, prices, fractionable={"BIG", "SMALL"})}
    whole = {o.symbol: o.qty for o in compute_rebalance_orders(target, {}, 100_000, prices)}
    assert frac["BIG"] == 1.833 and whole["BIG"] == 1
    assert whole["SMALL"] == 66


class AlpacaLike:
    """Records the order of events; sells fill, one buy stays open until cancelled."""
    FINAL_STATUSES = {"filled", "canceled", "rejected"}

    def __init__(self, stuck=()):
        self.events, self.stuck, self.n = [], set(stuck), 0

    def submit_order(self, symbol, qty, side, limit_price=None):
        self.n += 1
        self.events.append(("submit", side, symbol, round(limit_price, 2) if limit_price else None))
        return Order(symbol, qty, side, id=f"{symbol}-{self.n}", status="new")

    def wait_for_orders(self, ids, timeout=90):
        self.events.append(("wait", tuple(ids)))
        out = {}
        for oid in ids:
            sym = oid.split("-")[0]
            if sym in self.stuck:
                out[oid] = {"status": "new", "filled_qty": "0"}
            else:
                out[oid] = {"status": "filled", "filled_qty": "5", "filled_avg_price": "100.5"}
        return out

    def cancel_order(self, oid):
        self.events.append(("cancel", oid))


def test_sells_confirmed_before_buys_with_limits_and_unfilled_cancelled():
    broker = AlpacaLike(stuck={"NEW2"})
    orders = [PlannedOrder("NEW1", "buy", 5, "r"), PlannedOrder("OLD", "sell", 5, "r"), PlannedOrder("NEW2", "buy", 5, "r")]
    quotes = pd.Series({"NEW1": 100.0, "OLD": 100.0, "NEW2": 100.0})
    results = execute_orders(broker, orders, quotes=quotes, limit_buffer=0.005)

    kinds = [e[0] + (":" + e[1] if e[0] == "submit" else "") for e in broker.events]
    assert kinds.index("wait") < kinds.index("submit:buy")  # sells confirmed before any buy is sent
    assert ("submit", "buy", "NEW1", 100.5) in broker.events and ("submit", "sell", "OLD", 99.5) in broker.events
    by = {r["symbol"]: r for r in results}
    assert by["OLD"]["status"] == "filled" and by["OLD"]["fill_price"] == 100.5
    assert by["NEW2"]["status"] == "unfilled_cancelled" and by["NEW2"]["qty"] == 0 and by["NEW2"]["requested_qty"] == 5
    assert ("cancel", "NEW2-3") in broker.events


def test_without_quotes_orders_are_plain_market_orders():
    broker = AlpacaLike()
    execute_orders(broker, [PlannedOrder("X", "buy", 1, "r")])
    assert ("submit", "buy", "X", None) in broker.events


def test_fractional_limit_refused_falls_back_to_market(monkeypatch):
    from execution import alpaca_broker

    from types import SimpleNamespace

    monkeypatch.setattr(alpaca_broker, "API_KEYS", SimpleNamespace(
        alpaca_key_id="k", alpaca_secret_key="s", alpaca_base_url="https://paper-api.alpaca.markets"))
    b = alpaca_broker.AlpacaBroker()
    sent = []

    def fake_request(method, path, **kw):
        sent.append(dict(kw["json"]))
        if kw["json"]["type"] == "limit":
            raise RuntimeError("422 fractional orders must be market")
        return {"id": "1", "status": "new"}

    monkeypatch.setattr(b, "_request", fake_request)
    order = b.submit_order("BKNG", 0.612, "buy", limit_price=5025.0)
    assert [p["type"] for p in sent] == ["limit", "market"] and sent[1]["qty"] == "0.612"
    assert order.order_type == "market" and sent[0]["client_order_id"] != sent[1]["client_order_id"]
