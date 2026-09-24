"""Two runners (cloud + PC) sharing state through a local bare git repo."""
import subprocess
from datetime import datetime

import pytest

from scheduler import run_shared, shared_state
from scheduler.dispatcher import NY_TZ


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def clones(tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", str(remote))
    out = []
    for name in ("cloud", "pc"):
        d = tmp_path / name
        _git(tmp_path, "clone", "--quiet", str(remote), str(d))
        (d / ".state").mkdir()
        out.append(d)
    return out


def ny(hh, mm):
    return datetime(2026, 9, 21, hh, mm, tzinfo=NY_TZ)  # a Monday


def runner(monkeypatch, root, role, now, calls, market_open=True):
    monkeypatch.setenv("SP500_RUNNER_ROLE", role)
    monkeypatch.setenv("SP500_RUNNER_NAME", root.name)
    return run_shared.main(
        root=root,
        now_ny=now,
        run_session=lambda s: calls.append((root.name, s)) or 0,
        market_open=lambda: market_open,
    )


def _remote_file(root, rel):
    _git(root, "fetch", "--quiet", "origin", shared_state.STATE_BRANCH)
    return subprocess.run(["git", "show", f"FETCH_HEAD:{rel}"], cwd=root, capture_output=True, text=True).stdout


def test_primary_runs_and_backup_then_skips(monkeypatch, clones):
    cloud, pc = clones
    calls = []
    assert runner(monkeypatch, cloud, "primary", ny(9, 40), calls) == 0
    assert runner(monkeypatch, pc, "backup", ny(10, 5), calls) == 0
    assert calls == [("cloud", "morning")]
    assert _remote_file(pc, ".state/last_morning_run_by").startswith("cloud")


def test_backup_waits_then_covers_for_failed_primary(monkeypatch, clones):
    _, pc = clones
    calls = []
    assert runner(monkeypatch, pc, "backup", ny(9, 40), calls) == 0
    assert calls == []  # still giving the primary its head start
    assert runner(monkeypatch, pc, "backup", ny(10, 0), calls) == 0
    assert calls == [("pc", "morning")]


def test_simultaneous_claims_only_one_trades(monkeypatch, clones):
    cloud, pc = clones
    calls = []
    real_push = shared_state.push
    raced = []

    def pc_push(message, parent, root, remote="origin"):
        # Cloud claims between the PC's pull and the PC's claim push.
        if root == pc and not raced:
            raced.append(True)
            monkeypatch.setattr(shared_state, "push", real_push)
            assert runner(monkeypatch, cloud, "primary", ny(10, 0), calls) == 0
            monkeypatch.setattr(shared_state, "push", pc_push)
        return real_push(message, parent, root, remote)

    monkeypatch.setattr(shared_state, "push", pc_push)
    assert runner(monkeypatch, pc, "primary", ny(10, 0), calls) == 0
    assert calls == [("cloud", "morning")]


def test_trade_log_from_one_runner_reaches_the_other(monkeypatch, clones):
    cloud, pc = clones

    def session(s):
        (cloud / "trade_log.csv").write_text("row from cloud\n")
        return 0

    monkeypatch.setenv("SP500_RUNNER_ROLE", "primary")
    run_shared.main(root=cloud, now_ny=ny(9, 40), run_session=session, market_open=lambda: True)
    shared_state.pull(pc)
    assert (pc / "trade_log.csv").read_text() == "row from cloud\n"


def test_holiday_does_not_claim(monkeypatch, clones):
    cloud, _ = clones
    calls = []
    assert runner(monkeypatch, cloud, "primary", ny(9, 40), calls, market_open=False) == 0
    assert calls == []
    assert not (cloud / ".state" / "last_morning_run_date").exists()


def test_unreachable_remote_never_trades(monkeypatch, clones):
    cloud, _ = clones
    _git(cloud, "remote", "set-url", "origin", str(cloud.parent / "missing.git"))
    calls = []
    assert runner(monkeypatch, cloud, "primary", ny(9, 40), calls) == 1
    assert calls == []


def test_local_kill_switch_pauses_only_that_machine(monkeypatch, clones):
    cloud, pc = clones
    (pc / "KILL_SWITCH").write_text("paused\n")
    calls = []
    assert runner(monkeypatch, pc, "backup", ny(10, 5), calls) == 0
    assert calls == []
    assert runner(monkeypatch, cloud, "primary", ny(10, 5), calls) == 0
    assert calls == [("cloud", "morning")]


def test_empty_state_branch_is_handled(monkeypatch, clones):
    # The real agent-state branch started as an empty tree (first run before any secrets).
    cloud, _ = clones
    empty_tree = subprocess.run(["git", "hash-object", "-t", "tree", "/dev/null"], cwd=cloud, capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", empty_tree, "-m", "empty"],
        cwd=cloud, capture_output=True, text=True,
    ).stdout.strip()
    _git(cloud, "push", "--quiet", "origin", f"{commit}:refs/heads/{shared_state.STATE_BRANCH}")
    calls = []
    assert runner(monkeypatch, cloud, "primary", ny(9, 40), calls) == 0
    assert calls == [("cloud", "morning")]


def test_report_runs_after_close_on_a_trading_day(monkeypatch, clones):
    cloud, pc = clones
    calls = []
    runner(monkeypatch, cloud, "primary", ny(9, 40), calls)
    # After 4pm Alpaca's clock says "closed" - that must not block the report.
    assert runner(monkeypatch, cloud, "primary", ny(16, 20), calls, market_open=False) == 0
    assert runner(monkeypatch, pc, "backup", ny(16, 45), calls) == 0  # already reported
    assert calls == [("cloud", "morning"), ("cloud", "report")]


def test_no_report_on_a_day_nothing_ran(monkeypatch, clones):
    cloud, _ = clones
    calls = []
    assert runner(monkeypatch, cloud, "primary", ny(16, 20), calls) == 0
    assert calls == []


def test_backup_writes_report_if_primary_missed_it(monkeypatch, clones):
    cloud, pc = clones
    calls = []
    runner(monkeypatch, cloud, "primary", ny(9, 40), calls)
    assert runner(monkeypatch, pc, "backup", ny(16, 20), calls) == 0  # still in the primary's head start
    assert runner(monkeypatch, pc, "backup", ny(16, 40), calls) == 0
    assert calls == [("cloud", "morning"), ("pc", "report")]
