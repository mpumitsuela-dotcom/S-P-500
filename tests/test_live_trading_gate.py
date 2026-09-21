"""
Tests for the triple-gated live-trading safety mechanism. This is the
single most consequential guard in the package (it's the difference
between paper money and real money), so it gets its own dedicated test file.

config.API_KEYS is a frozen dataclass (deliberately - so nothing can
casually mutate a single field like alpaca_base_url at runtime), so these
tests swap in a whole replacement APIKeys instance via monkeypatch instead
of trying to set an individual attribute.
"""
import dataclasses

import pytest

from config import APIKeys
import execution.alpaca_broker as alpaca_broker_module
import execution.guards as guards_module
from execution.alpaca_broker import AlpacaBroker, LiveTradingNotConfirmedError
from execution.guards import check_not_live_unless_triple_confirmed


def _keys_with(**overrides) -> APIKeys:
    base = dataclasses.asdict(APIKeys())
    base.update(overrides)
    return APIKeys(**base)


def test_paper_url_passes_guard(monkeypatch):
    monkeypatch.setattr(guards_module, "API_KEYS", _keys_with(alpaca_base_url="https://paper-api.alpaca.markets"))
    result = check_not_live_unless_triple_confirmed()
    assert result.passed


def test_live_url_without_confirmation_fails_guard(monkeypatch):
    monkeypatch.setattr(guards_module, "API_KEYS", _keys_with(alpaca_base_url="https://api.alpaca.markets"))
    monkeypatch.delenv("LIVE_TRADING_CONFIRMED", raising=False)
    result = check_not_live_unless_triple_confirmed()
    assert not result.passed


def test_alpaca_broker_refuses_live_without_all_three_gates(monkeypatch):
    live_keys = _keys_with(
        alpaca_base_url="https://api.alpaca.markets",
        alpaca_key_id="dummy",
        alpaca_secret_key="dummy",
    )
    monkeypatch.setattr(alpaca_broker_module, "API_KEYS", live_keys)
    monkeypatch.delenv("LIVE_TRADING_CONFIRMED", raising=False)
    # allow_live=True alone (gate 3) is not enough without gate 2 (env var)
    with pytest.raises(LiveTradingNotConfirmedError):
        AlpacaBroker(allow_live=True)


def test_alpaca_broker_refuses_live_even_with_env_var_if_allow_live_false(monkeypatch):
    live_keys = _keys_with(
        alpaca_base_url="https://api.alpaca.markets",
        alpaca_key_id="dummy",
        alpaca_secret_key="dummy",
    )
    monkeypatch.setattr(alpaca_broker_module, "API_KEYS", live_keys)
    monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "YES_I_UNDERSTAND")
    # gate 2 satisfied but gate 3 (explicit code-level allow_live=True) is not
    with pytest.raises(LiveTradingNotConfirmedError):
        AlpacaBroker(allow_live=False)


def test_alpaca_broker_allows_paper_without_any_confirmation(monkeypatch):
    paper_keys = _keys_with(
        alpaca_base_url="https://paper-api.alpaca.markets",
        alpaca_key_id="dummy",
        alpaca_secret_key="dummy",
    )
    monkeypatch.setattr(alpaca_broker_module, "API_KEYS", paper_keys)
    monkeypatch.delenv("LIVE_TRADING_CONFIRMED", raising=False)
    AlpacaBroker()  # should not raise
