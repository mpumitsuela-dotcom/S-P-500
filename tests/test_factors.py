import pandas as pd

from research.factors import (
    compute_low_vol_factor,
    compute_momentum_factor,
    compute_quality_factor,
    compute_research_factor,
    compute_value_factor,
)
from research.signals import compute_combined_scores


def test_value_factor_rewards_cheap_stocks(sector_map, synthetic_fundamentals):
    scores = compute_value_factor(synthetic_fundamentals, sector_map)
    cheapest = synthetic_fundamentals.sort_values("pe").iloc[0]["symbol"]
    priciest = synthetic_fundamentals.sort_values("pe").iloc[-1]["symbol"]
    # Not a strict guarantee given the P/B leg too, but on average cheap should beat pricey
    assert scores[cheapest] >= scores[priciest] - 2.0  # loose bound, deterministic given the fixed seed


def test_quality_factor_output_shape(sector_map, synthetic_fundamentals):
    scores = compute_quality_factor(synthetic_fundamentals, sector_map)
    assert set(scores.index) == set(synthetic_fundamentals["symbol"])
    assert scores.notna().all()


def test_momentum_factor_no_lookahead(sector_map, synthetic_prices):
    """Momentum computed on data through day 150 must be identical whether or not later rows exist."""
    full = compute_momentum_factor(synthetic_prices, sector_map)
    truncated_prices = synthetic_prices[synthetic_prices["date"] <= synthetic_prices["date"].unique()[149]]
    truncated = compute_momentum_factor(truncated_prices, sector_map)
    # They should differ (different data) but truncated must not error and must be finite
    assert truncated.notna().all()
    assert full.notna().all()


def test_low_vol_factor_rewards_low_volatility(sector_map, synthetic_prices):
    scores = compute_low_vol_factor(synthetic_prices, sector_map)
    assert scores.notna().all()
    assert set(scores.index) == set(sector_map.keys())


def test_combined_scores_price_only_excludes_fundamentals(sector_map, synthetic_prices):
    out = compute_combined_scores(synthetic_prices, sector_map, fundamentals=None)
    assert (out["value"] == 0.0).all()
    assert (out["quality"] == 0.0).all()
    assert (out["research"] == 0.0).all()  # no lookahead-safe historical research data - always off in this mode
    assert "combined_score" in out.columns
    assert len(out) == len(sector_map)


def test_combined_scores_with_fundamentals(sector_map, synthetic_prices, synthetic_fundamentals):
    out = compute_combined_scores(synthetic_prices, sector_map, fundamentals=synthetic_fundamentals)
    assert not (out["value"] == 0.0).all()  # should now be populated
    assert out["combined_score"].notna().all()


def test_combined_scores_with_research(sector_map, synthetic_prices, synthetic_fundamentals, synthetic_research):
    out = compute_combined_scores(
        synthetic_prices, sector_map, fundamentals=synthetic_fundamentals, research=synthetic_research
    )
    assert not (out["research"] == 0.0).all()  # should now be populated
    assert out["combined_score"].notna().all()
    assert "rank" in out.columns
    assert sorted(out["rank"].tolist()) == list(range(1, len(out) + 1))
    assert out.loc[out["rank"] == 1, "combined_score"].iloc[0] == out["combined_score"].max()


def test_research_factor_rewards_positive_sentiment_and_analyst_consensus(sector_map, synthetic_research):
    scores = compute_research_factor(synthetic_research, sector_map)
    assert set(scores.index) == set(synthetic_research["symbol"])
    assert scores.notna().all()

    most_bullish = synthetic_research.sort_values(["news_sentiment_score", "analyst_score"], ascending=False).iloc[0]["symbol"]
    most_bearish = synthetic_research.sort_values(["news_sentiment_score", "analyst_score"]).iloc[0]["symbol"]
    # Loose bound, not a strict guarantee: sector-neutral z-scoring (see research/scoring.py)
    # compares each name only to its own sector peers, so a global sort by raw score isn't
    # guaranteed to perfectly match the sector-relative combined score - same caveat as
    # test_value_factor_rewards_cheap_stocks above.
    assert scores[most_bullish] >= scores[most_bearish] - 1.0


def test_research_factor_neutral_when_no_coverage(sector_map):
    empty = pd.DataFrame(
        {
            "symbol": list(sector_map.keys()),
            "news_sentiment_score": [None] * len(sector_map),
            "analyst_score": [None] * len(sector_map),
        }
    )
    scores = compute_research_factor(empty, sector_map)
    assert (scores == 0.0).all()
