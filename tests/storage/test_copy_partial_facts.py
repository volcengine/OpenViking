# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Copy facts must describe committed files even when the request fails."""

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from openviking.observability.events import (
    register_event_subscriber,
    unregister_event_subscriber,
)
from openviking.observability.reporting import runtime
from openviking.pyagfs.async_client import AsyncAGFSClient
from tests.storage.test_viking_fs_cp import _ctx, _DirectoryCopyAGFS, _viking_fs

_TARGET = "/local/acct/resources/target"
_TARGET_URI = "viking://resources/target"


class _PartialCopyAGFS(_DirectoryCopyAGFS):
    def __init__(self, *, raw=False, target_exists=False):
        super().__init__()
        self.source = "/local/acct/temp/import" if raw else "/local/acct/resources/source"
        self.directories = {"/local/acct/resources", self.source}
        self.files = {f"{self.source}/a.md": b"\x00encrypted-a", f"{self.source}/b.md": b"b"}
        if target_exists:
            self.directories.add(_TARGET)
            self.files[f"{_TARGET}/a.md"] = b"old"
        self.fail_leaf = "b.md"

    async def cp(self, source, target, recursive=False, fs_ctx=None):
        if target.endswith("/" + str(self.fail_leaf)):
            raise OSError("injected copy failure")
        await super().cp(source, target, recursive=recursive, fs_ctx=fs_ctx)


class _RawCopyClient:
    """Exercise the real async adapter and helpers, preserving opaque bytes."""

    def __init__(self, backend):
        self.backend = backend
        self.fast_paths = []
        self.contexts = []
        self.started = threading.Event()
        self.resume = threading.Event()
        self.block_copy = False

    def stat(self, path, *, ctx=None):
        if path in self.backend.directories:
            return {"isDir": True}
        if path in self.backend.files:
            return {"isDir": False}
        raise FileNotFoundError(path)

    def mkdir(self, path, *, ctx=None):
        self.backend.directories.add(path)

    def ls(self, path, *, ctx=None):
        prefix = path.rstrip("/") + "/"
        return [
            {"name": child[len(prefix) :], "isDir": child in self.backend.directories}
            for child in sorted(self.backend.directories | self.backend.files.keys())
            if child.startswith(prefix) and "/" not in child[len(prefix) :]
        ]

    def copy_within_mount(self, source, target, *, ctx=None):
        self.contexts.append(ctx)
        if target.endswith("/" + str(self.backend.fail_leaf)):
            raise OSError("injected copy failure")
        if self.block_copy:
            self.started.set()
            if not self.resume.wait(timeout=5):
                raise TimeoutError("test did not release copy")
        self.backend.files[target] = self.backend.files[source]
        self.fast_paths.append((source, target))
        return {"performed": True}

    def cat(self, *args, **kwargs):
        raise AssertionError("raw copy must not read or rewrite encrypted bytes")

    def write(self, *args, **kwargs):
        raise AssertionError("raw copy must preserve the same-mount fast path")


@pytest.fixture
def copy_events(monkeypatch):
    monkeypatch.setattr(runtime, "_reporter", object())
    events = []
    register_event_subscriber("partial_copy_facts", events.append)
    try:
        yield events
    finally:
        unregister_event_subscriber("partial_copy_facts")


def _file_facts(events):
    return [
        event.payload
        for event in events
        if event.event_name == "file_mutation" and not event.payload["is_dir"]
    ]


def _operation(monkeypatch, backend, *, raw):
    fs = _viking_fs(monkeypatch, backend)
    if raw:
        sync_client = _RawCopyClient(backend)
        monkeypatch.setattr(backend, "cp", AsyncAGFSClient(sync_client).cp)
        monkeypatch.setattr(fs, "_ensure_parent_dirs", AsyncMock())

        def run():
            return fs.persist_temp_tree(
                "viking://temp/import",
                _TARGET_URI,
                ctx=_ctx(),
                lease_ref=backend.operation_lease,
            )

        return fs, run, sync_client

    def run():
        return fs.cp(
            "viking://resources/source",
            _TARGET_URI,
            recursive=True,
            ctx=_ctx(),
            maintain_vectors=False,
        )

    return fs, run, None


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("target_exists", [False, True])
@pytest.mark.asyncio
async def test_partial_copy_and_retry_preserve_original_creation_count(
    monkeypatch, copy_events, raw, target_exists
):
    backend = _PartialCopyAGFS(raw=raw, target_exists=target_exists)
    _, run, sync_client = _operation(monkeypatch, backend, raw=raw)
    with pytest.raises(OSError, match="injected copy failure"):
        await run()
    facts = _file_facts(copy_events)
    assert [(fact["uri"], fact["change_type"]) for fact in facts] == [
        (_TARGET_URI + "/a.md", "modified" if target_exists else "added")
    ]
    assert backend.files[_TARGET + "/a.md"] == b"\x00encrypted-a"
    assert _TARGET + "/b.md" not in backend.files
    backend.fail_leaf = None
    await run()
    facts = _file_facts(copy_events)
    assert [(fact["uri"], fact["change_type"]) for fact in facts[1:]] == [
        (_TARGET_URI + "/a.md", "modified"),
        (_TARGET_URI + "/b.md", "added"),
    ]
    assert sum(fact["is_new_file"] for fact in facts) == (1 if target_exists else 2)
    assert all(fact["account_id"] == "acct" and fact["operation"] == "copy" for fact in facts)
    if raw:
        assert len(sync_client.fast_paths) == 3
        assert all(ctx["lease_ref"] == "copy-operation" for ctx in sync_client.contexts)
    else:
        assert backend.events[-1][0] == "release"
        # Failure reporting does not list or stat the target tree again.
        assert sum(event[0] == "ls" for event in backend.events) == 4


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.asyncio
async def test_copy_failure_before_first_write_does_not_report_existing_file(
    monkeypatch, copy_events, raw
):
    backend = _PartialCopyAGFS(raw=raw, target_exists=True)
    backend.fail_leaf = "a.md"
    _, run, _ = _operation(monkeypatch, backend, raw=raw)
    with pytest.raises(OSError, match="injected copy failure"):
        await run()
    assert _file_facts(copy_events) == []
    assert backend.files[_TARGET + "/a.md"] == b"old"


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.asyncio
async def test_partial_nested_copy_reports_only_completed_descendants(
    monkeypatch, copy_events, raw
):
    backend = _PartialCopyAGFS(raw=raw)
    backend.directories.add(backend.source + "/nested")
    backend.files = {
        backend.source + "/a.md": b"a",
        backend.source + "/nested/a.md": b"child",
        backend.source + "/nested/b.md": b"fail",
    }
    _, run, _ = _operation(monkeypatch, backend, raw=raw)
    with pytest.raises(OSError, match="injected copy failure"):
        await run()
    assert {fact["uri"] for fact in _file_facts(copy_events)} == {
        _TARGET_URI + "/a.md",
        _TARGET_URI + "/nested/a.md",
    }
    directories = {
        event.payload["uri"]
        for event in copy_events
        if event.event_name == "file_mutation" and event.payload["is_dir"]
    }
    assert directories == {_TARGET_URI, _TARGET_URI + "/nested"}


@pytest.mark.parametrize("copy_sidecars", [False, True])
@pytest.mark.asyncio
async def test_partial_copy_keeps_semantic_sidecar_filter(monkeypatch, copy_events, copy_sidecars):
    backend = _PartialCopyAGFS()
    backend.files[backend.source + "/.abstract.md"] = b"summary"
    fs = _viking_fs(monkeypatch, backend)
    with pytest.raises(OSError, match="injected copy failure"):
        await fs.cp(
            "viking://resources/source",
            _TARGET_URI,
            recursive=True,
            ctx=_ctx(),
            maintain_vectors=False,
            copy_semantic_sidecars=copy_sidecars,
        )
    assert {fact["uri"] for fact in _file_facts(copy_events)} == (
        {_TARGET_URI + "/a.md", _TARGET_URI + "/.abstract.md"}
        if copy_sidecars
        else {_TARGET_URI + "/a.md"}
    )


@pytest.mark.parametrize("surviving", [set(), {"a.md"}, {"a.md", "b.md"}])
@pytest.mark.asyncio
async def test_failed_vector_rollback_reports_only_surviving_copies(
    monkeypatch, copy_events, surviving
):
    backend = _PartialCopyAGFS()
    backend.fail_leaf = None
    fs = _viking_fs(monkeypatch, backend)
    monkeypatch.setattr(
        fs, "_copy_vector_store_uris", AsyncMock(side_effect=RuntimeError("vector failure"))
    )

    async def partial_cleanup(*args, **kwargs):
        for name in {"a.md", "b.md"} - surviving:
            backend.files.pop(_TARGET + "/" + name)
        if not surviving:
            backend.directories.discard(_TARGET)
        raise OSError("cleanup failure")

    monkeypatch.setattr(fs, "_cleanup_transfer_target", partial_cleanup)
    with pytest.raises(RuntimeError, match="vector failure"):
        await fs.cp("viking://resources/source", _TARGET_URI, recursive=True, ctx=_ctx())
    facts = _file_facts(copy_events)
    assert {fact["uri"] for fact in facts} == {_TARGET_URI + "/" + name for name in surviving}
    assert all(fact["change_type"] == "added" for fact in facts)
    assert backend.events[-1][0] == "release"


@pytest.mark.asyncio
async def test_cancelled_raw_copy_reports_settled_writes(monkeypatch, copy_events):
    backend = _PartialCopyAGFS(raw=True)
    backend.fail_leaf = None
    _, run, sync_client = _operation(monkeypatch, backend, raw=True)
    sync_client.block_copy = True
    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(sync_client.started.wait, 5)
        task.cancel()
        sync_client.resume.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert {fact["uri"] for fact in _file_facts(copy_events)} == {
            _TARGET_URI + "/a.md",
            _TARGET_URI + "/b.md",
        }
    finally:
        sync_client.resume.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_logical_copy_reports_write_before_releasing_lease(
    monkeypatch, copy_events
):
    backend = _PartialCopyAGFS()
    backend.fail_leaf = None

    class _LogicalCopyClient(_RawCopyClient):
        def cat(self, path, *, stream=False, ctx=None):
            data = self.backend.files[path]
            return iter([data]) if stream else data

        def write(self, path, data, *, ctx=None):
            self.started.set()
            if not self.resume.wait(timeout=5):
                raise TimeoutError("test did not release write")
            self.backend.files[path] = b"".join(data)

    client = _LogicalCopyClient(backend)
    fs, run, _ = _operation(monkeypatch, backend, raw=False)
    monkeypatch.setattr(backend, "cp", AsyncAGFSClient(client).cp)
    observed_before_release = []
    release = backend.pathlock_release

    async def capture_release(lease):
        observed_before_release.extend(_file_facts(copy_events))
        await release(lease)

    monkeypatch.setattr(backend, "pathlock_release", capture_release)
    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(client.started.wait, 5)
        task.cancel()
        client.resume.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert [fact["uri"] for fact in observed_before_release] == [_TARGET_URI + "/a.md"]
        assert backend.files[_TARGET + "/a.md"] == b"\x00encrypted-a"
        assert _TARGET + "/b.md" not in backend.files
        assert backend.events[-1][0] == "release"
    finally:
        client.resume.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_rollback_does_not_assume_unverifiable_targets_survive(
    monkeypatch, copy_events
):
    backend = _PartialCopyAGFS()
    backend.fail_leaf = None
    fs = _viking_fs(monkeypatch, backend)
    monkeypatch.setattr(
        fs, "_copy_vector_store_uris", AsyncMock(side_effect=RuntimeError("vector failure"))
    )
    stat = backend.stat
    cleanup_attempted = False

    async def unavailable_stat(path, fs_ctx=None):
        if cleanup_attempted and path.startswith(_TARGET):
            raise OSError("stat unavailable")
        return await stat(path, fs_ctx=fs_ctx)

    async def failed_cleanup(*args, **kwargs):
        nonlocal cleanup_attempted
        cleanup_attempted = True
        raise OSError("cleanup failure")

    monkeypatch.setattr(backend, "stat", unavailable_stat)
    monkeypatch.setattr(fs, "_cleanup_transfer_target", failed_cleanup)
    with pytest.raises(RuntimeError, match="vector failure"):
        await fs.cp("viking://resources/source", _TARGET_URI, recursive=True, ctx=_ctx())
    assert copy_events == []
    assert backend.events[-1][0] == "release"


@pytest.mark.parametrize("surviving", [set(), {"b.md"}])
@pytest.mark.asyncio
async def test_cancelled_vector_rollback_reports_only_retained_files(
    monkeypatch, copy_events, surviving
):
    backend = _PartialCopyAGFS()
    backend.fail_leaf = None
    fs = _viking_fs(monkeypatch, backend)
    monkeypatch.setattr(
        fs, "_copy_vector_store_uris", AsyncMock(side_effect=RuntimeError("vector failure"))
    )

    async def cancelled_cleanup(*args, **kwargs):
        for name in {"a.md", "b.md"} - surviving:
            backend.files.pop(_TARGET + "/" + name)
        if not surviving:
            backend.directories.discard(_TARGET)
        raise asyncio.CancelledError

    monkeypatch.setattr(fs, "_cleanup_transfer_target", cancelled_cleanup)
    with pytest.raises(asyncio.CancelledError):
        await fs.cp("viking://resources/source", _TARGET_URI, recursive=True, ctx=_ctx())
    assert {fact["uri"] for fact in _file_facts(copy_events)} == {
        _TARGET_URI + "/" + name for name in surviving
    }
    assert backend.events[-1][0] == "release"


@pytest.mark.asyncio
async def test_cancelled_sidecar_write_reports_committed_summary(monkeypatch, copy_events):
    backend = _PartialCopyAGFS()
    backend.files[backend.source + "/.abstract.md"] = b"summary"
    fs = _viking_fs(monkeypatch, backend)
    started = threading.Event()
    resume = threading.Event()

    class _SidecarWriter:
        def write(self, path, data, *, ctx=None):
            started.set()
            if not resume.wait(timeout=5):
                raise TimeoutError("test did not release sidecar write")
            backend.files[path] = data
            return path

    monkeypatch.setattr(backend, "write", AsyncAGFSClient(_SidecarWriter()).write)
    task = asyncio.create_task(
        fs.cp(
            "viking://resources/source",
            _TARGET_URI,
            recursive=True,
            ctx=_ctx(),
            maintain_vectors=False,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        facts = _file_facts(copy_events)
        assert [fact["uri"] for fact in facts] == [_TARGET_URI + "/.abstract.md"]
        assert facts[0]["output_role"] == "semantic_sidecar"
        assert backend.files[_TARGET + "/.abstract.md"].endswith(b"summary")
        assert backend.events[-1][0] == "release"
    finally:
        resume.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("cancel_count", [0, 1, 2])
@pytest.mark.parametrize("blocked_name", ["", "/a.md", "/b.md"])
@pytest.mark.parametrize("target_exists", [False, True])
async def test_survivor_check_finishes_reporting_before_cancellation_and_lease_release(
    monkeypatch, copy_events, cancel_count, blocked_name, target_exists
):
    backend = _PartialCopyAGFS(target_exists=target_exists)
    backend.fail_leaf = None
    fs = _viking_fs(monkeypatch, backend)
    started = threading.Event()
    resume = threading.Event()
    finished = threading.Event()
    rollback_failed = False

    class _SurvivorStatClient(_RawCopyClient):
        def stat(self, path, *, ctx=None):
            if rollback_failed and path == _TARGET + blocked_name and not finished.is_set():
                started.set()
                if not resume.wait(timeout=5):
                    raise TimeoutError("test did not release survivor stat")
                finished.set()
            return super().stat(path, ctx=ctx)

    monkeypatch.setattr(backend, "stat", AsyncAGFSClient(_SurvivorStatClient(backend)).stat)
    monkeypatch.setattr(
        fs, "_copy_vector_store_uris", AsyncMock(side_effect=RuntimeError("vector failure"))
    )

    async def failed_cleanup(*args, **kwargs):
        nonlocal rollback_failed
        rollback_failed = True
        raise OSError("rollback failed before deletion")

    monkeypatch.setattr(fs, "_cleanup_transfer_target", failed_cleanup)
    at_release = []
    release = backend.pathlock_release

    async def capture_release(lease):
        at_release.extend(_file_facts(copy_events))
        await release(lease)

    monkeypatch.setattr(backend, "pathlock_release", capture_release)
    task = asyncio.create_task(
        fs.cp("viking://resources/source", _TARGET_URI, recursive=True, ctx=_ctx())
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        for _ in range(cancel_count):
            assert task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        assert not any(event[0] == "release" for event in backend.events)
        resume.set()
        expected_error = asyncio.CancelledError if cancel_count else RuntimeError
        with pytest.raises(expected_error):
            await asyncio.wait_for(task, timeout=5)

        assert finished.is_set()
        first_facts = list(_file_facts(copy_events))
        assert [(fact["uri"], fact["change_type"]) for fact in first_facts] == [
            (_TARGET_URI + "/a.md", "modified" if target_exists else "added"),
            (_TARGET_URI + "/b.md", "added"),
        ]
        assert first_facts == at_release
        assert backend.files[_TARGET + "/a.md"] == b"\x00encrypted-a"
        assert backend.files[_TARGET + "/b.md"] == b"b"
        assert backend.events[-1][0] == "release"

        await fs.cp(
            "viking://resources/source",
            _TARGET_URI,
            recursive=True,
            ctx=_ctx(),
            maintain_vectors=False,
        )
        facts = _file_facts(copy_events)
        assert [(fact["uri"], fact["change_type"]) for fact in facts[2:]] == [
            (_TARGET_URI + "/a.md", "modified"),
            (_TARGET_URI + "/b.md", "modified"),
        ]
        assert sum(fact["is_new_file"] for fact in facts) == (1 if target_exists else 2)
    finally:
        resume.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
