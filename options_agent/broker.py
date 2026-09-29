"""
Alpaca client for the options agent: account, market clock, option
positions, price history, option chains (with greeks), quotes and orders.

It uses its OWN Alpaca paper account, separate from the S&P 500 agent's, so
the two agents never see or trade each other's positions:
  OPT_ALPACA_API_KEY_ID      paper key of the options account
  OPT_ALPACA_API_SECRET_KEY  its secret
  OPT_ALPACA_BASE_URL        defaults to the paper endpoint; anything else is refused

Options market data comes from Alpaca's free "indicative" feed. Docs:
  https://docs.alpaca.markets/docs/options-trading

The agent only ever BUYS to open (calls or puts) and SELLS to close, so it
never writes options and the most any position can lose is what it paid.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("options_agent.broker")

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
OCC_RE = re.compile(r"^([A-Z.]{1,6})(\d{6})([CP])(\d{8})$")


class NotPaperAccount(RuntimeError):
    pass


class _Transient(RuntimeError):
    pass


def key_id() -> str:
    return os.environ.get("OPT_ALPACA_API_KEY_ID", "")


def secret() -> str:
    return os.environ.get("OPT_ALPACA_API_SECRET_KEY", "")


def base_url() -> str:
    return os.environ.get("OPT_ALPACA_BASE_URL", PAPER_URL).rstrip("/")


def configured() -> bool:
    return bool(key_id() and secret())


def ensure_paper() -> None:
    """This agent is paper-only. There is no switch to make it trade real money."""
    if "paper-api.alpaca.markets" not in base_url():
        raise NotPaperAccount(f"OPT_ALPACA_BASE_URL must be the paper endpoint ({PAPER_URL}), got {base_url()!r}")


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


# Alpaca allows 200 requests a minute; stay well under it.
MIN_SECONDS_BETWEEN_CALLS = float(os.environ.get("OPT_ALPACA_MIN_SECONDS_BETWEEN_CALLS", "0.35"))
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
def _request(method: str, url: str, **kwargs):
    ensure_paper()
    _throttle()
    resp = requests.request(
        method, url, headers={"APCA-API-KEY-ID": key_id(), "APCA-API-SECRET-KEY": secret()}, timeout=30, **kwargs
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _Transient(f"Alpaca {resp.status_code} on {url}")
    if resp.status_code >= 400:
        raise RuntimeError(f"Alpaca {resp.status_code} on {method} {url}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


def _trading(method: str, path: str, **kwargs):
    return _request(method, f"{base_url()}{path}", **kwargs)


def _data(path: str, params: dict):
    return _request("GET", f"{DATA_URL}{path}", params=params)


# --- account ------------------------------------------------------------

def get_account() -> dict:
    """{"equity", "option_buying_power", "options_level"}."""
    a = _trading("GET", "/v2/account")
    bp = a.get("options_buying_power")
    return {
        "equity": float(a.get("equity") or 0),
        "option_buying_power": float(bp if bp is not None else a.get("buying_power") or 0),
        "options_level": a.get("options_trading_level"),
    }


def get_clock() -> dict:
    c = _trading("GET", "/v2/clock")
    return {"is_open": bool(c.get("is_open")), "description": f"open={c.get('is_open')} next_open={c.get('next_open')}"}


def get_option_positions() -> list[dict]:
    """[{symbol, qty, avg_entry_price}] (price per share, i.e. per 1/100th of a contract)."""
    out = []
    for p in _trading("GET", "/v2/positions") or []:
        if p.get("asset_class") != "us_option":
            continue
        qty = float(p.get("qty") or 0)
        if qty:
            out.append({"symbol": p["symbol"], "qty": qty, "avg_entry_price": float(p.get("avg_entry_price") or 0)})
    return out


# --- market data ---------------------------------------------------------

def _snapshots(symbols: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), 100):
        batch = symbols[i : i + 100]
        payload = _data("/v1beta1/options/snapshots", {"symbols": ",".join(batch), "feed": "indicative"})
        out.update(payload.get("snapshots") or {})
    return out


def get_quotes(symbols: list[str]) -> dict[str, dict]:
    """{option symbol: {bid, ask, last}}."""
    out = {}
    for sym, s in _snapshots(symbols).items():
        q, t = s.get("latestQuote") or {}, s.get("latestTrade") or {}
        out[sym] = {"bid": float(q.get("bp") or 0), "ask": float(q.get("ap") or 0), "last": float(t.get("p") or 0)}
    return out


def quote_of(q: dict) -> tuple[float, float]:
    return float((q or {}).get("bid") or 0), float((q or {}).get("ask") or 0)


def daily_closes(symbol: str, start: date, end: date) -> pd.Series:
    rows, token = {}, None
    while True:
        params = {"timeframe": "1Day", "start": start.isoformat(), "end": end.isoformat(), "feed": "iex", "limit": 10000}
        if token:
            params["page_token"] = token
        payload = _data(f"/v2/stocks/{symbol}/bars", params)
        for b in payload.get("bars") or []:
            rows[pd.Timestamp(b["t"][:10])] = float(b["c"])
        token = payload.get("next_page_token")
        if not token:
            break
    return pd.Series(rows, dtype=float).sort_index()


def option_chain(underlying: str, kind: str, exp_from: date, exp_to: date, strike_lo: float, strike_hi: float) -> list[dict]:
    """Contracts with quotes and greeks, normalised to
    {symbol, strike, expiration, bid, ask, open_interest, delta, iv}."""
    contracts, token = [], None
    while True:
        params = {
            "underlying_symbols": underlying, "type": kind, "status": "active",
            "expiration_date_gte": exp_from.isoformat(), "expiration_date_lte": exp_to.isoformat(),
            "strike_price_gte": f"{strike_lo:.2f}", "strike_price_lte": f"{strike_hi:.2f}", "limit": 1000,
        }
        if token:
            params["page_token"] = token
        payload = _trading("GET", "/v2/options/contracts", params=params)
        contracts.extend(c for c in payload.get("option_contracts") or [] if c.get("tradable", True))
        token = payload.get("next_page_token")
        if not token:
            break
    snaps = _snapshots([c["symbol"] for c in contracts]) if contracts else {}
    out = []
    for c in contracts:
        s = snaps.get(c["symbol"]) or {}
        q = s.get("latestQuote") or {}
        out.append({
            "symbol": c["symbol"], "strike": float(c["strike_price"]), "expiration": c["expiration_date"],
            "bid": float(q.get("bp") or 0), "ask": float(q.get("ap") or 0),
            "open_interest": int(float(c.get("open_interest") or 0)),
            "delta": (s.get("greeks") or {}).get("delta"), "iv": s.get("impliedVolatility"),
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
        "filled_qty": int(float(o.get("filled_qty") or 0)),
        "fill_price": float(o.get("filled_avg_price") or 0),
    }


def submit(symbol: str, qty: int, side: str, limit_price: float) -> dict:
    body = {
        "symbol": symbol,
        "qty": str(int(qty)),
        "side": side,
        "type": "limit",
        "limit_price": f"{limit_price:.2f}",
        "time_in_force": "day",
        "position_intent": "buy_to_open" if side == "buy" else "sell_to_close",
    }
    order = _trading("POST", "/v2/orders", json=body)
    if not order.get("id"):
        raise RuntimeError(f"Alpaca did not accept the order for {symbol}: {order}")
    logger.info("Submitted %s %d %s @ %.2f -> order %s", body["position_intent"], qty, symbol, limit_price, order["id"])
    return order


def get_order(order_id) -> dict:
    return _normalise_order(_trading("GET", f"/v2/orders/{order_id}"))


def cancel(order_id) -> None:
    try:
        _trading("DELETE", f"/v2/orders/{order_id}")
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
