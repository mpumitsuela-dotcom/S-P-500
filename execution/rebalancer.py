"""
Diffs current broker positions against a target portfolio and produces the
minimal set of orders to close the gap, subject to a turnover cap and a
minimum-drift threshold so the system doesn't churn tiny rebalances that
would just bleed out edge to slippage.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd

from config import STRATEGY
from execution.broker_base import Broker, Position

logger = logging.getLogger(__name__)

MIN_ORDER_DOLLARS = 1.0  # Alpaca's minimum for a fractional order


def share_qty(dollars: float, price: float, fractional: bool) -> float:
    """Shares that `dollars` buys, rounded DOWN: to 3 decimals when the stock
    trades in fractions (so each holding gets its intended weight even for a
    $1,800 stock), otherwise to whole shares."""
    if price <= 0 or dollars <= 0:
        return 0
    raw = dollars / price
    return math.floor(raw * 1000) / 1000 if fractional else float(math.floor(raw))


@dataclass
class PlannedOrder:
    symbol: str
    side: str
    qty: float
    reason: str


def compute_rebalance_orders(
    target: pd.DataFrame,  # [symbol, target_weight] or [symbol, target_shares]
    current_positions: dict[str, Position],
    account_equity: float,
    latest_prices: pd.Series,
    min_drift: float | None = None,
    max_turnover: float | None = None,
    fractionable: set[str] | None = None,
) -> list[PlannedOrder]:
    """fractionable: symbols that may be traded in fractions (None = whole shares only)."""
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

        qty = share_qty(abs(dollar_amount), price, symbol in (fractionable or set()))
        if qty <= 0 or qty * price < MIN_ORDER_DOLLARS:
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


def _limit_price(side: str, quote: float | None, buffer: float | None) -> float | None:
    """A marketable limit: a little above the quote to buy, a little below to sell."""
    if not quote or quote <= 0 or not buffer:
        return None
    return quote * (1 + buffer) if side == "buy" else quote * (1 - buffer)


def execute_orders(
    broker: Broker,
    orders: list[PlannedOrder],
    latest_prices: pd.Series | None = None,
    *,
    quotes: pd.Series | None = None,
    limit_buffer: float | None = None,
    fill_timeout: float = 90,
) -> list[dict]:
    """
    Executes sells before buys. On a broker that can report fills (Alpaca):
      - orders are marketable limits (quote +/- limit_buffer) when quotes and
        a buffer are given;
      - the sells are confirmed filled before any buy is sent, so a buy is never
        placed against cash that a failed sell didn't free up;
      - anything still unfilled after fill_timeout is cancelled, and each
        result carries the final status and the quantity actually filled.
    The SimulationBroker (backtests) needs an execution price: pass latest_prices.
    """
    can_confirm = hasattr(broker, "wait_for_orders")
    results: list[dict] = []

    def submit(o: PlannedOrder) -> dict:
        try:
            if latest_prices is not None:
                order = broker.submit_order(o.symbol, o.qty, o.side, price=latest_prices.get(o.symbol))
            else:
                limit = _limit_price(o.side, quotes.get(o.symbol) if quotes is not None else None, limit_buffer)
                order = (broker.submit_order(o.symbol, o.qty, o.side, limit_price=limit) if limit
                         else broker.submit_order(o.symbol, o.qty, o.side))
            return {"symbol": o.symbol, "side": o.side, "qty": o.qty, "status": order.status, "reason": o.reason, "order_id": order.id}
        except Exception as exc:  # noqa: BLE001 - one order failing must not abort the whole batch
            return {"symbol": o.symbol, "side": o.side, "qty": o.qty, "status": f"error: {exc}", "reason": o.reason, "order_id": None}

    def confirm(batch: list[dict]) -> None:
        ids = [r["order_id"] for r in batch if r.get("order_id")]
        if not can_confirm or not ids:
            return
        latest = broker.wait_for_orders(ids, timeout=fill_timeout)
        for r in batch:
            info = latest.get(r.get("order_id") or "")
            if not info:
                continue
            status = info.get("status", r["status"])
            filled = float(info.get("filled_qty") or 0)
            if status not in broker.FINAL_STATUSES:  # still working after the timeout: stop it
                broker.cancel_order(r["order_id"])
                status = "partially_filled_then_cancelled" if filled else "unfilled_cancelled"
            r["status"], r["requested_qty"], r["qty"] = status, r["qty"], filled
            if info.get("filled_avg_price"):
                r["fill_price"] = float(info["filled_avg_price"])

    sells = [submit(o) for o in orders if o.side == "sell"]
    confirm(sells)
    unfilled = [r["symbol"] for r in sells if can_confirm and r["status"] != "filled"]
    if unfilled:
        logger.warning("Sells not fully filled before buying: %s", unfilled)
    buys = [submit(o) for o in orders if o.side != "sell"]
    confirm(buys)
    results = sells + buys
    return results
