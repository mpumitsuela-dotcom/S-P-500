import pandas as pd
import pytest

from execution.broker_base import Position
from execution.paper_broker import SimulationBroker
from execution.rebalancer import compute_rebalance_orders, execute_orders


def test_simulation_broker_buy_and_sell_roundtrip():
    broker = SimulationBroker(starting_cash=10_000)
    order = broker.submit_order("AAA", 10, "buy", price=100.0)
    assert order.status == "filled"
    assert broker.positions["AAA"].qty == 10
    assert broker.cash < 10_000  # cash spent + slippage

    order2 = broker.submit_order("AAA", 5, "sell", price=110.0)
    assert order2.status == "filled"
    assert broker.positions["AAA"].qty == 5


def test_simulation_broker_rejects_oversized_buy():
    broker = SimulationBroker(starting_cash=100)
    order = broker.submit_order("AAA", 10, "buy", price=100.0)  # needs $1000+, only have $100
    assert order.status == "rejected"


def test_simulation_broker_rejects_overselling():
    broker = SimulationBroker(starting_cash=10_000)
    order = broker.submit_order("AAA", 5, "sell", price=100.0)  # no position held
    assert order.status == "rejected"


def test_compute_rebalance_orders_generates_buys_from_flat():
    # max_turnover is deliberately high here to isolate "does it generate buys at all"
    # from the turnover-cap behavior, which is covered separately below.
    target = pd.DataFrame({"symbol": ["AAA", "BBB"], "target_weight": [0.5, 0.3]})
    orders = compute_rebalance_orders(
        target, current_positions={}, account_equity=10_000,
        latest_prices=pd.Series({"AAA": 100.0, "BBB": 50.0}), max_turnover=1.0,
    )
    sides = {o.symbol: o.side for o in orders}
    assert sides.get("AAA") == "buy"
    assert sides.get("BBB") == "buy"


def test_compute_rebalance_orders_respects_turnover_cap():
    # Default max_turnover (0.35) should throttle a from-flat buy of an 0.8-weight
    # target book rather than deploying it all in one session - by design, this
    # caps how much the book can change in a single rebalance.
    target = pd.DataFrame({"symbol": ["AAA", "BBB"], "target_weight": [0.5, 0.3]})
    orders = compute_rebalance_orders(
        target, current_positions={}, account_equity=10_000,
        latest_prices=pd.Series({"AAA": 100.0, "BBB": 50.0}),
    )
    total_dollars = sum(o.qty * (100.0 if o.symbol == "AAA" else 50.0) for o in orders)
    assert total_dollars <= 0.35 * 10_000 + 1e-6


def test_compute_rebalance_orders_ignores_small_drift():
    target = pd.DataFrame({"symbol": ["AAA"], "target_weight": [0.501]})
    current = {"AAA": Position("AAA", qty=50, avg_entry_price=100.0, current_price=100.0)}  # already ~50% held
    orders = compute_rebalance_orders(target, current, account_equity=10_000, latest_prices=pd.Series({"AAA": 100.0}), min_drift=0.05)
    assert orders == []


def test_compute_rebalance_orders_never_sells_more_than_held():
    target = pd.DataFrame({"symbol": ["AAA"], "target_weight": [0.0]})
    current = {"AAA": Position("AAA", qty=3, avg_entry_price=100.0, current_price=100.0)}
    orders = compute_rebalance_orders(target, current, account_equity=10_000, latest_prices=pd.Series({"AAA": 100.0}), min_drift=0.0)
    assert orders[0].qty <= 3


def test_execute_orders_sells_before_buys():
    broker = SimulationBroker(starting_cash=100)
    broker.positions["AAA"] = __import__("execution.broker_base", fromlist=["Position"]).Position("AAA", 5, 100.0, 100.0)
    from execution.rebalancer import PlannedOrder

    orders = [PlannedOrder("BBB", "buy", 1, "test"), PlannedOrder("AAA", "sell", 5, "test")]
    prices = pd.Series({"AAA": 100.0, "BBB": 100.0})
    results = execute_orders(broker, orders, latest_prices=prices)
    # sell of AAA must be processed (and succeed) before the buy of BBB is attempted
    assert results[0]["symbol"] == "AAA"
    assert results[0]["status"] == "filled"
