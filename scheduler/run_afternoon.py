#!/usr/bin/env python3
"""
PM session: risk check + tactical guard, deliberately NOT a full re-rebalance.

Run near the close (e.g. 3:30pm America/New_York) on trading days. This
session:
  1. Re-checks every safety guard (trial period, drawdown, price sanity,
     kill switch).
  2. Pulls fresh news sentiment for currently-held names via Finnhub.
  3. Trims (does not fully exit, and never adds new names) any position
     whose news sentiment is flagged strongly negative AND whose price has
     already dropped materially intraday - a cheap circuit breaker for
     "something bad clearly happened today", not a second alpha signal.

This design is a direct response to a real tension in the request: forcing
a second full re-optimization every afternoon, when the underlying factor
scores haven't meaningfully changed since the morning, would mostly just
generate turnover and transaction costs - i.e. it would make the strategy
WORSE, not better. So "twice a day" is implemented as "twice-daily
risk-checked", not "twice-daily re-alpha'd". See README.md "Honest
expectations" for the full reasoning.

Bounded trial: same check_trial_period guard as run_morning.py, and the same
TRIAL_REPORT.md fallback generation, so the trial reliably ends on schedule
even if this is the first session to notice the window closed (e.g. the AM
run didn't fire that day because the computer was offline).
"""
from __future__ import annotations

import sys
import traceback
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow running as `python scheduler/run_afternoon.py`

import pandas as pd

from config import KILL_SWITCH_FILE, STRATEGY
from data.alpaca_data import get_latest_quotes
from data.finnhub_data import get_news_sentiment_frame
from execution import decisions
from execution.alpaca_broker import AlpacaBroker
from execution.guards import (
    GuardFailure,
    check_drawdown_halt,
    check_kill_switch,
    check_market_hours,
    check_not_live_unless_triple_confirmed,
    check_price_sanity,
    check_trial_period,
    run_all_guards,
)
from execution.rationale import explain_trim_decision
from execution.rebalancer import PlannedOrder, execute_orders
from execution.reporting import write_trial_report
from execution.trade_log import TradeLogEntry, record_trades
from execution.trial import get_or_init_trial_start, get_or_init_trial_start_equity
from scheduler.common import setup_logging

TRIM_FRACTION = 0.5  # halve a flagged position rather than fully exiting on a news signal alone
INTRADAY_DROP_THRESHOLD = -0.03  # only trim if price has also actually moved, not on headlines alone


def _write_trial_report_safely(logger, trial_start: date, today: date) -> None:
    try:
        broker = AlpacaBroker()
        current_equity = broker.get_account_equity()
        current_positions = broker.get_positions()
        starting_equity = get_or_init_trial_start_equity(current_equity)
        write_trial_report(trial_start, today, starting_equity, current_equity, current_positions)
        logger.error("=== 30-DAY TRIAL COMPLETE === report written to TRIAL_REPORT.md")
    except Exception:
        logger.error("Could not generate TRIAL_REPORT.md:\n%s", traceback.format_exc())


def _record_safely(logger, fn, *args, **kwargs) -> None:
    """Report bookkeeping must never stop or fail a trading session."""
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        logger.warning("Could not write decision record:\n%s", traceback.format_exc())


def main() -> int:
    logger = setup_logging("run_afternoon")
    logger.info("=== PM session start ===")

    today = date.today()
    trial_start = get_or_init_trial_start(today)

    try:
        run_all_guards(
            [
                check_kill_switch(),
                check_not_live_unless_triple_confirmed(),
                check_market_hours(allow_extended=True),
                check_trial_period(trial_start, today),
            ]
        )

        broker = AlpacaBroker()
        current_positions = broker.get_positions()
        if not current_positions:
            logger.info("No open positions - nothing to risk-check. Exiting cleanly.")
            _record_safely(logger, decisions.record_session, "PM", "no_trades", "No open positions to risk-check.")
            return 0

        symbols = list(current_positions.keys())
        latest_quotes = get_latest_quotes(symbols).set_index("symbol")["price"]
        reference_prices = pd.Series({s: p.avg_entry_price for s, p in current_positions.items()})
        equity_curve = broker.get_portfolio_history()

        run_all_guards(
            [
                check_price_sanity(latest_quotes, reference_prices),
                check_drawdown_halt(equity_curve),
            ]
        )

        sentiment = get_news_sentiment_frame(symbols).set_index("symbol")

        planned: list[PlannedOrder] = []
        moves: dict[str, float] = {}
        for sym, pos in current_positions.items():
            intraday_move = (latest_quotes.get(sym, pos.current_price) / pos.avg_entry_price) - 1.0
            moves[sym] = intraday_move
            sentiment_row = sentiment.loc[sym] if sym in sentiment.index else None
            flagged = bool(sentiment_row["flagged_negative"]) if sentiment_row is not None else False
            if flagged and intraday_move <= INTRADAY_DROP_THRESHOLD:
                trim_qty = int(pos.qty * TRIM_FRACTION)
                if trim_qty > 0:
                    planned.append(
                        PlannedOrder(
                            symbol=sym,
                            side="sell",
                            qty=trim_qty,
                            reason=explain_trim_decision(sym, intraday_move, sentiment_row),
                        )
                    )

        flagged_names = [s for s in symbols if s in sentiment.index and bool(sentiment.loc[s, "flagged_negative"])]
        if not planned:
            logger.info("No positions triggered the news+price risk check. No trades placed.")
            _record_safely(
                logger, decisions.record_session, "PM", "no_trades",
                f"Risk-checked {len(symbols)} holding(s): none had both clearly negative news and a price drop of "
                f"{abs(INTRADAY_DROP_THRESHOLD):.0%}+ since entry.",
                positions=len(symbols), negative_news=flagged_names,
            )
        else:
            logger.warning("Trimming %d position(s) on news+price risk signal: %s", len(planned), [p.symbol for p in planned])
            results = execute_orders(broker, planned)
            for r in results:
                logger.info("Order result: %s", r)
            record_trades(
                [
                    TradeLogEntry(session="PM", symbol=r["symbol"], side=r["side"], qty=r["qty"], status=r["status"], reason=r["reason"])
                    for r in results
                ]
            )
            snapshots = {r["symbol"]: decisions.research_snapshot(r["symbol"], research=sentiment) for r in results}
            extra = {r["symbol"]: {"move_since_entry": moves.get(r["symbol"])} for r in results}
            _record_safely(logger, decisions.record_trades, "PM", results, snapshots, latest_quotes, extra=extra)
            _record_safely(
                logger, decisions.record_session, "PM", "completed", f"Trimmed {len(results)} position(s) on the news+price risk check",
                positions=len(symbols), negative_news=flagged_names,
            )

        logger.info("=== PM session complete ===")
        return 0

    except GuardFailure as gf:
        logger.error("Guard failure - no trades placed: %s", gf)
        _record_safely(logger, decisions.record_session, "PM", "halted", f"Safety check stopped the session: {gf}")
        if any(r.name == "trial_period" and not r.passed for r in gf.results):
            _write_trial_report_safely(logger, trial_start, today)
        return 2
    except Exception:
        logger.error("Unhandled exception in PM session:\n%s", traceback.format_exc())
        _record_safely(logger, decisions.record_session, "PM", "crashed", traceback.format_exc(limit=3))
        logger.error("Setting kill switch to prevent further automated runs until a human reviews this.")
        KILL_SWITCH_FILE.write_text(f"auto-triggered by run_afternoon.py crash at {datetime.now().isoformat()}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
