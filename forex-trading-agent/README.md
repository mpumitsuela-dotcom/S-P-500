# Forex trading agent

A cloud day-trading agent for currencies. It runs on GitHub Actions every 15
minutes, Monday to Friday. It researches the currency markets (prices, news,
economic calendar, interest rates) and trades an **Alpaca paper account**. It
uses no real money.

## Honest expectations

- No strategy is guaranteed to make money, and short-term currency moves are
  mostly noise. This agent follows sensible, well-known rules (trend,
  momentum, news, interest rates) with strict risk limits. It has no secret
  edge. Judge it on its paper results over several weeks before trusting it.
- **Alpaca does not trade currency pairs** (EUR/USD etc.). The agent reads the
  real currency markets for its signals, then trades **currency ETFs** that
  follow each currency against the US dollar:

  | Currency | ETF bought when bullish |
  |---|---|
  | Euro | FXE |
  | Japanese yen | FXY |
  | British pound | FXB |
  | Australian dollar | FXA |
  | Canadian dollar | FXC |
  | Swiss franc | FXF |
  | US dollar up | UUP |
  | US dollar down | UDN |

  These ETFs only trade 9:30am–4pm New York time, so the agent does too.
  Some of them trade thinly; the agent skips a trade when the quote is too wide.
- It only buys (no short selling). A bearish view on a currency is expressed
  through the dollar ETFs.

## What it does each run

| Time (New York) | What happens |
|---|---|
| 9:30–9:45 | Watches only (lets the opening settle). Closes anything left over without a stop-loss. |
| 9:45–3:30 | Researches, closes trades whose signal has turned, opens new trades on the strongest signals. |
| 3:30–3:35 | No new trades. |
| 3:35–4:00 | Closes every position (a day trader ends the day flat). |
| after 4:00 | Writes the daily report and posts it as a GitHub issue (GitHub emails you). |

Holidays and early closes are handled automatically: each run asks Alpaca
whether the market is open.

### Research and signals

For each currency (USD, EUR, JPY, GBP, AUD, CAD, CHF) it blends five things
into one score from -1 (sell) to +1 (buy):

| Signal | Weight | Source |
|---|---|---|
| 15-minute trend (fast vs slow average) | 25% | Yahoo Finance FX rates (fallback: Alpaca ETF prices) |
| 4-hour momentum | 20% | same |
| 20-day trend | 20% | same |
| News | 25% | Finnhub forex news + FXStreet, Investing.com, investingLive, FXEmpire RSS feeds, read by Claude (or a keyword scorer if no Claude key) |
| Interest rates (carry) | 10% | FRED central-bank rates |

Overbought or oversold readings (RSI) halve the signal, so it doesn't chase a
move that is already stretched. It won't open a trade within 30 minutes of a
high-impact release (rate decisions, jobs, inflation) for that currency or the
dollar, using the ForexFactory economic calendar.

A trade needs a score of at least **0.35**.

### Risk rules

- Every buy is a **bracket order**: the stop-loss and take-profit are sent with
  the buy, so the position is protected even between runs.
- The stop is set from how much the currency normally moves (1.5× the 15-minute
  average range, between 0.15% and 2%); the take-profit is 2.5×.
- Each trade risks at most **1%** of the account if its stop is hit.
- At most **3** positions, each at most **25%** of the account.
- One entry per ETF per day, and never both dollar ETFs at once.
- **3% daily loss limit**: closes everything and stops for the day.
- Paper only: the code refuses to start against a live Alpaca URL, and there
  is no switch to turn live trading on.
- **Kill switch**: create a file `KILL_SWITCH` on the `agent-state` branch to
  pause new trades. Delete it to resume.

All of these are settings (see `.env.example`), but changing them changes how
much money is at risk, so they're the owner's call.

## Setup (one time)

1. **Create a second Alpaca paper account** (so this agent doesn't mix with the
   S&P 500 agent): in the Alpaca dashboard open the account menu at the top
   left, choose to open a new paper account, then generate API keys for it.
2. **Add GitHub secrets** in this repo: *Settings → Secrets and variables →
   Actions → New repository secret*:
   - `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` (required, from step 1)
   - `FINNHUB_API_KEY` (optional, free at finnhub.io; you can reuse the one
     from the S&P 500 agent)
   - `FRED_API_KEY` (optional, free at fred.stlouisfed.org)
   - `ANTHROPIC_API_KEY` (optional; Claude reads the news. About 13 calls a
     trading day, roughly $0.50–$1 a day)
3. **Test the connections**: *Actions → forex-agent → Run workflow* with
   "Only test the connections" ticked. It places no trades.
4. That's it. The schedule starts on its own on the next weekday.

## Where to see what it did

- **Daily report**: a GitHub issue each trading day, labelled `daily-report`.
- **Every decision with its reasons**: `decisions/<date>.jsonl` on the
  `agent-state` branch.
- **Latest research**: `research/latest.json` on `agent-state`.
- **Run logs**: the Actions tab.

## Code layout

```
fxagent/
  agent.py        one run: timing, safety checks, exits, entries
  signals.py      blends the research into a score per currency
  sentiment.py    news -> currency sentiment (Claude or keywords)
  risk.py         position sizing, daily loss limit, spread check
  alpaca.py       Alpaca paper trading + ETF market data
  report.py       daily report
  check.py        connection test (no trading)
  universe.py     currencies, ETFs, data symbols
  sources/        FX prices, news, economic calendar, interest rates
tests/            python -m pytest -q  (no network needed)
```

Run locally: copy `.env.example` to `.env`, fill it in, then
`pip install -r requirements.txt` and `python -m fxagent.check`.
