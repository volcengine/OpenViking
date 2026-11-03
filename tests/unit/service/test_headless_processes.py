# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise the actual spawn supervisor with signal-aware child processes."""

import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest


@pytest.mark.parametrize("mode", ["sigterm", "child_exit"])
def test_headless_supervisor_reaps_all_spawned_children(tmp_path, mode):
    script = tmp_path / "probe.py"
    script.write_text(
        textwrap.dedent("""\
        import os, signal, sys, threading
        from pathlib import Path
        from openviking.server import headless
        from openviking.server.bootstrap import _sigterm_runs_finally
        from openviking.server.config import ServerConfig

        def child(config, path):
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *args: stop.set())
            signal.signal(signal.SIGINT, lambda *args: stop.set())
            pid = os.getpid()
            print(f"CHILD_READY {pid}", flush=True)
            Path(path, str(pid) + ".ready").touch()
            if sys.argv[2] == "child_exit":
                while len(list(Path(path).glob("*.ready"))) < 2:
                    if stop.wait(0.01):
                        break
            if sys.argv[2] == "child_exit" and not Path(path, "claimed").exists():
                try:
                    fd = os.open(Path(path, "claimed"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                except FileExistsError:
                    pass
                else:
                    stop.set()
            stop.wait()
            Path(path, str(pid) + ".stopped").write_text("stopped")
            print(f"CHILD_STOPPED {pid}", flush=True)

        if __name__ == "__main__":
            headless._worker = child
            with _sigterm_runs_finally():
                headless.run_headless(
                    ServerConfig(roles=["queue_worker", "scheduler"], workers=2),
                    config_path=sys.argv[1],
                )
        """)
    )
    stderr_file = (tmp_path / "stderr.log").open("w")
    process = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path), mode],
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=stderr_file,
        text=True,
        start_new_session=True,
    )
    output = ""
    try:
        if mode == "sigterm":
            deadline = time.monotonic() + 90
            ready_files = []
            while time.monotonic() < deadline:
                ready_files = list(tmp_path.glob("*.ready"))
                if len(ready_files) == 2:
                    break
                assert process.poll() is None, "supervisor exited before both children became ready"
                time.sleep(0.05)
            assert len(ready_files) == 2, (
                "children did not become ready: " + (tmp_path / "stderr.log").read_text()
            )
            process.send_signal(signal.SIGTERM)
        remaining, _ = process.communicate(timeout=90)
        stderr = (tmp_path / "stderr.log").read_text()
        output += remaining
        assert process.returncode != 0
        if mode == "child_exit":
            assert "exited (code 0)" in stderr
        ready = {
            int(line.split()[1]) for line in output.splitlines() if line.startswith("CHILD_READY")
        }
        assert len(ready) == 2, output + stderr
        assert {int(path.stem) for path in tmp_path.glob("*.stopped")} == ready
        for pid in ready:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=10)
        stderr_file.close()
