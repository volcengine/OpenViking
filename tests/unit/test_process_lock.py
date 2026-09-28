# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Workspace exclusivity and recovery contracts, exercised with real processes."""

import errno
import multiprocessing
import os
import subprocess
import sys

import pytest

import openviking.utils.process_lock as process_lock_module
from openviking.utils.process_lock import (
    LOCK_FILENAME,
    DataDirectoryLocked,
    acquire_data_dir_lock,
    release_data_dir_lock,
)


def _probe_lock(workspace):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "from openviking.utils.process_lock import (\n"
            "    acquire_data_dir_lock, release_data_dir_lock, DataDirectoryLocked)\n"
            "try:\n"
            "    path = acquire_data_dir_lock(sys.argv[1])\n"
            "except DataDirectoryLocked as exc:\n"
            "    print(exc)\n"
            "    sys.exit(2)\n"
            "release_data_dir_lock(path)\n",
            str(workspace),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )


def _contend_for_lock(workspace, start, release, results):
    start.wait(timeout=20)
    try:
        lock_path = acquire_data_dir_lock(workspace)
    except DataDirectoryLocked:
        results.put((os.getpid(), "locked"))
        return
    try:
        results.put((os.getpid(), "acquired"))
        if not release.poll(30):
            raise TimeoutError("lock owner was not released")
        release.recv()
    finally:
        release.close()
        release_data_dir_lock(lock_path)


class TestAcquireDataDirLock:
    @pytest.mark.parametrize("failure", ["open", "lock"])
    def test_filesystem_errors_fail_acquisition(self, tmp_path, monkeypatch, failure):
        """Filesystem permission and unsupported-lock errors must fail startup."""
        error = PermissionError(errno.EACCES, "read-only workspace")
        if failure == "open":
            target, name = process_lock_module.os, "open"
        else:
            error = OSError(errno.ENOTSUP, "locking unsupported")
            if sys.platform == "win32":
                import msvcrt

                target, name = msvcrt, "locking"
            else:
                import fcntl

                target, name = fcntl, "flock"

        def fail(*args, **kwargs):
            raise error

        with monkeypatch.context() as patch:
            patch.setattr(target, name, fail)
            with pytest.raises(OSError) as raised:
                acquire_data_dir_lock(str(tmp_path))
            assert raised.value is error

        # Failed acquisition must not leave a process-local owner behind.
        lock_path = acquire_data_dir_lock(str(tmp_path))
        try:
            result = _probe_lock(tmp_path)
            assert result.returncode == 2, result.stderr
        finally:
            release_data_dir_lock(lock_path)
        assert _probe_lock(tmp_path).returncode == 0


class TestReleaseDataDirLock:
    @pytest.mark.parametrize("use_alias", [False, True])
    def test_symlink_alias_shares_the_same_process_local_refcount(self, tmp_path, use_alias):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        alias = workspace
        if use_alias:
            alias = tmp_path / "alias"
            try:
                alias.symlink_to(workspace, target_is_directory=True)
            except OSError as exc:
                pytest.skip(f"directory symlinks are unavailable: {exc}")

        lock_path = acquire_data_dir_lock(str(workspace))
        try:
            assert acquire_data_dir_lock(str(alias)) == lock_path
            release_data_dir_lock(lock_path)
            result = _probe_lock(workspace)
            assert result.returncode == 2, result.stderr
            assert "connect clients over HTTP" in result.stdout
            assert str(workspace) in result.stdout
        finally:
            release_data_dir_lock(lock_path)
            release_data_dir_lock(lock_path)

        assert (workspace / LOCK_FILENAME).exists()
        assert _probe_lock(workspace).returncode == 0


class TestProcessLockIntegration:
    @pytest.mark.parametrize("crash", [False, True])
    def test_concurrent_start_and_exit_release_lock(self, tmp_path, crash):
        """Only one simultaneous starter enters; graceful exit and kill release it."""
        ctx = multiprocessing.get_context("spawn")
        start, results = ctx.Barrier(4), ctx.Queue()
        pipes = [ctx.Pipe(duplex=False) for _ in range(4)]
        processes = [
            ctx.Process(target=_contend_for_lock, args=(str(tmp_path), start, reader, results))
            for reader, _ in pipes
        ]
        try:
            for process in processes:
                process.start()
            outcomes = [results.get(timeout=20) for _ in processes]
            owners = [pid for pid, status in outcomes if status == "acquired"]
            assert len(owners) == 1, outcomes
            assert sum(status == "locked" for _, status in outcomes) == 3
            owner_index = next(i for i, process in enumerate(processes) if process.pid == owners[0])
            owner = processes[owner_index]
            if crash:
                owner.kill()
            else:
                pipes[owner_index][1].send(None)
            for process in processes:
                process.join(timeout=20)
                assert not process.is_alive()
                if not crash or process is not owner:
                    assert process.exitcode == 0
            assert (tmp_path / LOCK_FILENAME).exists()
            result = _probe_lock(tmp_path)
            assert result.returncode == 0, result.stderr
        finally:
            for process in processes:
                if process.pid is not None:
                    if process.is_alive():
                        process.kill()
                    process.join(timeout=5)
            for reader, writer in pipes:
                reader.close()
                writer.close()
            results.close()
            results.join_thread()
