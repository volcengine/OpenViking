# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.queuefs.semantic_dag import DagStats
from openviking.storage.queuefs.semantic_lock import SemanticLockScope
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.transaction import BorrowedLockLease, LockHandoffRef


class _FakeHandle:
    def __init__(self, handle_id: str):
        self.id = handle_id
        self.locks = ["/fake/root/.path.ovlock"]


class _FakeLockManager:
    def __init__(self):
        self._handles = {"lock-1": _FakeHandle("lock-1")}
        self.release_calls = []

    def get_handle(self, handle_id: str):
        return self._handles.get(handle_id)

    async def release(self, handle):
        self.release_calls.append(handle.id)
        self._handles.pop(handle.id, None)

    def create_handle(self):
        handle = _FakeHandle("new-lock")
        self._handles[handle.id] = handle
        return handle

    async def acquire_tree(self, handle, lock_path):
        del handle, lock_path
        return True


class _RecoveringLockManager:
    def __init__(self):
        self._handles = {}
        self.acquire_tree_calls = []
        self.release_calls = []

    async def get_handle_async(self, handle_id):
        return self._handles.get(handle_id)

    async def adopt_handle_async(self, handle_id, lock_paths):
        del handle_id, lock_paths
        return None

    def get_handle(self, handle_id):
        return self._handles.get(handle_id)

    def create_handle(self):
        handle = _FakeHandle("new-lock")
        handle.locks = []
        self._handles[handle.id] = handle
        return handle

    async def acquire_tree(self, handle, lock_path):
        self.acquire_tree_calls.append(lock_path)
        handle.locks.append(f"{lock_path}/.path.ovlock")
        return True

    async def release(self, handle):
        self.release_calls.append(handle.id)
        self._handles.pop(handle.id, None)


class _FakeVikingFS:
    async def exists(self, uri, ctx=None):
        del uri, ctx
        return False

    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return f"/fake/{uri.replace('://', '/').strip('/')}"


@pytest.mark.asyncio
async def test_semantic_processor_borrows_caller_owned_lock(monkeypatch):
    processor = SemanticProcessor()
    lock_manager = _FakeLockManager()

    class _FakeDagExecutor:
        def __init__(self, **kwargs):
            self.lock = kwargs["lock"]
            self.stale = False

        async def run(self, root_uri):
            assert root_uri == "viking://resources/demo"
            assert self.lock.handle_id == "lock-1"

        def get_stats(self):
            return DagStats()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        lambda **kwargs: _FakeDagExecutor(**kwargs),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: lock_manager,
    )

    await processor.on_dequeue(
        SemanticMsg(
            uri="viking://resources/demo",
            context_type="resource",
            recursive=False,
        ).to_dict(),
        lock=BorrowedLockLease.from_handle(lock_manager, lock_manager.get_handle("lock-1")),
    )

    assert lock_manager.release_calls == []


@pytest.mark.asyncio
async def test_semantic_processor_recovers_stale_non_tree_handoff(monkeypatch):
    processor = SemanticProcessor()
    lock_manager = _RecoveringLockManager()

    class _FakeDagExecutor:
        def __init__(self, **kwargs):
            self.lock = kwargs["lock"]
            self.stale = False

        async def run(self, root_uri):
            assert root_uri == "viking://resources/demo"
            assert self.lock.handle_id == "new-lock"
            await self.lock.close()

        def get_stats(self):
            return DagStats()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        lambda **kwargs: _FakeDagExecutor(**kwargs),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_lock.get_lock_manager",
        lambda: lock_manager,
    )

    await processor.on_dequeue(
        SemanticMsg(
            uri="viking://resources/demo",
            context_type="resource",
            recursive=False,
            lock_handoff=LockHandoffRef(
                handle_id="stale-lock",
                lock_paths=("/fake/viking/resources/.mw_exact_demo.deadbeef",),
            ),
        ).to_dict()
    )

    assert lock_manager.acquire_tree_calls == ["/fake/viking/resources/demo"]
    assert lock_manager.release_calls == ["new-lock"]


@pytest.mark.asyncio
async def test_semantic_lock_scope_reacquires_tree_lock_when_handoff_handle_is_stale(
    monkeypatch,
):
    lock_manager = _RecoveringLockManager()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_lock.get_lock_manager",
        lambda: lock_manager,
    )

    scope = await SemanticLockScope.resolve(
        LockHandoffRef(
            handle_id="stale-lock",
            lock_paths=("/local/default/resources/CONTRIBUTING_CN_3/.path.ovlock",),
        ),
        fallback_path_factory=lambda: (_ for _ in ()).throw(
            AssertionError("tree handoffs must not evaluate the fallback path")
        ),
    )

    try:
        assert scope.lock.handle_id == "new-lock"
        assert lock_manager.acquire_tree_calls == ["/local/default/resources/CONTRIBUTING_CN_3"]
    finally:
        await scope.close()

    assert lock_manager.release_calls == ["new-lock"]


@pytest.mark.asyncio
async def test_semantic_lock_scope_reacquires_fallback_path_for_stale_non_tree_handoff(
    monkeypatch,
):
    lock_manager = _RecoveringLockManager()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_lock.get_lock_manager",
        lambda: lock_manager,
    )

    scope = await SemanticLockScope.resolve(
        LockHandoffRef(
            handle_id="stale-lock",
            lock_paths=("/local/default/resources/.mw_exact_demo.deadbeef",),
        ),
        fallback_path_factory=lambda: "/local/default/resources/demo",
    )

    try:
        assert scope.lock.handle_id == "new-lock"
        assert lock_manager.acquire_tree_calls == ["/local/default/resources/demo"]
    finally:
        await scope.close()

    assert lock_manager.release_calls == ["new-lock"]


@pytest.mark.asyncio
async def test_semantic_processor_lock_error_requeues_without_circuit_breaker(monkeypatch):
    processor = SemanticProcessor()
    reenqueue_calls = []
    success_called = False
    requeue_called = False
    error_called = False

    async def _reenqueue(msg):
        reenqueue_calls.append(msg.uri)

    def on_success():
        nonlocal success_called
        success_called = True

    def on_requeue():
        nonlocal requeue_called
        requeue_called = True

    def on_error(error_msg, error_data=None):
        del error_msg, error_data
        nonlocal error_called
        error_called = True

    def _record_failure(error):
        raise AssertionError(f"lock errors must not trip circuit breaker: {error}")

    processor.set_callbacks(on_success, on_requeue, on_error)
    processor._circuit_breaker.record_failure = _record_failure

    monkeypatch.setattr(processor, "_reenqueue_semantic_msg", _reenqueue)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LockAcquisitionError("lock handle is no longer active")
        ),
    )

    msg = SemanticMsg(
        uri="viking://resources/CONTRIBUTING_CN_3",
        context_type="resource",
        recursive=True,
        lock_handoff=LockHandoffRef(
            handle_id="stale-lock",
            lock_paths=("/local/default/resources/CONTRIBUTING_CN_3/.path.ovlock",),
        ),
    )

    await processor.on_dequeue(msg.to_dict())

    assert reenqueue_calls == ["viking://resources/CONTRIBUTING_CN_3"]
    assert requeue_called is True
    assert success_called is True
    assert error_called is False
