"""
Alpaca REST adapter for order execution. Defaults to the PAPER endpoint;
see execution/guards.py check_not_live_unless_triple_confirmed for how
switching to live is deliberately made inconvenient (three independent
things must all agree: env var URL, env var confirmation flag, and this
class's allow_live constructor argument).
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import API_KEYS, LIVE_TRADING_CONFIRM_VALUE, LIVE_TRADING_ENV_FLAG
from execution.broker_base import Broker, Order, Position, format_qty

logger = logging.getLogger(__name__)

AGENT_ORDER_PREFIX = "sp500agent-"


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
                qty=float(p["qty"]),
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

    def submit_order(self, symbol: str, qty: float, side: str, limit_price: float | None = None) -> Order:
        """A market order, or - with limit_price - a day limit order. The agent
        uses marketable limits (a little through the current price) so a fast
        market can't fill it at a much worse price than the one it planned on."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        payload = {
            "symbol": symbol,
            "qty": format_qty(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
            # Tags the order as this agent's, so execution/order_audit.py can
            # tell its orders apart from anything else trading the account.
            "client_order_id": f"{AGENT_ORDER_PREFIX}{uuid.uuid4().hex[:20]}",
        }
        if limit_price is not None:
            payload["type"] = "limit"
            payload["limit_price"] = f"{limit_price:.2f}" if limit_price >= 1 else f"{limit_price:.4f}"
        try:
            resp = self._request("POST", "/v2/orders", json=payload)
        except Exception:
            if payload["type"] != "limit" or float(qty).is_integer():
                raise
            # A fractional quantity with a limit price was refused: send the same
            # fractional quantity as a market order (always allowed) rather than
            # not trading at all.
            logger.warning("Fractional limit order for %s refused - resending as a market order", symbol, exc_info=True)
            payload.pop("limit_price")
            payload["type"] = "market"
            payload["client_order_id"] = f"{AGENT_ORDER_PREFIX}{uuid.uuid4().hex[:20]}"
            resp = self._request("POST", "/v2/orders", json=payload)
        logger.info("Submitted %s %s x%s (%s) -> order id %s, status %s", side, symbol, payload["qty"], payload["type"], resp.get("id"), resp.get("status"))
        return Order(symbol=symbol, qty=qty, side=side, order_type=payload["type"], id=resp.get("id"), status=resp.get("status", "unknown"))

    FINAL_STATUSES = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced", "stopped", "suspended"}

    def wait_for_orders(self, order_ids: list[str], timeout: float = 90, poll: float = 3) -> dict[str, dict]:
        """Poll until every order is in a final state or the timeout passes.
        Returns {order id: latest order JSON}. Never raises."""
        latest: dict[str, dict] = {}
        pending = [i for i in order_ids if i]
        deadline = time.monotonic() + timeout
        while pending:
            for oid in list(pending):
                try:
                    latest[oid] = self._request("GET", f"/v2/orders/{oid}")
                except Exception:  # noqa: BLE001
                    logger.warning("Could not check order %s", oid, exc_info=True)
                    continue
                if latest[oid].get("status") in self.FINAL_STATUSES:
                    pending.remove(oid)
            if not pending or time.monotonic() > deadline:
                break
            time.sleep(poll)
        return latest

    def cancel_order(self, order_id: str) -> None:
        try:
            self._request("DELETE", f"/v2/orders/{order_id}")
        except Exception:  # noqa: BLE001 - it may have filled in the meantime
            logger.warning("Could not cancel order %s", order_id, exc_info=True)

    def fractionable_symbols(self) -> set[str]:
        """US stocks Alpaca lets us trade in fractions. Empty on error, which
        just means whole shares for everything this run."""
        try:
            assets = self._request("GET", "/v2/assets", params={"status": "active", "asset_class": "us_equity"})
            return {a["symbol"] for a in assets if a.get("fractionable") and a.get("tradable")}
        except Exception:  # noqa: BLE001
            logger.warning("Could not load fractionable assets - using whole shares this run", exc_info=True)
            return set()

    def get_orders(self, after: datetime, limit: int = 500) -> list[dict]:
        """Every order (any status) submitted after `after`, newest first."""
        return self._request(
            "GET", "/v2/orders",
            params={"status": "all", "after": after.astimezone(timezone.utc).isoformat(), "limit": limit, "direction": "desc"},
        )

    def cancel_all_open_orders(self) -> None:
        self._request("DELETE", "/v2/orders")
