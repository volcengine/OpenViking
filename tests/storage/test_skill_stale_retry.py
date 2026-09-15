# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Superseded Skill refresh retries must relinquish their transferred lock."""

from uuid import uuid4

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.semantic_msg import SemanticMsg, build_semantic_coalesce_key
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.semantic_queue import is_semantic_msg_stale
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking_cli.session.user_id import UserIdentifier
from tests.server.conftest import service as service
from tests.server.conftest import temp_dir as temp_dir


async def test_stale_skill_retry_releases_lock_and_allows_latest_refresh(service, monkeypatch):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    root = "viking://agent/skills/stale-retry/references"
    fs = service.viking_fs
    await fs.mkdir(root, ctx=ctx)
    # File removal/copy can refresh a Skill's auxiliary directory, while the
    # package root itself is excluded from this automatic refresh path.
    assert service.fs._semantic_refresh_parent_uri(f"{root}/old.md", "skill") == root
    queue_manager = get_queue_manager()
    queue = queue_manager.get_queue(queue_manager.SEMANTIC)
    persisted = []

    async def capture_persistence(self, data):
        persisted.append(data)
        return str(len(persisted))

    # Keep messages pending for deterministic delivery; use the real enqueue
    # coalescing and native storage lock/handoff implementations.
    monkeypatch.setattr(NamedQueue, "enqueue", capture_persistence)
    common = {
        "uri": root,
        "context_type": "skill",
        "account_id": ctx.account_id,
        "user_id": ctx.user.user_id,
        "peer_id": ctx.user.user_id,
        "role": str(ctx.role),
        "generation_trigger": "content_delete",
        "coalesce_key": build_semantic_coalesce_key(
            context_type="skill",
            uri=root,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            peer_id=ctx.user.user_id,
        ),
        "skip_vectorization": True,
    }
    retry = SemanticMsg(**common, telemetry_id=str(uuid4()))
    latest = SemanticMsg(**common, telemetry_id=str(uuid4()))
    tracker = get_request_wait_tracker()
    for msg in (retry, latest):
        tracker.register_request(msg.telemetry_id)
        tracker.register_semantic_root(msg.telemetry_id, msg.id)
    processor = SemanticProcessor()
    scope = None
    try:
        await queue.enqueue(retry)
        scope = await processor._resolve_skill_semantic_lock(retry, ctx, None)
        await processor._enqueue_skill_retry(queue, retry, scope)
        assert retry.lock_handoff is not None and not scope._owned
        await queue.enqueue(latest)
        assert is_semantic_msg_stale(retry)
        assert not is_semantic_msg_stale(latest)

        await processor.on_dequeue(retry.to_dict())
        assert tracker.is_complete(retry.telemetry_id)
        assert tracker.build_queue_status(retry.telemetry_id)["Semantic"]["processed"] == 1
        lease = await fs._async_agfs.pathlock_acquire_tree(
            fs._uri_to_path(root, ctx=ctx), timeout_secs=0.01
        )
        await fs._async_agfs.pathlock_release(lease)

        await processor.on_dequeue(latest.to_dict())
        assert tracker.is_complete(latest.telemetry_id)
        assert tracker.build_queue_status(latest.telemetry_id)["Semantic"]["processed"] == 1
        assert await fs.exists(f"{root}/.overview.md", ctx=ctx)
        assert len(persisted) == 3, "The latest refresh must complete without another retry"
        lease = await fs._async_agfs.pathlock_acquire_tree(
            fs._uri_to_path(root, ctx=ctx), timeout_secs=0.01
        )
        await fs._async_agfs.pathlock_release(lease)
    finally:
        if scope is not None:
            await scope.close()
        await processor._release_cancelled_semantic_lock(retry)
        tracker.cleanup(retry.telemetry_id)
        tracker.cleanup(latest.telemetry_id)
