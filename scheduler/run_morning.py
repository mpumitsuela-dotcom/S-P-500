#!/usr/bin/env python3
"""
AM session: full signal generation + rebalance to target weights.

Run once near/after the open (e.g. 9:45am America/New_York, after the
opening-auction volatility settles) on trading days. This is where the
factor model's edge - such as it is - actually gets expressed, by moving
the book toward the current top-ranked names, using value/quality/
momentum/low-vol AND current research (news sentiment + analyst consensus -
see data/finnhub_data.py get_research_frame, research/factors.py
compute_research_factor) together.

Bounded trial: this session enforces a fixed-length trial (default 30 days,
config.TRIAL_LENGTH_DAYS) via check_trial_period. The trial clock starts
itself on the very first run (execution/trial.py) - once it's up, this
guard fails, like any other guard, and no further trades are placed on
either session until a human reviews TRIAL_REPORT.md (auto-written right
here when the trial ends) and decides whether to continue.

Every order placed is logged with a plain-English rationale (execution/
rationale.py, execution/trade_log.py) referencing the specific factor
scores and research detail that drove it - see TRADE_LOG.md / trade_log.csv
and the "why" section of TRIAL_REPORT.md.

Exit code 0 = ran (with or without trades); non-zero = halted by a guard
or crashed. The scheduler wrapper (see SETUP_GUIDE.md) auto-triggers the
kill switch on a crash, per config.KILL_SWITCH_FILE, so a code bug can't
silently keep firing broken trades twice a day forever.
"""
from __future__ import annotations

import sys
import traceback
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow running as `python scheduler/run_morning.py`

import pandas as pd

from config import KILL_SWITCH_FILE, STRATEGY
from data.alpaca_data import get_daily_bars, get_latest_quotes
from data.finnhub_data import get_research_frame
from data.fmp_data import get_fundamentals_frame
from execution.alpaca_broker import AlpacaBroker
from execution.guards import (
    GuardFailure,
    check_data_freshness,
    check_drawdown_halt,
    check_kill_switch,
    check_market_hours,
    check_not_live_unless_triple_confirmed,
    check_price_sanity,
    check_trial_period,
    check_universe_coverage,
    run_all_guards,
)
from execution.rationale import explain_rebalance_decision
from execution.rebalancer import compute_rebalance_orders, execute_orders
from execution.reporting import write_trial_report
from execution.trade_log import TradeLogEntry, record_trades
from execution.trial import get_or_init_trial_start, get_or_init_trial_start_equity
from portfolio.construction import build_target_portfolio
from portfolio.risk import apply_vol_target, estimate_portfolio_volatility
from research.signals import compute_combined_scores
from scheduler.common import lookback_window, load_universe, setup_logging


def _write_trial_report_safely(logger, trial_start: date, today: date) -> None:
    """Called once the trial guard fails. Independently pulls current account
    state (cheap - two calls) so the report always gets written even if this
    run halted before the main flow ever reached the broker."""
    try:
        broker = AlpacaBroker()
        current_equity = broker.get_account_equity()
        current_positions = broker.get_positions()
        starting_equity = get_or_init_trial_start_equity(current_equity)
        write_trial_report(trial_start, today, starting_equity, current_equity, current_positions)
        logger.error("=== 30-DAY TRIAL COMPLETE === report written to TRIAL_REPORT.md")
    except Exception:
        logger.error("Could not generate TRIAL_REPORT.md:\n%s", traceback.format_exc())


def main() -> int:
    logger = setup_logging("run_morning")
    logger.info("=== AM session start ===")

    today = date.today()
    trial_start = get_or_init_trial_start(today)

    try:
        # Cheap pre-checks first, before spending any API budget on market/research data.
        run_all_guards(
            [
                check_kill_switch(),
                check_not_live_unless_triple_confirmed(),
                check_market_hours(),
                check_trial_period(trial_start, today),
            ]
        )

        constituents, sector_map = load_universe()
        symbols = constituents["symbol"].tolist()

        broker = AlpacaBroker()
        account_equity = broker.get_account_equity()
        get_or_init_trial_start_equity(account_equity)  # records the baseline exactly once, on the first real run
        current_positions = broker.get_positions()
        reference_prices = pd.Series({s: p.current_price for s, p in current_positions.items()})

        start, end = lookback_window(date.today())
        prices = get_daily_bars(symbols, start, end)
        fundamentals = get_fundamentals_frame(symbols)
        research = get_research_frame(symbols)
        latest_quotes = get_latest_quotes(symbols).set_index("symbol")["price"]
        equity_curve = broker.get_portfolio_history()

        run_all_guards(
            [
                check_data_freshness(prices, pd.Timestamp(date.today())),
                check_price_sanity(latest_quotes, reference_prices),
                check_drawdown_halt(equity_curve),
            ]
        )

        scores = compute_combined_scores(prices, sector_map, fundamentals=fundamentals, research=research)
        scores = scores[scores.index.isin(latest_quotes.index)]

        run_all_guards([check_universe_coverage(scores, sector_map)])

        target = build_target_portfolio(scores, sector_map)
        weights_series = target.set_index("symbol")["target_weight"]
        realized_vol = estimate_portfolio_volatility(prices, weights_series)
        scaled_weights = apply_vol_target(weights_series, realized_vol)
        target = target.assign(target_weight=target["symbol"].map(scaled_weights))

        sizing_equity = account_equity
        if STRATEGY.capital_budget > 0:
            sizing_equity = min(account_equity, STRATEGY.capital_budget)
            if account_equity > 1.5 * STRATEGY.capital_budget:
                logger.warning(
                    "Account equity $%.0f is well above the $%.0f budget - only the budget is traded, so the "
                    "trial return (measured on the whole account) will understate it. Reset the paper account "
                    "balance to the budget in the Alpaca dashboard.",
                    account_equity, STRATEGY.capital_budget,
                )
        orders = compute_rebalance_orders(target, current_positions, sizing_equity, latest_quotes)
        logger.info("Planned %d orders (realized vol %.1f%%, target %.1f%%)", len(orders), realized_vol * 100, STRATEGY.target_annual_vol * 100)

        if not orders:
            logger.info("No orders meet the minimum-drift threshold - book is already close to target. No trades placed.")
        else:
            # Rationale for every order, before execution, so it's logged even if a later order in the batch errors.
            for o in orders:
                o.reason = explain_rebalance_decision(o.symbol, o.side, scores, research)

            results = execute_orders(broker, orders)
            for r in results:
                logger.info("Order result: %s", r)

            record_trades(
                [
                    TradeLogEntry(session="AM", symbol=r["symbol"], side=r["side"], qty=r["qty"], status=r["status"], reason=r["reason"])
                    for r in results
                ]
            )

        logger.info("=== AM session complete ===")
        return 0

    except GuardFailure as gf:
        logger.error("Guard failure - no trades placed: %s", gf)
        if any(r.name == "trial_period" and not r.passed for r in gf.results):
            _write_trial_report_safely(logger, trial_start, today)
        return 2
    except Exception:
        logger.error("Unhandled exception in AM session:\n%s", traceback.format_exc())
        logger.error("Setting kill switch to prevent further automated runs until a human reviews this.")
        KILL_SWITCH_FILE.write_text(f"auto-triggered by run_morning.py crash at {datetime.now().isoformat()}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
