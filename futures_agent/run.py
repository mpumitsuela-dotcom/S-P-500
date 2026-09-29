#!/usr/bin/env python3
"""
Entry point for the futures agent: `python -m futures_agent.run`.

GitHub Actions runs this every 30 minutes on weekdays
(.github/workflows/futures-agent.yml). In New York time:

  manage    9:45-15:50, every firing: make sure every position still has its
            stop-loss and target at Tradovate, and close positions whose exit
            rule applies (held too long, near expiry, research flipped, end of run).
  research  11:00-13:00, once a day: score each market, research it with
            Gemini, and open new positions where the data and Gemini agree.
  report    16:10-17:30, once a day: the daily report as a GitHub issue.

State lives on the `futures-agent-state` branch (files in .state/futures/).
A crash sets the kill switch, which stops NEW trades only: stops and targets
stay at Tradovate and the exit rules keep running.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import traceback
from datetime import date, datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from config import API_KEYS, LOG_DIR, PROJECT_ROOT, STATE_DIR  # noqa: E402
from futures_agent import research as rs  # noqa: E402
from futures_agent import strategy  # noqa: E402
from futures_agent import tradovate as tv  # noqa: E402
from futures_agent.settings import INSTRUMENTS, SETTINGS as S  # noqa: E402
from options_agent import gemini_research, signals  # noqa: E402
from options_agent.journal import Journal  # noqa: E402
from options_agent.report import _money, _pct  # noqa: E402
from scheduler import shared_state  # noqa: E402

NY = ZoneInfo("America/New_York")
MANAGE_WINDOW = (dtime(9, 45), dtime(15, 50))
RESEARCH_WINDOW = (dtime(11, 0), dtime(13, 0))
REPORT_WINDOW = (dtime(16, 10), dtime(17, 30))
MAX_NEW_PER_DAY = 2
CONTRACT_RE = re.compile(r"^([A-Z0-9]+?)[FGHJKMNQUVXZ]\d{1,2}$")

J = Journal(STATE_DIR / "futures")
logger = logging.getLogger("futures_agent")


def setup_logging() -> None:
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_DIR / "futures_agent.log")):
        h.setFormatter(fmt)
        logger.addHandler(h)


def root_of(symbol: str) -> str:
    m = CONTRACT_RE.match(symbol)
    return m.group(1) if m else symbol


def alert(key: str, title: str, body: str, day: date, owner_decision: bool = False) -> None:
    """One GitHub issue per problem per day, never raising."""
    sent = J.load("alerts.json", {})
    if sent.get(key) == day.isoformat():
        return
    try:
        from execution.daily_report import publish_issue

        prefix = "🟠 Futures agent: decision needed" if owner_decision else "⚠️ Futures agent needs attention"
        if os.environ.get("GITHUB_RUN_ID"):
            body += (f"\n\nRun logs: {os.environ.get('GITHUB_SERVER_URL')}/{os.environ.get('GITHUB_REPOSITORY')}"
                     f"/actions/runs/{os.environ['GITHUB_RUN_ID']}")
        publish_issue(f"{prefix} — {title}", body, "", "needs-attention")
        sent[key] = day.isoformat()
        J.save("alerts.json", sent)
    except Exception:  # noqa: BLE001
        logger.warning("Could not post alert %s", key, exc_info=True)


def record_trade(day: date, **fields) -> dict:
    rec = {"date": day.isoformat(), "time": tv.now_iso(), **fields}
    J.append("trades.jsonl", rec)
    return rec


def risk_in_use(notes: dict) -> float:
    return sum(float(n.get("risk_dollars") or 0) for n in notes.values())


# --- manage ----------------------------------------------------------------

def manage(today: date, final_day: bool) -> None:
    positions = tv.get_positions()
    notes = J.load("positions.json", {})
    held = {p["symbol"] for p in positions}

    # Positions closed at Tradovate by their stop or target since the last run.
    for sym in [s for s in notes if s not in held]:
        n = notes.pop(sym)
        record_trade(today, symbol=sym, side="close", qty=n.get("qty"), fill_price=None,
                     reason="Closed at Tradovate by its resting stop-loss or profit target.", entry_price=n.get("entry_price"))

    scores = J.load("run.json", {}).get("latest_scores", {})
    scores = scores.get("values", {}) if scores.get("date") == today.isoformat() else {}
    for p in positions:
        n = notes.get(p["symbol"])
        long = p["qty"] > 0
        if not n:
            alert(f"unknown_{p['symbol']}", f"position {p['symbol']} wasn't opened by the agent",
                  f"The Tradovate demo account holds {p['qty']} {p['symbol']} that the agent has no record of opening. "
                  "It leaves it alone. If something else is trading this account, turn it off.", today, owner_decision=True)
            continue
        # Safety: every position must have its stop/target resting at Tradovate.
        if not tv.working_orders(p["contract_id"]):
            logger.warning("%s had no stop/target at Tradovate: placing them again", p["symbol"])
            tv.protect(p["symbol"], "Sell" if long else "Buy", abs(p["qty"]), n["stop"], n["target"])
        reason = strategy.exit_decision(
            long, date.fromisoformat(n["opened"]), today,
            date.fromisoformat(n["expiration"]) if n.get("expiration") else None,
            scores.get(root_of(p["symbol"])), n.get("research_against"), final_day, S,
        )
        if not reason:
            continue
        logger.info("Closing %s: %s", p["symbol"], reason)
        qty, price, status = tv.close_position(p, S.order_wait_seconds)
        pnl = (price - p["avg_price"]) * (1 if long else -1) * qty * INSTRUMENTS[root_of(p["symbol"])].point_value if qty else None
        record_trade(today, symbol=p["symbol"], side="close", qty=qty, fill_price=price, status=status,
                     entry_price=p["avg_price"], pnl=pnl, reason=reason[0].upper() + reason[1:] + ".")
        if qty:
            notes.pop(p["symbol"], None)
    J.save("positions.json", notes)


# --- research ----------------------------------------------------------------

def research(today: date, equity: float, entries_block: str) -> None:
    instruments = [INSTRUMENTS[r] for r in S.instruments if r in INSTRUMENTS]
    companies = sorted({c for i in instruments for c in i.companies})
    closes = rs.fetch_closes([i.proxy for i in instruments] + companies, today)
    comp = rs.company_scores(companies, closes)
    if companies and sum(c["researched"] for c in comp.values()) < len(companies) / 2:
        alert("finnhub_coverage", "company research missing",
              "Finnhub news/analyst data could not be fetched for most companies, so the index scores are "
              "running on prices alone. This is a technical problem to fix.", today)

    markets = []
    for inst in instruments:
        feats = signals.price_features(closes.get(inst.proxy, pd.Series(dtype=float)))
        if not feats:
            logger.warning("No price history for %s (%s)", inst.root, inst.proxy)
            continue
        cs = [comp[c]["score"] for c in inst.companies if c in comp]
        sc = strategy.market_score(feats, sum(cs) / len(cs) if cs else None)
        markets.append({"inst": inst, "feats": feats, "score": sc,
                        "companies": {c: comp[c] for c in inst.companies if c in comp}})
    J.update_run(latest_scores={"date": today.isoformat(), "values": {m["inst"].root: m["score"]["score"] for m in markets}})

    positions = tv.get_positions()
    notes = J.load("positions.json", {})
    held_roots = {root_of(p["symbol"]) for p in positions}

    # Re-review held positions with Gemini every 3 days.
    for p in positions:
        n = notes.get(p["symbol"])
        m = next((m for m in markets if m["inst"].root == root_of(p["symbol"])), None)
        if not n or not m or not gemini_research.api_key():
            continue
        if n.get("last_review") and (today - date.fromisoformat(n["last_review"])).days < 3:
            continue
        try:
            v = rs.research_market(m["inst"], today, rs.market_facts(m["inst"], m["feats"], m["companies"], m["score"]))
        except gemini_research.GeminiUnavailable as exc:
            logger.warning("Re-review of %s skipped: %s", p["symbol"], exc)
            continue
        n["last_review"] = today.isoformat()
        opposite = "bearish" if p["qty"] > 0 else "bullish"
        if v["direction"] == opposite and v["conviction"] >= 60:
            n["research_against"] = f"Gemini now {opposite} (conviction {v['conviction']}): {v['thesis'][:200]}"
        J.append("research.jsonl", {"date": today.isoformat(), "type": "verdict", "symbol": m["inst"].root,
                                    "score": m["score"]["score"], "parts": m["score"]["parts"],
                                    "decision": f"re-review of the {'long' if p['qty'] > 0 else 'short'} position", "gemini": v})
    J.save("positions.json", notes)

    if entries_block:
        logger.info("No new positions today: %s", entries_block)
        return
    if not gemini_research.api_key():
        alert("gemini_missing", "Gemini API key missing: no new trades",
              "The agent needs a Gemini API key to research markets before trading. Create a free key at "
              "https://aistudio.google.com/apikey and add it as the repository secret GEMINI_API_KEY. Until then it "
              "only manages positions it already holds.", today, owner_decision=True)
        return

    opened = 0
    budget = min(equity, S.budget)
    for m in sorted(markets, key=lambda m: -abs(m["score"]["score"])):
        inst, score = m["inst"], m["score"]["score"]
        if opened >= MAX_NEW_PER_DAY or len(positions) + opened >= S.max_open_positions:
            break
        if inst.root in held_roots:
            continue
        if abs(score) < S.min_quant_score:
            J.append("research.jsonl", {"date": today.isoformat(), "type": "pass", "symbol": inst.root,
                                        "reason": f"data signal too weak ({score:+.2f})"})
            continue
        try:
            v = rs.research_market(inst, today, rs.market_facts(inst, m["feats"], m["companies"], m["score"]))
        except gemini_research.GeminiUnavailable as exc:
            alert("gemini_failed", "Gemini research failing", f"Gemini research could not be completed: {exc}. "
                  "No new trades are made without it. This is a technical problem to fix.", today)
            return
        rec = {"date": today.isoformat(), "type": "verdict", "symbol": inst.root, "score": score,
               "parts": m["score"]["parts"], "gemini": v}
        ok, why = strategy.entry_decision(score, v, S)
        if not ok:
            J.append("research.jsonl", {**rec, "decision": f"passed: {why}"})
            continue
        ref = rs.futures_price(inst.root)
        if not ref:
            J.append("research.jsonl", {**rec, "decision": "passed: no reference price to size the trade"})
            continue
        plan = strategy.plan_size(ref, rs.daily_vol(m["feats"]), inst, budget, risk_in_use(notes), S)
        if plan.qty < 1:
            J.append("research.jsonl", {**rec, "decision": f"passed: {plan.reason}"})
            continue

        contract = tv.front_contract(inst.root, today, S.min_days_to_expiry + 3)
        long = score > 0
        order_id = tv.market_order(contract["name"], "Buy" if long else "Sell", plan.qty)
        qty, fill, status = tv.wait_for_fill(order_id, S.order_wait_seconds)
        if not qty:
            tv.cancel(order_id)
            J.append("research.jsonl", {**rec, "decision": f"tried to {'buy' if long else 'sell'} {contract['name']}, not filled ({status})"})
            continue
        stop, target = strategy.stop_and_target(fill, long, plan.stop_points, inst, S)
        try:
            tv.protect(contract["name"], "Sell" if long else "Buy", qty, stop, target)
        except Exception as exc:  # noqa: BLE001 - never hold a futures position without a stop
            logger.error("Could not place the stop for %s, closing it: %s", contract["name"], exc)
            pos = {"contract_id": contract["id"], "symbol": contract["name"], "qty": qty if long else -qty}
            cqty, cprice, _ = tv.close_position(pos, S.order_wait_seconds)
            record_trade(today, symbol=contract["name"], side="close", qty=cqty, fill_price=cprice, entry_price=fill,
                         reason="Closed straight away: the stop-loss order was refused.")
            alert("stop_refused", "stop-loss order refused", f"Tradovate refused the stop for {contract['name']}: {exc}. "
                  "The position was closed immediately. This is a technical problem to fix.", today)
            continue
        side_words = "Bought (long, a bet on a rise)" if long else "Sold short (a bet on a fall)"
        reason = f"{side_words}: {why}. Gemini: {v['thesis'][:300]}"
        record_trade(today, symbol=contract["name"], side="buy" if long else "sell", qty=qty, fill_price=fill,
                     stop=stop, target=target, risk_dollars=plan.stop_points * inst.point_value * qty, reason=reason)
        J.append("research.jsonl", {**rec, "decision": f"{'LONG' if long else 'SHORT'} {qty} {contract['name']} at {fill:g}"})
        notes[contract["name"]] = {
            "why": f"{why}. {v['thesis'][:200]}", "opened": today.isoformat(), "qty": qty, "long": long,
            "entry_price": fill, "stop": stop, "target": target, "expiration": contract["expiration"],
            "risk_dollars": plan.stop_points * inst.point_value * qty, "last_review": today.isoformat(),
        }
        J.save("positions.json", notes)
        opened += 1


# --- report ----------------------------------------------------------------

def build_report(day: date, run: dict, equity: float, previous: float, notes: dict, positions: list[dict],
                 trades: list[dict], research: list[dict], final: bool) -> tuple[str, str]:
    start_eq = float(run.get("start_equity") or equity)
    base = min(start_eq, S.budget)
    since = equity - start_eq
    since_pct = since / base if base else 0.0
    title = f"{'🏁 Final report' if final else '📊 Futures agent'} — {day:%a %d %b %Y}: {_money(equity)} ({_pct(since_pct)} since start)"
    lines = [
        f"# Futures agent — {day:%A %d %B %Y}", "",
        f"- **Account value:** {_money(equity)} ({_money(equity - previous)} since the last report)",
        f"- **Since the start on {run.get('start_date')}:** {_money(since)} ({_pct(since_pct)} of the {_money(base)} budget)",
    ]
    if run.get("end_date") and not final:
        lines.append(f"- **Days left in the two-month run:** {max((date.fromisoformat(run['end_date']) - day).days, 0)}")
    if run.get("entries_halted"):
        lines.append(f"- **New trades paused:** {run['entries_halted']}")
    lines += ["", "## Positions", ""]
    if positions:
        lines += ["| Contract | Side | Qty | Entry | Stop | Target | Opened | Why |", "|---|---|---|---|---|---|---|---|"]
        for p in positions:
            n = notes.get(p["symbol"], {})
            lines.append(f"| {p['symbol']} | {'long' if p['qty'] > 0 else 'short'} | {abs(p['qty'])} | {p['avg_price']:g} | "
                         f"{n.get('stop', '?')} | {n.get('target', '?')} | {n.get('opened', '?')} | {n.get('why', '')[:160]} |")
    else:
        lines.append("None: no open futures positions.")
    lines += ["", "## Trades today", ""]
    for t in trades:
        pnl = f" Result: {_money(t['pnl'])}." if t.get("pnl") is not None else ""
        price = f" at {t['fill_price']:g}" if t.get("fill_price") else ""
        stops = f" Stop {t['stop']:g}, target {t['target']:g}." if t.get("stop") else ""
        lines.append(f"- **{t['side'].title()} {t.get('qty') or ''} {t['symbol']}**{price}.{stops}{pnl} {t['reason']}")
    if not trades:
        lines.append("No trades today.")
    lines += ["", "## Research today", ""]
    for r in research:
        if r.get("type") == "pass":
            lines.append(f"- {r['symbol']}: {r['reason']}")
            continue
        g = r.get("gemini") or {}
        lines += [f"### {r['symbol']}: {r.get('decision', '')}",
                  f"- **Data score:** {r.get('score', 0):+.2f} ({', '.join(f'{k} {v:+.2f}' for k, v in (r.get('parts') or {}).items())})",
                  f"- **Gemini ({g.get('model', '?')}):** {g.get('direction')}, conviction {g.get('conviction')}/100. {g.get('thesis', '')}"]
        if g.get("key_events"):
            lines.append(f"- **Upcoming events:** {'; '.join(g['key_events'][:3])}")
        if g.get("sources"):
            lines.append("- **Sources:** " + ", ".join(f"[{s['title'] or 'link'}]({s['url']})" for s in g["sources"][:5]))
        lines.append("")
    if not research:
        lines.append("No new research today (it runs once each trading day around 11:00 New York time).")
    lines += ["", "---", "This is a Tradovate demo (simulated money) account. Every position has a stop-loss at Tradovate, "
              "but futures can move past a stop in a fast market. Past results don't promise future ones."]
    return title, "\n".join(lines)


def daily_report(today: date, final: bool) -> None:
    from execution.daily_report import publish_issue

    equity = tv.get_equity()
    run = J.load("run.json", {})
    previous = float(run.get("last_report_equity") or run.get("start_equity") or equity)
    title, body = build_report(today, run, equity, previous, J.load("positions.json", {}), tv.get_positions(),
                               J.read_lines("trades.jsonl", today), J.read_lines("research.jsonl", today), final)
    rel = f"reports/futures/daily/{today.isoformat()}.md"
    (PROJECT_ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / rel).write_text(body)
    J.update_run(last_report_equity=equity)
    publish_issue(title, body, rel, "futures-report")


# --- dispatcher ------------------------------------------------------------

def market_open_today() -> bool:
    """US market holiday check through Alpaca's clock (read-only); weekdays if unavailable."""
    try:
        resp = requests.get(f"{API_KEYS.alpaca_base_url}/v2/clock", timeout=15, headers={
            "APCA-API-KEY-ID": API_KEYS.alpaca_key_id, "APCA-API-SECRET-KEY": API_KEYS.alpaca_secret_key})
        resp.raise_for_status()
        return bool(resp.json().get("is_open"))
    except Exception:  # noqa: BLE001
        return datetime.now(NY).weekday() < 5


def due_sessions(now_ny: datetime, market_open: bool, run: dict, force: str) -> list[str]:
    today, t = now_ny.date().isoformat(), now_ny.time()
    if run.get("finished"):
        return []
    if force in ("manage", "research", "report"):
        return [force, "manage"] if force == "research" else [force]
    due = []
    inside = lambda w: w[0] <= t <= w[1]  # noqa: E731
    if market_open and inside(RESEARCH_WINDOW) and run.get("last_research") != today:
        due.append("research")
    if market_open and inside(MANAGE_WINDOW):
        due.append("manage")
    if inside(REPORT_WINDOW) and run.get("last_report") != today and run.get("last_manage") == today:
        due.append("report")
    return due


def main() -> int:
    setup_logging()
    if not tv.configured():
        logger.warning("TRADOVATE_USERNAME / TRADOVATE_PASSWORD / TRADOVATE_CID / TRADOVATE_SECRET are not set: nothing to do")
        return 0
    tv.ensure_demo()
    shared_state.STATE_BRANCH = os.environ.get("FUT_STATE_BRANCH", "futures-agent-state")
    if shared_state.STATE_BRANCH == "agent-state":
        raise SystemExit("The futures agent must not use the S&P 500 agent's state branch")

    parent = shared_state.pull()
    now_ny = datetime.now(NY)
    today = now_ny.date()
    due = due_sessions(now_ny, market_open_today(), J.load("run.json", {}), os.environ.get("FUT_FORCE_SESSION", "none"))
    logger.info("New York time %s, sessions due: %s", now_ny.strftime("%H:%M"), due or "none")
    if not due:
        return 0

    rc = 0
    try:
        equity = tv.get_equity()
        run = J.run_info(today, equity, S.run_days)
        end = date.fromisoformat(run["end_date"])
        final_day = today >= end
        block = ""
        if J.kill_switch.exists():
            block = "paused by the kill switch after a crash (fix, then delete .state/futures/KILL_SWITCH)"
        elif final_day:
            block = "the two-month run is over"
        elif (end - today).days < S.no_new_entries_last_days:
            block = f"the last {S.no_new_entries_last_days} days of the run: closing out, not opening"
        elif equity < float(run["start_equity"]) - S.drawdown_halt_pct * S.budget:
            block = f"account down more than {S.drawdown_halt_pct:.0%} of the budget"
            if not run.get("drawdown_alerted"):
                alert("drawdown", "loss limit reached", (
                    f"The account is at ${equity:,.2f}, down more than {S.drawdown_halt_pct:.0%} of the ${S.budget:,.0f} "
                    f"budget since the start (${float(run['start_equity']):,.2f}). The agent has stopped opening new "
                    "positions; stops and exits on open ones still work. Decide whether to keep it paused, raise the "
                    "limit (FUT_DRAWDOWN_HALT), or stop."), today, True)
                J.update_run(drawdown_alerted=today.isoformat())
        J.update_run(entries_halted=block or None)

        if "research" in due:
            J.update_run(last_research=today.isoformat())
            parent = shared_state.push(f"futures agent: research claimed {today}", parent) or parent
            research(today, equity, block)
        if "manage" in due:
            manage(today, final_day)
            J.update_run(last_manage=today.isoformat())
        if "report" in due:
            daily_report(today, final_day)
            J.update_run(last_report=today.isoformat())
            if final_day and not tv.get_positions():
                J.update_run(finished=today.isoformat())
                alert("run_finished", "the two-month run has finished",
                      "All positions are closed and the final report is posted. Decide whether to run another period "
                      "(delete .state/futures/run.json on the futures-agent-state branch) or stop (disable the "
                      "futures-agent workflow).", today, True)
    except Exception as exc:  # noqa: BLE001
        rc = 1
        logger.error("Futures agent crashed: %s", exc, exc_info=True)
        J.kill_switch.parent.mkdir(parents=True, exist_ok=True)
        J.kill_switch.write_text(f"{tv.now_iso()} {type(exc).__name__}: {exc}\n")
        alert(f"crash_{type(exc).__name__}", f"crashed ({type(exc).__name__})",
              f"The {', '.join(due)} step failed:\n\n```\n{traceback.format_exc()[-2500:]}\n```\n\n"
              "New trades are paused by the kill switch until this is fixed. Stops and targets on open positions stay "
              "at Tradovate. This is a technical problem to fix.", today)
    finally:
        if shared_state.push(f"futures agent: {', '.join(due)} {today}", parent) is None:
            logger.error("Could not save state: the state branch changed during this run")
            rc = rc or 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
