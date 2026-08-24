"""#4210 — a recycled PID kept the data-directory lock alive forever on macOS.

`_is_pid_alive()` verified process identity only on Linux, through
/proc/<pid>/cmdline (#1088). macOS has no /proc, so the check degraded to a
bare `os.kill(pid, 0)`: after a reboot left a stale `.openviking.pid` behind
and the OS handed that PID to an unrelated process (the reported incident:
Spotlight's `mdwrite` inheriting PID 857), every start raised
`DataDirectoryLocked` and the server never recovered — ~50,000 launchd retries
over seven days, with no chance of self-healing because the impostor never
exits.

These tests drive the platform branches directly, so they cover the macOS path
from Linux and Windows too.
"""

import os
import subprocess
import sys
import tempfile

import pytest

from openviking.utils import process_lock
from openviking.utils.process_lock import (
    LOCK_FILENAME,
    DataDirectoryLocked,
    acquire_data_dir_lock,
)


class _CompletedProcess:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


@pytest.fixture
def on_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def test_darwin_recycled_pid_is_reported_dead(monkeypatch, on_darwin):
    """The incident: a live process that is not OpenViking must not hold the lock."""
    monkeypatch.setattr(
        process_lock.subprocess,
        "run",
        lambda *a, **k: _CompletedProcess("/System/Library/.../mdwrite"),
    )

    assert process_lock._is_pid_alive(os.getpid()) is False


def test_darwin_real_openviking_process_still_holds_the_lock(monkeypatch, on_darwin):
    monkeypatch.setattr(
        process_lock.subprocess,
        "run",
        lambda *a, **k: _CompletedProcess("/opt/homebrew/bin/python3 -m openviking-server"),
    )

    assert process_lock._is_pid_alive(os.getpid()) is True


def test_darwin_asks_ps_for_the_right_pid(monkeypatch, on_darwin):
    """Pin the command, since a wrong flag would silently answer for every PID."""
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        return _CompletedProcess("openviking-server")

    monkeypatch.setattr(process_lock.subprocess, "run", fake_run)

    process_lock._process_cmdline(4321)

    assert seen["argv"] == ["/bin/ps", "-p", "4321", "-o", "command="]
    assert seen["timeout"] == 2


@pytest.mark.parametrize(
    "outcome",
    [
        _CompletedProcess("", returncode=1),  # ps says the pid is gone
        _CompletedProcess("   ", returncode=0),  # empty answer
    ],
)
def test_darwin_unreadable_command_line_keeps_the_lock(monkeypatch, on_darwin, outcome):
    """When identity cannot be established, honour the lock instead of stealing it."""
    monkeypatch.setattr(process_lock.subprocess, "run", lambda *a, **k: outcome)

    assert process_lock._process_cmdline(4321) is None
    assert process_lock._is_pid_alive(os.getpid()) is True


@pytest.mark.parametrize("error", [OSError("no ps"), subprocess.TimeoutExpired("ps", 2)])
def test_darwin_ps_failure_keeps_the_lock(monkeypatch, on_darwin, error):
    def fake_run(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(process_lock.subprocess, "run", fake_run)

    assert process_lock._process_cmdline(4321) is None
    assert process_lock._is_pid_alive(os.getpid()) is True


def test_stale_darwin_lock_is_taken_over_end_to_end(monkeypatch, on_darwin):
    """Same path through acquire_data_dir_lock(), which is where it hurt."""
    monkeypatch.setattr(
        process_lock.subprocess, "run", lambda *a, **k: _CompletedProcess("mdwrite")
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        lock_path = os.path.join(tmpdir, LOCK_FILENAME)
        # A PID that is alive (this test process) but is not OpenViking, which is
        # exactly what a recycled PID looks like from the outside.
        with open(lock_path, "w") as handle:
            handle.write(str(os.getpid() + 0))
        # Written as another process's id so the same-pid short circuit is not
        # what makes this pass.
        monkeypatch.setattr(process_lock.os, "getpid", lambda: 999_999_001)

        acquire_data_dir_lock(tmpdir)

        with open(lock_path) as handle:
            assert int(handle.read().strip()) == 999_999_001


def test_a_live_openviking_process_still_blocks(monkeypatch, on_darwin):
    monkeypatch.setattr(
        process_lock.subprocess, "run", lambda *a, **k: _CompletedProcess("openviking-server")
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, LOCK_FILENAME), "w") as handle:
            handle.write(str(os.getpid()))
        monkeypatch.setattr(process_lock.os, "getpid", lambda: 999_999_002)

        with pytest.raises(DataDirectoryLocked):
            acquire_data_dir_lock(tmpdir)


def test_other_platforms_do_not_shell_out(monkeypatch):
    """Windows keeps the liveness probe as its only answer."""

    def fail(*_args, **_kwargs):
        raise AssertionError("no subprocess should run off the darwin path")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(process_lock.subprocess, "run", fail)

    assert process_lock._process_cmdline(4321) is None
