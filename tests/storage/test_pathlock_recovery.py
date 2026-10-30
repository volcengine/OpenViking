# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.errors import (
    HandoffRecoveryBlocked,
    HandoffRecoveryRequired,
    InvalidPathLockRequestError,
    LockAcquisitionError,
)
from openviking.storage.pathlock_recovery import (
    HANDOFF_RECOVERY_MAX_RETRIES,
    adopt_or_reacquire,
    prepare_handoff_retry,
)
from openviking.storage.pathlock_transfer import PathLockGuard

CTX = SimpleNamespace(account_id="tenant")
TREE = {"path": "/local/tenant/resources/tree", "kind": "tree"}
EXACT = {"path": "/local/tenant/resources/file.md", "kind": "exact"}


def client(error=None):
    return SimpleNamespace(
        pathlock_adopt=AsyncMock(side_effect=error, return_value={"lease_ref": "adopted"}),
        pathlock_acquire_batch=AsyncMock(return_value={"lease_ref": "fresh"}),
        pathlock_release=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_migration_preserves_mixed_coverage_and_account():
    agfs = client(HandoffRecoveryRequired("provider_changed"))
    requests = [TREE, EXACT]
    fallback = AsyncMock(return_value=requests)
    result = await adopt_or_reacquire(
        agfs,
        {"owner_id": "old", "covered_paths": requests},
        ctx=CTX,
        fallback_requests=fallback,
    )
    assert result == {"lease_ref": "fresh"}
    agfs.pathlock_acquire_batch.assert_awaited_once_with(requests, fs_ctx={"account_id": "tenant"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        InvalidPathLockRequestError("forged coverage"),
        LockAcquisitionError("already adopted"),
        LockAcquisitionError("invalidated"),
        RuntimeError("Redis disconnected during adopt"),
    ],
)
async def test_unrelated_adopt_errors_never_reacquire(error):
    agfs = client(error)
    with pytest.raises(type(error)) as raised:
        await adopt_or_reacquire(agfs, {"covered_paths": [TREE]}, ctx=CTX)
    assert raised.value is error
    agfs.pathlock_acquire_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_historic_tree_handles_are_inverted_as_a_complete_batch():
    agfs = client(HandoffRecoveryRequired("provider_changed"))
    paths = [TREE["path"], "/local/tenant/resources/other"]
    await adopt_or_reacquire(agfs, {"lock_paths": [p + "/.path.ovlock" for p in paths]}, ctx=CTX)
    agfs.pathlock_acquire_batch.assert_awaited_once_with(
        [{"path": p, "kind": "tree"} for p in paths], fs_ctx={"account_id": "tenant"}
    )


@pytest.mark.asyncio
async def test_historic_mixed_handles_require_complete_operation_fallback():
    agfs = client(HandoffRecoveryRequired("provider_changed"))
    handoff = {
        "lock_paths": [
            TREE["path"] + "/.path.ovlock",
            "/local/tenant/resources/.exact.ovlock.file.md.123",
        ]
    }
    with pytest.raises(InvalidPathLockRequestError):
        await adopt_or_reacquire(agfs, handoff, ctx=CTX)
    agfs.pathlock_acquire_batch.assert_not_awaited()
    await adopt_or_reacquire(
        agfs, handoff, ctx=CTX, fallback_requests=AsyncMock(return_value=[TREE, EXACT])
    )
    agfs.pathlock_acquire_batch.assert_awaited_once_with(
        [TREE, EXACT], fs_ctx={"account_id": "tenant"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "coverage",
    [
        {"path": "/local/other/resources/tree", "kind": "tree"},
        {"path": "/local/tenant/resources/../tree", "kind": "tree"},
        {"path": "/local/tenant/resources/tree", "kind": "unknown"},
        {"path": "/local/tenant/resources/tree", "kind": "exact"},
        {"path": "/local/tenant/resources/.path.ovlock", "kind": "tree"},
        {"path": "/local/tenant/resources/unrelated", "kind": "tree"},
    ],
)
async def test_recovery_rejects_invalid_account_path_kind_or_operation(coverage):
    agfs = client(HandoffRecoveryRequired("provider_changed"))
    with pytest.raises(InvalidPathLockRequestError):
        await adopt_or_reacquire(
            agfs,
            {"covered_paths": [coverage]},
            ctx=CTX,
            fallback_requests=AsyncMock(return_value=[TREE]),
        )
    agfs.pathlock_acquire_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_reacquire_conflict_retains_typed_retry_reason():
    agfs = client(HandoffRecoveryRequired("token_unavailable"))
    agfs.pathlock_acquire_batch.side_effect = LockAcquisitionError("new owner holds tree")
    with pytest.raises(HandoffRecoveryBlocked):
        await adopt_or_reacquire(agfs, {"covered_paths": [TREE]}, ctx=CTX)


@pytest.mark.asyncio
async def test_cancelled_recovery_records_and_releases_fresh_lease():
    started, finish = asyncio.Event(), asyncio.Event()
    agfs = client(HandoffRecoveryRequired("provider_changed"))

    async def acquire(*args, **kwargs):
        started.set()
        await finish.wait()
        return {"lease_ref": "fresh"}

    agfs.pathlock_acquire_batch.side_effect = acquire
    guard = PathLockGuard(agfs)
    task = asyncio.create_task(
        guard.acquire(lambda: adopt_or_reacquire(agfs, {"covered_paths": [TREE]}, ctx=CTX))
    )
    await started.wait()
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert guard.lease == {"lease_ref": "fresh"}
    await guard.close()
    agfs.pathlock_release.assert_awaited_once_with({"lease_ref": "fresh"})


@pytest.mark.asyncio
@pytest.mark.parametrize("processor_name", ["AddResource", "Reindex", "ContentWrite"])
@pytest.mark.parametrize("retry,delay", [(0, 0.5), (2, 2.0), (5, 16.0), (7, 30.0), (8, None)])
async def test_migration_blockage_is_requeued_even_after_previous_retry_limit(
    monkeypatch, processor_name, retry, delay
):
    from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
    from openviking.storage.queuefs.content_write_processor import ContentWriteProcessor
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor

    fs = SimpleNamespace(_async_agfs=SimpleNamespace())
    processors = {
        "AddResource": lambda: AddResourceProcessor(None, "AddResource", fs),
        "Reindex": lambda: ReindexProcessor(fs),
        "ContentWrite": lambda: ContentWriteProcessor(fs),
    }
    manager = SimpleNamespace(REINDEX="Reindex", CONTENT_WRITE="ContentWrite", enqueue=AsyncMock())
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.storage.pathlock_recovery.sleep", sleep)
    msg = SimpleNamespace(
        task_id="task",
        lock_handoff_retry=retry,
        to_dict=lambda: {"task_id": "task", "_task_work_id": "work"},
    )
    requeued = await processors[processor_name]()._requeue_lock_handoff(
        msg, HandoffRecoveryBlocked("busy")
    )
    assert requeued is (delay is not None)
    if delay is None:
        sleep.assert_not_awaited()
        manager.enqueue.assert_not_awaited()
        return
    sleep.assert_awaited_once_with(delay)
    manager.enqueue.assert_awaited_once_with(
        processor_name,
        {"task_id": "task", "_task_work_id": "work", "lock_handoff_retry": retry + 1},
    )


@pytest.mark.asyncio
async def test_content_write_historic_batch_recovers_all_exact_write_targets():
    from openviking.storage.queuefs.content_write_processor import ContentWriteProcessor

    agfs = client(HandoffRecoveryRequired("provider_changed"))
    fs = SimpleNamespace(
        _async_agfs=agfs,
        _uri_to_path=lambda uri, ctx: uri.replace("viking://", f"/local/{ctx.account_id}/"),
    )
    msg = SimpleNamespace(
        refresh={
            "kind": "batch",
            "refresh_kinds": {"viking://resources/a.md": "resource"},
            "resource_plans": [{"plan": {"root_uri": "viking://resources/b.md"}}],
        },
        result={"deleted": ["viking://resources/c.md"]},
        resource_id="viking://resources",
        lock_handoff={"lock_paths": ["/local/tenant/resources/.exact.ovlock.a.md.123"]},
    )
    await ContentWriteProcessor(fs)._load_lock(msg, CTX)
    agfs.pathlock_acquire_batch.assert_awaited_once_with(
        [{"path": f"/local/tenant/resources/{p}.md", "kind": "exact"} for p in "abc"],
        fs_ctx={"account_id": "tenant"},
    )


@pytest.mark.asyncio
async def test_semantic_failed_migration_requeue_does_not_ack_original(monkeypatch):
    from openviking.storage.queuefs.semantic_msg import SemanticMsg
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    monkeypatch.setattr("openviking.storage.pathlock_recovery.sleep", AsyncMock())
    processor = SemanticProcessor(
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=SimpleNamespace()))
    )
    work = SimpleNamespace(reenqueue_after_error=AsyncMock(side_effect=RuntimeError("disk full")))
    processor._cleanup_local_artifact = AsyncMock()
    with pytest.raises(RuntimeError, match="disk full"):
        await processor._requeue_semantic_msg_after_error(
            SemanticMsg(uri="viking://resources/tree", context_type="resource"),
            HandoffRecoveryBlocked("busy"),
            work=work,
        )
    processor._cleanup_local_artifact.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ctx", [None, SimpleNamespace(account_id=""), SimpleNamespace(account_id="../other")]
)
async def test_recovery_requires_valid_account_context(ctx):
    agfs = client(HandoffRecoveryRequired("provider_changed"))
    with pytest.raises(InvalidPathLockRequestError, match="account context"):
        await adopt_or_reacquire(agfs, {"covered_paths": [TREE]}, ctx=ctx)
    agfs.pathlock_adopt.assert_not_awaited()
    agfs.pathlock_acquire_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_backoff_keeps_original_message_unchanged(monkeypatch):
    started = asyncio.Event()

    async def wait(delay):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("openviking.storage.pathlock_recovery.sleep", wait)
    msg = SimpleNamespace(task_id="task", lock_handoff_retry=2, to_dict=lambda: {})
    retry = asyncio.create_task(
        prepare_handoff_retry(msg, HandoffRecoveryBlocked("busy"), queue="Reindex")
    )
    await started.wait()
    retry.cancel()
    with pytest.raises(asyncio.CancelledError):
        await retry
    assert msg.lock_handoff_retry == 2


@pytest.mark.asyncio
async def test_semantic_recovery_exhaustion_settles_failure_without_requeue():
    from openviking.storage.queuefs.process_result import ProcessOutcome
    from openviking.storage.queuefs.semantic_msg import SemanticMsg
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    processor = SemanticProcessor(vlm_resolver=SimpleNamespace(get_vlm=AsyncMock()))
    processor._cleanup_local_artifact = AsyncMock()
    work = SimpleNamespace(reenqueue_after_error=AsyncMock())
    msg = SemanticMsg(
        uri="viking://resources/tree",
        context_type="resource",
        lock_handoff_retry=HANDOFF_RECOVERY_MAX_RETRIES,
    )
    restored = SemanticMsg.from_dict(msg.to_dict())
    assert restored.lock_handoff_retry == HANDOFF_RECOVERY_MAX_RETRIES
    result = await processor._requeue_semantic_msg_after_error(
        restored, HandoffRecoveryBlocked("busy"), work=work
    )
    assert result.outcome is ProcessOutcome.FAILED
    assert "retry limit" in result.error
    work.reenqueue_after_error.assert_not_awaited()
    processor._cleanup_local_artifact.assert_awaited_once_with(restored)
