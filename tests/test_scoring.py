import numpy as np
import pandas as pd

from research.scoring import sector_neutral_zscore, winsorize, zscore


def test_zscore_mean_zero_std_one():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    z = zscore(s)
    assert abs(z.mean()) < 1e-9
    assert abs(z.std() - 1.0) < 1e-9


def test_zscore_constant_series_returns_zero():
    s = pd.Series([5.0, 5.0, 5.0])
    z = zscore(s)
    assert (z == 0.0).all()


def test_winsorize_clips_outliers():
    s = pd.Series([1.0] * 98 + [1000.0, -1000.0])
    w = winsorize(s, 0.01, 0.99)
    assert w.max() < 1000.0
    assert w.min() > -1000.0


def test_sector_neutral_zscore_compares_within_sector():
    df = pd.DataFrame(
        {
            "sector": ["Tech", "Tech", "Tech", "Util", "Util", "Util"],
            "value": [1, 2, 3, 100, 200, 300],  # Utilities on a totally different scale
        }
    )
    scored = sector_neutral_zscore(df, "value")
    # Within each sector, the top name should score highest regardless of absolute scale
    assert scored.iloc[2] > scored.iloc[0]  # Tech: 3 > 1
    assert scored.iloc[5] > scored.iloc[3]  # Util: 300 > 100
    # And the two sectors' top scorers should be roughly comparable (both ~+1.2 z)
    assert abs(scored.iloc[2] - scored.iloc[5]) < 0.5


def test_sector_neutral_zscore_small_sector_falls_back_to_global():
    df = pd.DataFrame({"sector": ["A", "A", "B"], "value": [1, 2, 3]})
    scored = sector_neutral_zscore(df, "value")
    assert not scored.isna().any()
