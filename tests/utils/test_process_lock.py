# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for process-lock interaction with host signal handling."""

import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from openviking.utils.process_lock import acquire_data_dir_lock


def test_acquire_does_not_override_sigterm_handler(tmp_path):
    """Uvicorn must retain ownership of SIGTERM for graceful lifespan shutdown."""
    original_handler = signal.getsignal(signal.SIGTERM)

    acquire_data_dir_lock(str(tmp_path))

    assert signal.getsignal(signal.SIGTERM) is original_handler


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM lifecycle is POSIX-specific")
def test_uvicorn_sigterm_runs_lifespan_and_releases_process_lock(tmp_path):
    """A real Uvicorn SIGTERM must reach lifespan cleanup without tracebacks."""
    workspace = tmp_path / "workspace"
    ready_file = tmp_path / "ready"
    script = textwrap.dedent(
        """
        from contextlib import asynccontextmanager
        from pathlib import Path
        import sys

        import uvicorn
        from fastapi import FastAPI

        from openviking.utils.process_lock import (
            acquire_data_dir_lock,
            release_data_dir_lock,
        )

        workspace = sys.argv[1]
        ready_file = Path(sys.argv[2])

        @asynccontextmanager
        async def lifespan(_app):
            lock_path = acquire_data_dir_lock(workspace)
            ready_file.write_text("ready")
            try:
                yield
            finally:
                release_data_dir_lock(lock_path)

        app = FastAPI(lifespan=lifespan)
        uvicorn.run(app, host="127.0.0.1", port=0, log_level="info")
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(workspace), str(ready_file)],
        cwd=os.getcwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not ready_file.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready_file.exists(), "Uvicorn child did not enter application lifespan"

        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    # Current Uvicorn restores and re-raises the captured POSIX signal after
    # its graceful shutdown completes, so both a clean return and -SIGTERM are
    # valid host-level terminal statuses.  The lifecycle evidence below is the
    # regression contract: lifespan completed and no exception traceback was
    # emitted by a process-lock handler.
    assert process.returncode in {0, -signal.SIGTERM}, output
    assert "Application shutdown complete" in output
    assert "Finished server process" in output
    assert not (workspace / ".openviking.pid").exists()
    assert "SystemExit" not in output
    assert "CancelledError" not in output
