#!/usr/bin/env python3
"""
One-screen status of the trading agent, read from the agent-state branch
(the shared state both runners use). Read-only: fetches the branch into a
temporary directory and never touches the working tree.

    python .claude/skills/trading-agent-ops/scripts/agent_status.py [--date YYYY-MM-DD]

Shows: kill switch, trial clock, which sessions ran today and on which runner,
today's session outcomes and trades, reports written, and the equity history.
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

BRANCH = "agent-state"
NY = ZoneInfo("America/New_York")


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def load_branch() -> dict[str, bytes]:
    git("fetch", "--quiet", "--depth=1", "origin", BRANCH)
    head = git("rev-parse", "FETCH_HEAD").decode().strip()
    files: dict[str, bytes] = {"__head__": head.encode()}
    if not git("ls-tree", "--name-only", head).strip():
        return files
    with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", head))) as tar:
        for m in tar.getmembers():
            if m.isfile():
                files[m.name] = tar.extractfile(m).read()
    return files


def text(files, path) -> str | None:
    return files[path].decode("utf-8", "replace").strip() if path in files else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(NY).date().isoformat(), help="NY trading date (default today)")
    day = ap.parse_args().date

    try:
        files = load_branch()
    except subprocess.CalledProcessError as exc:
        print(f"Could not read the {BRANCH} branch: {exc.stderr.decode().strip()}")
        return 1

    print(f"agent-state @ {files['__head__'].decode()[:10]}   (date {day})\n")

    ks = text(files, ".state/KILL_SWITCH")
    print(f"Kill switch:   {'SET - all trading paused: ' + ks if ks is not None else 'off'}")
    start = text(files, ".state/trial_start_date.txt")
    if start:
        n = (date.fromisoformat(day) - date.fromisoformat(start)).days + 1  # calendar day, 1-based
        print(f"Trial:         started {start} (day {n} of 30), start equity ${float(text(files, '.state/trial_start_equity.txt') or 0):,.2f}")
    else:
        print("Trial:         not started yet (no real session has run)")
    if "TRIAL_REPORT.md" in files:
        print("               TRIAL_REPORT.md exists - the trial has ended")

    print("\nSessions today:")
    for name in ("morning", "afternoon", "report"):
        ran = text(files, f".state/last_{name}_run_date") == day
        by = text(files, f".state/last_{name}_run_by") if ran else None
        print(f"  {name:<10} {'ran on ' + by if ran else 'not run'}")

    recs = []
    for line in (text(files, "reports/data/decisions.jsonl") or "").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("date") == day:
            recs.append(r)
    for r in (r for r in recs if r["type"] == "session"):
        extra = f" | excluded (no research): {', '.join(r['excluded_unresearched'])}" if r.get("excluded_unresearched") else ""
        print(f"  {r['session']} outcome: {r['outcome']} - {r.get('detail', '')}{extra}")
    trades = [r for r in recs if r["type"] == "trade"]
    if trades:
        buys = [t for t in trades if t["side"] == "buy"]
        sells = [t for t in trades if t["side"] == "sell"]
        print(f"\nTrades today: {len(buys)} buys (${sum(t.get('value') or 0 for t in buys):,.0f}), "
              f"{len(sells)} sells (${sum(t.get('value') or 0 for t in sells):,.0f})")
        bad = [t for t in trades if str(t.get("status", "")).startswith("error")]
        for t in bad:
            print(f"  FAILED ORDER {t['side']} {t['qty']} {t['symbol']}: {t['status']}")
        for t in sorted(trades, key=lambda t: -(t.get("value") or 0))[:8]:
            print(f"  {t['session']} {t['side']:<4} {t['qty']:>5} {t['symbol']:<6} ${t.get('value') or 0:>9,.0f}  {t.get('reason', '')[:90]}")

    daily = sorted(p for p in files if p.startswith("reports/daily/"))
    weekly = sorted(p for p in files if p.startswith("reports/weekly/"))
    print(f"\nReports: {len(daily)} daily (latest {daily[-1] if daily else '-'}), {len(weekly)} 5-day (latest {weekly[-1] if weekly else '-'})")

    eq = [json.loads(l) for l in (text(files, "reports/data/equity.jsonl") or "").splitlines() if l.strip()]
    if eq:
        print("Equity (end of day): " + ", ".join(f"{r['date']} ${r['equity']:,.0f}" for r in eq[-6:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
