# Lessons learned (the agent's memory between sessions)

Read this before changing anything. A fresh Claude session knows nothing about the trial except what is written here, in `SKILL.md` and in the repo.

**How to add to it:** after the evening check, if the day taught something new, append one entry under "Log" (newest first): date, what happened, what it showed, what was done (or "watching"). Only facts backed by the reports, logs or decisions record. Never edit history; add a new entry that supersedes an old one.

**What it is not:** the strategy settings are locked until the trial ends (~24 Oct). A lesson may *propose* a change; it never applies one. Proposals go to the owner at the trial end.

## Standing facts
- 30 days cannot prove an edge. The trial mainly tests that the agent runs reliably and behaves sensibly.
- The backtest uses today's data on past dates (look-ahead and survivorship bias). It is not evidence of an edge.
- The 20% volatility target was raised from 15% by the owner on day 2 (an early setting change; results before and after are not like-for-like).
- Market-breadth score was 28.6 (Weakening) on 28 Sep: information only, the owner kept the 20% limit.

## Log (newest first)
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
