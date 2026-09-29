"""Options agent rules - no network calls."""
from __future__ import annotations

from datetime import date, datetime, time as dtime

import numpy as np
import pandas as pd
import pytest

from options_agent import broker as alp
from options_agent import gemini_research, journal, report, signals, strategy
from options_agent.run import NY, due_sessions
from options_agent.settings import OptionsSettings

S = OptionsSettings()
TODAY = date(2026, 10, 1)


def test_parse_occ():
    o = alp.parse_occ("AAPL261120C00230000")
    assert (o.underlying, o.expiration, o.kind, o.strike) == ("AAPL", date(2026, 11, 20), "call", 230.0)
    assert alp.parse_occ("XOM261120P00112500").strike == 112.5
    with pytest.raises(ValueError):
        alp.parse_occ("AAPL")


def test_tick_round():
    assert alp.tick_round(3.12, up=True) == 3.15
    assert alp.tick_round(3.12, up=False) == 3.10
    assert alp.tick_round(1.234, up=True) == 1.24
    assert alp.tick_round(2.50, up=True) == 2.50


def test_parse_verdict_handles_fences_and_bad_values():
    v = gemini_research.parse_verdict(
        'Here you go:\n```json\n{"direction": "Bullish", "conviction": "140", "thesis": "Strong demand.", '
        '"next_earnings_date": "soon", "risks": ["x"]}\n```'
    )
    assert v["direction"] == "bullish" and v["conviction"] == 100 and v["next_earnings_date"] is None
    assert gemini_research.parse_verdict('{"direction": "up?"}')["direction"] == "neutral"
    with pytest.raises(ValueError):
        gemini_research.parse_verdict("no json here")


def test_gemini_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(gemini_research.GeminiUnavailable):
        gemini_research.research("AAPL", "Apple", TODAY, {})


def _closes(drift: float, n: int = 120, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.cumprod(1 + rng.normal(drift, 0.01, n)))


def test_direction_score_follows_trend_and_skips_missing_research():
    up = signals.price_features(_closes(0.006))
    down = signals.price_features(_closes(-0.006))
    s_up = signals.direction_score(up, None, 0, None)
    s_down = signals.direction_score(down, None, 0, None)
    assert s_up["score"] > 0.3 and s_down["score"] < -0.3
    assert set(s_up["parts"]) == {"trend", "momentum"}
    with_news = signals.direction_score(up, -5, 4, -1.0)
    assert with_news["score"] < s_up["score"] and "news" in with_news["parts"]
    assert signals.price_features(_closes(0.0, n=30)) is None


def _row(sym, score, rsi=55, dte_earn=None):
    return {"symbol": sym, "score": score, "rsi14": rsi, "days_to_earnings": dte_earn, "earnings_date": "2026-10-05"}


def test_pick_candidates_filters():
    rows = [_row("A", 0.8), _row("B", -0.7), _row("C", 0.6, rsi=80), _row("D", 0.6, dte_earn=3), _row("E", 0.5), _row("F", 0.1)]
    picked, skipped = strategy.pick_candidates(rows, {"E"}, S)
    assert [r["symbol"] for r in picked] == ["A", "B"]
    reasons = {s["symbol"]: s["reason"] for s in skipped}
    assert "overbought" in reasons["C"] and "earnings" in reasons["D"] and "already holding" in reasons["E"]
    assert "F" not in reasons


def _verdict(direction, conviction, earnings=None):
    return {"direction": direction, "conviction": conviction, "thesis": "t", "next_earnings_date": earnings}


def test_entry_needs_agreement_and_conviction():
    row = _row("A", 0.6)
    up = {"trend": "up"}
    assert strategy.entry_decision(row, _verdict("bullish", 70), up, S, TODAY)[0]
    assert not strategy.entry_decision(row, _verdict("bearish", 90), up, S, TODAY)[0]
    assert not strategy.entry_decision(row, _verdict("bullish", 50), up, S, TODAY)[0]
    # Against the market needs more conviction.
    assert not strategy.entry_decision(row, _verdict("bullish", 70), {"trend": "down"}, S, TODAY)[0]
    assert strategy.entry_decision(row, _verdict("bullish", 80), {"trend": "down"}, S, TODAY)[0]
    # Gemini found earnings soon.
    assert not strategy.entry_decision(row, _verdict("bullish", 90, "2026-10-04"), up, S, TODAY)[0]


def _c(sym, strike, bid, ask, delta=None, oi=500, exp="2026-11-20"):
    return {"symbol": sym, "strike": strike, "expiration": exp, "bid": bid, "ask": ask, "open_interest": oi, "delta": delta, "iv": None}


def test_choose_contract_prefers_target_delta_and_respects_limits():
    contracts = [_c("C1", 95, 7.0, 7.3, 0.70), _c("C2", 100, 4.0, 4.2, 0.55), _c("C3", 110, 1.0, 1.1, 0.25), _c("C4", 100, 4.0, 4.1, 0.55, oi=5)]
    pick, _ = strategy.choose_contract(contracts, 100, "call", TODAY, 800, 0.3, S)
    assert pick.symbol == "C2"
    # Too expensive for a $300 limit, and the cheap one is out of the delta range.
    pick, why = strategy.choose_contract(contracts, 100, "call", TODAY, 300, 0.3, S)
    assert pick is None and "too expensive" in why
    # Wide spread rejected.
    pick, why = strategy.choose_contract([_c("W", 100, 3.0, 4.0, 0.5)], 100, "call", TODAY, 800, 0.3, S)
    assert pick is None and "spread" in why


def test_choose_contract_estimates_delta_without_greeks():
    pick, _ = strategy.choose_contract([_c("P1", 100, 4.0, 4.2)], 100, "put", TODAY, 800, 0.3, S)
    assert pick is not None and 0.3 <= pick.delta <= 0.6


def test_bs_delta_signs():
    assert 0.5 < strategy.bs_delta(100, 100, 0.12, 0.3, "call") < 0.6
    assert -0.5 < strategy.bs_delta(100, 100, 0.12, 0.3, "put") < -0.4


def test_contracts_to_buy_limits():
    assert strategy.contracts_to_buy(2.0, 10_000, 0, S) == (4, "")  # $800 per trade / $200
    assert strategy.contracts_to_buy(2.0, 10_000, 3_700, S)[0] == 1  # only $300 of the $4,000 total left
    qty, why = strategy.contracts_to_buy(9.0, 10_000, 0, S)
    assert qty == 0 and "$900" in why


def test_limit_prices():
    assert strategy.entry_limit_price(4.0, 4.2) == 4.15
    assert strategy.exit_limit_price(4.0, 4.2, urgent=True) == 4.0
    assert strategy.exit_limit_price(4.0, 4.2, urgent=False) == 4.05
    assert strategy.exit_limit_price(0.0, 0.05, urgent=True) == 0.01


def _held(**kw):
    base = dict(symbol="X", underlying="X", kind="call", qty=1, entry_price=2.0, current_price=2.0, dte=40)
    base.update(kw)
    return strategy.HeldPosition(**base)


@pytest.mark.parametrize(
    "kw,final,expect",
    [
        ({}, False, None),
        ({}, True, "run ends"),
        ({"current_price": 1.0}, False, "stop loss"),
        ({"days_to_earnings": 1}, False, "earnings"),
        ({"dte": 10}, False, "expiry"),
        ({"current_price": 3.3}, False, "take profit"),
        ({"current_price": 2.2, "peak_gain": 0.5}, False, "trailing"),
        ({"current_score": -0.4}, False, "research flipped"),
        ({"kind": "put", "current_score": -0.4}, False, None),
        ({"research_against": "Gemini now bearish"}, False, "research flipped"),
    ],
)
def test_exit_rules(kw, final, expect):
    reason, _ = strategy.exit_decision(_held(**kw), S, final)
    assert (reason is None) if expect is None else (expect in reason)


def test_due_sessions():
    def at(h, m):
        return datetime(2026, 10, 1, h, m, tzinfo=NY)

    assert due_sessions(at(9, 50), True, {}, "none") == ["manage"]
    assert due_sessions(at(11, 5), True, {}, "none") == ["research", "manage"]
    assert due_sessions(at(11, 20), True, {"last_research": "2026-10-01"}, "none") == ["manage"]
    assert due_sessions(at(11, 5), False, {}, "none") == []  # holiday
    assert due_sessions(at(16, 20), False, {"last_manage": "2026-10-01"}, "none") == ["report"]
    assert due_sessions(at(16, 20), False, {}, "none") == []
    assert due_sessions(at(11, 5), True, {"finished": "2026-11-30"}, "none") == []
    assert due_sessions(at(20, 0), False, {}, "research") == ["research", "manage"]


def test_journal_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "DIR", tmp_path)
    info = journal.run_info(TODAY, 10_000.0, 61)
    assert info["end_date"] == "2026-12-01"
    assert journal.run_info(date(2026, 10, 9), 9_000.0, 61)["start_equity"] == 10_000.0
    journal.append("trades.jsonl", {"date": "2026-09-30", "x": 1})
    journal.append("trades.jsonl", {"date": "2026-10-01", "x": 2})
    assert [r["x"] for r in journal.read_lines("trades.jsonl", TODAY)] == [2]


def test_report_mentions_trades_and_sources():
    run = {"start_date": "2026-10-01", "start_equity": 10_000, "end_date": "2026-12-01"}
    positions = [{"underlying": "AAPL", "kind": "call", "strike": 230, "expiration": "2026-11-20", "qty": 2,
                  "entry_price": 4.0, "current_price": 5.0, "gain": 0.25, "dte": 50, "why": "strong demand"}]
    trades = [{"side": "buy", "label": "AAPL $230 call exp 2026-11-20", "filled_qty": 2, "fill_price": 4.0, "reason": "Bet on a rise."}]
    research = [{"type": "verdict", "symbol": "AAPL", "decision": "BOUGHT", "score": 0.6, "score_explained": "trend +0.8",
                 "gemini": {"model": "m", "direction": "bullish", "conviction": 75, "thesis": "iPhone.",
                            "sources": [{"title": "Reuters", "url": "https://example.com"}]}},
                {"type": "pass", "symbol": "TSLA", "reason": "earnings in 3 days"}]
    title, body = report.build(TODAY, run, 10_200, 10_000, positions, trades, research, 0.01)
    assert "+2.0%" in title
    assert "Bought 2 × AAPL" in body and "[Reuters](https://example.com)" in body and "TSLA: earnings" in body


# --- Tradier client (HTTP stubbed) ---------------------------------------

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.content, self.text = payload, status, b"x", str(payload)

    def json(self):
        return self._p


@pytest.fixture
def tradier(monkeypatch):
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "t")
    monkeypatch.setenv("TRADIER_ACCOUNT_ID", "VA1")
    monkeypatch.delenv("TRADIER_BASE_URL", raising=False)
    monkeypatch.setattr(alp, "MIN_SECONDS_BETWEEN_CALLS", 0)
    calls = []
    routes = {}

    def fake(method, url, **kw):
        calls.append((method, url, kw))
        for suffix, payload in routes.items():
            if url.endswith(suffix):
                return _Resp(payload)
        raise AssertionError(f"unexpected call {method} {url}")

    monkeypatch.setattr(alp.requests, "request", fake)
    return routes, calls


def test_refuses_live_tradier(monkeypatch):
    monkeypatch.setenv("TRADIER_BASE_URL", "https://api.tradier.com/v1")
    with pytest.raises(alp.NotPaperAccount):
        alp.ensure_paper()


def test_positions_single_and_empty(tradier):
    routes, _ = tradier
    routes["/accounts/VA1/positions"] = {"positions": {"position": {"symbol": "AAPL261120C00230000", "quantity": 2, "cost_basis": 830.0}}}
    pos = alp.get_option_positions()
    assert pos == [{"symbol": "AAPL261120C00230000", "qty": 2.0, "avg_entry_price": 4.15}]
    routes["/accounts/VA1/positions"] = {"positions": "null"}
    assert alp.get_option_positions() == []


def test_balances_margin_and_cash(tradier):
    routes, _ = tradier
    routes["/balances"] = {"balances": {"total_equity": 10000, "account_type": "margin", "margin": {"option_buying_power": 9000}}}
    assert alp.get_account()["option_buying_power"] == 9000
    routes["/balances"] = {"balances": {"total_equity": 10000, "account_type": "cash", "cash": {"cash_available": 7000}}}
    assert alp.get_account()["option_buying_power"] == 7000


def test_option_chain_filters_and_normalises(tradier):
    routes, _ = tradier
    routes["/markets/options/expirations"] = {"expirations": {"date": ["2026-10-09", "2026-11-06", "2026-11-20"]}}
    routes["/markets/options/chains"] = {"options": {"option": [
        {"symbol": "AAPL261120C00230000", "option_type": "call", "strike": 230, "expiration_date": "2026-11-20",
         "bid": 4.0, "ask": 4.2, "open_interest": 900, "greeks": {"delta": 0.52, "mid_iv": 0.28}},
        {"symbol": "AAPL261120P00230000", "option_type": "put", "strike": 230, "bid": 4.0, "ask": 4.2},
        {"symbol": "AAPL261120C00400000", "option_type": "call", "strike": 400, "bid": 0.1, "ask": 0.2},
    ]}}
    chain = alp.option_chain("AAPL", "call", date(2026, 10, 31), date(2026, 11, 30), 180, 280)
    assert [c["symbol"] for c in chain] == ["AAPL261120C00230000"] * 2  # two expirations in the window
    assert chain[0]["delta"] == 0.52 and chain[0]["open_interest"] == 900


def test_order_fill_and_unfilled_cancel(tradier, monkeypatch):
    routes, calls = tradier
    monkeypatch.setattr(alp.time, "sleep", lambda s: None)
    routes["/accounts/VA1/orders"] = {"order": {"id": 7, "status": "ok"}}
    routes["/accounts/VA1/orders/7"] = {"order": {"id": 7, "status": "filled", "exec_quantity": 2, "avg_fill_price": 4.1}}
    o = alp.submit_and_wait("AAPL261120C00230000", 2, "buy", 4.15, wait_seconds=5)
    assert o == {"id": 7, "status": "filled", "filled_qty": 2, "fill_price": 4.1}
    sent = calls[0][2]["data"]
    assert sent["side"] == "buy_to_open" and sent["symbol"] == "AAPL" and sent["option_symbol"] == "AAPL261120C00230000"

    routes["/accounts/VA1/orders/7"] = {"order": {"id": 7, "status": "open", "exec_quantity": 0}}
    o = alp.submit_and_wait("AAPL261120C00230000", 2, "sell", 4.0, wait_seconds=0)
    assert o["filled_qty"] == 0 and any(m == "DELETE" for m, _, _ in calls)


# --- a whole day, broker and research stubbed ----------------------------

def test_research_buys_when_data_and_gemini_agree_then_manage_takes_profit(tmp_path, monkeypatch):
    from options_agent import run

    monkeypatch.setattr(journal, "DIR", tmp_path)
    held: list[dict] = []
    orders: list[tuple] = []
    row = {"symbol": "AAPL", "score": 0.6, "score_parts": {"trend": 0.8, "momentum": 0.5}, "rsi14": 60, "price": 230.0,
           "vs_sma50_pct": 4, "return_5d_pct": 1, "return_20d_pct": 5, "annual_vol_pct": 28, "headlines": [], "analysts": {},
           "earnings_date": None, "days_to_earnings": None}
    monkeypatch.setattr(run.signals, "score_universe", lambda syms, today: (pd.DataFrame([row]), {"trend": "up"}))
    monkeypatch.setattr(run.signals, "company_names", lambda: {"AAPL": "Apple"})
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(run.gemini_research, "research", lambda *a: {**_verdict("bullish", 72), "thesis": "Demand strong.", "sources": [], "model": "m"})
    monkeypatch.setattr(run.alp, "option_chain", lambda *a, **k: [_c("AAPL261120C00230000", 230, 4.0, 4.2, 0.55)])
    quotes = {"AAPL261120C00230000": {"bid": 4.0, "ask": 4.2}}
    monkeypatch.setattr(run.alp, "get_quotes", lambda syms: quotes)
    monkeypatch.setattr(run.alp, "get_option_positions", lambda: held)

    def fill(symbol, qty, side, price, wait):
        orders.append((side, symbol, qty, price))
        if side == "buy":
            held.append({"symbol": symbol, "qty": qty, "avg_entry_price": price})
        else:
            held.clear()
        return {"id": len(orders), "status": "filled", "filled_qty": qty, "fill_price": price}

    monkeypatch.setattr(run.alp, "submit_and_wait", fill)
    account = {"equity": 10_000.0, "option_buying_power": 10_000.0}
    info = journal.run_info(TODAY, 10_000.0, 61)

    run.research(TODAY, info, 10_000.0, account, True, "")
    assert orders == [("buy", "AAPL261120C00230000", 1, 4.15)]  # $800 limit / $415 a contract
    notes = journal.load("positions.json", {})
    assert "Demand strong" in notes["AAPL261120C00230000"]["why"]
    assert any("BOUGHT" in r.get("decision", "") for r in journal.read_lines("research.jsonl"))

    run.manage(TODAY, final_day=False)  # nothing to do at the entry price
    assert len(orders) == 1
    quotes["AAPL261120C00230000"] = {"bid": 6.9, "ask": 7.1}  # +69%
    run.manage(TODAY, final_day=False)
    assert orders[-1][0] == "sell" and orders[-1][2] == 1
    assert "Take profit" in journal.read_lines("trades.jsonl")[-1]["reason"]


def test_research_makes_no_trade_without_gemini_key(tmp_path, monkeypatch):
    from options_agent import run

    monkeypatch.setattr(journal, "DIR", tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    row = {"symbol": "AAPL", "score": 0.6, "score_parts": {"trend": 0.8}, "rsi14": 60, "days_to_earnings": None}
    monkeypatch.setattr(run.signals, "score_universe", lambda syms, today: (pd.DataFrame([row]), {"trend": "up"}))
    monkeypatch.setattr(run.signals, "company_names", lambda: {})
    monkeypatch.setattr(run.alp, "get_option_positions", lambda: [])
    alerts = []
    monkeypatch.setattr(run, "alert", lambda key, *a, **k: alerts.append(key))
    monkeypatch.setattr(run.alp, "submit_and_wait", lambda *a: pytest.fail("must not trade"))
    run.research(TODAY, {}, 10_000.0, {"option_buying_power": 10_000}, True, "")
    assert "gemini_missing" in alerts
