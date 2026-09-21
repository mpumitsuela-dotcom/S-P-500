import pandas as pd

from execution.rationale import explain_rebalance_decision, explain_trim_decision
from execution.trade_log import TradeLogEntry, read_all_entries, record_trades


def _scores_frame():
    return pd.DataFrame(
        {
            "value": [1.2, -0.8],
            "quality": [0.5, -0.3],
            "momentum": [2.0, -1.5],
            "low_vol": [0.1, 0.2],
            "research": [1.0, -1.8],
            "combined_score": [1.6, -1.1],
            "rank": [1, 2],
        },
        index=pd.Index(["AAA", "BBB"], name="symbol"),
    )


def test_explain_rebalance_decision_buy_mentions_top_drivers_and_rank():
    scores = _scores_frame()
    text = explain_rebalance_decision("AAA", "buy", scores)
    assert "combined score" in text
    assert "ranked #1 of 2" in text
    assert "momentum" in text  # AAA's largest-magnitude leg in the fixture


def test_explain_rebalance_decision_includes_research_detail_when_available():
    scores = _scores_frame()
    research = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "headline_count": 3,
                "news_sentiment_score": 4,
                "analyst_score": 1.5,
                "total_analysts": 10,
                "analyst_breakdown": {"strongBuy": 5, "buy": 4, "hold": 1, "sell": 0, "strongSell": 0},
            }
        ]
    )
    text = explain_rebalance_decision("AAA", "buy", scores, research=research)
    assert "research detail" in text
    assert "headline" in text
    assert "analyst consensus" in text


def test_explain_rebalance_decision_unknown_symbol_does_not_crash():
    scores = _scores_frame()
    text = explain_rebalance_decision("ZZZ", "sell", scores)
    assert "ZZZ" not in text or "not present" in text  # graceful fallback, not a KeyError


def test_explain_trim_decision_mentions_move_and_headlines():
    sentiment_row = pd.Series({"sentiment_score": -4, "headline_count": 3})
    text = explain_trim_decision("AAA", -0.05, sentiment_row)
    assert "-5.0%" in text
    assert "headline" in text
    assert "circuit breaker" in text


def test_trade_log_round_trip(tmp_path, monkeypatch):
    csv_path = tmp_path / "trade_log.csv"
    md_path = tmp_path / "TRADE_LOG.md"
    monkeypatch.setattr("execution.trade_log.CSV_PATH", csv_path)
    monkeypatch.setattr("execution.trade_log.MD_PATH", md_path)

    record_trades([TradeLogEntry(session="AM", symbol="AAA", side="buy", qty=5, status="filled", reason="test reason")])
    entries = read_all_entries()
    assert len(entries) == 1
    assert entries[0]["symbol"] == "AAA"
    assert entries[0]["reason"] == "test reason"
    assert md_path.exists()
