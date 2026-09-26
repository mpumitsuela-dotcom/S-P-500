"""The AM and PM sessions, end to end with a fake broker and synthetic data,
write decision records the reports can use - and never let recording break trading."""
import json
from datetime import date

import pandas as pd
import pytest

import scheduler.run_afternoon as run_afternoon
import scheduler.run_morning as run_morning
from execution import decisions
from execution.broker_base import Order, Position
from execution.guards import GuardResult
from scripts.demo_backtest import make_synthetic_universe


class FakeBroker:
    def __init__(self, positions=None):
        self.positions = positions or {}
        self.orders = []

    def get_account_equity(self):
        return 100_000.0

    def get_positions(self):
        return self.positions

    def get_portfolio_history(self, *a, **k):
        return pd.Series([100_000.0, 100_000.0])

    def submit_order(self, symbol, qty, side):
        self.orders.append((symbol, qty, side))
        return Order(symbol, qty, side, status="accepted")


@pytest.fixture
def market(tmp_path, monkeypatch):
    prices, sector_map, _ = make_synthetic_universe(n_symbols=40, n_days=320)
    shift = pd.Timestamp(date.today()) - prices["date"].max()
    prices["date"] = prices["date"] + shift
    symbols = sorted(sector_map)
    last = prices.sort_values("date").groupby("symbol")["close"].last()
    quotes = pd.DataFrame({"symbol": last.index, "price": last.values})
    research = pd.DataFrame([
        {"symbol": s, "news_sentiment_score": 2, "headline_count": 3,
         "top_headlines": [{"headline": f"{s} wins contract", "source": "Reuters", "url": "", "datetime": None, "score": 2}],
         "analyst_score": 1.0, "total_analysts": 5,
         "analyst_breakdown": {"strongBuy": 2, "buy": 2, "hold": 1, "sell": 0, "strongSell": 0}}
        for s in symbols
    ])
    fundamentals = pd.DataFrame([
        {"symbol": s, "pe": 15 + i % 20, "pb": 2 + i % 5, "roe": 0.1 + (i % 7) / 50,
         "gross_margin": 0.3 + (i % 5) / 20, "debt_to_equity": 0.5 + (i % 4) / 4, "earnings_growth": None}
        for i, s in enumerate(symbols)
    ])

    monkeypatch.setattr(decisions, "DECISIONS_FILE", tmp_path / "decisions.jsonl")
    for mod in (run_morning, run_afternoon):
        monkeypatch.setattr(mod, "setup_logging", lambda name: __import__("logging").getLogger("test"))
        monkeypatch.setattr(mod, "get_or_init_trial_start", lambda today: today)
        monkeypatch.setattr(mod, "get_or_init_trial_start_equity", lambda eq: eq)
        monkeypatch.setattr(mod, "record_trades", lambda entries: None)  # keep trade_log.csv out of the repo
        monkeypatch.setattr(mod, "check_market_hours", lambda **k: GuardResult("market_hours", True, "test"))
        monkeypatch.setattr(mod, "check_kill_switch", lambda: GuardResult("kill_switch", True, "test"))
        monkeypatch.setattr(mod, "KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH")
    monkeypatch.setattr(run_morning, "load_universe", lambda: (pd.DataFrame({"symbol": symbols}), sector_map))
    monkeypatch.setattr(run_morning, "get_daily_bars", lambda syms, s, e: prices)
    monkeypatch.setattr(run_morning, "get_fundamentals", lambda syms, **k: fundamentals)
    monkeypatch.setattr(run_morning, "get_research_frame", lambda syms: research)
    for mod in (run_morning, run_afternoon):
        monkeypatch.setattr(mod, "get_latest_quotes", lambda syms: quotes[quotes["symbol"].isin(syms)])
    return tmp_path, symbols, research, quotes


def _records(tmp_path):
    return [json.loads(l) for l in (tmp_path / "decisions.jsonl").read_text().splitlines()]


def test_morning_session_records_trades_with_research(market, monkeypatch):
    tmp_path, _, _, _ = market
    broker = FakeBroker()
    monkeypatch.setattr(run_morning, "AlpacaBroker", lambda: broker)
    assert run_morning.main() == 0
    assert broker.orders, "expected the rebalance to place orders"

    recs = _records(tmp_path)
    trades = [r for r in recs if r["type"] == "trade"]
    assert len(trades) == len(broker.orders)
    t = trades[0]
    assert t["side"] == "buy" and t["price"] > 0
    assert t["research"]["factors"]["rank"] >= 1
    assert "pe" in t["research"]["fundamentals"]
    assert t["research"]["news"]["top_headlines"][0]["source"] == "Reuters"
    assert t["research"]["analysts"]["breakdown"]["strongBuy"] == 2
    session = [r for r in recs if r["type"] == "session"][-1]
    assert session["outcome"] == "completed" and session["target"]


def test_afternoon_trim_records_move_and_news(market, monkeypatch):
    tmp_path, symbols, _, quotes = market
    sym = symbols[0]
    price = float(quotes.set_index("symbol").loc[sym, "price"])
    broker = FakeBroker({sym: Position(sym, 10, price / 0.9, price)})  # down 10% since entry
    monkeypatch.setattr(run_afternoon, "AlpacaBroker", lambda: broker)
    sentiment = pd.DataFrame([{"symbol": sym, "headline_count": 4, "sentiment_score": -5, "flagged_negative": True,
                               "top_headlines": [{"headline": "Regulator opens probe", "source": "AP", "score": -3}]}])
    monkeypatch.setattr(run_afternoon, "get_news_sentiment_frame", lambda syms: sentiment)
    assert run_afternoon.main() == 0

    trade = [r for r in _records(tmp_path) if r["type"] == "trade"][0]
    assert trade["session"] == "PM" and trade["side"] == "sell" and trade["qty"] == 5
    assert trade["move_since_entry"] == pytest.approx(-0.1)
    assert trade["research"]["news"]["top_headlines"][0]["headline"] == "Regulator opens probe"


def test_recording_failure_never_breaks_trading(market, monkeypatch):
    broker = FakeBroker()
    monkeypatch.setattr(run_morning, "AlpacaBroker", lambda: broker)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(decisions, "_append", boom)
    assert run_morning.main() == 0
    assert broker.orders


def test_every_new_buy_is_researched_first(market, monkeypatch):
    """Names outside the budgeted research batch get researched before buying;
    a name whose research can't be fetched is never newly bought."""
    tmp_path, symbols, research, _ = market
    unresearchable = set(symbols[:10])
    partial = research[~research["symbol"].isin(symbols[:20])]  # budget pass skipped 20 names
    monkeypatch.setattr(run_morning, "get_research_frame", lambda syms, **k: partial)
    fetched = []

    def targeted(syms, max_new_symbols=None):
        fetched.append(set(syms))
        return research[research["symbol"].isin(set(syms) - unresearchable)]

    real = run_morning.get_research_frame
    monkeypatch.setattr(run_morning, "get_research_frame",
                        lambda syms, max_new_symbols=None: partial if max_new_symbols is None else targeted(syms))
    broker = FakeBroker()
    monkeypatch.setattr(run_morning, "AlpacaBroker", lambda: broker)
    assert run_morning.main() == 0

    assert fetched, "missing research should have been fetched for target names"
    bought = {sym for sym, _, side in broker.orders if side == "buy"}
    assert bought and not (bought & unresearchable)
    researched = set(partial["symbol"]) | set().union(*fetched) - unresearchable
    assert bought <= researched
    session = [r for r in _records(tmp_path) if r["type"] == "session"][-1]
    assert set(session["excluded_unresearched"]) <= unresearchable


def test_missing_fundamentals_alone_does_not_block_a_buy(market, monkeypatch):
    """FMP's free plan doesn't cover every stock; news + analyst research is what's required."""
    tmp_path, symbols, research, _ = market
    no_fmp = pd.DataFrame(columns=["symbol", "pe", "pb", "roe", "gross_margin", "debt_to_equity", "earnings_growth"])
    monkeypatch.setattr(run_morning, "get_fundamentals", lambda syms, **k: no_fmp)
    broker = FakeBroker()
    monkeypatch.setattr(run_morning, "AlpacaBroker", lambda: broker)
    assert run_morning.main() == 0
    assert len([o for o in broker.orders if o[2] == "buy"]) >= 5  # capped by the 35% per-session turnover limit
    session = [r for r in _records(tmp_path) if r["type"] == "session"][-1]
    assert session["excluded_unresearched"] == []
