"""
Agent state shared between runners (GitHub Actions and your PC) through the
`agent-state` git branch.

What is shared: .state/ (trial clock, "already ran today" markers, KILL_SWITCH),
trade_log.csv, TRADE_LOG.md, TRIAL_REPORT.md, and reports/ (daily and 5-day
reports plus the decision records behind them). Everything else (data cache,
logs) stays local to each runner.

Why git: a push only succeeds if the branch still points at the commit the
pusher started from. That compare-and-swap is what lets two machines agree on
who runs a session: each one pushes a "claim" commit before trading, and only
one of two competing claims can land (see scheduler/run_shared.py).
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from config import PROJECT_ROOT, STATE_DIR

STATE_BRANCH = os.environ.get("SP500_STATE_BRANCH", "agent-state")
STATE_PATHS = (".state", "trade_log.csv", "TRADE_LOG.md", "TRIAL_REPORT.md", "reports")
_GIT_IDENTITY = ["-c", "user.name=sp500-agent", "-c", "user.email=sp500-agent@users.noreply.github.com"]


class SharedStateError(RuntimeError):
    pass


def _git(root: Path, *args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, env=env)
    if check and proc.returncode != 0:
        raise SharedStateError(f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc


def pull(root: Path = PROJECT_ROOT, remote: str = "origin") -> str | None:
    """Replace the local shared state with the branch's. Returns the branch's
    commit (the parent for the next push), or None if the branch doesn't exist yet."""
    exists = _git(root, "ls-remote", "--exit-code", "--heads", remote, STATE_BRANCH, check=False)
    if exists.returncode == 2:
        return None
    if exists.returncode != 0:
        raise SharedStateError(f"cannot reach {remote}: {exists.stderr.decode(errors='replace').strip()}")

    _git(root, "fetch", "--quiet", "--depth=1", remote, STATE_BRANCH)
    parent = _git(root, "rev-parse", "FETCH_HEAD").stdout.decode().strip()
    # Read the branch fully before touching local files, so a bad download
    # never leaves this machine with no state at all.
    tar = None
    if _git(root, "ls-tree", "--name-only", parent).stdout.strip():
        archive = _git(root, "archive", "--format=tar", parent).stdout
        try:
            tar = tarfile.open(fileobj=io.BytesIO(archive))
            tar.getmembers()
        except tarfile.TarError as exc:
            raise SharedStateError(f"could not read {STATE_BRANCH}: {exc}") from exc

    for rel in STATE_PATHS:
        p = root / rel
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    if tar is not None:
        with tar:
            try:
                tar.extractall(root, filter="data")
            except TypeError:  # Python < 3.11.4 has no extraction filters
                tar.extractall(root)
    (root / ".state").mkdir(exist_ok=True)
    return parent


def push(message: str, parent: str | None, root: Path = PROJECT_ROOT, remote: str = "origin") -> str | None:
    """Commit the local shared state on top of `parent` and push it.
    Returns the new branch commit, or None if someone else pushed first."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        present = [rel for rel in STATE_PATHS if (root / rel).exists()]
        if present:
            _git(root, "add", "-f", "--", *present, env=env)
        tree = _git(root, "write-tree", env=env).stdout.decode().strip()

    if parent and tree == _git(root, "rev-parse", f"{parent}^{{tree}}").stdout.decode().strip():
        return parent  # nothing changed

    parent_args = ["-p", parent] if parent else []
    commit = _git(root, *_GIT_IDENTITY, "commit-tree", tree, *parent_args, "-m", message).stdout.decode().strip()
    pushed = _git(root, "push", "--quiet", remote, f"{commit}:refs/heads/{STATE_BRANCH}", check=False)
    if pushed.returncode != 0:
        err = pushed.stderr.decode(errors="replace")
        if "rejected" in err or "non-fast-forward" in err or "fetch first" in err:
            return None
        raise SharedStateError(f"push failed: {err.strip()}")
    return commit


def state_dir_is_shared() -> bool:
    try:
        return STATE_DIR.resolve().relative_to(PROJECT_ROOT) == Path(".state")
    except ValueError:
        return False
