"""All settings, read from environment variables (GitHub secrets in the cloud)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # local runs can use a .env file; the cloud uses real env vars
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except ImportError:
    pass

PAPER_URL = "https://paper-api.alpaca.markets"


def _f(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _i(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    alpaca_key: str = field(default_factory=lambda: os.environ.get("ALPACA_API_KEY_ID", ""))
    alpaca_secret: str = field(default_factory=lambda: os.environ.get("ALPACA_API_SECRET_KEY", ""))
    alpaca_base_url: str = field(default_factory=lambda: os.environ.get("ALPACA_BASE_URL", PAPER_URL).rstrip("/"))
    alpaca_data_url: str = field(default_factory=lambda: os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/"))
    finnhub_key: str = field(default_factory=lambda: os.environ.get("FINNHUB_API_KEY", ""))
    fred_key: str = field(default_factory=lambda: os.environ.get("FRED_API_KEY", ""))
    anthropic_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))

    # Risk
    risk_per_trade: float = field(default_factory=lambda: _f("FX_RISK_PER_TRADE", 0.01))
    max_position_pct: float = field(default_factory=lambda: _f("FX_MAX_POSITION_PCT", 0.25))
    max_positions: int = field(default_factory=lambda: _i("FX_MAX_POSITIONS", 3))
    daily_loss_limit: float = field(default_factory=lambda: _f("FX_DAILY_LOSS_LIMIT", 0.03))
    entry_threshold: float = field(default_factory=lambda: _f("FX_ENTRY_THRESHOLD", 0.35))
    max_spread_pct: float = field(default_factory=lambda: _f("FX_MAX_SPREAD_PCT", 0.004))
    stop_atr_mult: float = field(default_factory=lambda: _f("FX_STOP_ATR_MULT", 1.5))
    target_atr_mult: float = field(default_factory=lambda: _f("FX_TARGET_ATR_MULT", 2.5))
    news_blackout_minutes: int = field(default_factory=lambda: _i("FX_NEWS_BLACKOUT_MINUTES", 30))

    # Trading-day timing, New York time, minutes after the 9:30 open / before the close
    no_entry_first_minutes: int = 15   # let the opening auction settle
    no_entry_last_minutes: int = 30    # no new trades in the last 30 min
    flatten_last_minutes: int = 25     # start closing everything 25 min before the close (two scheduled runs get a chance)

    state_dir: Path = field(default_factory=lambda: Path(os.environ.get("FX_STATE_DIR", "state")))

    @property
    def is_paper(self) -> bool:
        return "paper-api" in self.alpaca_base_url


def load() -> Settings:
    return Settings()
