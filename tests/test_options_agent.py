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


# --- Alpaca client (HTTP stubbed) ----------------------------------------

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.content, self.text = payload, status, b"x", str(payload)

    def json(self):
        return self._p


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("OPT_ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("OPT_ALPACA_API_SECRET_KEY", "s")
    monkeypatch.delenv("OPT_ALPACA_BASE_URL", raising=False)
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


def test_refuses_live_alpaca(monkeypatch):
    monkeypatch.setenv("OPT_ALPACA_BASE_URL", "https://api.alpaca.markets")
    with pytest.raises(alp.NotPaperAccount):
        alp.ensure_paper()


def test_uses_its_own_keys_not_the_stock_agents(api, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "stock-agent-key")
    monkeypatch.delenv("OPT_ALPACA_API_KEY_ID")
    assert not alp.configured()


def test_positions_only_options(api):
    routes, _ = api
    routes["/v2/positions"] = [
        {"symbol": "AAPL261120C00230000", "asset_class": "us_option", "qty": "2", "avg_entry_price": "4.15"},
        {"symbol": "AAPL", "asset_class": "us_equity", "qty": "10", "avg_entry_price": "230"},
    ]
    assert alp.get_option_positions() == [{"symbol": "AAPL261120C00230000", "qty": 2.0, "avg_entry_price": 4.15}]


def test_account(api):
    routes, _ = api
    routes["/v2/account"] = {"equity": "10000", "options_buying_power": "9000", "options_trading_level": 3}
    assert alp.get_account() == {"equity": 10000.0, "option_buying_power": 9000.0, "options_level": 3}


def test_option_chain_joins_contracts_and_quotes(api):
    routes, calls = api
    routes["/v2/options/contracts"] = {"option_contracts": [
        {"symbol": "AAPL261120C00230000", "strike_price": "230", "expiration_date": "2026-11-20", "open_interest": "900", "tradable": True},
        {"symbol": "AAPL261120C00240000", "strike_price": "240", "expiration_date": "2026-11-20", "tradable": False},
    ]}
    routes["/v1beta1/options/snapshots"] = {"snapshots": {"AAPL261120C00230000": {
        "latestQuote": {"bp": 4.0, "ap": 4.2}, "greeks": {"delta": 0.52}, "impliedVolatility": 0.28}}}
    chain = alp.option_chain("AAPL", "call", date(2026, 10, 31), date(2026, 11, 30), 180, 280)
    assert chain == [{"symbol": "AAPL261120C00230000", "strike": 230.0, "expiration": "2026-11-20", "bid": 4.0, "ask": 4.2,
                      "open_interest": 900, "delta": 0.52, "iv": 0.28}]
    assert calls[0][2]["params"]["type"] == "call"


def test_daily_closes(api):
    routes, _ = api
    routes["/v2/stocks/SPY/bars"] = {"bars": [{"t": "2026-09-28T04:00:00Z", "c": 600.0}, {"t": "2026-09-29T04:00:00Z", "c": 601.5}]}
    s = alp.daily_closes("SPY", date(2026, 9, 20), date(2026, 9, 29))
    assert list(s.values) == [600.0, 601.5]


def test_order_fill_and_unfilled_cancel(api, monkeypatch):
    routes, calls = api
    monkeypatch.setattr(alp.time, "sleep", lambda s: None)
    routes["/v2/orders"] = {"id": "o1", "status": "accepted"}
    routes["/v2/orders/o1"] = {"id": "o1", "status": "filled", "filled_qty": "2", "filled_avg_price": "4.10"}
    o = alp.submit_and_wait("AAPL261120C00230000", 2, "buy", 4.15, wait_seconds=5)
    assert o == {"id": "o1", "status": "filled", "filled_qty": 2, "fill_price": 4.1}
    sent = calls[0][2]["json"]
    assert sent["position_intent"] == "buy_to_open" and sent["type"] == "limit" and sent["limit_price"] == "4.15"

    routes["/v2/orders/o1"] = {"id": "o1", "status": "new", "filled_qty": "0"}
    o = alp.submit_and_wait("AAPL261120C00230000", 2, "sell", 4.0, wait_seconds=0)
    assert o["filled_qty"] == 0 and any(m == "DELETE" for m, _, _ in calls)
    posts = [kw["json"] for m, _, kw in calls if m == "POST"]
    assert posts[-1]["position_intent"] == "sell_to_close"


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


def test_rank_models_prefers_newest_flash():
    names = ["gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.1-pro-preview", "gemini-3.8-flash-lite",
             "gemini-3.8-flash-image", "text-embedding-004", "gemini-3.8-flash-tts"]
    assert gemini_research.rank_models(names) == [
        "gemini-3.8-flash", "gemini-2.5-flash", "gemini-3.1-pro-preview", "gemini-3.8-flash-lite"]


def test_gemini_falls_back_to_discovered_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(gemini_research, "MODELS", ["retired-model"])
    monkeypatch.setattr(gemini_research, "_working_model", [])
    monkeypatch.setattr(gemini_research, "discover_models", lambda: ["gemini-9-flash"])
    answer = {"candidates": [{"content": {"parts": [{"text": '{"direction": "bullish", "conviction": 70, "thesis": "x"}'}]}}]}

    def call(model, prompt):
        if model == "retired-model":
            raise LookupError("404")
        return answer

    monkeypatch.setattr(gemini_research, "_call", call)
    v = gemini_research.research("AAPL", "Apple", TODAY, {})
    assert v["model"] == "gemini-9-flash" and v["direction"] == "bullish"
    assert gemini_research._working_model == ["gemini-9-flash"]
