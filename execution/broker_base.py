"""Common interface both brokers (simulation and Alpaca) implement."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Position:
    symbol: str
    qty: int
    avg_entry_price: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price


@dataclass
class Order:
    symbol: str
    qty: int
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
    def submit_order(self, symbol: str, qty: int, side: str) -> Order: ...

    @abstractmethod
    def is_market_open(self) -> bool: ...
