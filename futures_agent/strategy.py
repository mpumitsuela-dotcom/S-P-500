"""
The futures agent's rules, as pure functions (no network) so each is tested.

Direction score per market, in [-1, +1] (+ = go long, - = go short):
  trend     (40%)  the tracking ETF vs its 50-day average, 20-day vs 50-day
  momentum  (35%)  20-day return relative to its own volatility
  companies (25%)  equity indexes only: the average research score of the
                   index's biggest companies (price, news, analysts - the
                   same company research the options agent uses)

Entry, all must hold:
  1. |score| >= min_quant_score
  2. Gemini's research (live Google Search: company results, the Fed,
     inflation and jobs data, geopolitics, supply for oil and gold) agrees on
     the direction with conviction >= min_conviction
  3. No major scheduled market event (Fed decision, CPI, jobs report) in the
     next 2 days, per Gemini
  4. One contract's loss at the stop fits the per-trade and total risk limits

Every position gets a stop-loss and a profit target resting at Tradovate the
moment it fills. The agent also closes a position when it has been held
max_hold_days, the contract is near expiry, the research turns against it,
or the two-month run ends.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from futures_agent.settings import FuturesSettings, Instrument

WEIGHTS = {"trend": 0.40, "momentum": 0.35, "companies": 0.25}


def _clip(x: float) -> float:
    return float(max(-1.0, min(1.0, x)))


def market_score(features: dict, company_score: float | None) -> dict:
    """features: options_agent.signals.price_features of the tracking ETF."""
    parts = {
        "trend": 0.6 * _clip(features["vs_sma50_pct"] / 5) + 0.4 * _clip(features["sma20_vs_sma50_pct"] / 2.5),
        "momentum": _clip(features["_ret20"] / ((features["_vol20"] or 0.01) * math.sqrt(20)) / 2),
    }
    if company_score is not None:
        parts["companies"] = _clip(company_score / 0.5)
    w = sum(WEIGHTS[k] for k in parts)
    return {"score": round(sum(WEIGHTS[k] * v for k, v in parts.items()) / w, 3), "parts": {k: round(v, 2) for k, v in parts.items()}}


def direction_of(score: float) -> str:
    return "bullish" if score > 0 else "bearish"


def entry_decision(score: float, verdict: dict, s: FuturesSettings) -> tuple[bool, str]:
    if abs(score) < s.min_quant_score:
        return False, f"data signal too weak ({score:+.2f})"
    want = direction_of(score)
    if verdict["direction"] != want:
        return False, f"Gemini research says {verdict['direction']}, data says {want}: no agreement"
    if verdict["conviction"] < s.min_conviction:
        return False, f"Gemini conviction {verdict['conviction']} below {s.min_conviction}"
    if verdict.get("major_event_within_2_days"):
        events = "; ".join(verdict.get("key_events") or []) or "a major scheduled event"
        return False, f"waiting: {events} within 2 days"
    return True, f"data {want} ({score:+.2f}) and Gemini {verdict['direction']} (conviction {verdict['conviction']})"


def round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 6)


@dataclass
class TradePlan:
    qty: int
    stop_points: float
    risk_per_contract: float
    reason: str = ""


def plan_size(price: float, daily_vol: float, inst: Instrument, budget: float, risk_in_use: float, s: FuturesSettings) -> TradePlan:
    """How many contracts, from how far away the stop is. daily_vol: the
    market's typical daily move as a fraction (e.g. 0.009)."""
    stop_points = max(round_to_tick(price * daily_vol * s.stop_atr_multiple, inst.tick), 4 * inst.tick)
    per_contract = stop_points * inst.point_value
    cap = min(budget * s.max_risk_per_trade_pct, budget * s.max_total_risk_pct - risk_in_use)
    qty = min(s.max_contracts_per_trade, int(cap // per_contract)) if per_contract > 0 else 0
    if qty < 1:
        return TradePlan(0, stop_points, per_contract,
                         f"one contract risks ${per_contract:,.0f} at the stop, limit left is ${max(cap, 0):,.0f}")
    return TradePlan(qty, stop_points, per_contract)


def stop_and_target(fill: float, long: bool, stop_points: float, inst: Instrument, s: FuturesSettings) -> tuple[float, float]:
    target_points = stop_points * s.reward_ratio
    if long:
        return round_to_tick(fill - stop_points, inst.tick), round_to_tick(fill + target_points, inst.tick)
    return round_to_tick(fill + stop_points, inst.tick), round_to_tick(fill - target_points, inst.tick)


def trading_days_between(start: date, end: date) -> int:
    days, d = 0, start
    while d < end:
        d = date.fromordinal(d.toordinal() + 1)
        if d.weekday() < 5:
            days += 1
    return days


def exit_decision(
    long: bool, opened: date, today: date, expiration: date | None, current_score: float | None,
    research_against: str | None, final_day: bool, s: FuturesSettings,
) -> str | None:
    """Reasons to close beyond the resting stop/target orders."""
    if final_day:
        return "the two-month run ends today"
    if expiration and (expiration - today).days < s.min_days_to_expiry:
        return f"contract expires {expiration}: closing before the last days"
    held = trading_days_between(opened, today)
    if held >= s.max_hold_days:
        return f"held {held} trading days (the limit is {s.max_hold_days})"
    if research_against:
        return f"research flipped: {research_against}"
    if current_score is not None:
        against = -current_score if long else current_score
        if against >= s.thesis_flip_score:
            return f"research flipped: data score now {current_score:+.2f} against the position"
    return None
