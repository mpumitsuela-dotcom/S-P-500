"""
Tradovate client for the futures agent (Tradovate is NinjaTrader's futures
brokerage). Demo (paper) environment only: https://demo.tradovateapi.com/v1

Getting API access needs a funded live Tradovate account (minimum $1,000) with
the "API Access" add-on ($25/month); the keys it creates then work against the
free demo account, which is what this agent trades. See FUTURES_AGENT.md.

Settings (GitHub secrets):
  TRADOVATE_USERNAME, TRADOVATE_PASSWORD   the Tradovate login
  TRADOVATE_CID, TRADOVATE_SECRET          the API key's client id and secret
  TRADOVATE_APP_ID                         the API key's application name (default "futures-agent")
  TRADOVATE_DEVICE_ID                      any fixed id for this "device" (default below)
  TRADOVATE_BASE_URL                       defaults to demo; anything else is refused

Docs: https://api.tradovate.com
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("futures_agent.tradovate")

DEMO_URL = "https://demo.tradovateapi.com/v1"
DEFAULT_DEVICE_ID = "5c2f7e1a-futures-agent-github-actions"

_token: dict = {}
_account: dict = {}
_contract_names: dict[int, str] = {}


class NotDemoAccount(RuntimeError):
    pass


class TradovateError(RuntimeError):
    pass


class _Transient(RuntimeError):
    pass


def base_url() -> str:
    return os.environ.get("TRADOVATE_BASE_URL", DEMO_URL).rstrip("/")


def configured() -> bool:
    return all(os.environ.get(k) for k in ("TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_CID", "TRADOVATE_SECRET"))


def ensure_demo() -> None:
    """This agent is paper-only. There is no switch to make it trade real money."""
    if "demo.tradovateapi.com" not in base_url():
        raise NotDemoAccount(f"TRADOVATE_BASE_URL must be the demo environment ({DEMO_URL}), got {base_url()!r}")


def reset() -> None:
    """Forget the cached login and account (used by tests)."""
    _token.clear()
    _account.clear()
    _contract_names.clear()


def _authenticate() -> str:
    ensure_demo()
    body = {
        "name": os.environ.get("TRADOVATE_USERNAME", ""),
        "password": os.environ.get("TRADOVATE_PASSWORD", ""),
        "appId": os.environ.get("TRADOVATE_APP_ID", "futures-agent"),
        "appVersion": "1.0",
        "cid": os.environ.get("TRADOVATE_CID", ""),
        "sec": os.environ.get("TRADOVATE_SECRET", ""),
        "deviceId": os.environ.get("TRADOVATE_DEVICE_ID", DEFAULT_DEVICE_ID),
    }
    resp = requests.post(f"{base_url()}/auth/accesstokenrequest", json=body, timeout=30)
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400 or not data.get("accessToken"):
        # "p-ticket" means Tradovate wants a pause (or a captcha) after too many logins.
        detail = data.get("errorText") or ("login throttled (p-ticket), try again later" if data.get("p-ticket") else resp.text[:200])
        raise TradovateError(f"Tradovate login failed: {detail}")
    _token.update(token=data["accessToken"], got=time.monotonic())
    return data["accessToken"]


def _auth_header() -> dict:
    # Tokens last 90 minutes; a run takes a few, so one login per run.
    if not _token or time.monotonic() - _token["got"] > 75 * 60:
        _authenticate()
    return {"Authorization": f"Bearer {_token['token']}", "Accept": "application/json"}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    retry=retry_if_exception_type((_Transient, requests.ConnectionError, requests.Timeout)),
    reraise=True,
)
def _request(method: str, path: str, **kwargs):
    ensure_demo()
    resp = requests.request(method, f"{base_url()}{path}", headers=_auth_header(), timeout=30, **kwargs)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _Transient(f"Tradovate {resp.status_code} on {path}")
    if resp.status_code >= 400:
        raise TradovateError(f"Tradovate {resp.status_code} on {method} {path}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


# --- account ------------------------------------------------------------

def account() -> dict:
    """The demo account: {"id", "name"}. The first active account on the login."""
    if not _account:
        accounts = [a for a in _request("GET", "/account/list") or [] if a.get("active", True)]
        if not accounts:
            raise TradovateError("no active Tradovate account on this login")
        _account.update(id=accounts[0]["id"], name=accounts[0]["name"])
    return _account


def get_equity() -> float:
    """Account value: cash plus open profit/loss."""
    snap = _request("POST", "/cashBalance/getCashBalanceSnapshot", json={"accountId": account()["id"]}) or {}
    if snap.get("netLiq") is not None:
        return float(snap["netLiq"])
    return float(snap.get("totalCashValue") or 0) + float(snap.get("openPnL") or 0)


def contract_name(contract_id: int) -> str:
    if contract_id not in _contract_names:
        _contract_names[contract_id] = (_request("GET", "/contract/item", params={"id": contract_id}) or {}).get("name", str(contract_id))
    return _contract_names[contract_id]


def get_positions() -> list[dict]:
    """Open positions: [{contract_id, symbol, qty (negative = short), avg_price}]."""
    out = []
    for p in _request("GET", "/position/list") or []:
        if p.get("accountId") != account()["id"] or not p.get("netPos"):
            continue
        out.append({
            "contract_id": p["contractId"], "symbol": contract_name(p["contractId"]),
            "qty": int(p["netPos"]), "avg_price": float(p.get("netPrice") or 0),
        })
    return out


def working_orders(contract_id: int | None = None) -> list[dict]:
    return [
        o for o in _request("GET", "/order/list") or []
        if o.get("accountId") == account()["id"] and o.get("ordStatus") in ("Working", "PendingNew", "Suspended")
        and (contract_id is None or o.get("contractId") == contract_id)
    ]


# --- contracts ------------------------------------------------------------

def front_contract(root: str, today: date, min_days_to_expiry: int) -> dict:
    """The nearest contract for a product that isn't about to expire:
    {"id", "name", "expiration"}."""
    candidates = []
    for c in _request("GET", "/contract/suggest", params={"t": root, "l": 6}) or []:
        name = c.get("name", "")
        if not name.startswith(root) or len(name) > len(root) + 2:  # e.g. MESZ6 - skip spreads and options
            continue
        maturity = _request("GET", "/contractMaturity/item", params={"id": c["contractMaturityId"]}) or {}
        exp = str(maturity.get("expirationDate", ""))[:10]
        if not exp:
            continue
        if (date.fromisoformat(exp) - today).days >= min_days_to_expiry:
            candidates.append({"id": c["id"], "name": name, "expiration": exp})
    if not candidates:
        raise TradovateError(f"no {root} contract found with more than {min_days_to_expiry} days to expiry")
    return min(candidates, key=lambda c: c["expiration"])


# --- orders --------------------------------------------------------------

def _check(resp: dict, what: str) -> dict:
    if not resp or resp.get("failureReason") or not resp.get("orderId"):
        raise TradovateError(f"{what} refused: {resp.get('failureReason') if resp else ''} {resp.get('failureText', '') if resp else ''}".strip())
    return resp


def _order_base(symbol: str, action: str, qty: int) -> dict:
    a = account()
    return {"accountSpec": a["name"], "accountId": a["id"], "action": action, "symbol": symbol,
            "orderQty": int(qty), "isAutomated": True}


def market_order(symbol: str, action: str, qty: int) -> int:
    resp = _check(_request("POST", "/order/placeorder", json={**_order_base(symbol, action, qty), "orderType": "Market"}),
                  f"{action} {qty} {symbol}")
    logger.info("Market %s %d %s -> order %s", action, qty, symbol, resp["orderId"])
    return resp["orderId"]


def protect(symbol: str, exit_action: str, qty: int, stop_price: float, target_price: float) -> dict:
    """Stop-loss and profit-target orders resting at Tradovate, linked so that
    when one fills the other is cancelled (OCO)."""
    body = {
        **_order_base(symbol, exit_action, qty), "orderType": "Stop", "stopPrice": stop_price, "timeInForce": "GTC",
        "other": {"action": exit_action, "orderType": "Limit", "price": target_price, "timeInForce": "GTC"},
    }
    resp = _check(_request("POST", "/order/placeOCO", json=body), f"stop/target for {symbol}")
    logger.info("Protected %s: stop %.2f, target %.2f (orders %s/%s)", symbol, stop_price, target_price, resp.get("orderId"), resp.get("ocoId"))
    return resp


def order_status(order_id: int) -> str:
    return (_request("GET", "/order/item", params={"id": order_id}) or {}).get("ordStatus", "")


def fill_price(order_id: int) -> tuple[int, float]:
    """(filled quantity, average price) of an order."""
    fills = _request("GET", "/fill/deps", params={"masterid": order_id}) or []
    qty = sum(int(f.get("qty") or 0) for f in fills)
    if not qty:
        return 0, 0.0
    return qty, sum(float(f["price"]) * int(f.get("qty") or 0) for f in fills) / qty


def wait_for_fill(order_id: int, wait_seconds: int, poll: float = 2.0) -> tuple[int, float, str]:
    deadline = time.monotonic() + wait_seconds
    while True:
        status = order_status(order_id)
        if status in ("Filled", "Canceled", "Rejected", "Expired") or time.monotonic() >= deadline:
            qty, price = fill_price(order_id)
            return qty, price, status
        time.sleep(poll)


def cancel(order_id: int) -> None:
    try:
        _request("POST", "/order/cancelorder", json={"orderId": order_id})
    except TradovateError as exc:  # already filled or cancelled
        logger.info("Cancel %s: %s", order_id, exc)


def close_position(position: dict, wait_seconds: int) -> tuple[int, float, str]:
    """Cancel the position's stop/target, then close it with a market order."""
    for o in working_orders(position["contract_id"]):
        cancel(o["id"])
    action = "Sell" if position["qty"] > 0 else "Buy"
    order_id = market_order(position["symbol"], action, abs(position["qty"]))
    return wait_for_fill(order_id, wait_seconds)


def round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 6)


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
