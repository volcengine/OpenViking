# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""The server-managed gateway must not outlive a killed OpenViking server."""

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest
from vikingbot.utils import startup

posix_only = pytest.mark.skipif(os.name == "nt", reason="lifeline is POSIX-only")


@posix_only
def test_lifeline_fd_reads_env(monkeypatch):
    read_end, write_end = os.pipe()
    try:
        os.set_inheritable(read_end, True)
        monkeypatch.setenv("VIKINGBOT_LIFELINE_FD", str(read_end))
        assert startup.lifeline_fd() == read_end
        assert "VIKINGBOT_LIFELINE_FD" not in os.environ  # not handed on to our children
        assert not os.get_inheritable(read_end)  # the gateway's own children must not hold it
    finally:
        os.close(read_end)
        os.close(write_end)
    # A standalone gateway, or a bogus/closed fd, has no lifeline.
    for value in ("", "abc", str(read_end)):
        monkeypatch.setenv("VIKINGBOT_LIFELINE_FD", value)
        assert startup.lifeline_fd() is None
    assert startup.lifeline_fd() is None


@posix_only
async def test_watch_lifeline_fires_only_on_eof():
    read_end, write_end = os.pipe()
    gone = asyncio.Event()
    try:
        startup.watch_lifeline(read_end, gone.set)
        os.write(write_end, b"x")  # stray data is not a dead server
        await asyncio.sleep(0.05)
        assert not gone.is_set()
        os.close(write_end)
        write_end = None
        await asyncio.wait_for(gone.wait(), 2)
    finally:
        asyncio.get_running_loop().remove_reader(read_end)
        os.close(read_end)
        if write_end is not None:
            os.close(write_end)


# The "server" spawns a gateway exactly like bootstrap does (own session, read end
# passed, write end kept), prints the gateway PID, then waits to be SIGKILLed.
_SERVER = textwrap.dedent(
    """
    import os, subprocess, sys, time
    read_end, write_end = os.pipe()
    env = dict(os.environ, VIKINGBOT_LIFELINE_FD=str(read_end))
    child = subprocess.Popen(
        [sys.executable, "-c", sys.argv[1], sys.argv[2]],
        env=env,
        start_new_session=True,
        pass_fds=(read_end,),
    )
    os.close(read_end)
    print(child.pid, flush=True)
    time.sleep(60)
    """
)
_GATEWAY = textwrap.dedent(
    """
    import asyncio, sys
    from pathlib import Path
    from vikingbot.utils.startup import lifeline_fd, watch_lifeline

    async def main():
        task = asyncio.current_task()

        def on_gone():
            Path(sys.argv[1]).touch()  # proves the lifeline, not a crash, ended us
            task.cancel()

        watch_lifeline(lifeline_fd(), on_gone)
        await asyncio.sleep(60)

    try:
        asyncio.run(main())
    except asyncio.CancelledError:
        raise SystemExit(3)
    """
)


def _pid_exists(pid: int) -> bool:
    # An exited orphan may linger as a zombie where PID 1 does not reap (containers).
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return state.returncode == 0 and not state.stdout.strip().startswith("Z")


@posix_only
def test_gateway_exits_when_server_is_sigkilled(tmp_path):
    marker = tmp_path / "lifeline-fired"
    server = subprocess.Popen(
        [sys.executable, "-c", _SERVER, _GATEWAY, str(marker)],
        stdout=subprocess.PIPE,
        text=True,
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
    )
    gateway_pid = int(server.stdout.readline())
    try:
        time.sleep(0.5)
        assert _pid_exists(gateway_pid)
        server.send_signal(signal.SIGKILL)
        server.wait()
        deadline = time.monotonic() + 5
        while _pid_exists(gateway_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        # Also fails if the gateway inherited the write end: it would never see EOF.
        assert not _pid_exists(gateway_pid), "gateway outlived its SIGKILLed server"
        assert marker.exists(), "gateway died, but not through its lifeline"
    finally:
        if _pid_exists(gateway_pid):
            os.kill(gateway_pid, signal.SIGKILL)
