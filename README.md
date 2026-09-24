# sp500_agent

A research-grade, paper-trading S&P 500 factor investing agent: free data in,
Alpaca paper-account execution out, with safety guards designed to fail
closed rather than open.

**Read "Honest expectations" below before doing anything else.** This
system is built to be responsible, not to promise you'll beat the market -
nothing can honestly promise that.

## Honest expectations

- **No trading strategy, automated or not, can be guaranteed to beat the
  S&P 500 over any fixed window - including the 30-day trial described
  below.** Anyone who tells you otherwise is selling something. This
  system uses a well-documented, academically standard style of investing
  (multi-factor: value, quality, momentum, low-volatility, plus current
  research - see below) that has historically shown a modest, inconsistent
  premium over the market before costs - not a proprietary edge, and not a
  guarantee. What the system DOES do is tilt every decision toward that
  goal using real signals, place every trade for a documented reason, and
  report its performance vs. the S&P 500 honestly so you can judge the
  result for yourself rather than take a promise on faith.
- The included backtest (`scripts/demo_backtest.py`) runs on **synthetic**
  price data specifically so nobody mistakes a demo number for a real
  performance claim. When you point the backtester at real historical
  data, read the tearsheet's significance verdict before believing the
  headline return number - it will tell you plainly when a result isn't
  distinguishable from noise.
- The backtester intentionally does NOT use fundamentals (P/E, ROE, etc.)
  because the free-tier data available here only gives *current* snapshots,
  not point-in-time history. Using current fundamentals to backtest past
  decisions would leak future information into the past (lookahead bias) -
  see `research/signals.py` for the full explanation. Live/paper trading
  DOES use fundamentals, because "today's real fundamentals" is exactly the
  right information for a decision made today.
- The backtest universe is today's S&P 500 constituent list, which is a
  survivorship-bias risk (removed/failed companies are invisible to the
  test). The tearsheet prints this warning on every run.
- This is long-only, unlevered, and paper-trading by default. It will
  actively refuse to place live trades with real money unless you
  deliberately defeat three independent safety gates - see "Live trading"
  below.

## What this is and isn't

This is a **factor investing system that rebalances toward a
risk-constrained target portfolio**, checked twice a day, using both
historical data (value/quality/momentum/low-volatility) AND current
research (news sentiment + analyst consensus - see "Research-driven
decisions" below). It is not a day-trading bot and does not claim any edge
from trading more frequently. See "Why twice a day, and what that actually
means" below - this matters, please read it before turning on automation.

It also runs on a **bounded 30-day trial by default** (see "The 30-day
trial" below), not indefinitely - it stops itself automatically and writes
a report for you to review rather than trading on forever unattended.

## Architecture

```
config.py               Central config - reads all secrets from env vars
data/
  alpaca_data.py         Alpaca Market Data API - free OHLCV bars & quotes
  fmp_data.py             Financial Modeling Prep - fundamentals (free tier)
  finnhub_data.py          Finnhub - news sentiment + analyst consensus (free tier)
  universe.py               S&P 500 constituents from Wikipedia (free, no key)
  cache.py                   Disk cache (protects free-tier rate limits)
research/
  scoring.py               Winsorize, z-score, sector-neutralize
  factors.py                 Value, quality, momentum, low-volatility, research factors
  signals.py                   Combines factors into one ranking score
portfolio/
  construction.py           Top-N selection, position/sector cap enforcement
  risk.py                     Volatility targeting, weights -> share counts
execution/
  broker_base.py             Common broker interface
  paper_broker.py             In-memory simulation broker (for backtests)
  alpaca_broker.py             Real Alpaca REST adapter (paper by default)
  guards.py                     Pre-flight safety checks - see below
  rebalancer.py                   Diffs current vs target, turnover-capped
  trial.py                          30-day trial start/baseline tracking
  rationale.py                       Plain-English "why" for every order
  trade_log.py                        Append-only trade log (CSV + Markdown)
  reporting.py                         Builds TRIAL_REPORT.md
backtest/
  engine.py                 Walk-forward backtest, no-lookahead by construction
  metrics.py                  Stats + self-critical HTML tearsheet
scheduler/
  run_morning.py             AM job: full rebalance (uses research)
  run_afternoon.py             PM job: risk check + tactical trim only
scripts/
  demo_backtest.py            Runs the whole pipeline on synthetic data
  check_connections.py         Tests every API connection with your keys
.github/workflows/
  trading-agent.yml          Unattended twice-daily schedule on GitHub Actions
tests/                     70 tests, no network calls, run with `pytest`
```

## Safety guards (execution/guards.py)

Every scheduled run must pass ALL of these before any order is placed. Any
failure halts that run - no partial trading, no "best effort":

1. **Kill switch** - if `.state/KILL_SWITCH` exists, nothing trades. Create
   it yourself any time (`touch sp500_agent/.state/KILL_SWITCH`) to
   instantly pause the system; delete it to resume.
2. **Live-trading gate** - see "Live trading" below.
3. **Market hours** - refuses to trade outside the regular NYSE session
   (9:30am-4:00pm America/New_York, Mon-Fri).
4. **Trial period** - refuses to trade once the 30-day trial window has
   elapsed. See "The 30-day trial" below.
5. **Data freshness** - refuses to trade on stale price data (>5 days old).
6. **Universe coverage** - refuses to trade if fundamentals/price data
   couldn't be fetched for enough of the universe (default: need 80%).
7. **Drawdown halt** - stops new buys if the account has dropped >6% from
   its recent peak (configurable). Wired to Alpaca's real portfolio history
   on both sessions.
8. **Price sanity** - refuses to trade if any held symbol's price moved
   implausibly (>35% by default) versus the last known price - catches
   bad ticks, stale feeds, or unflagged corporate actions.

Guards 1-4 are checked *before* any market/research data is fetched, so a
halted run (market closed, kill switch set, trial ended) doesn't spend any
of the free-tier API budget.

On an unhandled crash, both scheduler scripts **automatically write the
kill switch file** so a bug can't keep firing broken trades twice a day
unattended. You have to manually clear it after reviewing what happened.

## Live trading (triple-gated, off by default)

This system is paper-trading only unless you deliberately defeat three
independent gates:

1. `ALPACA_BASE_URL` must point at the live endpoint (not the default paper URL).
2. The environment variable `LIVE_TRADING_CONFIRMED=YES_I_UNDERSTAND` must be set.
3. The code that constructs `AlpacaBroker` must pass `allow_live=True` explicitly.

All three must agree, or every code path refuses. This is intentional
friction. Given everything above about realistic expectations, I'd
recommend running this against the paper account for months, watching the
tearsheets, before ever considering gate 3.

## Why twice a day, and what that actually means

You asked for the system to trade twice a day, five days a week,
automatically. Here's the honest design tradeoff I made, rather than
building something that looks like it does what you asked while quietly
hurting your returns:

Factor-based signals (value, quality, momentum, low-vol) don't meaningfully
change within a single day - re-running the full factor model and
re-optimizing the whole book twice a day would mostly generate turnover
and transaction costs (spread, slippage) without any new information to
justify it. That would make performance *worse*, not better, and it would
contradict this project's own "index-minus-costs, no false promises"
principle.

So the two sessions do genuinely different jobs:

- **AM session (`run_morning.py`)**: the real rebalance. Recomputes factor
  scores using the latest prices and fundamentals, rebuilds the target
  portfolio, and trades toward it (capped by a turnover limit so it can't
  churn the whole book in one shot).
- **PM session (`run_afternoon.py`)**: a risk check, not a second
  optimization. Re-runs the safety guards, pulls fresh news sentiment on
  currently-held names via Finnhub, and only trims (never fully exits,
  never adds new names) a position if it's BOTH flagged by clearly
  negative news AND has already moved against you intraday. If nothing
  is wrong, it places zero trades - and logs that explicitly.

This satisfies "runs automatically, twice a day, five days a week" while
being honest that the afternoon session's job is catching problems, not
manufacturing a second dose of alpha that doesn't exist.

## Research-driven decisions

Buy/sell decisions are driven by five factors, not four - value, quality,
momentum, and low-volatility (all as before), plus a **research** factor
(`research/factors.py compute_research_factor`, weight 20% by default,
`SP500_WEIGHT_RESEARCH`) built from:

- **News sentiment** - a keyword/lexicon score over recent Finnhub company
  news headlines. Coarse by design (word-list scoring, not an NLP model) -
  described honestly as a signal, not a source of edge on its own.
- **Analyst consensus** - Finnhub's monthly recommendation trend
  (strongBuy/buy/hold/sell/strongSell counts), turned into a -2..+2
  consensus score. A symbol with no analyst coverage contributes a neutral
  0, never a penalty.

This is a real change from the initial build, where Finnhub news was only
used in the PM session's trim-only risk check. Now it also directly
competes with the other four factors for portfolio weight in the AM
rebalance - a research-favored name can be bought or held more heavily, a
research-unfavored one can be sold or excluded, exactly like any other
factor. The PM session's separate news+price trim check is unchanged and
still runs as an independent, narrower circuit breaker.

Like fundamentals, research is fetched fresh for a budget-capped batch of
symbols per run (`FINNHUB_MAX_NEW_SYMBOLS_PER_RUN`, default 120) and cached
(~20h for news, ~3 days for analyst trends) to respect Finnhub's free-tier
rate limit - coverage backfills across the universe over the first several
runs, same pattern as `fmp_data.py`. Like fundamentals, it is intentionally
**excluded from the backtester** (`research=None` there) because the free
tier only gives current snapshots, not point-in-time history - using
today's news/analyst data to score a past date would be lookahead bias.

## The 30-day trial

By default, this system runs for `TRIAL_LENGTH_DAYS` (30, `SP500_TRIAL_DAYS`)
calendar days from its first real run, then **stops itself automatically**:

- The trial clock starts itself, once, the first time `run_morning.py` or
  `run_afternoon.py` actually runs (`.state/trial_start_date.txt`) - nothing
  to configure.
- Every run of either session checks `check_trial_period`
  (`execution/guards.py`) before touching any market/research data. Once
  day 30 is reached, it fails exactly like any other guard - **no further
  trades on either session**, every day, until you act.
- The moment the guard first fails, both scripts write **`TRIAL_REPORT.md`**
  to the project root: starting vs. current paper equity, the agent's
  return, the S&P 500's (SPY) return over the identical window, current
  holdings with unrealized P&L, and a full list of every trade placed
  during the trial with its rationale (see "Trade log & rationale" below).
- **This report is an honest account, not a verdict.** A single 30-day
  sample from one specific start date is far too short and too
  path-dependent to prove or disprove whether the strategy has real edge,
  in either direction - see the report's own "Honest context" section.
  Whether the agent's return beat SPY's over those 30 days or not, treat it
  primarily as a functional check (did it run safely, unattended, twice a
  day, for a month, making explainable decisions).

**Extending or restarting the trial:** after reading `TRIAL_REPORT.md`,
decide how to proceed:

- To let it keep running unchanged: increase `SP500_TRIAL_DAYS` in `.env`
  (e.g. to 60) - the existing start date is kept, so this just extends the
  same window.
- To start a fresh 30-day window from today (keeping all history and the
  trade log): delete the two marker files -
  `rm .state/trial_start_date.txt .state/trial_start_equity.txt` (or call
  `execution.trial.reset_trial()`) - the next run starts a new trial.
  `TRIAL_REPORT.md` and the trade log are not touched by this; copy or
  rename `TRIAL_REPORT.md` first if you want to keep that exact snapshot.
- To stop entirely: `touch .state/KILL_SWITCH` (see "Safety guards" above).

## Trade log & rationale

Every order either session places is logged with a plain-English rationale
referencing the specific scores that drove it - not just what was traded,
but why:

- **`trade_log.csv`** - machine-readable: timestamp, session (AM/PM),
  symbol, side, qty, status, reason.
- **`TRADE_LOG.md`** - the same information as a running, human-readable
  log you can just open and read.

An AM rebalance buy/sell rationale looks like: *"combined score +1.42;
ranked #7 of 487; driven mainly by momentum +1.85, research +1.10;
entering/adding to the top-ranked target portfolio; research detail: 4
recent headline(s), net positive tone (+6); analyst consensus +1.20/2 (6
strong buy, 3 buy, 2 hold, 0 sell, 0 strong sell)."* A PM trim looks like:
*"intraday move -4.2% on the position since entry; 3 recent headline(s),
net negative tone (-5); trimmed 50% as a risk circuit breaker, not a full
exit."* `TRIAL_REPORT.md` pulls every trade from this log for its "why"
section automatically.

## Free data sources used, and their limits

| Source | Used for | Free tier limit (verify current terms before relying on this) |
|---|---|---|
| Alpaca Market Data | OHLCV bars, latest quotes | Free with any Alpaca account (IEX feed) |
| Financial Modeling Prep | P/E, P/B, ROE, margins, debt/equity | 250 requests/day |
| Finnhub | Company news sentiment + analyst recommendation trends | 60 requests/minute |
| Wikipedia | S&P 500 constituent list | No key, be a good citizen with request volume |

Not implemented in this build, but designed to slot into `data/` if you
want to extend it later: Tiingo (alternate price source), SEC EDGAR XBRL
(true point-in-time fundamentals, would remove the backtest's fundamentals
limitation but is a substantial project on its own).

### What actually broke when tested against real accounts (and what was fixed)

This isn't theoretical - every item below was found by running the real
code against real free-tier keys on 2026-09-21, not just reasoned about:

- **FMP retired their `/api/v3/` endpoints on August 31, 2025.** A free
  key now gets `403 Legacy Endpoint` on the old paths. Fixed: `data/fmp_data.py`
  now targets `/stable/...?symbol=X` instead of `/api/v3/.../X`.
- **Alpaca wants `BRK.B` / `BF.B` (dot notation), not `BRK-B` (dash).**
  This is true for both the market-data endpoint and the trading/assets
  endpoint. An earlier version of `data/universe.py` converted dots to
  dashes on the assumption that was the Alpaca convention - backwards.
  Fixed: Wikipedia's native dot notation is now kept as-is.
- **The FMP free tier's 250 requests/day cap is real, and a full 500-name
  universe needs ~1000 requests (500 symbols x 2 endpoints) for a cold
  cache** - it cannot all be fetched in one day. `get_fundamentals_frame()`
  fetches already-cached symbols for free, spends a capped budget on new
  ones each run (`FMP_MAX_NEW_SYMBOLS_PER_RUN` env var, default 55), and
  leaves the rest for future runs. Combined with a 7-day cache TTL, a cold
  universe backfills over roughly a week rather than failing outright.
  Symbols not yet covered simply get a neutral (0) value/quality score for
  that run - momentum and low-volatility still apply to them normally.
- **Retrying a 402/429 from FMP makes things worse, not better** - it was
  live-tested and confirmed that a naive "retry any exception 3x" policy
  burns through the daily quota roughly 3x faster for a guaranteed-repeat
  failure, and can exhaust a 250/day budget in well under 100 real symbols
  of testing. Fixed: `_get` now raises a dedicated `FMPQuotaExceeded` that
  tenacity is told NOT to retry, and `get_fundamentals_frame` trips a
  circuit breaker on the first confirmed quota hit so it stops spending
  the budget on symbols that are certain to fail for the rest of that run.
- **Each FMP call took ~4 seconds round-trip in testing.** Sequential
  fetching for a full universe would take over an hour. Fixed: new-symbol
  fetches run through a bounded thread pool (`FMP_MAX_WORKERS`, default 8).

None of this was caught by the unit tests, because those deliberately
mock all network calls (see `tests/`) - they prove the logic is correct,
not that a specific vendor's live API matches what the code assumes. Both
matter; neither substitutes for the other. If you extend this to another
data provider, budget time for exactly this kind of live-integration
surprise, and consider adding a smoke test against the real API (outside
the main suite, opt-in, since it costs real quota) rather than trusting
the mocked tests alone.

## Setup

1. **Get your free API keys** (all free, no credit card required for any of these):
   - Alpaca paper trading: sign up at https://app.alpaca.markets/signup,
     then go to the Paper Trading dashboard and generate an API key pair.
     **Use the Paper Trading keys, not Live Trading.**
   - Financial Modeling Prep: https://site.financialmodelingprep.com/developer/docs -
     free tier key.
   - Finnhub: https://finnhub.io/register - free tier key.

2. **Configure**:
   ```bash
   cd sp500_agent
   cp .env.example .env
   # edit .env and paste in your keys
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Validate the pipeline with zero API calls** (synthetic data):
   ```bash
   python3 scripts/demo_backtest.py
   ```
   This writes `demo_tearsheet.html` - open it in a browser. If this runs
   clean, the whole factor -> portfolio -> execution -> metrics pipeline is
   wired correctly before you spend a single real API call.

4. **Run the test suite** (70 tests, no network calls):
   ```bash
   pytest -q
   ```

5. **Dry-run against real data** - call the data providers directly in a
   Python shell to confirm your keys work and see real output before
   trusting the scheduler:
   ```python
   from data.universe import get_sp500_constituents
   from data.alpaca_data import get_daily_bars
   from datetime import date, timedelta
   u = get_sp500_constituents()
   print(u.head())
   bars = get_daily_bars(["AAPL", "MSFT"], date.today() - timedelta(days=30), date.today())
   print(bars.tail())
   ```

6. **Run the scheduler scripts manually first**, on a day the market is
   open, and watch the logs in `logs/`:
   ```bash
   python3 scheduler/run_morning.py
   python3 scheduler/run_afternoon.py
   ```
   Both exit 0 on a clean run (even a no-trade run), non-zero on a guard
   failure or crash. Read `logs/run_morning.log` either way.

   Expect the *first* run against the full ~500-symbol universe to take
   several minutes (FMP calls run concurrently but each still takes a
   few seconds) and to only get real value/quality fundamentals for a
   capped batch of new symbols (`FMP_MAX_NEW_SYMBOLS_PER_RUN`, default 55)
   - the rest use momentum/low-vol only until their turn comes up on a
   later run, backfilling over about a week. This is expected behavior
   given the free tier's daily quota, not a malfunction - see "What
   actually broke" above.

## A note on this cloud workspace vs. where this actually runs

This project was originally built in an isolated cloud sandbox that, by
default, can only reach a small allowlist of hosts (package registries
and Anthropic's own APIs) - it couldn't reach Wikipedia, Alpaca, FMP, or
Finnhub directly. That's a deliberate security boundary, not a bug, and
it's why the first pass of this code was validated with unit tests and
synthetic data rather than live calls.

Once the account's Admin Settings -> Capabilities allowlist was updated to
include `finnhub.io`, `financialmodelingprep.com`, `paper-api.alpaca.markets`,
`data.alpaca.markets`, and `en.wikipedia.org`, everything in this README's
"What actually broke" section above was found and fixed by running the
real code against real free-tier keys directly on this machine (not the
cloud sandbox) - including a real, successful end-to-end run of the
scoring/portfolio pipeline against the live S&P 500 universe, a real
$100,000 paper account, real Alpaca bars and quotes, real FMP fundamentals,
and real Finnhub news sentiment. The market-hours guard was also confirmed
working live: it correctly halted a test run outside regular NYSE hours.

## Cloud automation (GitHub Actions)

`.github/workflows/trading-agent.yml` runs the agent unattended. Your
computer can be off.

- **When it runs:** every 15 minutes, Mon-Fri, during US market hours.
  Each firing runs `scheduler/dispatcher.py`, which checks real New York
  time and Alpaca's market clock (this skips exchange holidays). It runs
  the **AM session once in the first hour after the open (9:30-10:35 ET)**
  and the **PM session once into the close (2:55-4:00 ET)**. All other
  firings do nothing. GitHub can delay scheduled runs by 5-15 minutes,
  which is why the windows are about an hour wide.
- **What persists between runs:** the trial clock, the "already ran today"
  markers, the kill switch, `trade_log.csv`, `TRADE_LOG.md` and
  `TRIAL_REPORT.md` are committed to the **`agent-state` branch** after
  every run. Open that branch on GitHub to read the trade log. The
  fundamentals/research cache goes in the Actions cache. Each run's logs
  are uploaded as a run artifact.
- **Failures are visible:** a guard halt or crash marks the run red, and
  GitHub emails you. A crash also writes the kill switch into the saved
  state, which stops all later runs. To resume after reviewing, delete
  `.state/KILL_SWITCH` on the `agent-state` branch.
- **Paper only:** the workflow pins `ALPACA_BASE_URL` to the paper endpoint.

### Setup

1. Merge this workflow into the default branch. GitHub only runs
   scheduled workflows from the default branch.
2. Repo **Settings -> Secrets and variables -> Actions**, add
   `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `FMP_API_KEY` and
   `FINNHUB_API_KEY` (use the Alpaca **Paper** keys).
3. In the Alpaca paper dashboard, reset the paper account to **$10,000** so
   it matches the budget (see below).
4. **Actions -> trading-agent -> Run workflow.** A manual run first
   runs `scripts/check_connections.py`, which tests every API with your keys
   and prints OK/FAIL for each. Fix any FAIL before relying on the schedule.
   You can run the same check locally: `python3 scripts/check_connections.py`.

### Budget

`SP500_CAPITAL_BUDGET` (default $10,000) caps how much the agent trades.
Positions are sized on `min(account equity, budget)`. Orders are for whole
shares, so the workflow uses 15 positions of about $650 each instead of 30
positions of about $330. At $330 per position, many S&P 500 stocks would
round down to zero shares. Resetting the paper account to $10K matters
because the trial report measures return on the whole account. On a
$100K account with $10K invested, the return would look about 10x too small.

GitHub Actions usage: roughly 36 short runs per trading day, about 800
minutes a month. That fits the free 2,000 minutes for a private repo, and
public repos are free.

## Extending this

- Add a real point-in-time fundamentals source (SEC EDGAR XBRL) to remove
  the backtest's fundamentals limitation.
- Add a holiday calendar to `check_market_hours` itself. The dispatcher
  already skips holidays using Alpaca's clock, but the guard only checks
  weekends when the session scripts are run by hand.
- Swap the keyword-based news sentiment for a proper NLP/sentiment model.
- Add Tiingo as a secondary price source for redundancy.

---
*Educational software. Not investment advice. Nothing in this repository
should be read as a promise or expectation of investment returns.*
