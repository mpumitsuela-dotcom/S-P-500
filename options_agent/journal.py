"""
The agent's memory, kept in .state/options/ and synced through the
options-agent-state git branch (scheduler/shared_state.py):

  run.json          start date/equity, end date, halts, last session dates
  positions.json    per open contract: why it was bought, peak gain, research
  trades.jsonl      one line per filled (or failed) order, with the reason
  research.jsonl    every Gemini verdict and every candidate the agent passed on
  KILL_SWITCH       present = trading paused (set by a crash)
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from config import STATE_DIR

DIR = STATE_DIR / "options"
KILL_SWITCH = DIR / "KILL_SWITCH"


def _path(name: str) -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    return DIR / name


def load(name: str, default):
    p = _path(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return default


def save(name: str, data) -> None:
    _path(name).write_text(json.dumps(data, indent=1, default=str))


def append(name: str, record: dict) -> None:
    with _path(name).open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def read_lines(name: str, since: date | None = None) -> list[dict]:
    p = _path(name)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since is None or str(rec.get("date", "")) >= since.isoformat():
            out.append(rec)
    return out


def run_info(today: date, equity: float, run_days: int) -> dict:
    """Start the two-month clock on the first run; return the run record."""
    info = load("run.json", {})
    if not info.get("start_date"):
        info = {
            "start_date": today.isoformat(),
            "start_equity": equity,
            "end_date": (today + timedelta(days=run_days)).isoformat(),
        }
        save("run.json", info)
    return info


def update_run(**fields) -> dict:
    info = load("run.json", {})
    info.update(fields)
    save("run.json", info)
    return info
