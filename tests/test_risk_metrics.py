"""Risk scorecard for the 5-day report."""
from datetime import date

import pytest

from execution import risk_metrics


def _rows(values, start=24):
    return [{"date": f"2026-09-{start + i:02d}", "equity": v} for i, v in enumerate(values)]


def test_scorecard_numbers():
    rows = _rows([101.0, 99.0, 102.0, 103.0])
    spy = {date(2026, 9, 24): 0.005, date(2026, 9, 25): -0.01, date(2026, 9, 26): 0.02, date(2026, 9, 27): 0.004}
    m = risk_metrics.compute(rows, 100.0, spy.get)
    assert m["days"] == 4
    assert m["agent_return"] == pytest.approx(0.03)
    assert m["max_drawdown"] == pytest.approx(99 / 101 - 1)
    assert m["beta"] > 1  # the account swung more than SPY on the same days
    assert m["alpha"] == pytest.approx(m["agent_return"] - m["beta"] * m["spy_return"])
    assert m["sharpe"] > 0


def test_render_is_plain_english_and_flags_small_samples():
    spy = lambda d: (d.day % 3 - 1) * 0.004  # noqa: E731
    text = "\n".join(risk_metrics.render(risk_metrics.compute(_rows([101.0, 100.0, 102.0, 101.5, 103.0]), 100.0, spy)))
    assert "Largest fall from a peak" in text and "Sharpe ratio" in text and "Beta" in text
    assert "Only 5 trading day(s) so far" in text


def test_ratios_withheld_with_too_few_days():
    text = "\n".join(risk_metrics.render(risk_metrics.compute(_rows([101.0, 102.0]), 100.0, lambda d: 0.001)))
    assert "Sharpe ratio:" not in text and "appear from trading day 5" in text
    assert "none so far" in text


def test_no_data():
    assert risk_metrics.compute([], 100.0, lambda d: None) == {"days": 0}
