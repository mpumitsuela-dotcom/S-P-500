"""Common interface both brokers (simulation and Alpaca) implement."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Position:
    symbol: str
    qty: float  # fractional shares are allowed (Alpaca supports them for most stocks)
    avg_entry_price: float
    current_price: float
    lastday_price: float | None = None  # previous close, for "today's change"

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price


@dataclass
class Order:
    symbol: str
    qty: float
    side: str  # "buy" | "sell"
    order_type: str = "market"
    time_in_force: str = "day"
    id: str | None = None
    status: str = "new"


class Broker(ABC):
    @abstractmethod
    def get_account_equity(self) -> float: ...

    @abstractmethod
    def get_positions(self) -> dict[str, Position]: ...

    @abstractmethod
    def submit_order(self, symbol: str, qty: float, side: str) -> Order: ...

    @abstractmethod
    def is_market_open(self) -> bool: ...


def format_qty(qty: float) -> str:
    """Share count for people and for the broker API: whole numbers without
    decimals, fractions to at most 4 decimal places."""
    return str(int(qty)) if float(qty).is_integer() else f"{qty:.4f}".rstrip("0").rstrip(".")
