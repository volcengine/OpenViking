# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real-subprocess regression tests for git timeout cancellation.

These tests deliberately do NOT mock the subprocess layer. They install a
real ``git-remote-block`` executable on PATH that spawns a long-lived child
and blocks on the helper protocol, reproducing the case where git's remote
helper (and its descendant) survive a kill of the direct git child and keep
the stdout/stderr pipes and the process tree occupied.
"""

import asyncio
import contextlib
import os
import shutil
import signal
import time

import pytest

from openviking.parse.accessors.git_accessor import GitAccessor

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("git") is None,
    reason="process-group cleanup requires POSIX and a real git binary",
)

# Cancellation must propagate plus complete the bounded group reap well within
# this window; without the fix the helper descendants linger for 30 seconds.
CANCEL_BOUND_SECONDS = 5.0

# A minimal blocking remote helper:
#   1. forks a ``sleep`` descendant that stays in the same process group;
#   2. records helper/child PIDs into a marker file;
#   3. blocks forever reading git's command stream (like a wedged helper).
_BLOCKING_HELPER = """#!/bin/sh
target="${1#block::}"
sleep 30 &
child=$!
printf 'helper=%s\\nchild=%s\\n' "$$" "$child" >> "$target"
while read -r _line; do :; done
wait "$child"
"""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_marker(marker, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"remote helper never started, marker missing: {marker}")


async def _wait_until_dead(pid: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"process {pid} still alive after cancellation")


async def test_blocking_remote_helper_tree_is_killed_on_timeout(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "helper.pids"
    helper = bin_dir / "git-remote-block"
    helper.write_text(_BLOCKING_HELPER, encoding="utf-8")
    helper.chmod(0o755)

    # An isolated environment so user/system git config cannot interfere and
    # git actually resolves our helper through PATH.
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["HOME"] = str(tmp_path)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    git_args = [
        "git",
        "-c",
        "protocol.block.allow=always",
        "ls-remote",
        f"block::{marker}",
    ]

    accessor = GitAccessor()
    # Start the command first so we can observe the real process tree before
    # cancellation.
    task = asyncio.ensure_future(accessor._run_git(git_args, env=env))
    helper_pid = None
    child_pid = None
    try:
        await _wait_for_marker(marker)
        for line in marker.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key == "helper":
                helper_pid = int(value)
            elif key == "child":
                child_pid = int(value)
        assert helper_pid is not None and child_pid is not None

        # The whole tree (git -> helper -> sleep child) must exist and block.
        assert _pid_alive(helper_pid)
        assert _pid_alive(child_pid)

        # Timeout cancels _run_git; cleanup must kill the process group and
        # return promptly instead of waiting for the 30s sleep descendant.
        start = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=0.5)
        elapsed = time.monotonic() - start

        # Helper and its descendant are SIGKILLed together with the group.
        await _wait_until_dead(helper_pid)
        await _wait_until_dead(child_pid)
        assert elapsed < CANCEL_BOUND_SECONDS
    finally:
        # Never leak helper processes if an assertion above failed.
        for pid in (helper_pid, child_pid):
            if pid is not None and _pid_alive(pid):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        if not task.done():
            task.cancel()
            with contextlib.suppress(Exception):
                await task
