"""
Settings for the options (contracts) agent. Every value can be changed with
an environment variable (set in .github/workflows/options-agent.yml) without
touching code.

Money-at-risk settings (budget, per-trade and total limits, the drawdown
halt, the run length) are the owner's decisions - see
.claude/skills/trading-agent-ops/SKILL.md.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


# Liquid, optionable large companies. Weekly/monthly options on all of these
# trade actively, so spreads are narrow enough for a small account.
DEFAULT_WATCHLIST = (
    "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,AMD,NFLX,CRM,ORCL,ADBE,QCOM,INTC,"
    "JPM,BAC,GS,V,MA,UNH,LLY,MRK,ABBV,JNJ,XOM,CVX,COST,WMT,HD,KO,PEP,PG,DIS,"
    "CAT,BA,IBM,UBER,PLTR,MU"
)


@dataclass(frozen=True)
class OptionsSettings:
    # The whole experiment: $10,000 for two months.
    budget: float = field(default_factory=lambda: _f("OPT_BUDGET", "10000"))
    run_days: int = field(default_factory=lambda: _i("OPT_RUN_DAYS", "61"))
    # No new positions in the final days, so the run ends on closed trades.
    no_new_entries_last_days: int = field(default_factory=lambda: _i("OPT_NO_NEW_ENTRIES_LAST_DAYS", "10"))

    watchlist: tuple[str, ...] = field(
        default_factory=lambda: tuple(s.strip().upper() for s in os.environ.get("OPT_WATCHLIST", DEFAULT_WATCHLIST).split(",") if s.strip())
    )

    # Risk limits. Only options are BOUGHT (calls or puts), so the most any
    # position can lose is what was paid for it.
    max_premium_per_trade_pct: float = field(default_factory=lambda: _f("OPT_MAX_TRADE_PCT", "0.08"))
    max_total_premium_pct: float = field(default_factory=lambda: _f("OPT_MAX_TOTAL_PCT", "0.40"))
    max_open_positions: int = field(default_factory=lambda: _i("OPT_MAX_POSITIONS", "5"))
    max_new_per_day: int = field(default_factory=lambda: _i("OPT_MAX_NEW_PER_DAY", "2"))
    drawdown_halt_pct: float = field(default_factory=lambda: _f("OPT_DRAWDOWN_HALT", "0.25"))

    # Exits
    # Owner's choice (30 Sep, D): at +60% sell half and let the rest run with the
    # trailing stop; the rest is also sold if its gain falls back to +20%.
    take_profit_pct: float = field(default_factory=lambda: _f("OPT_TAKE_PROFIT", "0.60"))
    take_profit_fraction: float = field(default_factory=lambda: _f("OPT_TAKE_PROFIT_FRACTION", "0.5"))
    runner_floor_pct: float = field(default_factory=lambda: _f("OPT_RUNNER_FLOOR", "0.20"))
    stop_loss_pct: float = field(default_factory=lambda: _f("OPT_STOP_LOSS", "0.45"))
    trail_arm_pct: float = field(default_factory=lambda: _f("OPT_TRAIL_ARM", "0.30"))
    trail_giveback_pct: float = field(default_factory=lambda: _f("OPT_TRAIL_GIVEBACK", "0.25"))
    min_dte_hold: int = field(default_factory=lambda: _i("OPT_MIN_DTE_HOLD", "14"))
    # Opposite-direction score that counts as "the research changed its mind".
    thesis_flip_score: float = field(default_factory=lambda: _f("OPT_THESIS_FLIP", "0.30"))
    rereview_days: int = field(default_factory=lambda: _i("OPT_REREVIEW_DAYS", "3"))

    # Entry research thresholds
    min_quant_score: float = field(default_factory=lambda: _f("OPT_MIN_QUANT_SCORE", "0.35"))
    min_conviction: int = field(default_factory=lambda: _i("OPT_MIN_CONVICTION", "65"))
    # Going against the overall market (a call while SPY is below its 50-day
    # average, or a put while above) needs this much conviction instead.
    min_conviction_against_market: int = field(default_factory=lambda: _i("OPT_MIN_CONVICTION_AGAINST_MARKET", "75"))
    max_candidates_for_ai: int = field(default_factory=lambda: _i("OPT_MAX_AI_CANDIDATES", "6"))
    # Owner's choices (30 Sep): A) skip options priced for much bigger moves than
    # the stock really makes; B) Gemini's expected move must cover break-even;
    # C) at most this many positions in one sector.
    max_iv_ratio: float = field(default_factory=lambda: _f("OPT_MAX_IV_RATIO", "1.5"))
    max_iv_premium: float = field(default_factory=lambda: _f("OPT_MAX_IV_PREMIUM", "0.10"))
    require_move_covers_breakeven: bool = field(default_factory=lambda: os.environ.get("OPT_REQUIRE_BREAKEVEN", "1") != "0")
    max_per_sector: int = field(default_factory=lambda: _i("OPT_MAX_PER_SECTOR", "2"))
    earnings_blackout_days: int = field(default_factory=lambda: _i("OPT_EARNINGS_BLACKOUT_DAYS", "7"))
    require_gemini: bool = field(default_factory=lambda: os.environ.get("OPT_REQUIRE_GEMINI", "1") != "0")

    # Contract choice
    min_dte: int = field(default_factory=lambda: _i("OPT_MIN_DTE", "30"))
    max_dte: int = field(default_factory=lambda: _i("OPT_MAX_DTE", "60"))
    target_delta: float = field(default_factory=lambda: _f("OPT_TARGET_DELTA", "0.55"))
    min_delta: float = field(default_factory=lambda: _f("OPT_MIN_DELTA", "0.30"))
    max_delta: float = field(default_factory=lambda: _f("OPT_MAX_DELTA", "0.75"))
    max_spread_pct: float = field(default_factory=lambda: _f("OPT_MAX_SPREAD_PCT", "0.12"))
    min_open_interest: int = field(default_factory=lambda: _i("OPT_MIN_OPEN_INTEREST", "100"))

    order_wait_seconds: int = field(default_factory=lambda: _i("OPT_ORDER_WAIT_SECONDS", "60"))


SETTINGS = OptionsSettings()
