"""Shared setup for the AM/PM scheduled jobs: logging, universe, sector map."""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

from config import LOG_DIR
from data.universe import get_sector_map, get_sp500_constituents


def setup_logging(job_name: str) -> logging.Logger:
    logger = logging.getLogger("sp500_agent")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.FileHandler(LOG_DIR / f"{job_name}.log")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def load_universe():
    constituents = get_sp500_constituents()
    sector_map = get_sector_map()
    return constituents, sector_map


def lookback_window(as_of: date, calendar_days: int = 400) -> tuple[date, date]:
    return as_of - timedelta(days=calendar_days), as_of
