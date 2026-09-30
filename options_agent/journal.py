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


class Journal:
    def __init__(self, directory: Path):
        self.dir = Path(directory)

    @property
    def kill_switch(self) -> Path:
        return self.dir / "KILL_SWITCH"

    def _path(self, name: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir / name

    def load(self, name: str, default):
        p = self._path(name)
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return default

    def save(self, name: str, data) -> None:
        self._path(name).write_text(json.dumps(data, indent=1, default=str))

    def append(self, name: str, record: dict) -> None:
        with self._path(name).open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def read_lines(self, name: str, since: date | None = None) -> list[dict]:
        p = self._path(name)
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

    def run_info(self, today: date, equity: float, run_days: int) -> dict:
        """Start the two-month clock on the first run; return the run record."""
        info = self.load("run.json", {})
        if not info.get("start_date"):
            info = {
                "start_date": today.isoformat(),
                "start_equity": equity,
                "end_date": (today + timedelta(days=run_days)).isoformat(),
            }
            self.save("run.json", info)
        return info

    def update_run(self, **fields) -> dict:
        info = self.load("run.json", {})
        info.update(fields)
        self.save("run.json", info)
        return info


# Module-level functions for the options agent (DIR is looked up on each call,
# so tests can point it somewhere else).
def _j() -> Journal:
    return Journal(DIR)


def load(name: str, default):
    return _j().load(name, default)


def save(name: str, data) -> None:
    _j().save(name, data)


def append(name: str, record: dict) -> None:
    _j().append(name, record)


def read_lines(name: str, since: date | None = None) -> list[dict]:
    return _j().read_lines(name, since)


def run_info(today: date, equity: float, run_days: int) -> dict:
    return _j().run_info(today, equity, run_days)


def update_run(**fields) -> dict:
    return _j().update_run(**fields)
