"""
Diffs current broker positions against a target portfolio and produces the
minimal set of orders to close the gap, subject to a turnover cap and a
minimum-drift threshold so the system doesn't churn tiny rebalances that
would just bleed out edge to slippage.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import STRATEGY
from execution.broker_base import Broker, Position


@dataclass
class PlannedOrder:
    symbol: str
    side: str
    qty: int
    reason: str


def compute_rebalance_orders(
    target: pd.DataFrame,  # [symbol, target_weight] or [symbol, target_shares]
    current_positions: dict[str, Position],
    account_equity: float,
    latest_prices: pd.Series,
    min_drift: float | None = None,
    max_turnover: float | None = None,
) -> list[PlannedOrder]:
    min_drift = min_drift if min_drift is not None else STRATEGY.min_rebalance_drift
    max_turnover = max_turnover if max_turnover is not None else STRATEGY.max_turnover_per_rebalance

    target_weight = target.set_index("symbol")["target_weight"] if "target_weight" in target.columns else None
    current_weight = pd.Series(
        {sym: pos.market_value / account_equity for sym, pos in current_positions.items() if account_equity > 0}
    )

    all_symbols = set(target_weight.index if target_weight is not None else []) | set(current_weight.index)
    drift = pd.Series(
        {
            s: (target_weight.get(s, 0.0) if target_weight is not None else 0.0) - current_weight.get(s, 0.0)
            for s in all_symbols
        }
    )

    material = drift[drift.abs() >= min_drift]
    turnover_used = 0.0
    orders: list[PlannedOrder] = []

    for symbol, d in material.reindex(material.abs().sort_values(ascending=False).index).items():
        if turnover_used >= max_turnover:
            break
        price = latest_prices.get(symbol)
        if not price or price <= 0:
            continue

        dollar_amount = d * account_equity
        remaining_turnover_budget = (max_turnover - turnover_used) * account_equity
        if abs(dollar_amount) > remaining_turnover_budget:
            dollar_amount = remaining_turnover_budget if dollar_amount > 0 else -remaining_turnover_budget

        qty = int(abs(dollar_amount) // price)
        if qty <= 0:
            continue

        side = "buy" if d > 0 else "sell"
        # never sell more than currently held
        if side == "sell":
            held = current_positions.get(symbol)
            qty = min(qty, held.qty if held else 0)
            if qty <= 0:
                continue

        orders.append(PlannedOrder(symbol=symbol, side=side, qty=qty, reason=f"drift {d:+.2%}"))
        turnover_used += (qty * price) / account_equity

    return orders


def execute_orders(broker: Broker, orders: list[PlannedOrder], latest_prices: pd.Series | None = None) -> list[dict]:
    """
    Executes sells before buys (frees up cash first). For the SimulationBroker,
    which needs an execution price, latest_prices must be supplied; the
    AlpacaBroker.submit_order signature doesn't take a price (market order).
    """
    results = []
    ordered = sorted(orders, key=lambda o: 0 if o.side == "sell" else 1)
    for o in ordered:
        try:
            if latest_prices is not None:
                order = broker.submit_order(o.symbol, o.qty, o.side, price=latest_prices.get(o.symbol))
            else:
                order = broker.submit_order(o.symbol, o.qty, o.side)
            results.append({"symbol": o.symbol, "side": o.side, "qty": o.qty, "status": order.status, "reason": o.reason})
        except Exception as exc:  # noqa: BLE001 - one order failing must not abort the whole batch
            results.append({"symbol": o.symbol, "side": o.side, "qty": o.qty, "status": f"error: {exc}", "reason": o.reason})
    return results
