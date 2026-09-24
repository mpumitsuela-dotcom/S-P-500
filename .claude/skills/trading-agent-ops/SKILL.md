---
name: trading-agent-ops
description: Operate, monitor and fix the S&P 500 paper-trading agent in this repo (GitHub Actions + optional PC backup, Alpaca/FMP/Finnhub, shared state on the agent-state branch). Use this whenever the conversation is about the trading agent running, its trades, its daily or 5-day reports, a failed or red trading-agent workflow run, a needs-attention issue, the kill switch, the 30-day trial, or the owner asking "what did it buy/sell", "is it working", "why did it stop", or "check on the agent" — even if they don't name files or the skill. Also use it before changing any code in scheduler/, execution/, data/ or the workflow, because it holds the owner's rules about which decisions Claude makes and which go to the owner.
---

# Operating the S&P 500 trading agent

The agent is a Python program, not a chat assistant. It trades an Alpaca **paper** account by itself on a schedule. Your job when this skill is active is to check on it, explain what it did in plain language, and fix technical problems. You don't make trading decisions for it.

## The owner's rules (read first)

The owner (the repo owner) is not a programmer. They set these rules explicitly, and they decide what you do without asking and what you ask first:

| Kind of decision | Who decides |
|---|---|
| **Technical**: bugs, crashes, API errors, stale data, report/issue posting failures, workflow problems, dependency breakage | **You.** Diagnose, fix, test, merge, verify, then tell the owner what you fixed. Don't ask permission first. |
| **Individual buys and sells** | **The agent, led by its research.** Never ask the owner about a trade, and never override, place, or cancel orders by hand. |
| **Account-level**: the trial ended, the drawdown halt fired, changing how much money is at risk (budget, position count, limits), changing the strategy/factor weights, live trading | **The owner.** Explain the situation plainly with the options, and ask. Don't change these on your own. |

The owner also wants the agent to research properly before buying and to keep researching. So treat anything that silently reduces research coverage (e.g. a Finnhub/FMP failure making research scores neutral) as a technical problem to fix, not something to live with.

Write to the owner in short, plain sentences, with no jargon. Money in dollars, returns in percent, times in New York time.

## How it runs

- `.github/workflows/trading-agent.yml` fires every 15 min on weekdays in market hours and runs `scheduler/run_shared.py` as the **primary** runner. GitHub's cron is unreliable, so Claude routines ("Trading agent: kick …") also start the workflow at 9:45, 15:10 and 16:25 ET. The owner's PC may run the same script as the **backup** (`scripts/run_pc.bat`, 25 min head start for the cloud).
- `run_shared.py` pulls shared state from the **`agent-state` branch** and claims a session by pushing a marker *before* acting. The git push compare-and-swap is what stops double-trading. Then it runs whichever slot is due:
  - **morning** 9:30–10:35 ET → `scheduler/run_morning.py`: the full research-ranked rebalance. It researches every target name before buying (`_research_before_buying`), and names with no research aren't newly bought.
  - **afternoon** 2:55–4:00 ET → `scheduler/run_afternoon.py`: a risk check only. It trims 50% of a holding only when it has negative news AND is down ≥3% since entry.
  - **report** 4:10–5:00 ET, only on days a session ran → `execution/daily_report.py`: the daily report, plus the 5-day report every 5th trading day. Both are posted as GitHub issues.
- On a halt or crash, `execution/alerts.py` opens a `needs-attention` issue right away. `🟠 Decision needed` means an owner decision; `⚠️` means technical.
- Safety guards live in `execution/guards.py`. A crash writes `.state/KILL_SWITCH`, which pauses all trading on every runner until it's cleared.

## Checking on it

1. **Status snapshot** (read-only; reads the agent-state branch without touching your checkout):
   ```bash
   python .claude/skills/trading-agent-ops/scripts/agent_status.py            # today (NY)
   python .claude/skills/trading-agent-ops/scripts/agent_status.py --date 2026-09-25
   ```
   It shows the kill switch, trial day, which sessions ran and on which runner, session outcomes, today's trades, failed orders, reports and equity.
2. **Workflow runs**: use the GitHub MCP tools (`actions_list` → `list_workflow_runs` for `trading-agent.yml`, then `get_job_logs`). Runs outside the windows finish in seconds and are no-ops. The run that did the work has a "running morning/afternoon/report session" line in the "Run agent" step.
3. **Open issues** labelled `needs-attention`, `daily-report` and `5-day-report`.
4. **What it bought and why**: `reports/daily/<date>.md` on agent-state, or the daily-report issue. For a quick summary, `reports/data/decisions.jsonl` has one JSON line per trade, with its full research snapshot.

When summarising trades for the owner, give the few biggest buys and sells with the *reason in words*, e.g. "bought Apple: ranked 7th of ~500, strong price trend, 9 of 11 analysts rate it buy, news this week positive (record iPhone demand)". Don't dump raw scores.

## Fixing a technical problem

1. Find the cause in the run logs before changing anything. `references/troubleshooting.md` maps the common failures (guard names, API errors, push rejections) to causes and fixes. Read it when a run is red or a session halted.
2. Work on branch `claude/clever-davinci-icv1sh`. If its last PR is already merged, restart it from main first: `git fetch origin main && git checkout -B claude/clever-davinci-icv1sh origin/main`.
3. Make the smallest fix that addresses the cause. Add a test that fails without it. Run the **whole** suite: `python -m pytest -q`. The tests make no network calls, so they're safe anywhere.
4. Commit, push, open a PR, and merge it. The owner has authorised merging technical fixes.
5. **Verify** with a manual workflow run on main (`actions_run_trigger` → `run_workflow`, ref `main`). Only trigger one *outside* the session windows unless you intend the session to run: a manual run inside a window whose session hasn't run today **will trade**. Outside the windows, a manual run checks all API connections and then does nothing.
6. If a crash set the kill switch, clear it only after the fix is merged. Delete `.state/KILL_SWITCH` on the agent-state branch (GitHub MCP `delete_file` with branch `agent-state`). Never force-push or rewrite agent-state: it's the trade history and the lock.
7. Close the matching needs-attention issue with a one-line comment saying what was fixed. Then tell the owner in two or three plain sentences.

If a report was missed because of a bug, regenerate it after the fix. Run `python -c "from execution.daily_report import run_end_of_day; from datetime import date; run_end_of_day(date(Y,M,D))"` inside a checkout whose shared state was pulled with `scheduler.shared_state.pull()`, then push with `shared_state.push(...)`. Easier: fix the bug and let the next evening's run write the new report, and tell the owner which day is missing.

## Things never to do

- Switch to live trading, or touch `ALPACA_BASE_URL` / `LIVE_TRADING_CONFIRMED`. That's triple-gated on purpose and is always the owner's call.
- Place, cancel or edit orders directly, or edit `trade_log.csv` / `decisions.jsonl` to change history.
- Loosen a guard (drawdown, price sanity, data freshness, coverage) to make a halt go away. A halt is information. Fix the data problem behind it, or, for drawdown and trial end, ask the owner.
- Put API keys in code, commits, issues or chat. They live in GitHub secrets and the PC's `.env`.

## When the 30-day trial ends

The `trial_period` guard halts both sessions and `TRIAL_REPORT.md` appears on agent-state. Summarise the agent's return against the S&P 500 over the trial, noting honestly that 30 days is too short to prove an edge either way. Then offer the owner the choices: extend (raise `SP500_TRIAL_DAYS`), start fresh (delete `.state/trial_start_date.txt` and `.state/trial_start_equity.txt` on agent-state), or stop. Wait for their answer. If Claude check-in routines exist (names starting "Trading agent:"), delete them once the owner has decided to stop.
