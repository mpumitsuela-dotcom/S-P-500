"""
Central configuration for the sp500_agent package.

All secrets are read from environment variables (or a local .env file loaded
via python-dotenv). Nothing here should ever contain a real API key.

Free-tier data sources used by this build:
  - Alpaca Market Data API   : OHLCV bars + latest quotes (free with any Alpaca account)
  - Alpaca Trading API       : paper-trading order execution
  - Financial Modeling Prep  : fundamentals (P/E, P/B, ROE, margins, growth) - free tier
  - Finnhub                  : company news + basic financials / sentiment  - free tier
  - Wikipedia                : current S&P 500 constituent list (no key required)

See SETUP_GUIDE.md for where to get each free key and the free-tier limits
that shape the rate-limiting choices made in data/cache.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # python-dotenv is optional; env vars can be set directly instead.
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get("SP500_AGENT_CACHE_DIR", PROJECT_ROOT / ".cache"))
STATE_DIR = Path(os.environ.get("SP500_AGENT_STATE_DIR", PROJECT_ROOT / ".state"))
LOG_DIR = Path(os.environ.get("SP500_AGENT_LOG_DIR", PROJECT_ROOT / "logs"))
for _d in (CACHE_DIR, STATE_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class APIKeys:
    alpaca_key_id: str = field(default_factory=lambda: os.environ.get("ALPACA_API_KEY_ID", ""))
    alpaca_secret_key: str = field(default_factory=lambda: os.environ.get("ALPACA_API_SECRET_KEY", ""))
    # Alpaca has two base URLs: paper and live. This build refuses to run
    # against anything but the paper URL unless LIVE_TRADING_CONFIRMED=YES_I_UNDERSTAND
    # is also set (see execution/guards.py: triple-gated live trading).
    alpaca_base_url: str = field(
        default_factory=lambda: os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    )
    alpaca_data_url: str = field(
        default_factory=lambda: os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets")
    )
    fmp_key: str = field(default_factory=lambda: os.environ.get("FMP_API_KEY", ""))
    finnhub_key: str = field(default_factory=lambda: os.environ.get("FINNHUB_API_KEY", ""))

    def missing(self) -> list[str]:
        missing = []
        if not self.alpaca_key_id or not self.alpaca_secret_key:
            missing.append("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY")
        if not self.fmp_key:
            missing.append("FMP_API_KEY")
        if not self.finnhub_key:
            missing.append("FINNHUB_API_KEY")
        return missing


@dataclass(frozen=True)
class StrategyConfig:
    # Universe
    universe_size: int = int(os.environ.get("SP500_UNIVERSE_SIZE", "500"))

    # Factor weights (must sum to 1.0) - see research/factors.py for definitions.
    # weight_research (news sentiment + analyst consensus, data/finnhub_data.py
    # get_research_frame) was added so live decisions are explicitly driven in
    # part by current research, not just price/fundamentals history - see
    # research/signals.py for how it's renormalized away in the backtester,
    # where no point-in-time historical research data is available.
    weight_value: float = float(os.environ.get("SP500_WEIGHT_VALUE", "0.20"))
    weight_quality: float = float(os.environ.get("SP500_WEIGHT_QUALITY", "0.20"))
    weight_momentum: float = float(os.environ.get("SP500_WEIGHT_MOMENTUM", "0.25"))
    weight_low_vol: float = float(os.environ.get("SP500_WEIGHT_LOW_VOL", "0.15"))
    weight_research: float = float(os.environ.get("SP500_WEIGHT_RESEARCH", "0.20"))

    # Portfolio construction
    num_positions: int = int(os.environ.get("SP500_NUM_POSITIONS", "30"))
    max_position_weight: float = float(os.environ.get("SP500_MAX_POSITION_WEIGHT", "0.06"))
    max_sector_weight: float = float(os.environ.get("SP500_MAX_SECTOR_WEIGHT", "0.25"))
    cash_buffer: float = float(os.environ.get("SP500_CASH_BUFFER", "0.02"))

    # Risk
    max_daily_drawdown_halt: float = float(os.environ.get("SP500_MAX_DAILY_DD_HALT", "0.06"))
    max_position_daily_move_sanity: float = float(os.environ.get("SP500_PRICE_SANITY_PCT", "0.35"))
    target_annual_vol: float = float(os.environ.get("SP500_TARGET_VOL", "0.15"))

    # Turnover control - the PM session should NOT re-trade the full book;
    # it only acts on guard breaches or material overnight news, to avoid
    # bleeding out edge in transaction costs. See scheduler/run_afternoon.py.
    min_rebalance_drift: float = float(os.environ.get("SP500_MIN_REBALANCE_DRIFT", "0.015"))
    max_turnover_per_rebalance: float = float(os.environ.get("SP500_MAX_TURNOVER", "0.35"))

    # Costs assumed in backtests (Alpaca is commission-free, but spread/slippage are not)
    assumed_slippage_bps: float = float(os.environ.get("SP500_SLIPPAGE_BPS", "5"))

    account_equity_fallback: float = float(os.environ.get("SP500_PAPER_EQUITY_FALLBACK", "100000"))


API_KEYS = APIKeys()
STRATEGY = StrategyConfig()

KILL_SWITCH_FILE = STATE_DIR / "KILL_SWITCH"
LIVE_TRADING_ENV_FLAG = "LIVE_TRADING_CONFIRMED"
LIVE_TRADING_CONFIRM_VALUE = "YES_I_UNDERSTAND"

# Bounded trial: run for a fixed number of days, then stop automatically and
# wait for a human decision, rather than trading on indefinitely by default.
# See execution/trial.py and execution/guards.py check_trial_period.
TRIAL_LENGTH_DAYS = int(os.environ.get("SP500_TRIAL_DAYS", "30"))
