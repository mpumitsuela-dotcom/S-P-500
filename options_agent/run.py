#!/usr/bin/env python3
"""
Entry point for the options agent: `python -m options_agent.run`.

GitHub Actions runs this every 15 minutes on weekdays
(.github/workflows/options-agent.yml). Each firing, in New York time:

  manage    9:45-15:50, every firing: check each contract held against the
            exit rules and sell where one applies.
  research  11:00-13:00, once a day: score the watchlist, have Gemini
            research the strongest candidates, buy contracts where the
            data and Gemini agree, and re-review held positions.
            (11:00 keeps it clear of the S&P 500 agent's morning run,
            which shares the free Finnhub quota.)
  report    16:10-17:30, once a day: the daily report as a GitHub issue.

State lives on the `options-agent-state` branch. A crash sets the kill
switch, which stops NEW trades only - the agent keeps managing and selling
what it holds, so a bug can't leave a losing contract unwatched.
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import LOG_DIR, PROJECT_ROOT  # noqa: E402
from options_agent import broker as alp  # noqa: E402
from options_agent import gemini_research, journal, report, signals, strategy  # noqa: E402
from options_agent.settings import SETTINGS as S  # noqa: E402
from scheduler import shared_state  # noqa: E402

NY = ZoneInfo("America/New_York")
MANAGE_WINDOW = (dtime(9, 45), dtime(15, 50))
RESEARCH_WINDOW = (dtime(11, 0), dtime(13, 0))
REPORT_WINDOW = (dtime(16, 10), dtime(17, 30))

logger = logging.getLogger("options_agent")


def setup_logging() -> None:
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_DIR / "options_agent.log")):
        h.setFormatter(fmt)
        logger.addHandler(h)


# --- alerts ---------------------------------------------------------------

def alert(key: str, title: str, body: str, day: date, owner_decision: bool = False) -> None:
    """One GitHub issue per problem per day, never raising."""
    sent = journal.load("alerts.json", {})
    if sent.get(key) == day.isoformat():
        return
    try:
        from execution.daily_report import publish_issue

        prefix = "🟠 Options agent: decision needed" if owner_decision else "⚠️ Options agent needs attention"
        run_link = "/".join(os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY"))
        if os.environ.get("GITHUB_RUN_ID"):
            body += f"\n\nRun logs: {run_link}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        publish_issue(f"{prefix} — {title}", body, "", "needs-attention")
        sent[key] = day.isoformat()
        journal.save("alerts.json", sent)
    except Exception:  # noqa: BLE001
        logger.warning("Could not post alert %s", key, exc_info=True)


# --- positions -----------------------------------------------------------

def held_positions(today: date) -> list[dict]:
    """Alpaca option positions joined with the agent's own notes and live quotes."""
    raw = alp.get_option_positions()
    notes = journal.load("positions.json", {})
    snaps = alp.get_quotes([p["symbol"] for p in raw]) if raw else {}
    out = []
    for p in raw:
        occ = alp.parse_occ(p["symbol"])
        bid, ask = alp.quote_of(snaps.get(p["symbol"], {}))
        quote_ok = bid > 0 and ask >= bid
        # No live quote: fall back to Alpaca's own valuation of the position. Never
        # treat a missing price as zero - that would look like a 100% loss and
        # trigger the stop-loss on a data glitch.
        mark = (bid + ask) / 2 if quote_ok else float(p.get("current_price") or 0)
        entry = float(p.get("avg_entry_price") or 0)
        n = notes.get(p["symbol"], {})
        out.append({
            "symbol": p["symbol"], "underlying": occ.underlying, "kind": occ.kind, "strike": occ.strike,
            "expiration": occ.expiration.isoformat(), "dte": (occ.expiration - today).days,
            "qty": int(abs(float(p["qty"]))), "entry_price": entry, "current_price": round(mark, 2),
            "bid": bid, "ask": ask, "quote_ok": quote_ok, "priced": mark > 0,
            "gain": (mark / entry - 1) if entry and mark > 0 else 0.0,
            "why": n.get("why", "(bought before this agent kept notes)"), "notes": n,
        })
    # Forget notes on contracts no longer held (sold, or expired).
    held = {p["symbol"] for p in out}
    if set(notes) - held:
        journal.save("positions.json", {k: v for k, v in notes.items() if k in held})
    return out


def premium_at_risk(positions: list[dict]) -> float:
    return sum(p["qty"] * p["entry_price"] * 100 for p in positions)


def record_trade(day: date, side: str, symbol: str, label: str, qty: int, order: dict, reason: str, extra: dict | None = None) -> dict:
    filled, price = order["filled_qty"], order["fill_price"]
    rec = {
        "date": day.isoformat(), "time": alp.now_iso(), "side": side, "symbol": symbol, "label": label,
        "qty": qty, "filled_qty": filled, "fill_price": price, "status": order.get("status"),
        "order_id": order.get("id"), "reason": reason, **(extra or {}),
    }
    journal.append("trades.jsonl", rec)
    return rec


def label_of(p: dict) -> str:
    return f"{p['underlying']} ${p['strike']:g} {p['kind']} exp {p['expiration']}"


# --- sessions --------------------------------------------------------------

def manage(today: date, final_day: bool) -> None:
    positions = held_positions(today)
    notes = journal.load("positions.json", {})
    scores = journal.load("run.json", {}).get("latest_scores", {})
    scores = scores.get("values", {}) if scores.get("date") == today.isoformat() else {}
    for p in positions:
        n = notes.setdefault(p["symbol"], {})
        if not p["priced"] and not final_day:
            logger.warning("No price for %s: skipping its exit checks this run", p["symbol"])
            alert(f"no_price_{p['symbol']}", f"no price for {label_of(p)}",
                  f"Neither a live quote nor Alpaca's valuation was available for {label_of(p)}, so its exit rules "
                  "(stop-loss, take-profit) couldn't be checked. They are retried every 30 minutes. This is a "
                  "technical problem to look into if it persists.", today)
            continue
        n["peak_gain"] = max(float(n.get("peak_gain", 0.0)), p["gain"])
        earnings = n.get("earnings_date")
        hp = strategy.HeldPosition(
            symbol=p["symbol"], underlying=p["underlying"], kind=p["kind"], qty=p["qty"],
            entry_price=p["entry_price"], current_price=p["current_price"], dte=p["dte"],
            peak_gain=n["peak_gain"], days_to_earnings=signals.days_to_earnings(earnings, today),
            current_score=scores.get(p["underlying"]), research_against=n.get("research_against"),
        )
        reason, urgent = strategy.exit_decision(hp, S, final_day)
        if not reason:
            continue
        logger.info("Selling %s: %s", p["symbol"], reason)
        if p["quote_ok"]:
            price = strategy.exit_limit_price(p["bid"], p["ask"], urgent)
            order = alp.submit_and_wait(p["symbol"], p["qty"], "sell", price, S.order_wait_seconds)
        else:
            order = {"filled_qty": 0, "status": "no quote"}
        # A stop-loss, earnings or end-of-run sale must not wait 30 minutes for
        # the next try: sell whatever is left at the market.
        left = p["qty"] - order["filled_qty"]
        if left > 0 and (urgent or not p["quote_ok"]):
            logger.info("Limit sale of %s didn't fill: selling %d at the market", p["symbol"], left)
            mkt = alp.submit_and_wait(p["symbol"], left, "sell", None, S.order_wait_seconds)
            filled = order["filled_qty"] + mkt["filled_qty"]
            avg = ((order["filled_qty"] * order.get("fill_price", 0) + mkt["filled_qty"] * mkt["fill_price"]) / filled) if filled else 0.0
            order = {**mkt, "filled_qty": filled, "fill_price": avg}
        rec = record_trade(today, "sell", p["symbol"], label_of(p), p["qty"], order, reason[0].upper() + reason[1:] + ".",
                           {"entry_price": p["entry_price"]})
        if rec["filled_qty"]:
            pnl = (rec["fill_price"] - p["entry_price"]) * rec["filled_qty"] * 100
            logger.info("Sold %s: %s $%.2f", p["symbol"], "gain" if pnl >= 0 else "loss", abs(pnl))
    journal.save("positions.json", notes)


def _explain(parts: dict) -> str:
    names = {"trend": "trend", "momentum": "momentum", "news": "news", "analysts": "analysts"}
    return ", ".join(f"{names[k]} {v:+.2f}" for k, v in parts.items())


def _facts(row: dict, regime: dict) -> dict:
    return {
        "price": row["price"], "vs_50_day_average_pct": row["vs_sma50_pct"], "return_5_days_pct": row["return_5d_pct"],
        "return_20_days_pct": row["return_20d_pct"], "annual_volatility_pct": row["annual_vol_pct"], "rsi14": row["rsi14"],
        "recent_headlines": row.get("headlines") or [], "analyst_ratings": row.get("analysts") or {},
        "next_earnings_date_per_calendar": row.get("earnings_date"), "overall_market": regime,
        "agent_data_score_-1_to_+1": row["score"],
        "company_financials": signals.company_financials(row["symbol"]),
    }


def research(today: date, run: dict, equity: float, account: dict, new_entries_allowed: bool, entries_block: str) -> str:
    """Returns "retry" when Gemini was unavailable, so the next firing in the
    research window tries again instead of skipping the day."""
    positions = held_positions(today)
    held_under = {p["underlying"] for p in positions}
    universe = sorted(set(S.watchlist) | held_under)
    table, regime = signals.score_universe(universe, today)
    rows = table.to_dict("records") if not table.empty else []
    by_sym = {r["symbol"]: r for r in rows}
    journal.update_run(latest_scores={"date": today.isoformat(), "values": {r["symbol"]: r["score"] for r in rows}},
                       market_regime=regime)
    names = signals.company_names()

    # Research coverage check: the owner wants decisions made on research.
    covered = sum(1 for r in rows if r.get("headline_count") or r.get("analyst_score") is not None)
    if rows and covered < len(rows) / 2:
        alert("finnhub_coverage", f"research data missing for {len(rows) - covered} of {len(rows)} companies",
              "Finnhub news/analyst data could not be fetched for most companies, so the data score is running on "
              "price data alone. This is a technical problem to fix.", today)

    # Keep earnings dates for held names up to date, and re-review held names with Gemini.
    notes = journal.load("positions.json", {})
    for p in positions:
        n = notes.setdefault(p["symbol"], {})
        r = by_sym.get(p["underlying"], {})
        if r.get("earnings_date"):
            n["earnings_date"] = r["earnings_date"]
        last = n.get("last_review")
        if last and (today - date.fromisoformat(last)).days < S.rereview_days:
            continue
        if not r or not gemini_research.api_key():
            continue
        try:
            v = gemini_research.research(p["underlying"], names.get(p["underlying"], p["underlying"]), today, _facts(r, regime))
        except gemini_research.GeminiUnavailable as exc:
            logger.warning("Re-review of %s skipped: %s", p["underlying"], exc)
            continue
        n["last_review"] = today.isoformat()
        wanted = "bullish" if p["kind"] == "call" else "bearish"
        opposite = "bearish" if wanted == "bullish" else "bullish"
        if v["direction"] == opposite and v["conviction"] >= 60:
            n["research_against"] = f"Gemini now {opposite} (conviction {v['conviction']}): {v['thesis'][:200]}"
        if v.get("next_earnings_date"):
            n["earnings_date"] = min(filter(None, [n.get("earnings_date"), v["next_earnings_date"]]))
        journal.append("research.jsonl", {"date": today.isoformat(), "type": "verdict", "symbol": p["underlying"],
                                          "score": r["score"], "score_explained": _explain(r["score_parts"]),
                                          "decision": f"re-review of held {p['kind']}", "gemini": v})
    journal.save("positions.json", notes)

    if not new_entries_allowed:
        logger.info("No new positions today: %s", entries_block)
        return
    open_slots = S.max_open_positions - len(positions)
    if open_slots <= 0:
        logger.info("Holding the maximum %d positions: no new ones", S.max_open_positions)
        return

    budget = min(equity, S.budget)
    candidates, skipped = strategy.pick_candidates(rows, held_under, S)
    for sk in skipped:
        journal.append("research.jsonl", {"date": today.isoformat(), "type": "pass", **sk})
    if not candidates:
        logger.info("No company has a strong enough data signal today")
        return
    if not gemini_research.api_key() and S.require_gemini:
        alert("gemini_missing", "Gemini API key missing: no new trades",
              "The agent needs a Gemini API key to research companies before buying. Create a free key at "
              "https://aistudio.google.com/apikey (sign in with your Google account) and add it to this repository "
              "as a secret named GEMINI_API_KEY (Settings → Secrets and variables → Actions). Until then it only "
              "manages contracts it already holds.", today, owner_decision=True)
        return

    bought = 0
    buying_power = float(account.get("option_buying_power") or 0)
    for row in candidates:
        if bought >= min(open_slots, S.max_new_per_day):
            break
        sym = row["symbol"]
        direction = strategy.direction_of(row["score"])
        try:
            v = gemini_research.research(sym, names.get(sym, sym), today, _facts(row, regime))
        except gemini_research.GeminiUnavailable as exc:
            logger.warning("Gemini unavailable (%s): will retry at the next run in the research window", exc)
            return "retry"
        ok, why = strategy.entry_decision(row, v, regime, S, today)
        rec = {"date": today.isoformat(), "type": "verdict", "symbol": sym, "score": row["score"],
               "score_explained": _explain(row["score_parts"]), "gemini": v}
        if not ok:
            journal.append("research.jsonl", {**rec, "decision": f"passed: {why}"})
            continue

        kind = strategy.kind_for(direction)
        spot = row["price"]
        contracts = alp.option_chain(sym, kind, today + timedelta(days=S.min_dte), today + timedelta(days=S.max_dte),
                                     spot * 0.8, spot * 1.2)
        cap = min(budget * S.max_premium_per_trade_pct, budget * S.max_total_premium_pct - premium_at_risk(positions))
        choice, none_why = strategy.choose_contract(contracts, spot, kind, today, cap, row["annual_vol_pct"] / 100, S)
        if not choice:
            journal.append("research.jsonl", {**rec, "decision": f"passed: agreed {direction}, but {none_why}"})
            continue
        qty, qty_why = strategy.contracts_to_buy(choice.ask, budget, premium_at_risk(positions), S)
        qty = min(qty, int(buying_power // (choice.ask * 100)))
        if qty < 1:
            journal.append("research.jsonl", {**rec, "decision": f"passed: {qty_why or 'not enough buying power'}"})
            continue

        label = f"{sym} ${choice.strike:g} {kind} exp {choice.expiration}"
        reason = (f"{'Bet on a rise' if kind == 'call' else 'Bet on a fall'}: {why}. Gemini: {v['thesis'][:300]} "
                  f"Contract: delta {choice.delta:.2f}, {choice.dte} days, bid/ask {choice.bid:.2f}/{choice.ask:.2f}.")
        price = strategy.entry_limit_price(choice.bid, choice.ask)
        order = alp.submit_and_wait(choice.symbol, qty, "buy", price, S.order_wait_seconds)
        trade = record_trade(today, "buy", choice.symbol, label, qty, order, reason,
                             {"score": row["score"], "gemini_conviction": v["conviction"]})
        journal.append("research.jsonl", {**rec, "decision": f"BOUGHT {trade['filled_qty']} × {label}" if trade["filled_qty"]
                                          else f"tried to buy {label}, order did not fill"})
        if trade["filled_qty"]:
            bought += 1
            notes = journal.load("positions.json", {})
            notes[choice.symbol] = {
                "why": f"{why}. {v['thesis'][:200]}", "opened": today.isoformat(), "peak_gain": 0.0,
                "earnings_date": v.get("next_earnings_date") or row.get("earnings_date"), "last_review": today.isoformat(),
            }
            journal.save("positions.json", notes)
            positions.append({"qty": trade["filled_qty"], "entry_price": trade["fill_price"]})
            buying_power -= trade["filled_qty"] * trade["fill_price"] * 100
    return "done"


def spy_return_since(start: date, today: date) -> float | None:
    try:
        closes = signals.fetch_closes(["SPY"], today).get("SPY")
        if closes is None or closes.empty:
            return None
        base = closes[closes.index >= str(start)]
        return float(closes.iloc[-1] / base.iloc[0] - 1) if not base.empty else None
    except Exception:  # noqa: BLE001
        return None


def daily_report(today: date, final: bool) -> None:
    from execution.daily_report import publish_issue

    account = alp.get_account()
    run = journal.load("run.json", {})
    positions = held_positions(today)
    equity = float(account["equity"])
    previous = float(run.get("last_report_equity") or run.get("start_equity") or equity)
    title, body = report.build(
        today, run, equity, previous, positions,
        journal.read_lines("trades.jsonl", today), journal.read_lines("research.jsonl", today),
        spy_return_since(date.fromisoformat(run["start_date"]), today) if run.get("start_date") else None, final, S.budget,
    )
    rel = f"reports/options/daily/{today.isoformat()}.md"
    path = PROJECT_ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    journal.update_run(last_report_equity=equity)
    publish_issue(title, body, rel, "options-report")


# --- dispatcher ------------------------------------------------------------

def in_window(t: dtime, w: tuple[dtime, dtime]) -> bool:
    return w[0] <= t <= w[1]


def due_sessions(now_ny: datetime, market_open: bool, run: dict, force: str) -> list[str]:
    today, t = now_ny.date().isoformat(), now_ny.time()
    due = []
    if run.get("finished"):
        return due
    if force in ("manage", "research", "report"):
        due.append(force)
        if force == "research":
            due.append("manage")
        return due
    if market_open and in_window(t, RESEARCH_WINDOW) and run.get("last_research") != today:
        due.append("research")
    if market_open and in_window(t, MANAGE_WINDOW):
        due.append("manage")
    if in_window(t, REPORT_WINDOW) and run.get("last_report") != today and run.get("last_manage") == today:
        due.append("report")
    return due


def main() -> int:
    setup_logging()
    if not alp.configured():
        logger.warning("OPT_ALPACA_API_KEY_ID / OPT_ALPACA_API_SECRET_KEY are not set: nothing to do")
        return 0
    alp.ensure_paper()
    # Never the S&P 500 agent's `agent-state` branch: its own history and lock.
    shared_state.STATE_BRANCH = os.environ.get("OPT_STATE_BRANCH", "options-agent-state")
    if shared_state.STATE_BRANCH == "agent-state":
        raise SystemExit("The options agent must not use the S&P 500 agent's state branch")

    parent = shared_state.pull()
    now_ny = datetime.now(NY)
    today = now_ny.date()
    clock = alp.get_clock()
    run = journal.load("run.json", {})
    force = os.environ.get("OPT_FORCE_SESSION", "none")
    due = due_sessions(now_ny, bool(clock.get("is_open")), run, force)
    logger.info("New York time %s, market %s, sessions due: %s", now_ny.strftime("%H:%M"),
                "open" if clock.get("is_open") else "closed", due or "none")
    if not due:
        return 0

    rc = 0
    try:
        account = alp.get_account()
        equity = float(account["equity"])
        run = journal.run_info(today, equity, S.run_days)
        end = date.fromisoformat(run["end_date"])
        final_day = today >= end

        block = ""
        if journal.KILL_SWITCH.exists():
            block = "paused by the kill switch after a crash (fix, then delete .state/options/KILL_SWITCH)"
        elif final_day or run.get("finished"):
            block = "the two-month run is over"
        elif (end - today).days < S.no_new_entries_last_days:
            block = f"the last {S.no_new_entries_last_days} days of the run: closing out, not opening"
        elif equity < float(run["start_equity"]) - S.drawdown_halt_pct * S.budget:
            block = f"account down more than {S.drawdown_halt_pct:.0%} of the budget"
            if not run.get("drawdown_alerted"):
                alert("drawdown", "loss limit reached", (
                    f"The account is at ${equity:,.2f}, down more than {S.drawdown_halt_pct:.0%} of the "
                    f"${S.budget:,.0f} budget since the start (${float(run['start_equity']):,.2f}). The agent has "
                    "stopped opening new contracts; it still manages and sells the ones it holds. Decide whether to "
                    "keep going as-is (it stays paused), raise the limit (OPT_DRAWDOWN_HALT), or stop."), today, True)
                journal.update_run(drawdown_alerted=today.isoformat())
        journal.update_run(entries_halted=block or None)

        if "research" in due:
            journal.update_run(last_research=today.isoformat())
            parent = shared_state.push(f"options agent: research claimed {today}", parent) or parent
            if research(today, run, equity, account, not block, block) == "retry":
                # The schedule fires every 30 minutes, so after 12:30 there's no later try today.
                if now_ny.time() >= dtime(12, 30) or force == "research":
                    alert("gemini_failed", "Gemini research failing",
                          "Gemini could not be reached during today's research window, so no new trades were made "
                          "today. Contracts already held are still managed. This is a technical problem to fix.", today)
                else:
                    journal.update_run(last_research=None)
        if "manage" in due:
            manage(today, final_day)
            journal.update_run(last_manage=today.isoformat())
        if "report" in due:
            daily_report(today, final_day)
            journal.update_run(last_report=today.isoformat())
            if final_day and not held_positions(today):
                journal.update_run(finished=today.isoformat())
                alert("run_finished", "the two-month run has finished",
                      "All contracts are closed and the final report is posted. Decide whether to run another "
                      "period (delete .state/options/run.json on the options-agent-state branch to restart the "
                      "clock) or stop (disable the options-agent workflow).", today, True)
    except Exception as exc:  # noqa: BLE001
        rc = 1
        logger.error("Options agent crashed: %s", exc, exc_info=True)
        journal.KILL_SWITCH.write_text(f"{alp.now_iso()} {type(exc).__name__}: {exc}\n")
        alert(f"crash_{type(exc).__name__}", f"crashed ({type(exc).__name__})",
              f"The {', '.join(due)} step failed:\n\n```\n{traceback.format_exc()[-2500:]}\n```\n\n"
              "New trades are paused by the kill switch until this is fixed. Contracts already held are still "
              "checked and sold by the exit rules. This is a technical problem to fix.", today)
    finally:
        if shared_state.push(f"options agent: {', '.join(due)} {today}", parent) is None:
            logger.error("Could not save state: the state branch changed during this run")
            rc = rc or 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
