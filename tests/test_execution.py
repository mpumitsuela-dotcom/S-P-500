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


def _leverage_case():
    # $100k account, fully invested in OLD1..OLD3 ($30k, $30k, $38k) with $2k cash.
    positions = {
        "OLD1": Position("OLD1", 300, 100.0, 100.0),
        "OLD2": Position("OLD2", 300, 100.0, 100.0),
        "OLD3": Position("OLD3", 380, 100.0, 100.0),
    }
    prices = pd.Series({"OLD1": 100.0, "OLD2": 100.0, "OLD3": 100.0, "NEW1": 100.0, "NEW2": 100.0})
    # New target: all new names, big weights (biggest drifts are the buys).
    target = pd.DataFrame({"symbol": ["NEW1", "NEW2"], "target_weight": [0.49, 0.49]})
    return positions, prices, target


def test_rebalance_never_buys_more_than_cash_plus_sell_proceeds():
    positions, prices, target = _leverage_case()
    orders = compute_rebalance_orders(target, positions, 100_000, prices, max_turnover=0.35,
                                      fractionable={"NEW1", "NEW2", "OLD1", "OLD2", "OLD3"})
    sells = sum(o.qty * prices[o.symbol] for o in orders if o.side == "sell")
    buys = sum(o.qty * prices[o.symbol] for o in orders if o.side == "buy")
    cash = 100_000 - sum(p.market_value for p in positions.values())
    assert buys <= cash + sells + 1e-6
    assert sells > 0  # the turnover budget is not all spent on buys


def test_rebalance_makes_no_buys_while_account_is_borrowing():
    positions, prices, target = _leverage_case()
    positions["OLD3"] = Position("OLD3", 600, 100.0, 100.0)  # holdings now exceed equity: cash is negative
    orders = compute_rebalance_orders(target, positions, 100_000, prices, max_turnover=0.05,
                                      fractionable={"NEW1", "NEW2", "OLD1", "OLD2", "OLD3"})
    assert all(o.side == "sell" for o in orders)


def test_rebalance_spends_idle_cash_even_when_many_sells_are_planned():
    # $100k account, $30k cash, three holdings to sell down, two names to buy.
    positions = {f"OLD{i}": Position(f"OLD{i}", 200, 100.0, 100.0) for i in range(1, 4)}  # $20k each = $60k; plus 10k other
    positions["KEEP"] = Position("KEEP", 100, 100.0, 100.0)  # $10k held, stays
    prices = pd.Series({"OLD1": 100.0, "OLD2": 100.0, "OLD3": 100.0, "KEEP": 100.0, "NEW1": 100.0, "NEW2": 100.0})
    target = pd.DataFrame({"symbol": ["KEEP", "NEW1", "NEW2"], "target_weight": [0.10, 0.44, 0.44]})
    frac = {"OLD1", "OLD2", "OLD3", "KEEP", "NEW1", "NEW2"}
    orders = compute_rebalance_orders(target, positions, 100_000, prices, max_turnover=0.35, fractionable=frac)
    buys = sum(o.qty * prices[o.symbol] for o in orders if o.side == "buy")
    sells = sum(o.qty * prices[o.symbol] for o in orders if o.side == "sell")
    cash = 100_000 - sum(p.market_value for p in positions.values())  # $30k
    assert buys >= cash - 0.02 * 100_000 - 1.0   # the idle cash (minus the 2% buffer) is put to work first
    assert buys <= cash + sells + 1e-6       # and still never more than cash + sale proceeds
    assert (buys + sells) / 100_000 <= 0.35 + 1e-9


def test_rebalance_keeps_the_cash_buffer_when_spending_idle_cash():
    from config import STRATEGY

    positions = {"KEEP": Position("KEEP", 900, 100.0, 100.0)}  # $90k held, $10k cash
    prices = pd.Series({"KEEP": 100.0, "NEW1": 100.0})
    target = pd.DataFrame({"symbol": ["KEEP", "NEW1"], "target_weight": [0.90, 0.10]})
    orders = compute_rebalance_orders(target, positions, 100_000, prices, max_turnover=1.0, fractionable={"KEEP", "NEW1"})
    buys = sum(o.qty * prices[o.symbol] for o in orders if o.side == "buy")
    assert buys <= 10_000 - STRATEGY.cash_buffer * 100_000 + 1e-6
