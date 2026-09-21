"""
Local simulation broker used by the backtester. Applies a simple flat
slippage assumption (config.StrategyConfig.assumed_slippage_bps) on every
fill since Alpaca is commission-free but bid/ask spread and market impact
are real costs a backtest must not ignore.
"""
from __future__ import annotations

from config import STRATEGY
from execution.broker_base import Broker, Order, Position


class SimulationBroker(Broker):
    def __init__(self, starting_cash: float):
        self.cash = starting_cash
        self.positions: dict[str, Position] = {}
        self._order_seq = 0
        self.fills: list[dict] = []

    def get_account_equity(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    def get_positions(self) -> dict[str, Position]:
        return self.positions

    def mark_to_market(self, prices: dict[str, float]) -> None:
        for sym, pos in self.positions.items():
            if sym in prices:
                pos.current_price = prices[sym]

    def is_market_open(self) -> bool:
        return True  # backtester controls the clock; always "open" when called

    def submit_order(self, symbol: str, qty: int, side: str, price: float | None = None) -> Order:
        if qty <= 0 or price is None:
            raise ValueError("SimulationBroker.submit_order requires qty>0 and an execution price")

        slippage = price * (STRATEGY.assumed_slippage_bps / 10_000.0)
        fill_price = price + slippage if side == "buy" else price - slippage

        self._order_seq += 1
        order = Order(symbol=symbol, qty=qty, side=side, id=f"sim-{self._order_seq}", status="filled")

        cost = fill_price * qty
        if side == "buy":
            if cost > self.cash:
                order.status = "rejected"
                return order
            self.cash -= cost
            existing = self.positions.get(symbol)
            if existing:
                total_qty = existing.qty + qty
                existing.avg_entry_price = (existing.avg_entry_price * existing.qty + cost) / total_qty
                existing.qty = total_qty
                existing.current_price = price
            else:
                self.positions[symbol] = Position(symbol, qty, fill_price, price)
        else:  # sell
            existing = self.positions.get(symbol)
            if not existing or existing.qty < qty:
                order.status = "rejected"
                return order
            self.cash += cost
            existing.qty -= qty
            existing.current_price = price
            if existing.qty == 0:
                del self.positions[symbol]

        self.fills.append({"symbol": symbol, "side": side, "qty": qty, "price": fill_price})
        return order
