"""
Settings for the futures agent. Every value can be changed with an
environment variable (set in .github/workflows/futures-agent.yml).

Money-at-risk settings (budget, risk per trade, total risk, loss limit, run
length) are the owner's decisions - see .claude/skills/trading-agent-ops/SKILL.md.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Instrument:
    root: str          # Tradovate product code, e.g. MES
    name: str
    point_value: float  # dollars per 1.00 move of the futures price, per contract
    tick: float         # smallest price step
    proxy: str          # ETF that tracks the same market; used for free daily price history
    # Companies whose research feeds this market's score (the biggest weights).
    companies: tuple[str, ...] = ()


# Micro contracts only: 1/10th of the standard size, which is what fits $10,000.
INSTRUMENTS: dict[str, Instrument] = {
    i.root: i
    for i in (
        Instrument("MES", "Micro S&P 500", 5.0, 0.25, "SPY",
                   ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "BRK.B", "JPM", "LLY", "TSLA", "XOM")),
        Instrument("MNQ", "Micro Nasdaq-100", 2.0, 0.25, "QQQ",
                   ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "COST", "NFLX", "AMD", "ADBE")),
        Instrument("MYM", "Micro Dow", 0.5, 1.0, "DIA",
                   ("GS", "UNH", "MSFT", "HD", "CAT", "V", "AMGN", "MCD", "AXP", "JPM")),
        Instrument("M2K", "Micro Russell 2000", 5.0, 0.1, "IWM"),
        Instrument("MGC", "Micro Gold", 10.0, 0.1, "GLD"),
        Instrument("MCL", "Micro Crude Oil", 100.0, 0.01, "USO"),
    )
}


@dataclass(frozen=True)
class FuturesSettings:
    budget: float = field(default_factory=lambda: _f("FUT_BUDGET", "10000"))
    run_days: int = field(default_factory=lambda: _i("FUT_RUN_DAYS", "61"))
    no_new_entries_last_days: int = field(default_factory=lambda: _i("FUT_NO_NEW_ENTRIES_LAST_DAYS", "5"))
    instruments: tuple[str, ...] = field(
        default_factory=lambda: tuple(s.strip().upper() for s in os.environ.get("FUT_INSTRUMENTS", ",".join(INSTRUMENTS)).split(",") if s.strip())
    )

    # Risk. Every position has a stop-loss order resting at Tradovate from the
    # moment it opens, so a loss is capped even when the agent isn't running
    # (futures trade nearly 24 hours). A fast market can still jump past a stop.
    max_risk_per_trade_pct: float = field(default_factory=lambda: _f("FUT_MAX_RISK_PER_TRADE", "0.05"))
    max_total_risk_pct: float = field(default_factory=lambda: _f("FUT_MAX_TOTAL_RISK", "0.12"))
    max_open_positions: int = field(default_factory=lambda: _i("FUT_MAX_POSITIONS", "3"))
    max_contracts_per_trade: int = field(default_factory=lambda: _i("FUT_MAX_CONTRACTS", "2"))
    drawdown_halt_pct: float = field(default_factory=lambda: _f("FUT_DRAWDOWN_HALT", "0.20"))
    # Stop distance = this many typical daily moves; profit target = reward_ratio x stop.
    stop_atr_multiple: float = field(default_factory=lambda: _f("FUT_STOP_ATR", "1.5"))
    reward_ratio: float = field(default_factory=lambda: _f("FUT_REWARD_RATIO", "2.0"))
    max_hold_days: int = field(default_factory=lambda: _i("FUT_MAX_HOLD_DAYS", "10"))
    # Don't hold into the contract's last days; the agent picks the next contract instead.
    min_days_to_expiry: int = field(default_factory=lambda: _i("FUT_MIN_DAYS_TO_EXPIRY", "7"))
    thesis_flip_score: float = field(default_factory=lambda: _f("FUT_THESIS_FLIP", "0.30"))

    # Research thresholds
    min_quant_score: float = field(default_factory=lambda: _f("FUT_MIN_QUANT_SCORE", "0.35"))
    min_conviction: int = field(default_factory=lambda: _i("FUT_MIN_CONVICTION", "65"))

    order_wait_seconds: int = field(default_factory=lambda: _i("FUT_ORDER_WAIT_SECONDS", "45"))


SETTINGS = FuturesSettings()
