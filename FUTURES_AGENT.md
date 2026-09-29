# Futures agent (Tradovate demo, $10,000, two months)

An agent that **buys and sells futures contracts**: it goes **long** (bets on a
rise) or **short** (bets on a fall) on micro futures, based on research. It
trades a **Tradovate demo (simulated money) account**, runs in the cloud on
GitHub Actions, and keeps its own records apart from the other agents.

Tradovate is NinjaTrader's futures brokerage; <https://web.ninjatrader.com> and
<https://trader.tradovate.com> are the same accounts.

## What it trades

Micro contracts, which are 1/10th of the standard size and fit a $10,000 budget:

| Contract | Market | $ per 1-point move |
|---|---|---|
| MES | S&P 500 | $5 |
| MNQ | Nasdaq-100 | $2 |
| MYM | Dow Jones | $0.50 |
| M2K | Russell 2000 | $5 |
| MGC | Gold | $10 |
| MCL | Crude oil | $100 |

There are no futures on individual US companies. The company research goes
into the index contracts instead: the S&P 500, Nasdaq and Dow scores include
the research on their biggest companies (Apple, Microsoft, Nvidia, Amazon…).

## How it decides

Every trading day around 11:00 New York time:

1. **Data score** per market, from -1 (points down) to +1 (points up): the
   market's trend and momentum, plus, for the stock indexes, the price, news
   and analyst research on their biggest companies.
2. **Deep research.** Google Gemini, searching the web live, researches each
   market: company results, the Federal Reserve, inflation and jobs data, and
   for oil and gold, supply and geopolitics. It also checks the economic
   calendar. It returns bullish, bearish or neutral, a conviction from 0 to
   100, its reasons and its sources.
3. **A trade needs both to agree**, with conviction of at least 65, and no Fed
   decision, inflation report or jobs report in the next 2 days.
4. **Protection.** As soon as a position opens, a **stop-loss** and a
   **profit target** (twice as far as the stop) are placed at Tradovate. They
   work around the clock, even while the agent isn't running.

It also closes a position when it has been held 10 trading days, when the
contract is about to expire, when the research turns against it, or at the
end of the two months.

## Money limits (your decisions, set in `.github/workflows/futures-agent.yml`)

| Setting | Default | Meaning |
|---|---|---|
| `FUT_BUDGET` | 10000 | dollars it trades with |
| `FUT_MAX_RISK_PER_TRADE` | 0.05 | at most $500 lost if a trade hits its stop |
| `FUT_MAX_TOTAL_RISK` | 0.12 | at most $1,200 at risk across all positions |
| `FUT_MAX_POSITIONS` | 3 | positions open at once |
| `FUT_DRAWDOWN_HALT` | 0.20 | stops opening trades if the account is down $2,000; asks you |
| `FUT_RUN_DAYS` | 61 | length of the run; no new trades in the last 5 days |

A market too volatile to fit the $500 limit with even one contract (often the
Nasdaq and gold) is skipped that day.

**Futures can lose more than the stop in a fast market** (a gap overnight or
on news). **No strategy can guarantee a profit**, and two months is a short test.
The code only accepts Tradovate's demo address, so it can't trade real money.

## Setup

1. **Tradovate account and API access.** Sign up at <https://www.tradovate.com>
   (or <https://web.ninjatrader.com>). Tradovate only gives API keys to a
   **live, funded account (minimum $1,000) with the API Access add-on
   ($25/month)**. Once you have that, the keys also work on your **free demo
   account**, which is what the agent trades.
   - Log in at <https://trader.tradovate.com>, open **Application Settings**
     (top right), then the **API Access** tab. Subscribe, then **Generate API
     Key**. Copy the **cid** (client id) and **secret**.
2. **Gemini API key.** Get a free key at <https://aistudio.google.com/apikey>
   by signing in with your Google account.
3. **GitHub secrets.** Add these at
   <https://github.com/mpumitsuela-dotcom/S-P-500/settings/secrets/actions>:
   - `TRADOVATE_USERNAME`
   - `TRADOVATE_PASSWORD`
   - `TRADOVATE_CID`
   - `TRADOVATE_SECRET`
   - `GEMINI_API_KEY`
   - optional: `TRADOVATE_APP_ID`, the application name you gave the API key
     (default `futures-agent`)

   `FINNHUB_API_KEY` and the S&P 500 agent's Alpaca keys are already there. The
   futures agent only *reads* prices with the Alpaca keys and never trades on
   that account.
4. **Test it.** Go to *Actions → futures-agent → Run workflow*. The *Check
   connections* step shows OK or FAIL for each service. After that it runs by
   itself every weekday.

Never paste passwords or keys into chat, code or issues; only into GitHub secrets.

## What you'll get

- A **daily report** issue (label `futures-report`), which GitHub emails you:
  account value, gain or loss since the start, open positions with their stops
  and targets, every trade with its reason, and Gemini's research with sources.
- A **needs-attention** issue straight away if something breaks (⚠️, which Claude
  fixes) or needs your decision (🟠).

## Where things are

- Code: `futures_agent/`. Tests: `tests/test_futures_agent.py`
  (`python -m pytest -q`; no network calls).
- Records: the `futures-agent-state` branch, in `.state/futures/` and
  `reports/futures/daily/`.
- Pause new trades: create `.state/futures/KILL_SWITCH` on that branch. Stops,
  targets and exits keep working.
- Connection check: `python -m futures_agent.check`.
