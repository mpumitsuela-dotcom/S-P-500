"""Free-tier API behaviour found live on 2026-09-24: FMP's free plan doesn't cover
every symbol, and bursting Finnhub past 60 calls/minute fails nearly every fetch."""
import pandas as pd
import pytest
import requests
from tenacity import wait_none

import data.cache as cache
from data import finnhub_data, fmp_data


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(fmp_data, "API_KEYS", type("K", (), {"fmp_key": "k", "finnhub_key": "k"})())
    monkeypatch.setattr(finnhub_data, "API_KEYS", type("K", (), {"fmp_key": "k", "finnhub_key": "k"})())
    monkeypatch.setattr(fmp_data._get.retry, "wait", wait_none())
    monkeypatch.setattr(finnhub_data._get.retry, "wait", wait_none())


PREMIUM = "Premium Query Parameter: 'Special Endpoint : This value set for 'symbol' is not available under your current subscription"


def test_fmp_uncovered_symbol_does_not_stop_other_symbols(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url.rsplit("/", 1)[1], params["symbol"]))
        if params["symbol"] == "AOS":
            return Resp(402, text=PREMIUM)
        return Resp(200, [{"priceToEarningsRatioTTM": 20.0, "returnOnEquityTTM": 0.3}])

    monkeypatch.setattr(fmp_data.requests, "get", fake_get)
    frame = fmp_data.get_fundamentals_frame(["AOS", "AAPL", "MSFT"], max_new_symbols=10)
    rows = frame.set_index("symbol")
    assert set(rows.index) == {"AOS", "AAPL", "MSFT"}
    assert rows.loc["AAPL", "pe"] == 20.0 and pd.isna(rows.loc["AOS", "pe"])
    assert calls.count(("key-metrics-ttm", "AOS")) == 1 and ("ratios-ttm", "AOS") not in calls

    calls.clear()
    fmp_data.get_fundamentals_frame(["AOS"], max_new_symbols=10)
    assert calls == []  # "not covered" is cached, not re-asked every run


def test_fmp_real_quota_still_trips_the_circuit_breaker(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["symbol"])
        return Resp(429, text="Limit Reach")

    monkeypatch.setattr(fmp_data, "MAX_WORKERS", 1)
    monkeypatch.setattr(fmp_data.requests, "get", fake_get)
    frame = fmp_data.get_fundamentals_frame(["A", "B", "C", "D"], max_new_symbols=10)
    assert frame.empty or "symbol" not in frame.columns or frame["symbol"].tolist() == []
    assert len(calls) == 1  # no retry, no further symbols


def test_finnhub_calls_are_spaced_to_the_rate_limit(monkeypatch):
    sleeps, clock = [], [1000.0]
    monkeypatch.setattr(finnhub_data, "CALLS_PER_MINUTE", 60.0)
    monkeypatch.setattr(finnhub_data, "_next_call_at", 0.0)
    monkeypatch.setattr(finnhub_data.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(finnhub_data.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(finnhub_data.requests, "get", lambda url, params, timeout: Resp(200, []))
    for _ in range(3):
        finnhub_data._get("/company-news", {"symbol": "AAPL"})
    assert sleeps == [pytest.approx(1.0), pytest.approx(2.0)]  # 1 call/second, no burst


def test_finnhub_429_is_retried_until_it_succeeds(monkeypatch):
    monkeypatch.setattr(finnhub_data, "CALLS_PER_MINUTE", 0)
    responses = [Resp(429), Resp(429), Resp(200, [{"headline": "ok"}])]
    monkeypatch.setattr(finnhub_data.requests, "get", lambda url, params, timeout: responses.pop(0))
    assert finnhub_data._get("/company-news", {"symbol": "AAPL"}) == [{"headline": "ok"}]


def test_finnhub_auth_error_is_not_retried(monkeypatch):
    monkeypatch.setattr(finnhub_data, "CALLS_PER_MINUTE", 0)
    calls = []
    monkeypatch.setattr(finnhub_data.requests, "get", lambda url, params, timeout: calls.append(1) or Resp(401))
    with pytest.raises(requests.HTTPError):
        finnhub_data._get("/company-news", {"symbol": "AAPL"})
    assert len(calls) == 1


def test_uncovered_symbols_do_not_break_scoring(monkeypatch):
    """Crash on 2026-09-24: None fundamentals made object-dtype columns and pandas 3
    refused to write float z-scores back (TypeError in sector_neutral_zscore)."""
    from research.factors import compute_quality_factor, compute_value_factor

    def fake_get(url, params, timeout):
        i = int(params["symbol"][1:])
        if i >= 8:
            return Resp(402, text=PREMIUM)
        return Resp(200, [{"priceToEarningsRatioTTM": 10.0 + i, "priceToBookRatioTTM": 2.0 + i,
                           "returnOnEquityTTM": 0.1 * i, "grossProfitMarginTTM": 0.3, "debtToEquityRatioTTM": 1.0}])

    monkeypatch.setattr(fmp_data.requests, "get", fake_get)
    symbols = [f"S{i}" for i in range(12)]
    sector_map = {s: "Tech" if i < 6 else "Energy" for i, s in enumerate(symbols)}
    frame = fmp_data.get_fundamentals_frame(symbols, max_new_symbols=20)
    assert frame["pe"].dtype.kind == "f"
    value = compute_value_factor(frame, sector_map)
    quality = compute_quality_factor(frame, sector_map)
    assert value.notna().all() and quality.notna().all()


def test_scoring_tolerates_object_columns():
    """Belt and braces: an object column with None (e.g. research analyst_score) still scores."""
    from research.scoring import sector_neutral_zscore

    df = pd.DataFrame({"x": [1.0, 2.0, None, 4.0, 5.0, None], "sector": ["A"] * 3 + ["B"] * 3}, dtype=object)
    out = sector_neutral_zscore(df, "x")
    assert out.dtype.kind == "f" and out.notna().all()
