"""Decision records -> daily and 5-day reports."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from execution import daily_report, decisions
from execution.broker_base import Position
from execution.decisions import NY_TZ


@pytest.fixture
def report_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(decisions, "DECISIONS_FILE", tmp_path / "data" / "decisions.jsonl")
    monkeypatch.setattr(daily_report, "DAILY_DIR", tmp_path / "daily")
    monkeypatch.setattr(daily_report, "WEEKLY_DIR", tmp_path / "weekly")
    monkeypatch.setattr(daily_report, "EQUITY_FILE", tmp_path / "data" / "equity.jsonl")
    monkeypatch.setattr(daily_report, "_trial_start_equity", lambda: 100_000.0)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return tmp_path


class FakeBroker:
    def __init__(self, equity):
        self.equity = equity

    def get_account_equity(self):
        return self.equity

    def get_positions(self):
        return {"AAPL": Position("AAPL", 10, 300.0, 330.0)}


def _frames():
    scores = pd.DataFrame(
        {"combined_score": [1.42, -0.9], "rank": [7, 480], "value": [0.1, -0.5], "quality": [0.3, -0.2],
         "momentum": [1.85, -1.1], "low_vol": [0.2, np.nan], "research": [1.1, -0.8], "sector": ["Information Technology", "Energy"]},
        index=pd.Index(["AAPL", "XOM"], name="symbol"),
    )
    fundamentals = pd.DataFrame([{"symbol": "AAPL", "pe": 32.1, "pb": 45.0, "roe": 1.5, "gross_margin": 0.46, "debt_to_equity": 1.8}])
    research = pd.DataFrame([{
        "symbol": "AAPL", "news_sentiment_score": 6, "headline_count": 4,
        "top_headlines": [{"headline": "Apple beats estimates on record iPhone demand", "source": "Reuters",
                           "url": "https://example.com/a", "datetime": 1790000000, "score": 3}],
        "analyst_score": 1.2, "total_analysts": 11, "analyst_period": "2026-09-01",
        "analyst_breakdown": {"strongBuy": 6, "buy": 3, "hold": 2, "sell": 0, "strongSell": 0},
    }])
    dates = pd.date_range("2025-08-01", periods=300, freq="B")
    prices = pd.DataFrame({"date": dates, "symbol": "AAPL", "close": np.linspace(250, 330, 300)})
    return scores, fundamentals, research, prices


def _record_day(d: date):
    scores, fundamentals, research, prices = _frames()
    results = [
        {"symbol": "AAPL", "side": "buy", "qty": 10, "status": "accepted", "reason": "combined score +1.42; ranked #7"},
        {"symbol": "XOM", "side": "sell", "qty": 5, "status": "accepted", "reason": "dropped out of the top-ranked names"},
    ]
    snaps = {r["symbol"]: decisions.research_snapshot(r["symbol"], scores, fundamentals, research, prices) for r in results}
    now = datetime(d.year, d.month, d.day, 9, 45, tzinfo=NY_TZ)
    decisions.record_trades("AM", results, snaps, pd.Series({"AAPL": 330.0, "XOM": 110.0}), now=now)
    decisions.record_session("AM", "completed", "2 order(s) placed", now=now)


def test_snapshot_captures_research_and_cleans_nan(report_dirs):
    scores, fundamentals, research, prices = _frames()
    snap = decisions.research_snapshot("AAPL", scores, fundamentals, research, prices)
    assert snap["factors"]["rank"] == 7
    assert snap["fundamentals"]["pe"] == 32.1
    assert snap["news"]["top_headlines"][0]["source"] == "Reuters"
    assert snap["analysts"]["breakdown"]["strongBuy"] == 6
    assert snap["price"]["return_12m_ex_1m"] > 0
    assert decisions.research_snapshot("XOM", scores)["factors"]["low_vol"] is None  # NaN -> None


def test_daily_report_explains_buys_and_sells(report_dirs, monkeypatch):
    d = date(2026, 9, 24)
    _record_day(d)
    monkeypatch.setattr(daily_report, "_spy_closes", lambda s, e: {"2026-09-23": 500.0, "2026-09-24": 505.0})
    assert daily_report.run_end_of_day(d, broker=FakeBroker(101_500.0)) == 0

    text = (report_dirs / "daily" / "2026-09-24.md").read_text()
    assert "# Daily trading report — Thursday 24 September 2026" in text
    assert "+1.5% today" in text and "S&P 500 (SPY) today:** +1.0%" in text
    buy_part = text.split("## Why I bought")[1].split("## Why I sold")[0]
    assert "AAPL — bought 10 shares" in buy_part
    assert "[Apple beats estimates on record iPhone demand](https://example.com/a)" in buy_part
    assert "6 strong buy, 3 buy, 2 hold" in buy_part
    assert "P/E 32.1" in buy_part and "return on equity 150.0%" in buy_part
    sell_part = text.split("## Why I sold")[1]
    assert "XOM — sold 5 shares" in sell_part
    assert "not collected for this stock" in sell_part
    assert not (report_dirs / "weekly").exists()


def test_five_day_report_on_every_fifth_trading_day(report_dirs, monkeypatch):
    monkeypatch.setattr(daily_report, "_spy_closes", lambda s, e: {})
    days = [date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]
    for i, d in enumerate(days):
        _record_day(d)
        daily_report.run_end_of_day(d, broker=FakeBroker(100_000.0 + 500 * (i + 1)))

    weekly = list((report_dirs / "weekly").glob("*.md"))
    assert [p.name for p in weekly] == ["5-day-ending-2026-09-30.md"]
    text = weekly[0].read_text()
    assert "Trading days 1–5 of the trial" in text
    assert "$100,000 → $102,500 (+2.5%)" in text
    assert "Main reason behind buys:** Momentum (5)" in text
    assert "Sectors bought:** Information Technology (5)" in text
    assert text.count("AAPL — bought 10 shares") == 5


def test_report_posted_as_issue_when_token_present(report_dirs, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(daily_report, "_spy_closes", lambda s, e: {})
    posted = []

    class Resp:
        status_code = 201

        def raise_for_status(self):
            pass

        def json(self):
            return {"html_url": "https://github.com/owner/repo/issues/1"}

    monkeypatch.setattr(daily_report.requests, "post", lambda url, **kw: posted.append((url, kw["json"])) or Resp())
    _record_day(date(2026, 9, 24))
    assert daily_report.run_end_of_day(date(2026, 9, 24), broker=FakeBroker(100_000.0)) == 0
    url, payload = posted[0]
    assert url == "https://api.github.com/repos/owner/repo/issues"
    assert payload["title"] == "Daily trading report — Thu 24 Sep 2026"
    assert payload["labels"] == ["daily-report"]
    assert "blob/agent-state/reports/daily/2026-09-24.md" in payload["body"]


def test_no_trade_day_explains_why(report_dirs, monkeypatch):
    monkeypatch.setattr(daily_report, "_spy_closes", lambda s, e: {})
    d = date(2026, 9, 24)
    now = datetime(2026, 9, 24, 9, 45, tzinfo=NY_TZ)
    decisions.record_session("AM", "halted", "Safety check stopped the session: data_freshness: stale", now=now)
    daily_report.run_end_of_day(d, broker=FakeBroker(100_000.0))
    text = (report_dirs / "daily" / "2026-09-24.md").read_text()
    assert "Morning rebalance:** HALTED — Safety check stopped the session: data_freshness: stale" in text
    assert "No stocks were bought." in text
