"""
Alpaca options client for the paper account: account, clock, option
positions, contract lists, quotes/greeks, and orders.

Options market data comes from Alpaca's free "indicative" feed. Docs:
  https://docs.alpaca.markets/docs/options-trading
  https://docs.alpaca.markets/reference/optionchain

The agent only ever BUYS to open (calls or puts) and SELLS to close, so it
never writes options and its most any position can lose is what it paid.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import API_KEYS

logger = logging.getLogger("options_agent.alpaca")

OCC_RE = re.compile(r"^([A-Z.]{1,6})(\d{6})([CP])(\d{8})$")


class NotPaperAccount(RuntimeError):
    pass


class _Transient(RuntimeError):
    pass


@dataclass(frozen=True)
class OccSymbol:
    underlying: str
    expiration: date
    kind: str  # "call" or "put"
    strike: float


def parse_occ(symbol: str) -> OccSymbol:
    m = OCC_RE.match(symbol)
    if not m:
        raise ValueError(f"not an option symbol: {symbol}")
    root, ymd, cp, strike = m.groups()
    exp = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    return OccSymbol(root, exp, "call" if cp == "C" else "put", int(strike) / 1000)


def _headers() -> dict:
    return {"APCA-API-KEY-ID": API_KEYS.alpaca_key_id, "APCA-API-SECRET-KEY": API_KEYS.alpaca_secret_key}


def ensure_paper() -> None:
    """This agent is paper-only. There is no switch to make it trade real money."""
    if "paper-api" not in API_KEYS.alpaca_base_url:
        raise NotPaperAccount(f"ALPACA_BASE_URL must be the paper endpoint, got {API_KEYS.alpaca_base_url!r}")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((_Transient, requests.ConnectionError, requests.Timeout)),
    reraise=True,
)
def _request(method: str, url: str, **kwargs):
    resp = requests.request(method, url, headers=_headers(), timeout=20, **kwargs)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _Transient(f"Alpaca {resp.status_code} on {url}")
    if resp.status_code >= 400:
        raise RuntimeError(f"Alpaca {resp.status_code} on {method} {url}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


def _trading(method: str, path: str, **kwargs):
    ensure_paper()
    return _request(method, f"{API_KEYS.alpaca_base_url}{path}", **kwargs)


def _data(path: str, params: dict):
    return _request("GET", f"{API_KEYS.alpaca_data_url}{path}", params=params)


# --- account ------------------------------------------------------------

def get_account() -> dict:
    return _trading("GET", "/v2/account")


def get_clock() -> dict:
    return _trading("GET", "/v2/clock")


def get_option_positions() -> list[dict]:
    return [p for p in _trading("GET", "/v2/positions") if p.get("asset_class") == "us_option"]


# --- contracts and quotes ------------------------------------------------

def list_contracts(underlying: str, kind: str, exp_from: date, exp_to: date, strike_lo: float, strike_hi: float) -> list[dict]:
    out, token = [], None
    while True:
        params = {
            "underlying_symbols": underlying,
            "type": kind,
            "status": "active",
            "expiration_date_gte": exp_from.isoformat(),
            "expiration_date_lte": exp_to.isoformat(),
            "strike_price_gte": f"{strike_lo:.2f}",
            "strike_price_lte": f"{strike_hi:.2f}",
            "limit": 1000,
        }
        if token:
            params["page_token"] = token
        payload = _trading("GET", "/v2/options/contracts", params=params)
        out.extend(payload.get("option_contracts") or [])
        token = payload.get("next_page_token")
        if not token:
            return out


def get_snapshots(symbols: list[str]) -> dict[str, dict]:
    """Latest quote, trade, greeks and implied volatility per option symbol."""
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), 100):
        batch = symbols[i : i + 100]
        payload = _data("/v1beta1/options/snapshots", {"symbols": ",".join(batch), "feed": "indicative"})
        out.update(payload.get("snapshots") or {})
    return out


def quote_of(snapshot: dict) -> tuple[float, float]:
    q = (snapshot or {}).get("latestQuote") or {}
    return float(q.get("bp") or 0), float(q.get("ap") or 0)


# --- orders --------------------------------------------------------------

def tick_round(price: float, up: bool) -> float:
    """Option prices trade in $0.05 steps at $3 and above, $0.01 below."""
    tick = 0.05 if price >= 3 else 0.01
    steps = round(price / tick, 6)  # 4.05 / 0.05 is 80.999... in floating point
    steps = int(steps) + (1 if up and steps != int(steps) else 0)
    return round(max(steps, 1) * tick, 2)


def submit(symbol: str, qty: int, side: str, limit_price: float) -> dict:
    intent = "buy_to_open" if side == "buy" else "sell_to_close"
    body = {
        "symbol": symbol,
        "qty": str(int(qty)),
        "side": side,
        "type": "limit",
        "limit_price": f"{limit_price:.2f}",
        "time_in_force": "day",
        "position_intent": intent,
    }
    order = _trading("POST", "/v2/orders", json=body)
    logger.info("Submitted %s %d %s @ %.2f (%s) -> %s", side, qty, symbol, limit_price, intent, order.get("id"))
    return order


def get_order(order_id: str) -> dict:
    return _trading("GET", f"/v2/orders/{order_id}")


def cancel(order_id: str) -> None:
    try:
        _trading("DELETE", f"/v2/orders/{order_id}")
    except RuntimeError as exc:  # already filled or cancelled
        logger.info("Cancel %s: %s", order_id, exc)


def submit_and_wait(symbol: str, qty: int, side: str, limit_price: float, wait_seconds: int, poll: float = 3.0) -> dict:
    """Place a limit order and wait for it. Anything unfilled at the end is
    cancelled, so no order is left working after the run. Returns the final
    order (status, filled_qty, filled_avg_price)."""
    order = submit(symbol, qty, side, limit_price)
    deadline = time.monotonic() + wait_seconds
    while True:
        order = get_order(order["id"])
        if order.get("status") in ("filled", "canceled", "expired", "rejected"):
            return order
        if time.monotonic() >= deadline:
            cancel(order["id"])
            time.sleep(1)
            return get_order(order["id"])
        time.sleep(poll)


def order_fill(order: dict) -> tuple[int, float]:
    qty = int(float(order.get("filled_qty") or 0))
    price = float(order.get("filled_avg_price") or 0)
    return qty, price


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
