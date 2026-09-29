"""Position sizing and the safety rules every trade must pass."""
from __future__ import annotations

import math
from dataclasses import dataclass

MIN_STOP_PCT = 0.0015   # never set a stop tighter than 0.15% (the spread alone could hit it)
MAX_STOP_PCT = 0.02


@dataclass
class OrderPlan:
    qty: int
    limit: float
    stop: float
    target: float
    risk_dollars: float


def plan_order(
    *,
    equity: float,
    buying_power: float,
    ask: float,
    atr_pct: float,
    risk_per_trade: float,
    max_position_pct: float,
    stop_atr_mult: float,
    target_atr_mult: float,
) -> OrderPlan | None:
    """Size a long trade so that hitting the stop loses about risk_per_trade of equity."""
    if ask <= 0 or equity <= 0:
        return None
    stop_pct = min(MAX_STOP_PCT, max(MIN_STOP_PCT, atr_pct * stop_atr_mult))
    target_pct = stop_pct * (target_atr_mult / stop_atr_mult)
    limit = round(ask * 1.0005, 2)             # a hair through the ask so it fills
    stop = round(limit * (1 - stop_pct), 2)
    target = round(limit * (1 + target_pct), 2)
    per_share_risk = limit - stop
    if per_share_risk <= 0:
        return None
    qty_risk = math.floor(equity * risk_per_trade / per_share_risk)
    qty_cap = math.floor(equity * max_position_pct / limit)
    qty_bp = math.floor(buying_power * 0.95 / limit)
    qty = min(qty_risk, qty_cap, qty_bp)
    if qty < 1:
        return None
    return OrderPlan(qty=qty, limit=limit, stop=stop, target=target, risk_dollars=round(qty * per_share_risk, 2))


def daily_loss_hit(equity: float, start_of_day_equity: float, limit: float) -> bool:
    if start_of_day_equity <= 0:
        return False
    return (equity / start_of_day_equity - 1) <= -abs(limit)


def spread_ok(bid: float, ask: float, max_spread_pct: float) -> bool:
    if bid <= 0 or ask <= 0 or ask < bid:
        return False
    return (ask - bid) / ((ask + bid) / 2) <= max_spread_pct
