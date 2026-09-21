import pandas as pd
import pytest

from portfolio.construction import build_target_portfolio, cap_and_redistribute, select_top_names
from portfolio.risk import weights_to_target_shares


@pytest.fixture
def toy_scores():
    return pd.DataFrame(
        {"combined_score": [3.0, 2.5, 2.0, 1.5, 1.0, 0.5, 0.0, -0.5]},
        index=["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"],
    ).assign(sector=["Tech", "Tech", "Tech", "Fin", "Fin", "Energy", "Energy", "Util"])


def test_select_top_names(toy_scores):
    top = select_top_names(toy_scores, num_positions=3)
    assert list(top.index) == ["AAA", "BBB", "CCC"]


def test_cap_and_redistribute_respects_position_cap():
    weights = pd.Series({"AAA": 0.5, "BBB": 0.3, "CCC": 0.2})
    sector_map = {"AAA": "Tech", "BBB": "Fin", "CCC": "Energy"}
    capped = cap_and_redistribute(weights, sector_map, max_position_weight=0.35, max_sector_weight=1.0)
    assert capped.max() <= 0.35 + 1e-6
    assert abs(capped.sum() - 1.0) < 1e-6


def test_cap_and_redistribute_respects_sector_cap():
    weights = pd.Series({"AAA": 0.4, "BBB": 0.4, "CCC": 0.2})
    sector_map = {"AAA": "Tech", "BBB": "Tech", "CCC": "Energy"}  # Tech = 0.8 combined
    capped = cap_and_redistribute(weights, sector_map, max_position_weight=1.0, max_sector_weight=0.5)
    tech_total = capped["AAA"] + capped["BBB"]
    assert tech_total <= 0.5 + 1e-6
    assert abs(capped.sum() - 1.0) < 1e-6


def test_build_target_portfolio_leaves_cash_buffer(toy_scores):
    sector_map = dict(zip(toy_scores.index, toy_scores["sector"]))
    target = build_target_portfolio(toy_scores, sector_map, num_positions=4)
    assert target["target_weight"].sum() < 1.0  # cash buffer held back
    assert target["target_weight"].sum() > 0.9


def test_weights_to_target_shares_floors_and_drops_missing_prices():
    target = pd.DataFrame({"symbol": ["AAA", "BBB"], "target_weight": [0.5, 0.5]})
    prices = pd.Series({"AAA": 100.0})  # BBB has no price -> should be dropped
    out = weights_to_target_shares(target, account_equity=10_000, latest_prices=prices)
    assert list(out["symbol"]) == ["AAA"]
    assert out.iloc[0]["target_shares"] == 50  # 5000 / 100
