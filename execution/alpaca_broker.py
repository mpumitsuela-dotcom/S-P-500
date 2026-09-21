"""
Alpaca REST adapter for order execution. Defaults to the PAPER endpoint;
see execution/guards.py check_not_live_unless_triple_confirmed for how
switching to live is deliberately made inconvenient (three independent
things must all agree: env var URL, env var confirmation flag, and this
class's allow_live constructor argument).
"""
from __future__ import annotations

import logging

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import API_KEYS, LIVE_TRADING_CONFIRM_VALUE, LIVE_TRADING_ENV_FLAG
from execution.broker_base import Broker, Order, Position

logger = logging.getLogger(__name__)


class LiveTradingNotConfirmedError(Exception):
    pass


class AlpacaBroker(Broker):
    def __init__(self, allow_live: bool = False):
        import os

        is_live_url = "paper" not in API_KEYS.alpaca_base_url
        confirmed_env = os.environ.get(LIVE_TRADING_ENV_FLAG) == LIVE_TRADING_CONFIRM_VALUE
        if is_live_url and not (allow_live and confirmed_env):
            raise LiveTradingNotConfirmedError(
                "Refusing to trade against a non-paper Alpaca endpoint. This requires "
                "ALPACA_BASE_URL to be the paper URL, OR all three of: the live URL, "
                f"{LIVE_TRADING_ENV_FLAG}={LIVE_TRADING_CONFIRM_VALUE}, and allow_live=True "
                "passed explicitly in code. This is intentional - see execution/guards.py."
            )
        if not API_KEYS.alpaca_key_id or not API_KEYS.alpaca_secret_key:
            raise RuntimeError("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set - see .env.example")

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": API_KEYS.alpaca_key_id,
            "APCA-API-SECRET-KEY": API_KEYS.alpaca_secret_key,
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = requests.request(method, f"{API_KEYS.alpaca_base_url}{path}", headers=self._headers(), timeout=20, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def get_account_equity(self) -> float:
        account = self._request("GET", "/v2/account")
        return float(account["equity"])

    def get_positions(self) -> dict[str, Position]:
        raw = self._request("GET", "/v2/positions")
        out = {}
        for p in raw:
            out[p["symbol"]] = Position(
                symbol=p["symbol"],
                qty=int(float(p["qty"])),
                avg_entry_price=float(p["avg_entry_price"]),
                current_price=float(p["current_price"]),
            )
        return out

    def is_market_open(self) -> bool:
        clock = self._request("GET", "/v2/clock")
        return bool(clock.get("is_open"))

    def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> pd.Series:
        """
        Daily equity curve, most-recent last - feeds
        execution/guards.py check_drawdown_halt (previously computed but
        never actually wired into either scheduled session; now used by
        both, see scheduler/run_morning.py and run_afternoon.py). Returns an
        empty Series (guard passes trivially - "no equity history yet") on
        any error rather than raising, since a halted drawdown check isn't
        allowed to itself block a run.
        """
        try:
            payload = self._request(
                "GET", "/v2/account/portfolio/history", params={"period": period, "timeframe": timeframe}
            )
            equity = payload.get("equity") or []
            timestamps = payload.get("timestamp") or []
            series = pd.Series(equity, index=pd.to_datetime(timestamps, unit="s"))
            return series.dropna().sort_index()  # Alpaca returns ascending already; sort defensively so "current" (iloc[-1]) is reliably the latest day
        except Exception:  # noqa: BLE001 - a history-fetch failure must not block the run itself
            logger.warning("Could not fetch portfolio history for drawdown check", exc_info=True)
            return pd.Series(dtype=float)

    def submit_order(self, symbol: str, qty: int, side: str) -> Order:
        if qty <= 0:
            raise ValueError("qty must be positive")
        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        resp = self._request("POST", "/v2/orders", json=payload)
        logger.info("Submitted %s %s x%d -> order id %s, status %s", side, symbol, qty, resp.get("id"), resp.get("status"))
        return Order(symbol=symbol, qty=qty, side=side, id=resp.get("id"), status=resp.get("status", "unknown"))

    def cancel_all_open_orders(self) -> None:
        self._request("DELETE", "/v2/orders")
