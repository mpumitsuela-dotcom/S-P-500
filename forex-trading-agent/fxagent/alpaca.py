"""Alpaca REST client: trading (paper account only) + ETF market data."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from .config import Settings

log = logging.getLogger(__name__)
ORDER_PREFIX = "fxagent-"


class NotPaperError(RuntimeError):
    pass


class Alpaca:
    def __init__(self, settings: Settings):
        if not settings.is_paper:
            # Deliberate: this agent has no live-trading switch at all.
            raise NotPaperError(f"Refusing to run: ALPACA_BASE_URL is not the paper endpoint ({settings.alpaca_base_url})")
        if not settings.alpaca_key or not settings.alpaca_secret:
            raise RuntimeError("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are not set")
        self.s = settings
        self.session = requests.Session()
        self.session.headers.update({"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret})

    # ------------------------------------------------------------------ http
    def _req(self, method: str, url: str, **kw):
        last = None
        for attempt in range(3):
            try:
                r = self.session.request(method, url, timeout=20, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"{r.status_code}: {r.text[:200]}", response=r)
                if r.status_code >= 400:
                    # 4xx is a real rejection (bad order etc.) - don't retry
                    raise AlpacaError(r.status_code, r.text[:500])
                return r.json() if r.content else {}
            except requests.RequestException as exc:
                last = exc
                time.sleep(2 ** attempt)
        raise last  # type: ignore[misc]

    def _t(self, method: str, path: str, **kw):
        return self._req(method, f"{self.s.alpaca_base_url}{path}", **kw)

    def _d(self, path: str, **kw):
        return self._req("GET", f"{self.s.alpaca_data_url}{path}", **kw)

    # --------------------------------------------------------------- account
    def clock(self) -> dict:
        return self._t("GET", "/v2/clock")

    def account(self) -> dict:
        return self._t("GET", "/v2/account")

    def positions(self) -> dict[str, dict]:
        return {p["symbol"]: p for p in self._t("GET", "/v2/positions")}

    def open_orders(self) -> list[dict]:
        return self._t("GET", "/v2/orders", params={"status": "open", "nested": "false", "limit": 500})  # flat: bracket legs listed on their own

    def fills_since(self, after: datetime) -> list[dict]:
        return self._t("GET", "/v2/account/activities/FILL", params={"after": after.isoformat(), "direction": "asc", "page_size": 100})

    # ---------------------------------------------------------------- orders
    def bracket_buy(self, symbol: str, qty: int, limit_price: float, stop_price: float, target_price: float) -> dict:
        payload = {
            "symbol": symbol,
            "qty": str(int(qty)),
            "side": "buy",
            "type": "limit",
            "limit_price": f"{limit_price:.2f}",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{target_price:.2f}"},
            "stop_loss": {"stop_price": f"{stop_price:.2f}"},
            "client_order_id": f"{ORDER_PREFIX}{uuid.uuid4().hex[:24]}",
        }
        resp = self._t("POST", "/v2/orders", json=payload)
        log.info("BUY %s x%s limit %.2f stop %.2f target %.2f -> %s", symbol, qty, limit_price, stop_price, target_price, resp.get("status"))
        return resp

    def cancel_order(self, order_id: str) -> None:
        self._t("DELETE", f"/v2/orders/{order_id}")

    def cancel_all_orders(self) -> None:
        self._t("DELETE", "/v2/orders")

    def close_position(self, symbol: str) -> dict:
        return self._t("DELETE", f"/v2/positions/{symbol}")

    def close_all_positions(self) -> list:
        return self._t("DELETE", "/v2/positions", params={"cancel_orders": "true"})

    # ------------------------------------------------------------------ data
    def bars(self, symbols: list[str], timeframe: str, days: int) -> dict[str, pd.DataFrame]:
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "feed": "iex", "limit": 10000, "adjustment": "all"}
        raw: dict[str, list] = {s: [] for s in symbols}
        while True:
            data = self._d("/v2/stocks/bars", params=params)
            for sym, rows in (data.get("bars") or {}).items():
                raw.setdefault(sym, []).extend(rows)
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        out = {}
        for sym, rows in raw.items():
            if not rows:
                out[sym] = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
                continue
            df = pd.DataFrame(rows)
            df.index = pd.to_datetime(df["t"], utc=True)
            out[sym] = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})[["open", "high", "low", "close", "volume"]].astype(float)
        return out

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        data = self._d("/v2/stocks/quotes/latest", params={"symbols": ",".join(symbols), "feed": "iex"})
        return data.get("quotes") or {}


class AlpacaError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Alpaca {status}: {body}")
        self.status = status
