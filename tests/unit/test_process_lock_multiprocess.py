"""Cross-process tests for data-directory lock ownership."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
from queue import Empty
from threading import BrokenBarrierError
from typing import Any

import pytest


def _race_for_lock(
    workspace: str,
    barrier: Any,
    result_queue: Any,
    release_event: Any,
) -> None:
    import openviking.utils.process_lock as process_lock

    original_makedirs = process_lock.os.makedirs

    def _synchronized_makedirs(path: str, *args: Any, **kwargs: Any) -> None:
        original_makedirs(path, *args, **kwargs)
        barrier.wait(timeout=20)

    process_lock.os.makedirs = _synchronized_makedirs
    try:
        lock_path = process_lock.acquire_data_dir_lock(workspace)
    except process_lock.DataDirectoryLocked as exc:
        result_queue.put(("locked", os.getpid(), str(exc)))
        return
    except (BrokenBarrierError, BaseException) as exc:
        result_queue.put(("error", os.getpid(), repr(exc)))
        return

    result_queue.put(("acquired", os.getpid(), Path(lock_path).read_text()))
    if not release_event.wait(timeout=20):
        result_queue.put(("error", os.getpid(), "timed out waiting for release"))
        return
    process_lock.release_data_dir_lock(lock_path)


def _controlled_owner(workspace: str, connection: Any) -> None:
    import openviking.utils.process_lock as process_lock

    try:
        lock_path = process_lock.acquire_data_dir_lock(workspace)
    except BaseException as exc:
        connection.send(("error", os.getpid(), repr(exc)))
        connection.close()
        return

    connection.send(("acquired", os.getpid(), Path(lock_path).read_text()))
    command = connection.recv()
    if command == "release":
        process_lock.release_data_dir_lock(lock_path)
        connection.send(("released", os.getpid(), Path(lock_path).exists()))
        connection.close()
        return
    if command == "crash":
        connection.close()
        os._exit(23)
    connection.send(("error", os.getpid(), f"unknown command: {command!r}"))
    connection.close()


def _acquire_and_release(workspace: str, connection: Any) -> None:
    import openviking.utils.process_lock as process_lock

    try:
        lock_path = process_lock.acquire_data_dir_lock(workspace)
    except process_lock.DataDirectoryLocked as exc:
        connection.send(("locked", os.getpid(), str(exc)))
    except BaseException as exc:
        connection.send(("error", os.getpid(), repr(exc)))
    else:
        connection.send(("acquired", os.getpid(), Path(lock_path).read_text()))
        process_lock.release_data_dir_lock(lock_path)
    finally:
        connection.close()


def _start_controlled_process(
    context: multiprocessing.context.BaseContext,
    target: Any,
    workspace: Path,
) -> tuple[multiprocessing.Process, Any]:
    parent_connection, child_connection = context.Pipe()
    process = context.Process(target=target, args=(str(workspace), child_connection))
    process.start()
    child_connection.close()
    return process, parent_connection


def _finish_process(process: multiprocessing.Process, timeout: float = 20) -> None:
    process.join(timeout)
    if process.is_alive():
        process.kill()
        process.join(5)
        pytest.fail(f"child process {process.pid} did not exit")


def test_simultaneous_processes_have_exactly_one_lock_winner(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    contender_count = 6
    barrier = context.Barrier(contender_count)
    result_queue = context.Queue()
    release_event = context.Event()
    processes = [
        context.Process(
            target=_race_for_lock,
            args=(str(tmp_path), barrier, result_queue, release_event),
        )
        for _ in range(contender_count)
    ]

    for process in processes:
        process.start()

    try:
        results = [result_queue.get(timeout=30) for _ in processes]
        winners = [result for result in results if result[0] == "acquired"]
        losers = [result for result in results if result[0] == "locked"]
        errors = [result for result in results if result[0] == "error"]

        assert errors == []
        assert len(winners) == 1
        assert len(losers) == contender_count - 1
        assert winners[0][2] == str(winners[0][1])
        assert all(f"PID {winners[0][1]}" in result[2] for result in losers)
    except Empty:
        pytest.fail("timed out waiting for synchronized lock contenders")
    finally:
        release_event.set()
        for process in processes:
            _finish_process(process)
        result_queue.close()
        result_queue.join_thread()

    assert all(process.exitcode == 0 for process in processes)
    assert not (tmp_path / ".openviking.pid").exists()


def test_stale_pid_file_is_reclaimed_by_another_process(tmp_path: Path):
    stale_pid = 999_999_999
    (tmp_path / ".openviking.pid").write_text(str(stale_pid))
    context = multiprocessing.get_context("spawn")
    process, connection = _start_controlled_process(context, _acquire_and_release, tmp_path)

    result = connection.recv()
    connection.close()
    _finish_process(process)

    assert result[0] == "acquired"
    assert result[1] != stale_pid
    assert result[2] == str(result[1])
    assert process.exitcode == 0
    assert not (tmp_path / ".openviking.pid").exists()


def test_crashed_owner_releases_kernel_lock_for_reacquire(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    owner, owner_connection = _start_controlled_process(context, _controlled_owner, tmp_path)
    acquired = owner_connection.recv()
    assert acquired[0] == "acquired"

    owner_connection.send("crash")
    owner_connection.close()
    _finish_process(owner)
    assert owner.exitcode == 23
    assert (tmp_path / ".openviking.pid").read_text() == str(acquired[1])

    successor, successor_connection = _start_controlled_process(
        context, _acquire_and_release, tmp_path
    )
    successor_result = successor_connection.recv()
    successor_connection.close()
    _finish_process(successor)

    assert successor_result[0] == "acquired"
    assert successor_result[1] != acquired[1]
    assert successor_result[2] == str(successor_result[1])
    assert successor.exitcode == 0
    assert not (tmp_path / ".openviking.pid").exists()


def test_release_allows_another_process_to_reacquire(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    owner, owner_connection = _start_controlled_process(context, _controlled_owner, tmp_path)
    acquired = owner_connection.recv()
    assert acquired[0] == "acquired"

    contender, contender_connection = _start_controlled_process(
        context, _acquire_and_release, tmp_path
    )
    blocked = contender_connection.recv()
    contender_connection.close()
    _finish_process(contender)

    assert blocked[0] == "locked"
    assert f"PID {acquired[1]}" in blocked[2]
    assert contender.exitcode == 0

    owner_connection.send("release")
    released = owner_connection.recv()
    owner_connection.close()
    _finish_process(owner)
    assert released == ("released", acquired[1], False)
    assert owner.exitcode == 0

    successor, successor_connection = _start_controlled_process(
        context, _acquire_and_release, tmp_path
    )
    reacquired = successor_connection.recv()
    successor_connection.close()
    _finish_process(successor)

    assert reacquired[0] == "acquired"
    assert reacquired[2] == str(reacquired[1])
    assert successor.exitcode == 0
    assert not (tmp_path / ".openviking.pid").exists()
