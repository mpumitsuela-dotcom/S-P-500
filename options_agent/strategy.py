"""
The trading rules, as pure functions (no network), so every rule is tested.

Entry, per company, all of these must hold:
  1. The agent's own data score is strong enough (|score| >= min_quant_score)
     and the stock isn't already stretched that way (RSI < 75 for a call,
     > 25 for a put).
  2. No earnings report within earnings_blackout_days.
  3. Gemini's deep research, done independently with live Google Search,
     reaches the SAME direction with conviction >= min_conviction (higher
     when betting against the overall market's trend).
  4. A liquid contract exists 30-60 days out, near delta 0.55, with a tight
     bid/ask spread, that fits the per-trade and total money limits.

Exit, per position, the first that applies:
  final day of the run -> stop loss -> earnings tomorrow -> close to
  expiry -> take profit -> trailing stop after a good gain -> Gemini's
  re-review or the data score turned against the position.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from options_agent.settings import OptionsSettings


def direction_of(score: float) -> str:
    return "bullish" if score > 0 else "bearish"


def kind_for(direction: str) -> str:
    return "call" if direction == "bullish" else "put"


def pick_candidates(rows: list[dict], held_underlyings: set[str], s: OptionsSettings) -> tuple[list[dict], list[dict]]:
    """rows: scored companies, strongest first. Returns (candidates, skipped)
    where skipped entries carry a plain-words reason."""
    picked, skipped = [], []
    for r in rows:
        score = r["score"]
        why = None
        if r["symbol"] in held_underlyings:
            why = "already holding a contract on it"
        elif abs(score) < s.min_quant_score:
            why = f"data signal too weak ({score:+.2f})"
        elif score > 0 and r.get("rsi14", 50) >= 75:
            why = f"already overbought (RSI {r['rsi14']:.0f})"
        elif score < 0 and r.get("rsi14", 50) <= 25:
            why = f"already oversold (RSI {r['rsi14']:.0f})"
        elif r.get("days_to_earnings") is not None and r["days_to_earnings"] <= s.earnings_blackout_days:
            why = f"earnings in {r['days_to_earnings']} days ({r['earnings_date']})"
        if why:
            if abs(score) >= s.min_quant_score:
                skipped.append({"symbol": r["symbol"], "score": score, "reason": why})
            continue
        picked.append(r)
        if len(picked) >= s.max_candidates_for_ai:
            break
    return picked, skipped


def entry_decision(row: dict, verdict: dict, regime: dict, s: OptionsSettings, today: date) -> tuple[bool, str]:
    quant_dir = direction_of(row["score"])
    if verdict["direction"] != quant_dir:
        return False, f"Gemini research says {verdict['direction']}, data says {quant_dir}: no agreement"
    against_market = (quant_dir == "bullish" and regime.get("trend") == "down") or (
        quant_dir == "bearish" and regime.get("trend") == "up"
    )
    needed = s.min_conviction_against_market if against_market else s.min_conviction
    if verdict["conviction"] < needed:
        extra = " (going against the market trend)" if against_market else ""
        return False, f"Gemini conviction {verdict['conviction']} below {needed}{extra}"
    if verdict.get("next_earnings_date"):
        try:
            days = (date.fromisoformat(verdict["next_earnings_date"]) - today).days
        except ValueError:
            days = None
        if days is not None and 0 <= days <= s.earnings_blackout_days:
            return False, f"Gemini found earnings on {verdict['next_earnings_date']}"
    return True, f"data {quant_dir} ({row['score']:+.2f}) and Gemini {verdict['direction']} (conviction {verdict['conviction']})"


# --- contract choice -----------------------------------------------------

def bs_delta(spot: float, strike: float, years: float, vol: float, kind: str, rate: float = 0.04) -> float:
    """Black-Scholes delta, used only when Tradier's chain has no greeks."""
    if years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
    nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    return nd1 if kind == "call" else nd1 - 1


@dataclass
class ContractChoice:
    symbol: str
    expiration: str
    strike: float
    kind: str
    bid: float
    ask: float
    mid: float
    delta: float
    open_interest: int
    dte: int

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid if self.mid else 1.0


def choose_contract(
    contracts: list[dict], spot: float, kind: str, today: date,
    max_premium_dollars: float, annual_vol: float, s: OptionsSettings,
) -> tuple[ContractChoice | None, str]:
    """Best contract: liquid, 30-60 days, delta nearest target, affordable.
    contracts: {symbol, strike, expiration, bid, ask, open_interest, delta, iv}
    (options_agent/broker.py option_chain). Returns (choice, reason when none)."""
    usable: list[ContractChoice] = []
    reasons = {"no quote": 0, "spread too wide": 0, "open interest too low": 0, "delta out of range": 0, "too expensive": 0}
    for c in contracts:
        bid, ask = float(c.get("bid") or 0), float(c.get("ask") or 0)
        if bid <= 0 or ask <= 0 or ask < bid:
            reasons["no quote"] += 1
            continue
        mid = (bid + ask) / 2
        if (ask - bid) / mid > s.max_spread_pct:
            reasons["spread too wide"] += 1
            continue
        oi = int(c.get("open_interest") or 0)
        if oi < s.min_open_interest:
            reasons["open interest too low"] += 1
            continue
        dte = (date.fromisoformat(c["expiration"]) - today).days
        strike = float(c["strike"])
        delta = c.get("delta")
        if delta is None:
            delta = bs_delta(spot, strike, dte / 365, float(c.get("iv") or annual_vol), kind)
        adelta = abs(float(delta))
        if not (s.min_delta <= adelta <= s.max_delta):
            reasons["delta out of range"] += 1
            continue
        if ask * 100 > max_premium_dollars:
            reasons["too expensive"] += 1
            continue
        usable.append(ContractChoice(c["symbol"], c["expiration"], strike, kind, bid, ask, round(mid, 2), round(adelta, 3), oi, dte))
    if not usable:
        worst = ", ".join(f"{k}: {v}" for k, v in reasons.items() if v) or "no contracts listed"
        return None, f"no suitable contract ({worst})"
    # Closest to the target delta; ties go to the tighter spread, then more open interest.
    usable.sort(key=lambda c: (round(abs(c.delta - s.target_delta), 2), round(c.spread_pct, 3), -c.open_interest))
    return usable[0], ""


def entry_limit_price(bid: float, ask: float) -> float:
    """Pay a little above the middle of the bid/ask, never the full ask."""
    from options_agent.broker import tick_round

    return min(ask, tick_round((bid + ask) / 2 + 0.1 * (ask - bid), up=True))


def exit_limit_price(bid: float, ask: float, urgent: bool) -> float:
    from options_agent.broker import tick_round

    if bid <= 0:
        return 0.01
    if urgent:
        return tick_round(bid, up=False)
    return max(bid, tick_round((bid + ask) / 2 - 0.25 * (ask - bid), up=False))


def contracts_to_buy(ask: float, budget: float, premium_at_risk: float, s: OptionsSettings) -> tuple[int, str]:
    per_trade = budget * s.max_premium_per_trade_pct
    room = budget * s.max_total_premium_pct - premium_at_risk
    cap = min(per_trade, room)
    cost = ask * 100
    if cost <= 0:
        return 0, "no price"
    qty = int(cap // cost)
    if qty < 1:
        return 0, f"one contract costs ${cost:,.0f}, limit left is ${max(cap, 0):,.0f}"
    return qty, ""


# --- exits ---------------------------------------------------------------

@dataclass
class HeldPosition:
    symbol: str
    underlying: str
    kind: str
    qty: int
    entry_price: float
    current_price: float
    dte: int
    peak_gain: float = 0.0  # best gain seen, as a fraction
    days_to_earnings: int | None = None
    current_score: float | None = None  # today's data score for the company
    research_against: str | None = None  # set when Gemini's re-review turned against it

    @property
    def gain(self) -> float:
        return self.current_price / self.entry_price - 1 if self.entry_price else 0.0


def exit_decision(p: HeldPosition, s: OptionsSettings, final_day: bool) -> tuple[str | None, bool]:
    """(reason to sell, urgent). urgent sells hit the bid to be sure of a fill."""
    g = p.gain
    if final_day:
        return "the two-month run ends today", True
    if g <= -s.stop_loss_pct:
        return f"stop loss: down {g:.0%}", True
    if p.days_to_earnings is not None and p.days_to_earnings <= 1:
        return "earnings report tomorrow: not holding through it", True
    if p.dte <= s.min_dte_hold:
        return f"{p.dte} days to expiry: time decay speeds up from here", False
    if g >= s.take_profit_pct:
        return f"take profit: up {g:.0%}", False
    if p.peak_gain >= s.trail_arm_pct and g <= p.peak_gain - s.trail_giveback_pct:
        return f"trailing stop: gave back from +{p.peak_gain:.0%} to {g:+.0%}", False
    if p.research_against:
        return f"research flipped: {p.research_against}", False
    if p.current_score is not None:
        against = -p.current_score if p.kind == "call" else p.current_score
        if against >= s.thesis_flip_score:
            return f"research flipped: data score now {p.current_score:+.2f} against the position", False
    return None, False
