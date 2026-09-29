"""Futures agent rules and Tradovate client - no network calls."""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from futures_agent import strategy
from futures_agent import tradovate as tv
from futures_agent.settings import INSTRUMENTS, FuturesSettings
from options_agent import signals
from options_agent.journal import Journal

S = FuturesSettings()
TODAY = date(2026, 10, 1)
MES = INSTRUMENTS["MES"]


def _feats(drift):
    rng = np.random.default_rng(3)
    return signals.price_features(pd.Series(100 * np.cumprod(1 + rng.normal(drift, 0.008, 120))))


def test_market_score_direction_and_company_leg():
    up, down = strategy.market_score(_feats(0.004), None), strategy.market_score(_feats(-0.004), None)
    assert up["score"] > 0.3 and down["score"] < -0.3 and "companies" not in up["parts"]
    assert strategy.market_score(_feats(0.004), -0.5)["score"] < up["score"]


def _v(direction, conviction, event=False):
    return {"direction": direction, "conviction": conviction, "major_event_within_2_days": event, "key_events": ["CPI on Oct 2"]}


def test_entry_decision():
    assert strategy.entry_decision(0.5, _v("bullish", 70), S)[0]
    assert strategy.entry_decision(-0.5, _v("bearish", 70), S)[0]  # short
    assert not strategy.entry_decision(0.2, _v("bullish", 90), S)[0]
    assert not strategy.entry_decision(0.5, _v("bearish", 90), S)[0]
    assert not strategy.entry_decision(0.5, _v("bullish", 60), S)[0]
    ok, why = strategy.entry_decision(0.5, _v("bullish", 90, event=True), S)
    assert not ok and "CPI" in why


def test_plan_size_and_limits():
    # MES at 6,600 moving 0.9% a day: stop 1.5 x 0.9% = 89.1 pts -> $445.50 a contract, limit $500.
    plan = strategy.plan_size(6600, 0.009, MES, 10_000, 0, S)
    assert plan.qty == 1 and plan.stop_points == 89.0 and plan.risk_per_contract == 445.0
    assert strategy.plan_size(6600, 0.009, MES, 10_000, 1_000, S).qty == 0  # only $200 of the $1,200 total left
    nq = strategy.plan_size(24000, 0.012, INSTRUMENTS["MNQ"], 10_000, 0, S)
    assert nq.qty == 0 and "risks $864" in nq.reason


def test_stop_and_target_both_directions():
    assert strategy.stop_and_target(6600.0, True, 89.0, MES, S) == (6511.0, 6778.0)
    assert strategy.stop_and_target(6600.0, False, 89.0, MES, S) == (6689.0, 6422.0)
    assert strategy.round_to_tick(6600.13, 0.25) == 6600.25


@pytest.mark.parametrize("kw,expect", [
    ({}, None),
    ({"final_day": True}, "run ends"),
    ({"expiration": date(2026, 10, 5)}, "expires"),
    ({"opened": date(2026, 9, 15)}, "trading days"),
    ({"current_score": -0.4}, "research flipped"),
    ({"long": False, "current_score": -0.4}, None),
    ({"research_against": "Gemini now bearish"}, "research flipped"),
])
def test_exit_rules(kw, expect):
    args = dict(long=True, opened=date(2026, 9, 29), today=TODAY, expiration=date(2026, 12, 18), current_score=None,
                research_against=None, final_day=False)
    args.update(kw)
    reason = strategy.exit_decision(**args, s=S)
    assert (reason is None) if expect is None else (expect in reason)


def test_root_of():
    from futures_agent.run import root_of

    assert root_of("MESZ6") == "MES" and root_of("M2KZ6") == "M2K" and root_of("MCLX26") == "MCL"


def test_due_sessions():
    from futures_agent.run import NY, due_sessions

    at = lambda h, m: datetime(2026, 10, 1, h, m, tzinfo=NY)  # noqa: E731
    assert due_sessions(at(11, 5), True, {}, "none") == ["research", "manage"]
    assert due_sessions(at(14, 0), True, {}, "none") == ["manage"]
    assert due_sessions(at(11, 5), False, {}, "none") == []
    assert due_sessions(at(16, 20), False, {"last_manage": "2026-10-01"}, "none") == ["report"]
    assert due_sessions(at(11, 5), True, {"finished": "x"}, "none") == []


def test_prompt_asks_for_events():
    from futures_agent.research import build_prompt

    p = build_prompt(MES, TODAY, {"x": 1})
    assert "Micro S&P 500" in p and "major_event_within_2_days" in p and "SHORT" in p


# --- Tradovate client (HTTP stubbed) ---------------------------------------

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.content, self.text = payload, status, b"x", str(payload)

    def json(self):
        return self._p


@pytest.fixture
def api(monkeypatch):
    for k, v in {"TRADOVATE_USERNAME": "u", "TRADOVATE_PASSWORD": "p", "TRADOVATE_CID": "1", "TRADOVATE_SECRET": "s"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TRADOVATE_BASE_URL", raising=False)
    tv.reset()
    routes = {"/auth/accesstokenrequest": {"accessToken": "tok"}, "/account/list": [{"id": 7, "name": "DEMO123", "active": True}]}
    calls = []

    def fake(method, url, **kw):
        calls.append((method, url, kw))
        for suffix, payload in routes.items():
            if url.endswith(suffix):
                return _Resp(payload(kw) if callable(payload) else payload)
        raise AssertionError(f"unexpected call {method} {url}")

    monkeypatch.setattr(tv.requests, "request", fake)
    monkeypatch.setattr(tv.requests, "post", lambda url, **kw: fake("POST", url, **kw))
    monkeypatch.setattr(tv.time, "sleep", lambda s: None)
    yield routes, calls
    tv.reset()


def test_refuses_live_tradovate(monkeypatch):
    monkeypatch.setenv("TRADOVATE_BASE_URL", "https://live.tradovateapi.com/v1")
    with pytest.raises(tv.NotDemoAccount):
        tv.ensure_demo()


def test_login_failure_is_reported(api):
    routes, _ = api
    routes["/auth/accesstokenrequest"] = {"errorText": "Incorrect username or password"}
    with pytest.raises(tv.TradovateError, match="Incorrect"):
        tv.account()


def test_positions_and_equity(api):
    routes, _ = api
    routes["/position/list"] = [{"accountId": 7, "contractId": 55, "netPos": -1, "netPrice": 6600.25},
                                {"accountId": 7, "contractId": 56, "netPos": 0}, {"accountId": 8, "contractId": 57, "netPos": 2}]
    routes["/contract/item"] = {"id": 55, "name": "MESZ6"}
    assert tv.get_positions() == [{"contract_id": 55, "symbol": "MESZ6", "qty": -1, "avg_price": 6600.25}]
    routes["/cashBalance/getCashBalanceSnapshot"] = {"totalCashValue": 10_000, "openPnL": -125.5}
    assert tv.get_equity() == 9874.5


def test_front_contract_skips_near_expiry(api):
    routes, _ = api
    routes["/contract/suggest"] = [{"id": 1, "name": "MESZ6", "contractMaturityId": 11}, {"id": 2, "name": "MESH7", "contractMaturityId": 12},
                                   {"id": 3, "name": "MESZ6-MESH7", "contractMaturityId": 13}]
    routes["/contractMaturity/item"] = lambda kw: {11: {"expirationDate": "2026-10-05T00:00"}, 12: {"expirationDate": "2027-03-19T00:00"}}[kw["params"]["id"]]
    assert tv.front_contract("MES", TODAY, 10) == {"id": 2, "name": "MESH7", "expiration": "2027-03-19"}


def test_orders_protect_and_close(api):
    routes, calls = api
    routes["/order/placeorder"] = {"orderId": 900}
    routes["/order/item"] = {"id": 900, "ordStatus": "Filled"}
    routes["/fill/deps"] = [{"price": 6600.0, "qty": 1}, {"price": 6601.0, "qty": 1}]
    oid = tv.market_order("MESZ6", "Buy", 2)
    assert tv.wait_for_fill(oid, 5) == (2, 6600.5, "Filled")
    placed = [kw["json"] for m, u, kw in calls if u.endswith("/order/placeorder")][0]
    assert placed["accountSpec"] == "DEMO123" and placed["orderType"] == "Market" and placed["isAutomated"]

    routes["/order/placeOCO"] = {"orderId": 901, "ocoId": 902}
    tv.protect("MESZ6", "Sell", 2, 6511.0, 6778.0)
    oco = [kw["json"] for m, u, kw in calls if u.endswith("/order/placeOCO")][0]
    assert oco["orderType"] == "Stop" and oco["stopPrice"] == 6511.0 and oco["other"]["price"] == 6778.0

    routes["/order/list"] = [{"id": 901, "accountId": 7, "contractId": 55, "ordStatus": "Working"},
                             {"id": 903, "accountId": 7, "contractId": 99, "ordStatus": "Working"}]
    routes["/order/cancelorder"] = {}
    tv.close_position({"contract_id": 55, "symbol": "MESZ6", "qty": 2}, 5)
    cancels = [kw["json"]["orderId"] for m, u, kw in calls if u.endswith("/order/cancelorder")]
    assert cancels == [901]
    last = [kw["json"] for m, u, kw in calls if u.endswith("/order/placeorder")][-1]
    assert last["action"] == "Sell" and last["orderQty"] == 2


def test_refused_order_raises(api):
    routes, _ = api
    routes["/order/placeorder"] = {"failureReason": "RiskCheck", "failureText": "Insufficient margin"}
    with pytest.raises(tv.TradovateError, match="margin"):
        tv.market_order("MESZ6", "Buy", 1)


# --- a whole day, broker and research stubbed ----------------------------

def test_research_opens_protected_position_then_manage_detects_stop_out(tmp_path, monkeypatch):
    from futures_agent import run

    monkeypatch.setattr(run, "J", Journal(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    closes = {"SPY": pd.Series(100 * np.cumprod(1 + np.random.default_rng(3).normal(0.004, 0.008, 120)))}
    monkeypatch.setattr(run.rs, "fetch_closes", lambda syms, today: closes)
    monkeypatch.setattr(run.rs, "company_scores", lambda comps, cl: {})
    monkeypatch.setattr(run.rs, "research_market", lambda inst, today, facts: {**_v("bullish", 75), "thesis": "Earnings strong.", "model": "m", "sources": []})
    monkeypatch.setattr(run.rs, "futures_price", lambda root: 6600.0)
    held: list[dict] = []
    protects = []
    monkeypatch.setattr(run.tv, "get_positions", lambda: held)
    monkeypatch.setattr(run.tv, "front_contract", lambda root, today, d: {"id": 55, "name": f"{root}Z6", "expiration": "2026-12-18"})
    monkeypatch.setattr(run.tv, "market_order", lambda sym, action, qty: held.append({"contract_id": 55, "symbol": sym, "qty": qty, "avg_price": 6600.0}) or 1)
    monkeypatch.setattr(run.tv, "wait_for_fill", lambda oid, w: (held[-1]["qty"], 6600.0, "Filled"))
    monkeypatch.setattr(run.tv, "protect", lambda *a: protects.append(a))
    monkeypatch.setattr(run.tv, "working_orders", lambda cid=None: [{"id": 1}])
    monkeypatch.setattr(run, "INSTRUMENTS", {"MES": MES})
    monkeypatch.setattr(run, "S", FuturesSettings(instruments=("MES",)))

    run.research(TODAY, 10_000.0, "")
    assert [p["symbol"] for p in held] == ["MESZ6"] and held[0]["qty"] == 1
    assert protects and protects[0][1] == "Sell" and protects[0][3] < 6600 < protects[0][4]  # stop below, target above
    notes = run.J.load("positions.json", {})
    assert notes["MESZ6"]["long"] and "Earnings strong" in notes["MESZ6"]["why"]

    run.manage(TODAY, final_day=False)  # nothing due
    assert "MESZ6" in run.J.load("positions.json", {})
    held.clear()  # the stop filled at Tradovate overnight
    run.manage(TODAY, final_day=False)
    assert run.J.load("positions.json", {}) == {}
    assert "resting stop-loss or profit target" in run.J.read_lines("trades.jsonl")[-1]["reason"]


def test_report_lists_positions_and_research():
    from futures_agent.run import build_report

    run = {"start_date": "2026-10-01", "start_equity": 10_000, "end_date": "2026-12-01"}
    notes = {"MESZ6": {"stop": 6511.0, "target": 6778.0, "opened": "2026-10-01", "why": "strong earnings"}}
    positions = [{"symbol": "MESZ6", "qty": 1, "avg_price": 6600.0}]
    trades = [{"side": "buy", "qty": 1, "symbol": "MESZ6", "fill_price": 6600.0, "stop": 6511.0, "target": 6778.0, "reason": "Bought."}]
    research = [{"type": "verdict", "symbol": "MES", "decision": "LONG", "score": 0.5, "parts": {"trend": 0.8},
                 "gemini": {"model": "m", "direction": "bullish", "conviction": 75, "thesis": "t", "key_events": ["CPI Oct 10"],
                            "sources": [{"title": "Reuters", "url": "https://example.com"}]}}]
    title, body = build_report(TODAY, run, 10_150, 10_000, notes, positions, trades, research, False)
    assert "+1.5%" in title and "| MESZ6 | long | 1 |" in body and "CPI Oct 10" in body and "[Reuters]" in body
