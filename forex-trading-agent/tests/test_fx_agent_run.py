"""Full runs of agent.run() against a fake Alpaca - no network."""
from datetime import datetime, timezone

import pytest

from fxagent import agent, config
from test_fx_core import bars


class FakeBroker:
    def __init__(self, *, is_open=True, next_close="2026-09-29T16:00:00-04:00", equity=100_000.0, last_equity=100_000.0, positions=None, orders=None):
        self._clock = {"is_open": is_open, "next_close": next_close}
        self.equity, self.last_equity = equity, last_equity
        self._positions = positions or {}
        self._orders = orders or []
        self.buys, self.closed, self.cancelled = [], [], []

    def clock(self): return self._clock
    def account(self): return {"equity": str(self.equity), "last_equity": str(self.last_equity), "buying_power": str(self.equity * 2)}
    def positions(self): return {k: v for k, v in self._positions.items() if k not in self.closed}
    def open_orders(self): return [o for o in self._orders if o["id"] not in self.cancelled]
    def fills_since(self, after): return []
    def quotes(self, syms): return {s: {"bp": 99.99, "ap": 100.01} for s in syms}
    def bracket_buy(self, sym, qty, limit, stop, target):
        self.buys.append(sym)
        self._orders.append({"id": f"o{len(self._orders)}", "symbol": sym, "side": "buy"})
        return {"status": "accepted"}
    def cancel_order(self, oid): self.cancelled.append(oid)
    def close_position(self, sym): self.closed.append(sym); return {}
    def bars(self, *a, **k): return {}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("FX_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent.calendar, "fetch", lambda: [])
    monkeypatch.setattr(agent.news, "fetch_all", lambda *a, **k: [])
    monkeypatch.setattr(agent.macro, "latest_rate", lambda *a, **k: None)
    # Every currency rallies vs a falling dollar.
    prices = {c: (bars(0.0004), bars(0.002, 150, "1D")) for c in ["EUR", "JPY", "GBP", "AUD", "CAD", "CHF"]}
    prices["USD"] = (bars(-0.0004), bars(-0.002, 150, "1D"))
    monkeypatch.setattr(agent, "gather_prices", lambda broker: prices)

    def use(broker):
        monkeypatch.setattr(agent, "Alpaca", lambda s: broker)
        return broker
    return use, tmp_path


NOON = datetime(2026, 9, 29, 16, 0, tzinfo=timezone.utc)      # 12:00 ET
LATE = datetime(2026, 9, 29, 19, 45, tzinfo=timezone.utc)     # 15:45 ET


def test_opens_up_to_max_positions(setup):
    use, _ = setup
    b = use(FakeBroker())
    assert agent.run(NOON) == 0
    assert len(b.buys) == config.load().max_positions
    assert not ({"UUP", "UDN"} <= set(b.buys))


def test_flattens_near_close(setup):
    use, _ = setup
    b = use(FakeBroker(positions={"FXE": {"qty": "10"}}, orders=[{"id": "stop1", "symbol": "FXE", "side": "sell"}]))
    agent.run(LATE)
    assert b.closed == ["FXE"] and "stop1" in b.cancelled and not b.buys


def test_daily_loss_limit_halts(setup):
    use, root = setup
    b = use(FakeBroker(equity=96_000, positions={"FXY": {"qty": "5"}}, orders=[{"id": "s", "symbol": "FXY", "side": "sell"}]))
    agent.run(NOON)
    assert b.closed == ["FXY"] and not b.buys
    assert (root / "halts" / "2026-09-29.txt").exists()
    b2 = use(FakeBroker())
    agent.run(NOON)
    assert not b2.buys  # still halted for the day


def test_unprotected_leftover_position_is_closed(setup):
    use, _ = setup
    b = use(FakeBroker(positions={"FXB": {"qty": "3"}}))
    agent.run(NOON)
    assert "FXB" in b.closed


def test_does_not_touch_other_symbols(setup):
    use, _ = setup
    b = use(FakeBroker(positions={"AAPL": {"qty": "1"}}))
    agent.run(LATE)
    assert b.closed == []


def test_kill_switch_blocks_entries(setup):
    use, root = setup
    (root / "KILL_SWITCH").write_text("paused")
    b = use(FakeBroker())
    agent.run(NOON)
    assert not b.buys


def test_report_after_close(setup, monkeypatch):
    use, root = setup
    use(FakeBroker())
    agent.run(NOON)  # trades, writes session marker
    b = use(FakeBroker(is_open=False))
    monkeypatch.setattr(agent.report, "post_issue", lambda t, body: None)
    agent.run(datetime(2026, 9, 29, 20, 20, tzinfo=timezone.utc))  # 16:20 ET
    text = (root / "reports" / "2026-09-29.md").read_text()
    assert "bought" in text and "Why:" in text


def test_refuses_live_endpoint(monkeypatch):
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    from fxagent.alpaca import Alpaca, NotPaperError
    with pytest.raises(NotPaperError):
        Alpaca(config.load())
