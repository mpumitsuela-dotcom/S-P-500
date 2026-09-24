# Troubleshooting the trading agent

Match the symptom in the run log, `needs-attention` issue, or `agent_status.py` output.

## Guard halts (session record `outcome: halted`, detail `Safety check stopped the session: <guard>: ...`)

| Guard | Meaning | Who acts | What to do |
|---|---|---|---|
| `kill_switch` | `.state/KILL_SWITCH` exists on agent-state | Technical if a crash set it (file text says "auto-triggered by ... crash"); owner if they set it | After a crash: find the crash in the previous failing run, fix it, merge, then delete the file on agent-state. If the owner set it, leave it. |
| `trial_period` | 30 days since `.state/trial_start_date.txt` | **Owner** | See "When the 30-day trial ends" in SKILL.md. |
| `drawdown_halt` | Account fell more than `SP500_MAX_DAILY_DD_HALT` (6%) from its recent peak | **Owner** | Explain the loss in dollars and percent against the S&P 500 over the same days. Options: keep going as-is (the halt clears when equity recovers), raise the limit, or pause. Don't change the limit yourself. |
| `market_hours` | Session ran outside 9:30–16:00 ET | Technical | Usually a manual run or clock skew. Check `scheduler/dispatcher.py` windows vs the time in the log. |
| `data_freshness` | Latest Alpaca bar is >5 days old | Technical | Alpaca data outage or the key lost data access. Run `scripts/check_connections.py` in a manual workflow run and read the bars line. |
| `universe_coverage` | Prices/scores missing for >20% of the S&P 500 | Technical | Usually an Alpaca bars failure (batch 400 from a bad symbol) or the Wikipedia constituents parse. The log shows which fetch came back short. |
| `price_sanity` | A held stock's price moved >35% vs its reference | Technical, but look first | Often a stock split or bad tick. Check the symbol's real price history. If it's a genuine split, the guard is doing its job: tell the owner in plain words; the next day's reference prices move on. |
| `live_trading_gate` | Config points at live trading without all three confirmations | **Owner** | Never "fix" by adding confirmations. Find out how the config changed. |

## API errors

| Symptom | Cause | Fix |
|---|---|---|
| FMP `401 Invalid API KEY` | `FMP_API_KEY` secret wrong or expired | Owner must update the secret (Settings → Secrets and variables → Actions). Tell them exactly that; you can't set secrets. |
| FMP `402`/`429`, `FMPQuotaExceeded` in logs | Free tier 250 requests/day used up | Expected occasionally. The circuit breaker stops further calls; names without fundamentals aren't newly bought. If it happens daily, lower `FMP_MAX_NEW_SYMBOLS_PER_RUN` or `RESEARCH_FETCH_ROUNDS` (a technical change). |
| FMP `403 Legacy Endpoint` | FMP retired an endpoint again | Update paths in `data/fmp_data.py` (it uses `/stable/...`). Check FMP docs. |
| Finnhub `429` | 60 requests/min exceeded | Lower `FINNHUB_MAX_WORKERS` (default 6) or `FINNHUB_MAX_NEW_SYMBOLS_PER_RUN` (env vars read by `data/finnhub_data.py`). |
| Finnhub `401`/`403` | Key wrong, or the endpoint isn't in the free tier | Owner updates the secret if the key is wrong. If the endpoint went premium, find a free alternative. |
| Alpaca `401`/`403` | Paper keys wrong or regenerated | Owner updates `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`. Keys must come from the same paper account (the owner uses the original ~$100K one). |
| Alpaca order `status: error: 403 insufficient buying power` | Sizing exceeded cash | Technical: check `SP500_CASH_BUFFER` and sell-before-buy ordering in `execution/rebalancer.py`. |
| Alpaca `422` on an order | Symbol not tradable, or qty 0 | Check the symbol (Alpaca uses `BRK.B` dot notation). Skip untradable names in the rebalancer. |

## Runner / state errors

| Symptom | Cause | Fix |
|---|---|---|
| `cannot load shared state, not trading` | Runner couldn't fetch agent-state (GitHub outage, bad credentials on the PC) | Cloud: usually transient; the next firing retries. PC: the owner's git sign-in expired; ask them to run `scripts\run_pc.bat` once by hand and sign in. |
| `could not claim after several attempts` | Push rejected 3 times in a row | Two runners were hammering at once, or agent-state history was rewritten. Check the agent-state commit log. |
| `WARNING: could not publish the session's state` | Final push failed | The session ran but its trade log didn't reach agent-state. Its claim marker did land, so it won't re-run. Recover the trades from the run's logs and tell the owner. |
| Report issue not posted, log `Could not post the daily report issue` | Missing `issues: write` permission, or token problem | Check the workflow `permissions:` block and that the run step passes `GITHUB_TOKEN`. The report file is still saved on agent-state. |
| No report on a trading day | Report slot never claimed | Cron firings between 16:10 and 17:00 ET may have failed or been delayed. Check runs in that window. The PC backup covers from 16:35 if it's set up. |
| Every run red at "pip install" | A dependency released a breaking version | Pin it in `requirements.txt`, run pytest, merge. |

## Where to look

- Run logs: GitHub MCP `get_job_logs` (job id from `list_workflow_jobs`).
- Shared state: `python .claude/skills/trading-agent-ops/scripts/agent_status.py`.
- Full research behind a trade: `reports/data/decisions.jsonl` on agent-state (`git fetch origin agent-state && git show FETCH_HEAD:reports/data/decisions.jsonl`).
- API check: a manual `workflow_dispatch` run outside the session windows runs `scripts/check_connections.py` (7 checks) and then does nothing.
