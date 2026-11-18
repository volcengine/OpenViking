# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Native transfers and centralized durable recovery must remain distinct."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.storage.errors import HandoffRecoveryBlocked, HandoffRecoveryRequired
from openviking.storage.pathlock_recovery import adopt_or_reacquire, prepare_handoff_retry
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig

CTX = SimpleNamespace(account_id="handoff-test")
PATH = "/local/handoff-test/resources/input.md"
REQUESTS = [{"path": PATH, "kind": "tree"}]


@pytest.fixture(params=["filesystem", "sqlite"])
def agfs(tmp_path, request):
    return AsyncAGFSClient(
        create_agfs_client(
            RagfsBindingConfig(
                agfs=AGFSConfig(
                    path=str(tmp_path), backend="local", pathlock={"provider": request.param}
                )
            )
        )
    )


@pytest.mark.asyncio
async def test_live_handoff_adopts_without_explicit_account_and_reclaims_failed_enqueue(agfs):
    guard = PathLockGuard(agfs)
    await guard.acquire(lambda: agfs.pathlock_acquire_tree(PATH))
    handoff = await guard.to_handoff()

    async def fail():
        raise OSError("enqueue failed")

    try:
        with pytest.raises(OSError, match="enqueue failed"):
            await guard.handoff_and_dispatch(handoff, fail)
        assert guard.lease is not None
        assert guard.lease["owner_id"] == handoff["owner_id"]
        assert guard.lease["lease_ref"] != handoff["lease_ref"]
    finally:
        await guard.close()
    assert not await agfs.pathlock_is_locked(PATH)


@pytest.mark.asyncio
async def test_missing_source_requires_recovery_preserving_outer_owner(agfs):
    outer = await agfs.pathlock_acquire_tree(PATH)
    source = await agfs.pathlock_acquire_tree(PATH, owner_lease_ref=outer)
    handoff = await agfs.pathlock_to_handoff(source)
    await agfs.pathlock_release(source)
    recovered = None
    try:
        with pytest.raises(HandoffRecoveryRequired) as raised:
            await agfs.pathlock_adopt(handoff)
        assert raised.value.reason == "token_unavailable"
        recovered = await adopt_or_reacquire(agfs, handoff, ctx=CTX)
        assert recovered["owner_id"] == outer["owner_id"]
        assert recovered["lease_ref"] not in {outer["lease_ref"], source["lease_ref"]}
        assert recovered["ownership_ref"] != source["ownership_ref"]
        assert await agfs.pathlock_refresh(outer) == "refreshed"
        await agfs.pathlock_release(recovered)
        recovered = None
        assert await agfs.pathlock_is_locked(PATH)
    finally:
        if recovered is not None:
            await agfs.pathlock_release(recovered)
        await agfs.pathlock_release(outer)
    assert not await agfs.pathlock_is_locked(PATH)


@pytest.mark.asyncio
async def test_replay_goes_through_helper_and_creates_independent_holder(agfs):
    source = await agfs.pathlock_acquire_tree(PATH)
    handoff = await agfs.pathlock_to_handoff(source)
    await agfs.pathlock_handoff(source)
    adopted = await agfs.pathlock_adopt(handoff)
    adopted_released = False
    replay = None
    try:
        with pytest.raises(HandoffRecoveryRequired):
            await agfs.pathlock_adopt(handoff)
        replay = await adopt_or_reacquire(agfs, handoff, ctx=CTX)
        assert replay["owner_id"] == adopted["owner_id"]
        assert replay["lease_ref"] != adopted["lease_ref"]
        await agfs.pathlock_release(adopted)
        adopted_released = True
        assert await agfs.pathlock_refresh(replay) == "refreshed"
    finally:
        if replay is not None:
            await agfs.pathlock_release(replay)
        if not adopted_released:
            await agfs.pathlock_release(adopted)
    assert not await agfs.pathlock_is_locked(PATH)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", ["holder", "legacy_logical", "legacy_filesystem", "legacy_tree"]
)
async def test_all_recovery_contention_uses_persisted_bounded_retries(agfs, payload, monkeypatch):
    handoff = {
        "owner_id": "historic-owner",
        "covered_paths": REQUESTS,
        "lock_paths": [PATH],
        "handle_mode": "logical_path",
    }
    if payload == "holder":
        handoff.update(holder_id="missing-source", lease_ref="missing-source")
    elif payload.startswith("legacy_") and payload != "legacy_logical":
        handoff.update(lock_paths=[PATH + "/.path.ovlock"], handle_mode="lock_path")
    if payload == "legacy_tree":
        handoff.pop("covered_paths")
        handoff.pop("handle_mode")
    foreign = await agfs.pathlock_acquire_tree(PATH)
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.storage.pathlock_recovery.sleep", sleep)
    msg = SemanticMsg(
        uri="viking://resources/input.md",
        context_type="resource",
        account_id=CTX.account_id,
        lock_handoff=handoff,
    )
    work = SimpleNamespace(reenqueue_after_error=AsyncMock())
    processor = SemanticProcessor()
    try:
        for retry in range(3):
            with pytest.raises(HandoffRecoveryBlocked) as raised:
                await adopt_or_reacquire(agfs, handoff, ctx=CTX)
            await processor._requeue_semantic_msg_after_error(msg, raised.value, work=work)
            assert msg.lock_handoff_retry == retry + 1
        msg.lock_handoff_retry = 8
        assert await prepare_handoff_retry(msg, raised.value, queue="Reindex") is None
        assert sleep.await_count == 3
        assert work.reenqueue_after_error.await_count == 3
    finally:
        await agfs.pathlock_release(foreign)


@pytest.mark.asyncio
async def test_cancelled_native_reacquisition_releases_fresh_holder(agfs):
    foreign = await agfs.pathlock_acquire_tree(PATH)
    task = asyncio.create_task(
        agfs.pathlock_reacquire_batch(REQUESTS, "durable-owner", timeout_secs=2)
    )
    foreign_released = False
    try:
        async with asyncio.timeout(2):
            while not (await agfs.pathlock_observe())["waiting_locks"]:
                await asyncio.sleep(0.01)
        task.cancel()
        await agfs.pathlock_release(foreign)
        foreign_released = True
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await agfs.pathlock_is_locked(PATH)
    finally:
        if not foreign_released:
            await agfs.pathlock_release(foreign)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
