import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from fxagent import report, risk, sentiment, signals
from fxagent.sources import calendar, fx_prices, news
from fxagent.sources.news import Headline


def bars(trend: float, n: int = 120, freq: str = "15min") -> pd.DataFrame:
    rng = np.random.default_rng(0)
    close = 1.10 * np.exp(np.cumsum(trend + rng.normal(0, 0.0005, n)))
    idx = pd.date_range("2026-09-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": close, "high": close * 1.0006, "low": close * 0.9994, "close": close}, index=idx)


def test_yahoo_parse_and_invert():
    payload = {"chart": {"result": [{"timestamp": [1, 2], "indicators": {"quote": [{"open": [150.0, 151.0], "high": [152.0, 152.0], "low": [149.0, 150.0], "close": [151.0, None]}]}}]}}
    df = fx_prices.parse_chart(payload, invert=True)
    assert len(df) == 1  # the row with a missing close is dropped
    assert abs(df["close"].iloc[0] - 1 / 151.0) < 1e-12
    assert df["high"].iloc[0] == 1 / 149.0  # old low becomes the new high


def test_uptrend_scores_bullish_and_downtrend_bearish():
    up = signals.price_components(bars(0.0004), bars(0.002, 150, "1D"))[0]
    down = signals.price_components(bars(-0.0004), bars(-0.002, 150, "1D"))[0]
    assert signals.blend(up) > 0.3
    assert signals.blend(down) < -0.3


def test_trade_ideas_usd_bearish_buys_udn():
    prices = {c: (bars(0.0004), bars(0.002, 150, "1D")) for c in ["EUR", "JPY", "GBP", "AUD", "CAD", "CHF"]}
    prices["USD"] = (bars(-0.0004), bars(-0.002, 150, "1D"))
    sigs = signals.build_signals(prices, {}, {})
    ideas = signals.trade_ideas(sigs, {}, 0.3)
    etfs = {i.etf for i in ideas}
    assert "UDN" in etfs and "UUP" not in etfs and "FXE" in etfs
    assert signals.held_view("UDN", sigs) > 0 and signals.held_view("UUP", sigs) < 0


def test_news_is_relative_to_dollar():
    views = {c: sentiment.CurrencyView() for c in sentiment.CURRENCY_CODES}
    views["EUR"] = sentiment.CurrencyView(score=0.5, headlines=3, reason="ECB hawkish")
    views["USD"] = sentiment.CurrencyView(score=0.5, headlines=3, reason="strong payrolls")
    sigs = signals.build_signals({}, views, {})
    assert sigs["EUR"].components["news"] == 0.0


def test_carry_component():
    sigs = signals.build_signals({}, {}, {"USD": 4.0, "EUR": 2.0, "JPY": 0.5})
    assert sigs["EUR"].components["carry"] < 0
    assert sigs["USD"].components["carry"] > 0


def test_keyword_sentiment_pairs_and_mentions():
    now = datetime.now(timezone.utc)
    hs = [
        Headline("x", "EUR/USD rises after hawkish ECB comments", "", now),
        Headline("x", "Yen slumps as BoJ stays dovish", "", now),
    ]
    v = sentiment.keyword_scores(hs)
    assert v["EUR"].score > 0 and v["USD"].score < 0 and v["JPY"].score < 0


def test_claude_json_parsing_clamps():
    text = json.dumps({"currencies": [{"code": "EUR", "score": 3, "reason": "r"}, {"code": "XXX", "score": 1, "reason": ""}], "market_summary": "s"})
    views, summary = sentiment.parse_claude_json(text)
    assert views["EUR"].score == 1.0 and summary == "s" and "XXX" not in views


def test_rss_parse():
    xml = """<rss><channel><item><title>Pound rallies</title><description>&lt;p&gt;BoE&lt;/p&gt;</description>
    <pubDate>Tue, 29 Sep 2026 12:00:00 GMT</pubDate></item><item><title>no date</title></item></channel></rss>"""
    items = news.parse_rss(xml, "t")
    assert len(items) == 1 and items[0].summary == "BoE"


def test_calendar_blackout():
    evs = calendar.parse([{"title": "Non-Farm Payrolls", "country": "USD", "date": "2026-10-02T08:30:00-04:00", "impact": "High"}])
    at = datetime(2026, 10, 2, 12, 45, tzinfo=timezone.utc)
    assert calendar.high_impact_near(evs, "USD", at, 30)
    assert not calendar.high_impact_near(evs, "USD", at + timedelta(hours=1), 30)
    assert not calendar.high_impact_near(evs, "EUR", at, 30)


def test_order_sizing_respects_risk_and_caps():
    plan = risk.plan_order(equity=100_000, buying_power=200_000, ask=100.0, atr_pct=0.002, risk_per_trade=0.01,
                           max_position_pct=0.25, stop_atr_mult=1.5, target_atr_mult=2.5)
    assert plan is not None
    assert plan.qty * plan.limit <= 25_000 + 1          # position cap
    assert plan.risk_dollars <= 1_000 + 1               # 1% risk
    assert plan.stop < plan.limit < plan.target
    assert risk.plan_order(equity=100, buying_power=50, ask=100.0, atr_pct=0.002, risk_per_trade=0.01,
                           max_position_pct=0.25, stop_atr_mult=1.5, target_atr_mult=2.5) is None


def test_daily_loss_and_spread():
    assert risk.daily_loss_hit(96_900, 100_000, 0.03)
    assert not risk.daily_loss_hit(97_100, 100_000, 0.03)
    assert risk.spread_ok(99.98, 100.02, 0.004) and not risk.spread_ok(99, 101, 0.004) and not risk.spread_ok(0, 100, 0.01)


def test_report_realized_pnl_and_text():
    fills = [
        {"symbol": "FXE", "side": "buy", "qty": "10", "price": "100"},
        {"symbol": "FXE", "side": "sell", "qty": "10", "price": "101"},
    ]
    assert report.realized_by_symbol(fills) == {"FXE": 10.0}
    text = report.build("2026-09-29", 100_010, 100_000, fills,
                        [{"action": "buy", "time": "10:00", "symbol": "FXE", "qty": 10, "currency": "EUR", "view": "bullish",
                          "score": 0.5, "limit": 100, "stop": 99.7, "target": 100.5, "risk_dollars": 3, "reason": "trend"}], None)
    assert "bought 10 FXE" in text and "+$10.00" in text
