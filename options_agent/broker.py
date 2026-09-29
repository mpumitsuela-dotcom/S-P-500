"""
Tradier brokerage client for the options agent: account, market clock,
option positions, price history, option chains (with greeks), quotes and
orders.

Paper trading uses Tradier's free sandbox (https://sandbox.tradier.com/v1),
whose market data is delayed 15 minutes. Docs: https://documentation.tradier.com/brokerage-api

Settings (GitHub secrets for the workflow, or a local .env):
  TRADIER_ACCESS_TOKEN   the sandbox access token
  TRADIER_ACCOUNT_ID     the sandbox account number (e.g. VA12345678)
  TRADIER_BASE_URL       defaults to the sandbox; this agent refuses anything else

The agent only ever BUYS to open (calls or puts) and SELLS to close, so it
never writes options and the most any position can lose is what it paid.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("options_agent.broker")

SANDBOX_URL = "https://sandbox.tradier.com/v1"
OCC_RE = re.compile(r"^([A-Z.]{1,6})(\d{6})([CP])(\d{8})$")


class NotPaperAccount(RuntimeError):
    pass


class _Transient(RuntimeError):
    pass


def token() -> str:
    return os.environ.get("TRADIER_ACCESS_TOKEN", "")


def account_id() -> str:
    return os.environ.get("TRADIER_ACCOUNT_ID", "")


def base_url() -> str:
    return os.environ.get("TRADIER_BASE_URL", SANDBOX_URL).rstrip("/")


def configured() -> bool:
    return bool(token() and account_id())


def ensure_paper() -> None:
    """This agent is paper-only. There is no switch to make it trade real money."""
    if "sandbox.tradier.com" not in base_url():
        raise NotPaperAccount(f"TRADIER_BASE_URL must be the sandbox ({SANDBOX_URL}), got {base_url()!r}")


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


def as_list(x) -> list:
    """Tradier returns a single object instead of a one-item list, and the
    string "null" instead of an empty list."""
    if not x or x == "null":
        return []
    return x if isinstance(x, list) else [x]


# The sandbox allows about 60 requests a minute; stay just under it.
MIN_SECONDS_BETWEEN_CALLS = float(os.environ.get("TRADIER_MIN_SECONDS_BETWEEN_CALLS", "1.05"))
_last_call = 0.0


def _throttle() -> None:
    global _last_call
    wait = _last_call + MIN_SECONDS_BETWEEN_CALLS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    retry=retry_if_exception_type((_Transient, requests.ConnectionError, requests.Timeout)),
    reraise=True,
)
def _request(method: str, path: str, **kwargs) -> dict:
    ensure_paper()
    _throttle()
    resp = requests.request(
        method, f"{base_url()}{path}",
        headers={"Authorization": f"Bearer {token()}", "Accept": "application/json"}, timeout=30, **kwargs,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _Transient(f"Tradier {resp.status_code} on {path}")
    if resp.status_code >= 400:
        raise RuntimeError(f"Tradier {resp.status_code} on {method} {path}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


# --- account ------------------------------------------------------------

def get_account() -> dict:
    """{"equity", "option_buying_power", "account_type"}."""
    b = _request("GET", f"/accounts/{account_id()}/balances").get("balances") or {}
    kind = b.get("account_type", "")
    if kind == "cash":
        bp = (b.get("cash") or {}).get("cash_available")
    else:
        bp = (b.get(kind) or b.get("margin") or {}).get("option_buying_power")
    return {
        "equity": float(b.get("total_equity") or 0),
        "option_buying_power": float(bp if bp is not None else b.get("total_cash") or 0),
        "account_type": kind,
    }


def get_clock() -> dict:
    c = _request("GET", "/markets/clock").get("clock") or {}
    return {"is_open": c.get("state") == "open", "state": c.get("state"), "description": c.get("description", "")}


def get_option_positions() -> list[dict]:
    """[{symbol, qty, avg_entry_price}] for option positions (price per share,
    i.e. per 1/100th of a contract, like a quote)."""
    payload = _request("GET", f"/accounts/{account_id()}/positions").get("positions")
    out = []
    for p in as_list((payload or {}).get("position") if isinstance(payload, dict) else payload):
        if not OCC_RE.match(p.get("symbol", "")):
            continue
        qty = float(p.get("quantity") or 0)
        if qty == 0:
            continue
        out.append({"symbol": p["symbol"], "qty": qty, "avg_entry_price": abs(float(p.get("cost_basis") or 0)) / (abs(qty) * 100)})
    return out


# --- market data ---------------------------------------------------------

def get_quotes(symbols: list[str], greeks: bool = False) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), 100):
        batch = symbols[i : i + 100]
        payload = _request("POST", "/markets/quotes", data={"symbols": ",".join(batch), "greeks": str(greeks).lower()})
        for q in as_list((payload.get("quotes") or {}).get("quote")):
            out[q["symbol"]] = q
    return out


def quote_of(q: dict) -> tuple[float, float]:
    return float((q or {}).get("bid") or 0), float((q or {}).get("ask") or 0)


def daily_closes(symbol: str, start: date, end: date) -> pd.Series:
    payload = _request("GET", "/markets/history", params={"symbol": symbol, "interval": "daily", "start": start.isoformat(), "end": end.isoformat()})
    days = as_list((payload.get("history") or {}).get("day") if isinstance(payload.get("history"), dict) else None)
    if not days:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(d["date"]): float(d["close"]) for d in days if d.get("close") is not None})
    return s.sort_index()


def option_chain(underlying: str, kind: str, exp_from: date, exp_to: date, strike_lo: float, strike_hi: float, max_expirations: int = 4) -> list[dict]:
    """Contracts with quotes and greeks, normalised to
    {symbol, strike, expiration, bid, ask, open_interest, delta, iv}."""
    payload = _request("GET", "/markets/options/expirations", params={"symbol": underlying})
    dates = [d for d in as_list((payload.get("expirations") or {}).get("date")) if exp_from.isoformat() <= d <= exp_to.isoformat()]
    if len(dates) > max_expirations:  # spread the picks across the window
        step = (len(dates) - 1) / (max_expirations - 1)
        dates = [dates[round(i * step)] for i in range(max_expirations)]
    out = []
    for exp in dates:
        chain = _request("GET", "/markets/options/chains", params={"symbol": underlying, "expiration": exp, "greeks": "true"})
        for o in as_list((chain.get("options") or {}).get("option")):
            if o.get("option_type") != kind:
                continue
            strike = float(o.get("strike") or 0)
            if not (strike_lo <= strike <= strike_hi):
                continue
            g = o.get("greeks") or {}
            out.append({
                "symbol": o["symbol"], "strike": strike, "expiration": o.get("expiration_date", exp),
                "bid": float(o.get("bid") or 0), "ask": float(o.get("ask") or 0),
                "open_interest": int(o.get("open_interest") or 0),
                "delta": g.get("delta"), "iv": g.get("mid_iv") or g.get("smv_vol"),
            })
    return out


# --- orders --------------------------------------------------------------

def tick_round(price: float, up: bool) -> float:
    """Option prices trade in $0.05 steps at $3 and above, $0.01 below."""
    tick = 0.05 if price >= 3 else 0.01
    steps = round(price / tick, 6)  # 4.05 / 0.05 is 80.999... in floating point
    steps = int(steps) + (1 if up and steps != int(steps) else 0)
    return round(max(steps, 1) * tick, 2)


def _normalise_order(o: dict) -> dict:
    return {
        "id": o.get("id"),
        "status": o.get("status"),
        "filled_qty": int(float(o.get("exec_quantity") or 0)),
        "fill_price": float(o.get("avg_fill_price") or 0),
    }


def submit(symbol: str, qty: int, side: str, limit_price: float) -> dict:
    occ = parse_occ(symbol)
    body = {
        "class": "option",
        "symbol": occ.underlying,
        "option_symbol": symbol,
        "side": "buy_to_open" if side == "buy" else "sell_to_close",
        "quantity": str(int(qty)),
        "type": "limit",
        "duration": "day",
        "price": f"{limit_price:.2f}",
    }
    resp = _request("POST", f"/accounts/{account_id()}/orders", data=body)
    order = resp.get("order") or {}
    if not order.get("id"):
        raise RuntimeError(f"Tradier did not accept the order for {symbol}: {resp}")
    logger.info("Submitted %s %d %s @ %.2f -> order %s", body["side"], qty, symbol, limit_price, order["id"])
    return order


def get_order(order_id) -> dict:
    return _normalise_order(_request("GET", f"/accounts/{account_id()}/orders/{order_id}").get("order") or {})


def cancel(order_id) -> None:
    try:
        _request("DELETE", f"/accounts/{account_id()}/orders/{order_id}")
    except RuntimeError as exc:  # already filled or cancelled
        logger.info("Cancel %s: %s", order_id, exc)


def submit_and_wait(symbol: str, qty: int, side: str, limit_price: float, wait_seconds: int, poll: float = 3.0) -> dict:
    """Place a limit order and wait for it. Anything unfilled at the end is
    cancelled, so no order is left working after the run. Returns
    {id, status, filled_qty, fill_price}."""
    order_id = submit(symbol, qty, side, limit_price)["id"]
    deadline = time.monotonic() + wait_seconds
    while True:
        order = get_order(order_id)
        if order["status"] in ("filled", "canceled", "expired", "rejected"):
            return order
        if time.monotonic() >= deadline:
            cancel(order_id)
            time.sleep(2)
            return get_order(order_id)
        time.sleep(poll)


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def lookback_start(today: date, days: int = 150) -> date:
    return today - timedelta(days=days)
