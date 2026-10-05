# Lessons learned (the agent's memory between sessions)

Read this before changing anything. A fresh Claude session knows nothing about the trial except what is written here, in `SKILL.md` and in the repo.

**How to add to it:** after the evening check, if the day taught something new, append one entry under "Log" (newest first): date, what happened, what it showed, what was done (or "watching"). Only facts backed by the reports, logs or decisions record. Never edit history; add a new entry that supersedes an old one.

**What it is not:** the strategy settings are locked until the trial ends (~24 Oct). A lesson may *propose* a change; it never applies one. Proposals go to the owner at the trial end.

## Standing facts
- 30 days cannot prove an edge. The trial mainly tests that the agent runs reliably and behaves sensibly.
- The backtest uses today's data on past dates (look-ahead and survivorship bias). It is not evidence of an edge.
- The 20% volatility target was raised from 15% by the owner on day 2 (an early setting change; results before and after are not like-for-like).
- Market-breadth score was 28.6 (Weakening) on 28 Sep: information only, the owner kept the 20% limit.

## Idea for after the trial: options (owner asked to record it, 1 Oct)
Not for this trial: it would change the strategy mid-trial and make the result unreadable. Alpaca supports paper options; the free data sources do not give reliable options prices. Pros: hedging with puts, income from covered calls, defined loss when buying. Cons: time decay, total loss of premium is common, wide bid/ask spreads (2-10%), the 10% stop-loss and volatility target do not fit, extra complexity and bug risk. If the owner wants it after ~24 Oct, the lowest-risk versions are protective puts on the whole portfolio, or covered calls on the largest holdings. Needs the owner's approval (strategy change). Note: a separate options agent was built in this repo and removed at the owner's request on 1 Oct (it never held a position; its state branch `options-agent-state` was kept for the record).

## Log (newest first)
- **2026-10-05** (1) The 4:25 PM ET report run (workflow run 37369638669) was cancelled by GitHub after exactly 15 minutes (log gone, 404); the report window closed. Fixed by running the workflow with force_session=report afterwards, which regenerated the report (read-only, no trades). If it recurs, check whether the evening warm-up or state push is hanging. (2) Second strong up day (SPY +0.7%) with the agent behind: account $99,008 (-0.43% since start) vs SPY about +0.9%. Turnover high again: 5 sells (JNJ, CNC, C, EQIX, VRT), several bought only days earlier. Pattern now: lags in rallies, churns holdings. Propose at trial end: a rebalance band and review of the low-volatility/cash tilt. Settings stay locked.
- **2026-10-02** Worst relative day so far: agent +0.0% vs SPY +0.7% (-0.70 points) on a strong up day; account $99,087 (-0.35% since start). Likely beta/cash drag from a lower-volatility, partly-in-cash portfolio rather than a fault. FRT was bought 1 Oct and sold 2 Oct (rank #37), another one-day round trip. Open question to check in the 5-day report: does the agent systematically lag on up days (cash and low-volatility tilt)?
- **2026-10-01** Turnover fell: 3 buys and 2 sells (vs 8 and 8 the day before). GOOG was bought 30 Sep and sold 1 Oct (rank #32), another one-day round trip, but the count of such trips is not growing fast. Day +0.3% vs SPY +0.2%; account $99,065 (-0.37% since start). Watching.
- **2026-10-01** Owner approved moving the routines to fresh sessions (cost about $25-55 over 30 days instead of $220-440) and doing less work in the long session; tested on the report kick first. Fresh sessions know only the repo, so this file and SKILL.md must stay complete.
- **2026-09-30** First 5-day report (trading days 1-5): agent -0.63% vs SPY -0.73% (alpha +0.09%, beta 0.99, max drawdown -1.34%); too short to show an edge. Turnover is high: 8 buys and 8 sells today, and USB was bought 29 Sep (rank #14) and sold 30 Sep (rank #55), the third one-to-two-day round trip after UNP and SPG. Still tracking the market closely. Watching; rebalance band stays a trial-end proposal.
- **2026-09-29** Rank flip-flop confirmed: UNP and SPG were bought on 28 Sep (ranked #7 and #4 of 503, scores +0.72/+0.79) and sold on 29 Sep (ranked #75 and #38, scores +0.30/+0.41), a one-day round trip of about $5.7K each. Day return was still +0.1% vs SPY -0.1%. Cost is spread and slippage, not a loss on the trade itself. Locked settings, so watching; proposal for the trial end: a rebalance band so a holding is sold only when it falls well below the top-N cut-off. Open question: how often does this happen across 30 days?
- **2026-09-29** Routine wake-ups all reach this session; a very large context makes every wake-up expensive (about $2-4 each). Watching; owner asked about a fresh session for routines.
- **2026-09-28** Owner-run audit (AI Trading Bot Reality Checker) said FAIL: nothing yet proves an edge. Valid technical points were fixed (below); strategy points (stop-loss, drift threshold, legacy holdings) left to the owner.
- **2026-09-28** Fixed after live trading: fractional shares (whole shares distorted sizing); marketable limit orders 0.5% (market fallback if refused); sells confirmed filled before buys; unfilled orders cancelled after 90 s; no new buy without financials as well as news and analyst research.
- **2026-09-28** Added a 10% stop-loss with a 7-day buy-back cooldown (owner's choice). The audit argues stops can whipsaw a factor strategy: check the stop-loss trades in the 5-day report for that pattern.
- **2026-09-27** Score outliers: the old winsorize did nothing inside small sectors, so one extreme value could dominate. Scores are now capped at +/-3. Watch for UNP/SPG-style rank flip-flopping after this change.
- **2026-09-26** Research cache could be overwritten by a smaller one; now saved only if not smaller than what was restored. Evening warm-up pre-fetches ratings and financials.
- **2026-09-24** GitHub's own cron fired zero times on day 1, so the "Trading agent: kick" routines are the real scheduler. After 1 Nov (US clocks change) every routine cron needs +1 hour UTC.

## Open questions to answer from the data
- Does the agent beat the S&P 500 after costs, or just carry market beta? (Compare in the 5-day risk scorecard once 20+ days exist.)
- Do stop-loss sales get bought back later at a higher price (whipsaw)?
- Does rank flip-flopping cause needless turnover?
- Do limit orders fill reliably, and at what cost versus the quote?
