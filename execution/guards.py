"""
Pre-flight safety guards. Every scheduled run must pass ALL guards before
any order is placed. Each guard returns (passed: bool, message: str); a
single failure halts that run entirely (fails safe / closed, not open).

This is the most important file in the whole package: a bug anywhere else
produces a bad trade; a bug here produces bad trades on autopilot, twice a
day, unattended. Keep it simple, keep it conservative, keep it tested.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd

from config import (
    KILL_SWITCH_FILE,
    LIVE_TRADING_CONFIRM_VALUE,
    LIVE_TRADING_ENV_FLAG,
    STRATEGY,
    TRIAL_LENGTH_DAYS,
    API_KEYS,
)

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")


@dataclass
class GuardResult:
    name: str
    passed: bool
    message: str


class GuardFailure(Exception):
    """Raised by run_all_guards when any guard fails; run scripts catch this and halt."""

    def __init__(self, results: list[GuardResult]):
        self.results = results
        failed = [r for r in results if not r.passed]
        super().__init__("; ".join(f"{r.name}: {r.message}" for r in failed))


def check_kill_switch() -> GuardResult:
    """Manual/automatic emergency stop. Touch config.KILL_SWITCH_FILE to halt all trading immediately."""
    if KILL_SWITCH_FILE.exists():
        return GuardResult("kill_switch", False, f"Kill switch file present at {KILL_SWITCH_FILE}")
    return GuardResult("kill_switch", True, "no kill switch set")


def check_not_live_unless_triple_confirmed() -> GuardResult:
    """
    Triple-gated live trading: (1) base URL must not be the live endpoint,
    UNLESS (2) LIVE_TRADING_CONFIRMED=YES_I_UNDERSTAND is explicitly set,
    AND (3) the caller passes allow_live=True at the call site (see
    execution/alpaca_broker.py AlpacaBroker.__init__). This function only
    checks gates (1) and (2); gate (3) lives in code, not config, so it
    can't be flipped by an env var alone.
    """
    is_live_url = "paper" not in API_KEYS.alpaca_base_url
    confirmed = _live_trading_confirmed()
    if is_live_url and not confirmed:
        return GuardResult(
            "live_trading_gate",
            False,
            "ALPACA_BASE_URL points at a non-paper endpoint but LIVE_TRADING_CONFIRMED is not set",
        )
    return GuardResult("live_trading_gate", True, "paper endpoint or explicitly confirmed")


def _live_trading_confirmed() -> bool:
    import os

    return os.environ.get(LIVE_TRADING_ENV_FLAG) == LIVE_TRADING_CONFIRM_VALUE


def check_trial_period(start_date: date, today: date, length_days: int = TRIAL_LENGTH_DAYS) -> GuardResult:
    """
    Bounded trial: the user asked this to run for a fixed window and then
    stop so they can decide how to proceed, rather than trading on
    indefinitely by default. start_date is set once, automatically, on the
    very first run (see execution/trial.py get_or_init_trial_start) - this
    guard just compares today against it. Fails safe once the window is up:
    no further orders, on either session, until a human clears the trial
    marker (see README.md) or the kill switch.
    """
    elapsed = (today - start_date).days
    if elapsed >= length_days:
        return GuardResult(
            "trial_period",
            False,
            f"the {length_days}-day trial (started {start_date}) ended {elapsed - length_days + 1} day(s) ago "
            "- no further trades until you review TRIAL_REPORT.md and decide how to proceed",
        )
    remaining = length_days - elapsed
    return GuardResult("trial_period", True, f"trial day {elapsed + 1} of {length_days} ({remaining} day(s) remaining)")


def check_market_hours(now: datetime | None = None, allow_extended: bool = False) -> GuardResult:
    """
    Regular NYSE session is 9:30-16:00 America/New_York, Mon-Fri
    (holidays are NOT checked here - see SETUP_GUIDE.md for adding a
    holiday calendar; on a holiday this guard will pass but Alpaca itself
    will reject the order, which is a safe failure mode).
    """
    now = now or datetime.now(NY_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=NY_TZ)
    if now.weekday() >= 5:
        return GuardResult("market_hours", False, f"{now.date()} is a weekend")
    open_t, close_t = dtime(9, 30), dtime(16, 0)
    if allow_extended:
        open_t, close_t = dtime(4, 0), dtime(20, 0)
    if not (open_t <= now.time() <= close_t):
        return GuardResult("market_hours", False, f"{now.time()} outside session window {open_t}-{close_t}")
    return GuardResult("market_hours", True, "within session window")


def check_data_freshness(prices: pd.DataFrame, as_of: pd.Timestamp, max_staleness_days: int = 5) -> GuardResult:
    if prices.empty:
        return GuardResult("data_freshness", False, "price frame is empty")
    latest = prices["date"].max()
    staleness = (pd.Timestamp(as_of) - pd.Timestamp(latest)).days
    if staleness > max_staleness_days:
        return GuardResult("data_freshness", False, f"latest price data is {staleness} days old")
    return GuardResult("data_freshness", True, f"data is {staleness} days old")


def check_universe_coverage(scores: pd.DataFrame, sector_map: dict[str, str], min_coverage: float = 0.8) -> GuardResult:
    coverage = len(scores) / max(len(sector_map), 1)
    if coverage < min_coverage:
        return GuardResult("universe_coverage", False, f"only {coverage:.0%} of universe has scores (need {min_coverage:.0%})")
    return GuardResult("universe_coverage", True, f"{coverage:.0%} universe coverage")


def check_drawdown_halt(equity_curve: pd.Series) -> GuardResult:
    """Halts new buys if today's drawdown from the recent peak exceeds the configured threshold."""
    if equity_curve.empty:
        return GuardResult("drawdown_halt", True, "no equity history yet")
    peak = equity_curve.cummax().iloc[-1]
    current = equity_curve.iloc[-1]
    drawdown = (current / peak) - 1.0 if peak > 0 else 0.0
    if drawdown < -STRATEGY.max_daily_drawdown_halt:
        return GuardResult("drawdown_halt", False, f"drawdown {drawdown:.1%} exceeds halt threshold")
    return GuardResult("drawdown_halt", True, f"drawdown {drawdown:.1%} within threshold")


def check_price_sanity(target_prices: pd.Series, reference_prices: pd.Series) -> GuardResult:
    """
    Rejects the run if any symbol's price moved implausibly (>35% by
    default) versus the last known reference price - catches bad ticks,
    stock splits not yet reflected in the data, or a stale/garbled feed.
    """
    aligned = pd.concat({"new": target_prices, "ref": reference_prices}, axis=1).dropna()
    if aligned.empty:
        return GuardResult("price_sanity", True, "no overlapping symbols to check")
    pct_move = (aligned["new"] / aligned["ref"] - 1.0).abs()
    breaches = pct_move[pct_move > STRATEGY.max_position_daily_move_sanity]
    if not breaches.empty:
        return GuardResult(
            "price_sanity", False, f"{len(breaches)} symbol(s) moved >{STRATEGY.max_position_daily_move_sanity:.0%}: {list(breaches.index)}"
        )
    return GuardResult("price_sanity", True, "no implausible price moves")


def run_all_guards(results: list[GuardResult]) -> None:
    """Raises GuardFailure if any guard failed; callers should catch this, log it, and skip trading for the run."""
    failed = [r for r in results if not r.passed]
    if failed:
        raise GuardFailure(results)
    logger.info("All %d guards passed", len(results))
