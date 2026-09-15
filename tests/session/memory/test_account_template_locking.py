# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Template lock contention must not starve storage I/O in the default executor."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from openviking.pyagfs import AGFSNotFoundError
from openviking.session.memory.account_templates import (
    account_memory_template_path,
    read_account_memory_template,
    resolve_account_memory_registry,
    update_account_memory_template,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError


class LockingAGFS:
    """Blocking sync backend, including the legacy long-wait failure mode."""

    def __init__(self):
        self.files = {}
        self.locks = {}
        self.leases = {}
        self.timeouts = []

    def pathlock_acquire_exact(self, ctx, path, timeout_secs=0, owner_lease_ref=None):
        return self.pathlock_acquire_exact_batch(ctx, [path], timeout_secs, owner_lease_ref)

    def pathlock_acquire_exact_batch(self, ctx, paths, timeout_secs=0, owner_lease_ref=None):
        self.timeouts.append(timeout_secs)
        held = []
        for path in sorted(paths):
            lock = self.locks.setdefault(path, threading.Lock())
            # Bound failures on the old implementation so the regression is fast.
            if not lock.acquire(timeout=min(timeout_secs, 0.15)):
                for previous in held:
                    self.locks[previous].release()
                raise LockAcquisitionError("test lock conflict")
            held.append(path)
        lease = {"lease_ref": paths[0], "paths": held}
        self.leases[paths[0]] = lease
        return lease

    def pathlock_release(self, ctx, lease):
        path = lease["lease_ref"]
        assert ctx["account_id"] == "locking-test"
        assert self.leases.pop(path) == lease
        for locked_path in lease["paths"]:
            self.locks[locked_path].release()

    def read(self, path, **kwargs):
        if path not in self.files:
            raise AGFSNotFoundError(path)
        return self.files[path]

    def write(self, path, data, *, ctx):
        if not path.endswith(".backup"):
            assert path.endswith(".tmp"), "the active file must never be overwritten in place"
            assert path in self.leases[ctx["lease_ref"]]["paths"]
        self.files[path] = data

    def mv(self, old_path, new_path, *, ctx):
        held = self.leases[ctx["lease_ref"]]["paths"]
        assert old_path in held and new_path in held
        self.files[new_path] = self.files.pop(old_path)

    def ensure_parent_dirs(self, path, **kwargs):
        pass

    def rm(self, path, *, ctx):
        assert path in self.leases[ctx["lease_ref"]]["paths"]
        self.files.pop(path, None)


@pytest.mark.parametrize("workers", [1, 2])
def test_template_readers_finish_with_a_small_default_executor(workers):
    agfs = LockingAGFS()
    fs = SimpleNamespace(agfs=agfs)

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=workers))
        results = await asyncio.wait_for(
            asyncio.gather(
                *(read_account_memory_template(fs, "locking-test", "profile") for _ in range(8)),
                return_exceptions=True,
            ),
            timeout=4,
        )
        assert results == [None] * 8
        assert not agfs.leases
        assert not agfs.timeouts, "readers must not acquire locks at all"

    asyncio.run(run())


@pytest.mark.parametrize("workers", [1, 2])
def test_concurrent_template_writers_do_not_starve_the_lock_holder(workers):
    agfs = LockingAGFS()
    fs = SimpleNamespace(agfs=agfs)
    registry = MemoryTypeRegistry()

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=workers))
        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    update_account_memory_template(
                        fs, "locking-test", "profile", {"description": f"Writer {index}"}, registry
                    )
                    for index in range(8)
                )
            ),
            timeout=3,
        )
        assert [item["description"] for item in results] == [
            f"Writer {index}" for index in range(8)
        ]
        assert not agfs.leases
        assert set(agfs.timeouts) == {0.0}
        assert not any(path.endswith(".tmp") for path in agfs.files)

    asyncio.run(run())


@pytest.mark.parametrize("workers", [1, 2])
def test_template_resolve_and_publish_share_a_small_executor(workers):
    agfs = LockingAGFS()
    fs = SimpleNamespace(agfs=agfs)
    registry = MemoryTypeRegistry()

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=workers))
        await update_account_memory_template(
            fs, "locking-test", "profile", {"description": "Before"}, registry
        )
        results = await asyncio.wait_for(
            asyncio.gather(
                *(resolve_account_memory_registry(fs, "locking-test", registry) for _ in range(8)),
                update_account_memory_template(
                    fs, "locking-test", "profile", {"description": "After"}, registry
                ),
            ),
            timeout=4,
        )
        assert all(
            result.get("profile").description in {"Before", "After"} for result in results[:-1]
        )
        assert results[-1]["description"] == "After"
        assert not agfs.leases

    asyncio.run(run())


async def _wait_for_flag(flag):
    async def wait():
        while not flag.is_set():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=2)


@pytest.mark.parametrize("existing", [False, True], ids=["first-publication", "replacement"])
def test_readers_never_see_a_partial_staging_file(monkeypatch, existing):
    agfs = LockingAGFS()
    fs = SimpleNamespace(agfs=agfs)
    registry = MemoryTypeRegistry()
    entered = threading.Event()
    proceed = threading.Event()

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=2))
        if existing:
            await update_account_memory_template(
                fs, "locking-test", "profile", {"description": "Before"}, registry
            )
        write = agfs.write

        def slow_write(path, data, **kwargs):
            if path.endswith(".tmp"):
                agfs.files[path] = b"partial YAML"
                entered.set()
                assert proceed.wait(3)
            return write(path, data, **kwargs)

        monkeypatch.setattr(agfs, "write", slow_write)
        writer = asyncio.create_task(
            update_account_memory_template(
                fs, "locking-test", "profile", {"description": "After"}, registry
            )
        )
        try:
            await _wait_for_flag(entered)
            readers = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        read_account_memory_template(fs, "locking-test", "profile")
                        for _ in range(16)
                    )
                ),
                timeout=1,
            )
            assert all(
                result["description"] == "Before" if existing else result is None
                for result in readers
            )
        finally:
            proceed.set()
            await writer
        assert (await read_account_memory_template(fs, "locking-test", "profile"))[
            "description"
        ] == "After"
        assert not agfs.leases
        assert not any(path.endswith(".tmp") for path in agfs.files)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["acquire", "write", "move", "release"])
def test_cancelled_publication_drains_io_and_releases_leases(monkeypatch, phase):
    agfs = LockingAGFS()
    fs = SimpleNamespace(agfs=agfs)
    registry = MemoryTypeRegistry()
    entered = threading.Event()
    proceed = threading.Event()
    method = {
        "acquire": "pathlock_acquire_exact_batch",
        "write": "write",
        "move": "mv",
        "release": "pathlock_release",
    }[phase]
    original = getattr(agfs, method)

    def blocked(*args, **kwargs):
        result = original(*args, **kwargs) if phase != "release" else None
        entered.set()
        assert proceed.wait(3)
        return original(*args, **kwargs) if phase == "release" else result

    monkeypatch.setattr(agfs, method, blocked)

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        writer = asyncio.create_task(
            update_account_memory_template(
                fs, "locking-test", "profile", {"description": "Complete"}, registry
            )
        )
        try:
            await _wait_for_flag(entered)
            writer.cancel()
            await asyncio.sleep(0.01)
            writer.cancel()
            await asyncio.sleep(0.01)
            assert not writer.done(), "cancellation must drain the in-flight native call"
            assert agfs.leases, "do not release the lease before the worker finishes"
        finally:
            proceed.set()
            with pytest.raises(asyncio.CancelledError):
                await writer
        assert not agfs.leases
        assert all(not lock.locked() for lock in agfs.locks.values())
        assert not any(path.endswith(".tmp") for path in agfs.files)
        result = await read_account_memory_template(fs, "locking-test", "profile")
        assert result is None if phase == "acquire" else result["description"] == "Complete"

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancel"])
def test_waiting_writer_does_not_block_readers_or_leak_locks(monkeypatch, cancel):
    import openviking.session.memory.account_templates as templates

    monkeypatch.setattr(templates, "_TEMPLATE_LOCK_TIMEOUT_SECONDS", 0.05)
    agfs = LockingAGFS()
    fs = SimpleNamespace(agfs=agfs)
    registry = MemoryTypeRegistry()
    path = account_memory_template_path("locking-test", "profile")
    lock = threading.Lock()
    agfs.locks[path] = lock

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        with lock:
            writer = asyncio.create_task(
                update_account_memory_template(
                    fs, "locking-test", "profile", {"description": "Waiting"}, registry
                )
            )
            while not agfs.timeouts:
                await asyncio.sleep(0.001)
            assert await read_account_memory_template(fs, "locking-test", "profile") is None
            if cancel:
                writer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await writer
            else:
                with pytest.raises(ResourceBusyError) as exc:
                    await writer
                assert exc.value.uri == path
                assert exc.value.conflict_type == "memory_templates_busy"
        assert agfs.timeouts and set(agfs.timeouts) == {0.0}
        assert not agfs.leases
        assert not agfs.files
        assert all(not held.locked() for held in agfs.locks.values())

    asyncio.run(run())


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["staging", "before-move", "after-move", "cleanup"])
def test_publication_failures_never_overwrite_the_active_file(monkeypatch, existing, failure):
    agfs = LockingAGFS()
    fs = SimpleNamespace(agfs=agfs)
    registry = MemoryTypeRegistry()
    path = account_memory_template_path("locking-test", "profile")

    async def run():
        if existing:
            await update_account_memory_template(
                fs, "locking-test", "profile", {"description": "Before"}, registry
            )
        old = agfs.files.get(path)
        write, move, remove = agfs.write, agfs.mv, agfs.rm

        def faulty_write(target, data, **kwargs):
            if failure == "staging" and target.endswith(".tmp"):
                agfs.files[target] = b"partial"
                raise OSError("staging failed")
            return write(target, data, **kwargs)

        def faulty_move(source, target, **kwargs):
            if failure == "before-move":
                raise OSError("publish failed")
            if failure == "after-move":
                # S3 CopyObject succeeded but deleting its source failed.
                agfs.files[target] = agfs.files[source]
                raise OSError("source cleanup failed")
            return move(source, target, **kwargs)

        def faulty_cleanup(target, **kwargs):
            if failure in {"cleanup", "after-move"} and target.endswith(".tmp"):
                raise OSError("cleanup failed")
            return remove(target, **kwargs)

        monkeypatch.setattr(agfs, "write", faulty_write)
        monkeypatch.setattr(agfs, "mv", faulty_move)
        monkeypatch.setattr(agfs, "rm", faulty_cleanup)
        update = update_account_memory_template(
            fs, "locking-test", "profile", {"description": "After"}, registry
        )
        if failure in {"staging", "before-move"}:
            with pytest.raises(OSError):
                await update
            assert agfs.files.get(path) == old
        else:
            assert (await update)["description"] == "After"
            assert (await read_account_memory_template(fs, "locking-test", "profile"))[
                "description"
            ] == "After"
        assert not agfs.leases
        assert all(not held.locked() for held in agfs.locks.values())
        if existing:
            assert agfs.files[path + ".backup"] == old

    asyncio.run(run())


@pytest.mark.parametrize("workers", [None, 1], ids=["default-executor", "one-worker"])
@pytest.mark.parametrize("encrypted", [False, True], ids=["plaintext", "encrypted"])
def test_native_template_read_publish_and_reset(tmp_path, workers, encrypted):
    from openviking.pyagfs import get_binding_client
    from openviking.utils.agfs_utils import RagfsBindingConfig, mount_agfs_backend
    from openviking_cli.utils.config.agfs_config import AGFSConfig

    try:
        client_type, _ = get_binding_client()
    except ImportError:
        pytest.skip("RAGFS native binding is not installed")
    if not hasattr(client_type, "pathlock_acquire_exact_batch"):
        pytest.skip("RAGFS native binding predates the current PathLock API")
    config = RagfsBindingConfig(
        agfs=AGFSConfig(path=str(tmp_path), backend="local"),
        root_key=b"k" * 32 if encrypted else None,
        provider_type=1 if encrypted else None,
    )
    native = client_type(None, config=config.to_binding_dict())
    fs = SimpleNamespace(agfs=native)
    registry = MemoryTypeRegistry()

    async def run():
        if workers is not None:
            asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=workers))
        # Original B2 reproduction: no override, no writer, concurrent registry reads.
        empty = await asyncio.wait_for(
            asyncio.gather(
                *(resolve_account_memory_registry(fs, "locking-test", registry) for _ in range(8))
            ),
            timeout=5,
        )
        assert all(
            result.get("profile").description == registry.get("profile").description
            for result in empty
        )
        await update_account_memory_template(
            fs, "locking-test", "profile", {"description": "Before"}, registry
        )
        for index in range(3):
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        read_account_memory_template(fs, "locking-test", "profile")
                        for _ in range(24)
                    ),
                    update_account_memory_template(
                        fs, "locking-test", "profile", {"description": f"Version {index}"}, registry
                    ),
                ),
                timeout=5,
            )
            expected_old = "Before" if index == 0 else f"Version {index - 1}"
            assert all(
                result["description"] in {expected_old, f"Version {index}"} for result in results
            )
        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    update_account_memory_template(
                        fs,
                        "locking-test",
                        "profile",
                        {"description": f"Concurrent {index}"},
                        registry,
                    )
                    for index in range(4)
                )
            ),
            timeout=5,
        )
        assert [result["description"] for result in results] == [
            f"Concurrent {index}" for index in range(4)
        ]
        final = await read_account_memory_template(fs, "locking-test", "profile")
        results = await asyncio.wait_for(
            asyncio.gather(
                *(read_account_memory_template(fs, "locking-test", "profile") for _ in range(24)),
                update_account_memory_template(fs, "locking-test", "profile", None, registry),
            ),
            timeout=5,
        )
        assert all(
            result is None or result["description"] == final["description"] for result in results
        )
        assert await read_account_memory_template(fs, "locking-test", "profile") is None

    try:
        mount_agfs_backend(native, config)
        asyncio.run(run())
    finally:
        native.close()
