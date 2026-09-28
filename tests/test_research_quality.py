"""News relevance filter and the Finnhub fundamentals fallback."""
import pandas as pd

from data import finnhub_data, fundamentals


def test_clean_name_strips_legal_suffixes():
    assert finnhub_data._clean_name("Darden Restaurants, Inc.") == "Darden Restaurants"
    assert finnhub_data._clean_name("The Home Depot") == "Home Depot"
    assert finnhub_data._clean_name("Alphabet Inc. (Class A)") == "Alphabet"
    assert finnhub_data._clean_name("Amazon.com") == "Amazon"
    assert finnhub_data._clean_name("Casey's General Stores") == "Casey's General Stores"


def test_is_about_keeps_company_articles_and_drops_others():
    art = lambda h, s="": {"headline": h, "summary": s}  # noqa: E731
    assert finnhub_data.is_about(art("Micron beats on AI memory demand"), "MU", "Micron")
    assert finnhub_data.is_about(art("Shares of MU jump"), "MU", "Micron")
    assert finnhub_data.is_about(art("Casey’s raises outlook"), "CASY", "Casey's")
    assert not finnhub_data.is_about(art("Darden Restaurants tops estimates"), "BAC", "Bank of America")
    assert not finnhub_data.is_about(art("Adobe launches new AI tools"), "GOOGL", "Alphabet")
    # one-letter tickers only count in the "(C)" form
    assert not finnhub_data.is_about(art("Plan C for the Fed"), "C", "Citigroup")
    assert finnhub_data.is_about(art("Citi (C) upgraded"), "C", "Citigroup")
    # ticker match is case-sensitive: "mu" in prose is not Micron
    assert not finnhub_data.is_about(art("The mu-law codec explained"), "MU", "Micron")


def test_fundamentals_row_maps_finnhub_metric_units(monkeypatch):
    metric = {"peTTM": 20.0, "pbQuarterly": 3.0, "roeTTM": 25.0, "grossMarginTTM": 40.0,
              "totalDebt/totalEquityQuarterly": 0.8}
    monkeypatch.setattr(finnhub_data, "get_basic_financials", lambda s: metric)
    row = finnhub_data.fundamentals_row("XYZ")
    assert row == {"symbol": "XYZ", "pe": 20.0, "pb": 3.0, "roe": 0.25, "gross_margin": 0.4,
                   "debt_to_equity": 0.8, "earnings_growth": None}


def test_get_fundamentals_fills_fmp_gaps_from_finnhub(monkeypatch):
    fmp = pd.DataFrame([
        {"symbol": "AAA", "pe": 15.0, "pb": 2.0, "roe": 0.2, "gross_margin": 0.5, "debt_to_equity": 1.0},
        {"symbol": "BBB", "pe": None, "pb": None, "roe": None, "gross_margin": None, "debt_to_equity": None},
    ])
    monkeypatch.setattr(fundamentals, "get_fundamentals_frame", lambda syms, **kw: fmp)
    monkeypatch.setattr(fundamentals, "is_fresh", lambda *a: False)
    fetched = []

    def fake_row(sym):
        fetched.append(sym)
        if sym == "CCC":
            return {"symbol": sym, "pe": None, "pb": None, "roe": None, "gross_margin": None, "debt_to_equity": None}
        return {"symbol": sym, "pe": 30.0, "pb": 5.0, "roe": 0.1, "gross_margin": 0.3, "debt_to_equity": 0.5}

    monkeypatch.setattr(fundamentals.finnhub_data, "fundamentals_row", fake_row)
    out = fundamentals.get_fundamentals(["AAA", "BBB", "CCC"]).set_index("symbol")
    assert fetched == ["BBB", "CCC"]  # FMP-covered AAA is not looked up again
    assert out.loc["AAA", "source"] == "FMP" and out.loc["BBB", "source"] == "Finnhub"
    assert out.loc["BBB", "pe"] == 30.0
    assert "CCC" not in out.index  # no data from either source


def test_get_fundamentals_respects_new_lookup_budget(monkeypatch):
    monkeypatch.setattr(fundamentals, "get_fundamentals_frame", lambda syms, **kw: pd.DataFrame())
    monkeypatch.setattr(fundamentals, "is_fresh", lambda ns, s, ttl: s == "Z")
    monkeypatch.setattr(fundamentals, "MAX_NEW_FINNHUB", 1)
    fetched = []
    monkeypatch.setattr(fundamentals.finnhub_data, "fundamentals_row",
                        lambda s: fetched.append(s) or {"symbol": s, "pe": 10.0})
    out = fundamentals.get_fundamentals(["X", "Y", "Z"])
    assert fetched == ["Z", "X"]  # cached Z is free; only one new lookup
    assert set(out["symbol"]) == {"Z", "X"}


def test_one_extreme_stock_cannot_dominate_a_factor():
    from research.scoring import Z_CAP, sector_neutral_zscore

    df = pd.DataFrame({"sector": ["Tech"] * 40, "momentum_raw": [0.1] * 20 + [0.2] * 19 + [30.0]})
    z = sector_neutral_zscore(df, "momentum_raw")
    assert z.max() == Z_CAP and z.min() >= -Z_CAP


def test_warm_up_refreshes_only_stale_or_expiring_research(monkeypatch, tmp_path):
    import time
    from data import cache, prefetch

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    fresh_ns = cache._key_to_path("finnhub_recommendation", "FRESH")
    fresh_ns.write_text('{"_cached_at": %f, "data": {}}' % time.time())
    calls = []
    monkeypatch.setattr(prefetch.finnhub_data, "get_analyst_recommendation", lambda s: calls.append(("rec", s)))
    monkeypatch.setattr(prefetch.finnhub_data, "get_basic_financials", lambda s: calls.append(("fin", s)))
    done = prefetch.warm_slow_research(["FRESH", "OLD"])
    assert ("rec", "OLD") in calls and ("rec", "FRESH") not in calls
    assert {("fin", "FRESH"), ("fin", "OLD")} <= set(calls)
    assert done["analysts"] == 1 and done["financials"] == 2


def test_warm_up_stops_at_its_deadline(monkeypatch, tmp_path):
    from data import cache, prefetch

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(prefetch.finnhub_data, "get_analyst_recommendation", lambda s: None)
    monkeypatch.setattr(prefetch.finnhub_data, "get_basic_financials", lambda s: None)
    done = prefetch.warm_slow_research(["A", "B", "C"], deadline_seconds=-1)
    assert done["analysts"] == 0 and done["left"] == 6
