"""One run of the forex agent. The cloud scheduler calls this every 15 minutes
on weekdays; each run looks at the clock and does whatever is due:

  market closed, after 4pm ET  -> write/post the daily report (once)
  last 25 minutes of the day   -> close everything (day trader: flat by close)
  daily loss limit hit         -> close everything, stop for the day
  otherwise                    -> research, exit trades whose case has flipped,
                                  open new trades on the strongest signals

Every trade is a bracket order: the buy, a stop-loss and a take-profit are sent
together, so a position is always protected even between runs.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import config, report, risk, sentiment, signals
from .alpaca import Alpaca, AlpacaError
from .sources import calendar, fx_prices, macro, news
from .universe import ALL_ETFS, CURRENCIES, USD_BEAR_ETF, USD_BULL_ETF, USD_FRED_RATE, USD_INDEX_YAHOO

log = logging.getLogger("fxagent")
NY = ZoneInfo("America/New_York")
NEWS_REFRESH_MINUTES = 30


# ----------------------------------------------------------------- state files
class State:
    def __init__(self, root: Path, today: str):
        self.root = root
        self.today = today
        root.mkdir(parents=True, exist_ok=True)

    @property
    def kill_switch(self) -> Path:
        return self.root / "KILL_SWITCH"

    @property
    def halted_today(self) -> Path:
        return self.root / "halts" / f"{self.today}.txt"

    @property
    def decisions(self) -> Path:
        return self.root / "decisions" / f"{self.today}.jsonl"

    @property
    def research(self) -> Path:
        return self.root / "research" / "latest.json"

    @property
    def traded_marker(self) -> Path:
        return self.root / "sessions" / f"{self.today}.txt"

    @property
    def report(self) -> Path:
        return self.root / "reports" / f"{self.today}.md"

    def log_decision(self, **entry) -> None:
        self.decisions.parent.mkdir(parents=True, exist_ok=True)
        entry["time"] = datetime.now(NY).strftime("%H:%M")
        with self.decisions.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def halt(self, reason: str) -> None:
        self.halted_today.parent.mkdir(parents=True, exist_ok=True)
        self.halted_today.write_text(reason + "\n")


# ------------------------------------------------------------------- research
def gather_prices(broker: Alpaca | None) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    prices = {}
    for code, c in CURRENCIES.items():
        prices[code] = (fx_prices.fetch(c.yahoo, "15m", "5d", c.invert), fx_prices.fetch(c.yahoo, "1d", "6mo", c.invert))
    prices["USD"] = (fx_prices.fetch(USD_INDEX_YAHOO, "15m", "5d"), fx_prices.fetch(USD_INDEX_YAHOO, "1d", "6mo"))
    # Fallback: if the FX feed is down, read the currency from its ETF on Alpaca.
    missing = [code for code, (intra, _) in prices.items() if len(intra) < 30]
    if missing and broker is not None:
        log.warning("FX feed missing for %s - falling back to ETF prices", missing)
        etf_of = {code: CURRENCIES[code].etf for code in missing if code != "USD"}
        if "USD" in missing:
            etf_of["USD"] = USD_BULL_ETF
        try:
            intra = broker.bars(list(etf_of.values()), "15Min", 7)
            daily = broker.bars(list(etf_of.values()), "1Day", 200)
            for code, etf in etf_of.items():
                prices[code] = (intra.get(etf, pd.DataFrame()), daily.get(etf, pd.DataFrame()))
        except Exception as exc:  # noqa: BLE001
            log.warning("ETF price fallback failed: %s", exc)
    return prices


def gather_news(settings: config.Settings, state: State, events: list[calendar.Event], now: datetime):
    """News scoring is the slow/expensive part, so reuse it for 30 minutes."""
    if state.research.exists():
        try:
            cached = json.loads(state.research.read_text())
            age = now - datetime.fromisoformat(cached["time"])
            if age < timedelta(minutes=NEWS_REFRESH_MINUTES):
                views = {c: sentiment.CurrencyView(**v) for c, v in cached["views"].items()}
                return views, cached.get("summary", ""), cached.get("method", "cached"), cached.get("headline_count", 0)
        except (KeyError, ValueError, TypeError):
            pass
    headlines = news.fetch_all(settings.finnhub_key, hours=12, now=now)
    views, summary, method = sentiment.score_news(settings.anthropic_key, headlines, calendar.upcoming(events, now))
    state.research.parent.mkdir(parents=True, exist_ok=True)
    state.research.write_text(
        json.dumps(
            {"time": now.isoformat(), "method": method, "summary": summary, "headline_count": len(headlines),
             "views": {c: v.__dict__ for c, v in views.items()}},
            indent=1,
        )
    )
    return views, summary, method, len(headlines)


def gather_rates(settings: config.Settings) -> dict[str, float | None]:
    rates = {"USD": macro.latest_rate(USD_FRED_RATE, settings.fred_key)}
    for code, c in CURRENCIES.items():
        rates[code] = macro.latest_rate(c.fred_rate, settings.fred_key)
    return rates


def currency_of(etf: str) -> str:
    if etf in (USD_BULL_ETF, USD_BEAR_ETF):
        return "USD"
    return next(code for code, c in CURRENCIES.items() if c.etf == etf)


# ---------------------------------------------------------------- the session
def run(now_utc: datetime | None = None) -> int:
    settings = config.load()
    broker = Alpaca(settings)
    clock = broker.clock()
    now_utc = now_utc or datetime.now(timezone.utc)
    now_ny = now_utc.astimezone(NY)
    state = State(settings.state_dir, now_ny.date().isoformat())

    if not clock.get("is_open"):
        if now_ny.hour >= 16 and state.traded_marker.exists() and not state.report.exists():
            report.end_of_day(broker, state, now_ny)
        else:
            log.info("Market closed (%s ET) - nothing to do", now_ny.strftime("%a %H:%M"))
        return 0

    next_close = datetime.fromisoformat(clock["next_close"]).astimezone(NY)
    mins_to_close = (next_close - now_ny).total_seconds() / 60
    mins_since_open = (now_ny - now_ny.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 60
    state.traded_marker.parent.mkdir(parents=True, exist_ok=True)
    state.traded_marker.write_text(now_ny.isoformat() + "\n")
    account = broker.account()
    equity, start_equity = float(account["equity"]), float(account.get("last_equity") or account["equity"])
    log.info("Market open. %s ET, %.0f min to close. Equity $%,.2f (day start $%,.2f)", now_ny.strftime("%H:%M"), mins_to_close, equity, start_equity)
    if account.get("trading_blocked") or account.get("account_blocked"):
        log.error("Alpaca says this account is blocked from trading")
        return 1

    # 1. End of day: close everything.
    if mins_to_close <= settings.flatten_last_minutes:
        flatten(broker, state, "end of day - day trader closes all positions before the close")
        return 0

    # 2. Safety stops.
    if state.kill_switch.exists():
        log.warning("KILL_SWITCH present - no new trades (existing stop-losses stay active)")
        return 0
    if state.halted_today.exists():
        log.info("Halted for today: %s", state.halted_today.read_text().strip())
        return 0
    if risk.daily_loss_hit(equity, start_equity, settings.daily_loss_limit):
        reason = f"daily loss limit hit: equity ${equity:,.2f} vs ${start_equity:,.2f} at the start of the day"
        flatten(broker, state, reason)
        state.halt(reason)
        return 0

    # 3. Research.
    events = calendar.fetch()
    prices = gather_prices(broker)
    views, summary, method, n_headlines = gather_news(settings, state, events, now_utc)
    rates = gather_rates(settings)
    sigs = signals.build_signals(prices, views, rates)
    snapshot = {c: round(s.score, 3) for c, s in sigs.items()}
    log.info("Currency scores: %s (news via %s, %d headlines)", snapshot, method, n_headlines)
    if summary:
        log.info("Market summary: %s", summary)

    # 4. Exit positions whose case has flipped.
    positions = broker.positions()
    orders = broker.open_orders()
    for sym, pos in positions.items():
        if sym not in ALL_ETFS:
            continue  # not ours - never touch
        protected = any(o.get("symbol") == sym and o.get("side") == "sell" for o in orders)
        if not protected:
            # Stop-loss legs expire at the close, so anything without one is
            # left over (e.g. yesterday's close-out run was delayed). Close it.
            close_one(broker, orders, sym, state, "position had no stop-loss protecting it (left over) - closed for safety", pos)
            continue
        view = signals.held_view(sym, sigs)
        if view <= -0.10:
            reason = f"signal turned against the position (score {view:+.2f})"
            close_one(broker, orders, sym, state, reason, pos)

    # 5. New entries.
    if mins_since_open < settings.no_entry_first_minutes or mins_to_close <= settings.no_entry_last_minutes:
        log.info("Outside the entry window - managing existing trades only")
        return 0
    positions = broker.positions()
    orders = broker.open_orders()
    pending_buys = {o["symbol"] for o in orders if o.get("side") == "buy"}
    bought_today = {f["symbol"] for f in broker.fills_since(now_ny.replace(hour=0, minute=0)) if f.get("side") == "buy"}
    held = set(positions) | pending_buys
    slots = settings.max_positions - len([s for s in held if s in ALL_ETFS])
    ideas = signals.trade_ideas(sigs, views, settings.entry_threshold)
    if not ideas:
        log.info("No currency signal is strong enough to trade (threshold %.2f)", settings.entry_threshold)
    candidates = [i for i in ideas if i.etf not in held and i.etf not in bought_today]
    if not candidates or slots <= 0:
        return 0
    quotes = broker.quotes([i.etf for i in candidates])
    account = broker.account()
    for idea in candidates:
        if slots <= 0:
            break
        # Opposite dollar ETFs cancel out - never hold both.
        opposite = {USD_BULL_ETF: USD_BEAR_ETF, USD_BEAR_ETF: USD_BULL_ETF}.get(idea.etf)
        if opposite and opposite in held:
            log.info("Skip %s: holding the opposite dollar ETF", idea.etf)
            continue
        blocked = calendar.high_impact_near(events, idea.currency, now_utc, settings.news_blackout_minutes)
        if not blocked and idea.currency != "USD":
            blocked = calendar.high_impact_near(events, "USD", now_utc, settings.news_blackout_minutes)
        if blocked:
            state.log_decision(action="skip", symbol=idea.etf, reason=f"high-impact release nearby: {blocked.currency} {blocked.title} at {blocked.when.astimezone(NY):%H:%M} ET")
            continue
        q = quotes.get(idea.etf) or {}
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if not risk.spread_ok(bid, ask, settings.max_spread_pct):
            state.log_decision(action="skip", symbol=idea.etf, reason=f"quote too wide or missing (bid {bid}, ask {ask})")
            continue
        atr_pct = idea.signal.atr_pct or 0.002
        plan = risk.plan_order(
            equity=float(account["equity"]), buying_power=float(account["buying_power"]), ask=ask, atr_pct=atr_pct,
            risk_per_trade=settings.risk_per_trade, max_position_pct=settings.max_position_pct,
            stop_atr_mult=settings.stop_atr_mult, target_atr_mult=settings.target_atr_mult,
        )
        if plan is None:
            state.log_decision(action="skip", symbol=idea.etf, reason="not enough buying power for one share within the risk limits")
            continue
        try:
            broker.bracket_buy(idea.etf, plan.qty, plan.limit, plan.stop, plan.target)
        except AlpacaError as exc:
            state.log_decision(action="order_rejected", symbol=idea.etf, reason=str(exc))
            continue
        slots -= 1
        held.add(idea.etf)
        state.log_decision(
            action="buy", symbol=idea.etf, currency=idea.currency, view=idea.direction, qty=plan.qty,
            limit=plan.limit, stop=plan.stop, target=plan.target, risk_dollars=plan.risk_dollars,
            score=round(idea.strength, 3), reason=idea.reason, news_method=method,
        )
    return 0


def close_one(broker: Alpaca, orders: list[dict], sym: str, state: State, reason: str, pos: dict) -> None:
    cancelled = False
    for o in orders:
        if o.get("symbol") == sym:
            try:
                broker.cancel_order(o["id"])
                cancelled = True
            except AlpacaError as exc:
                log.warning("Could not cancel order %s: %s", o["id"], exc)
    if cancelled:
        time.sleep(2)  # let the stop/target cancels settle so the shares are free to sell
    try:
        broker.close_position(sym)
        state.log_decision(action="sell", symbol=sym, qty=pos.get("qty"), unrealized_pl=pos.get("unrealized_pl"), reason=reason)
    except AlpacaError as exc:
        state.log_decision(action="sell_failed", symbol=sym, reason=f"{reason}; error: {exc}")


def flatten(broker: Alpaca, state: State, reason: str) -> None:
    positions = {s: p for s, p in broker.positions().items() if s in ALL_ETFS}
    orders = [o for o in broker.open_orders() if o.get("symbol") in ALL_ETFS]
    if not positions and not orders:
        log.info("Flat already (%s)", reason)
        return
    for sym, pos in positions.items():
        close_one(broker, orders, sym, state, reason, pos)
    for o in orders:  # leftover unfilled entries
        if o.get("symbol") not in positions:
            try:
                broker.cancel_order(o["id"])
            except AlpacaError:
                pass


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    for noisy in ("urllib3", "httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return run()
    except Exception:
        log.exception("Run crashed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
