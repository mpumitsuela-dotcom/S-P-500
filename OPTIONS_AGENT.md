# Options agent (Alpaca, $10,000, two months)

A second, separate agent that **buys and sells option contracts** on large US
companies: a **call** when research says the stock will rise, a **put** when it
says it will fall. It trades its **own Alpaca paper account**, runs in the
cloud on GitHub Actions, and keeps its own records apart from the S&P 500 agent.

## How it decides

Every trading day around 11:00 New York time:

1. **Data score.** For ~40 of the most-traded US companies it scores price
   trend, momentum, company news headlines (Finnhub) and analyst ratings
   (Finnhub) into one number from -1 (points down) to +1 (points up). It also
   checks the overall market (S&P 500 trend) and each company's next
   earnings date.
2. **Deep research.** The strongest few go to **Google Gemini**. It gets the
   company's latest headlines (with source and date), analyst ratings, key
   financials (growth, margins, valuation, debt), price data, the earnings date
   and the overall market. It weighs these with what it knows about the
   business and its competitors, then gives a verdict: bullish, bearish or
   neutral, a conviction from 0 to 100, its reasons and its risks.
   Gemini doesn't search the web itself (your choice, 30 Sep): the free Gemini
   tier doesn't allow it. To turn web search on later, enable billing on the
   key's project in Google AI Studio and set `GEMINI_GOOGLE_SEARCH: "on"` in
   the workflow.
3. **A trade needs both to agree.** Both the data score and Gemini must point
   the same way, with conviction of at least 65 (75 when betting against the
   overall market). No earnings report within 7 days.
4. **Contract choice.** 30–60 days to expiry, delta near 0.55, tight bid/ask
   spread, at least 100 open interest.

Every 30 minutes during market hours it checks each contract it holds and sells when:

| Rule | Default |
|---|---|
| Take profit | up 60% |
| Stop loss | down 45% |
| Trailing stop | after being up 30%+, gives back 25 points |
| Close to expiry | 14 days left |
| Earnings | report due tomorrow |
| Research changed | Gemini's re-review (every 3 days) or the data turns against it |
| End of run | last day of the two months |

## Money limits (your decisions, set in `.github/workflows/options-agent.yml`)

| Setting | Default | Meaning |
|---|---|---|
| `OPT_BUDGET` | 10000 | dollars it trades with |
| `OPT_MAX_TRADE_PCT` | 0.08 | at most $800 on one trade |
| `OPT_MAX_TOTAL_PCT` | 0.40 | at most $4,000 in contracts at once; the rest stays cash |
| `OPT_MAX_POSITIONS` | 5 | contracts held at once |
| `OPT_MAX_NEW_PER_DAY` | 2 | new trades a day |
| `OPT_DRAWDOWN_HALT` | 0.25 | stops opening trades if the account is down $2,500; asks you |
| `OPT_RUN_DAYS` | 61 | length of the run; no new trades in the last 10 days |

It only **buys** options, so the most any trade can lose is what was paid for it.
Paper only: the code refuses any Alpaca address except the paper one.
**No strategy can guarantee a profit.** Options can lose value fast, and two
months is a short test.

## Setup (one time, about 15 minutes)

1. **A second Alpaca paper account.** Log in at <https://app.alpaca.markets>
   (the same login as your S&P 500 agent). Open the account menu at the top left
   and choose **Open New Paper Account**. Set its starting balance to
   **$10,000**. It must be a separate account, so the two agents never trade
   each other's positions. In that new paper account, open the **API Keys**
   panel on the home page and click **Generate New Keys**. Copy the **Key** and
   the **Secret**. The secret is shown only once.
   Options trading is switched on for Alpaca paper accounts by default.
2. **Gemini API key.** Go to <https://aistudio.google.com/apikey>, sign in with
   your Google account, and click *Create API key*. A Gemini Pro chat
   subscription can't be connected to a program; this key is how the agent
   uses Gemini.
3. **Add the secrets to GitHub.** In the repository go to *Settings → Secrets and
   variables → Actions → New repository secret*, and add:
   - `OPT_ALPACA_API_KEY_ID` (the new account's Key)
   - `OPT_ALPACA_API_SECRET_KEY` (its Secret)
   - `GEMINI_API_KEY`

   (`FINNHUB_API_KEY` is already there for the S&P 500 agent.)
4. **Test it.** Go to *Actions → options-agent → Run workflow* (leave "none").
   The *Check connections* step shows OK or FAIL for Alpaca, Finnhub and Gemini.
   After that it runs by itself every weekday.

Never paste keys into chat, code or issues; only into GitHub secrets.

## What you'll get

- A **daily report** issue (label `options-report`), which GitHub emails you:
  account value, gain or loss since the start compared with the S&P 500,
  contracts held, every trade with its reason, and the research behind each
  decision.
- A **needs-attention** issue straight away if something breaks (⚠️, which Claude
  fixes) or needs your decision (🟠, such as the loss limit being hit or the run
  finishing).

## Where things are

- Code: `options_agent/`. Tests: `tests/test_options_agent.py`
  (`python -m pytest -q`; no network calls).
- Schedule: `.github/workflows/options-agent.yml`.
- Records: the `options-agent-state` branch, in `.state/options/`
  (`trades.jsonl`, `research.jsonl`, `positions.json`, `run.json`) and
  `reports/options/daily/`.
- Pause new trades: create `.state/options/KILL_SWITCH` on that branch.
  Held contracts are still managed and sold by the exit rules.
- Connection check: `python -m options_agent.check`.
