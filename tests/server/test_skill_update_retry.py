# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Skill retries preserve the lease shared with the waiting update request."""

import asyncio
import threading

import pytest

from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
from tests.server.test_api_skills import _add_skill, _skill_md
from tests.server.test_api_skills import _stub_mcp_endpoint as _stub_mcp_endpoint
from tests.server.test_skill_update_cancellation import _download, _wait_until
from tests.server.test_skill_update_lock import _assert_locked, _ctx


def _fail_first_run(monkeypatch, root, *, block_retries=False):
    original = SemanticTreeExecutor.run
    attempts = []

    async def fail_once(self, root_uri):
        if root_uri == root:
            attempts.append(root_uri)
            if len(attempts) == 1:
                raise RuntimeError("one transient failure after adopting the package lock")
            if block_retries:
                await asyncio.Event().wait()
        return await original(self, root_uri)

    monkeypatch.setattr(SemanticTreeExecutor, "run", fail_once)
    return attempts


async def _assert_unlocked(fs, root):
    lease = await fs._async_agfs.pathlock_acquire_tree(
        fs._uri_to_path(root, ctx=_ctx()), timeout_secs=0.01
    )
    await fs._async_agfs.pathlock_release(lease)


async def test_update_wait_true_retries_transient_failure_without_conflicting_with_its_lock(
    client, service, monkeypatch
):
    name = "update-transient-retry"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    attempts = _fail_first_run(monkeypatch, root)

    response = await client.put(
        f"/api/v1/skills/{name}",
        json={"data": _skill_md(name, "Replacement"), "wait": True, "timeout": 5},
    )

    assert response.status_code == 200, response.text
    assert len(attempts) == 2
    assert b"Replacement" in await _download(client, f"{root}/SKILL.md")
    await _assert_unlocked(service.viking_fs, root)


@pytest.mark.parametrize("enqueue_committed", [False, True])
async def test_update_timeout_waits_for_retry_handoff_to_settle_before_restoring(
    client, service, monkeypatch, enqueue_committed
):
    name = f"update-cancel-retry-enqueue-{enqueue_committed}"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    old_content = await _download(client, f"{root}/SKILL.md")
    # A published retry may already be running while its enqueue is returning.
    # Keep that work pending so this case actually exercises a request timeout.
    _fail_first_run(monkeypatch, root, block_retries=enqueue_committed)
    queue_manager = get_queue_manager()
    queue = queue_manager.get_queue(queue_manager.SEMANTIC)
    original_enqueue = queue.enqueue
    retry_started = threading.Event()
    resume_enqueue = threading.Event()
    enqueues = 0

    def pause():
        retry_started.set()
        assert resume_enqueue.wait(5)

    async def slow_retry_enqueue(msg):
        nonlocal enqueues
        if msg.uri == root:
            enqueues += 1
            if enqueues == 2:
                if enqueue_committed:
                    result = await original_enqueue(msg)
                    await asyncio.to_thread(pause)
                    return result
                await asyncio.to_thread(pause)
        return await original_enqueue(msg)

    monkeypatch.setattr(queue, "enqueue", slow_retry_enqueue)
    updating = asyncio.create_task(
        client.put(
            f"/api/v1/skills/{name}",
            json={"data": _skill_md(name, "Replacement"), "wait": True, "timeout": 2},
        )
    )
    try:
        await _wait_until(retry_started.is_set)
        await asyncio.sleep(2.1)
        assert not updating.done(), "Rollback must wait for the started retry enqueue"
        await _assert_locked(service.viking_fs, root)
        resume_enqueue.set()
        response = await asyncio.wait_for(updating, 10)
        assert response.status_code == 504, response.text
        assert await _download(client, f"{root}/SKILL.md") == old_content
        await _assert_unlocked(service.viking_fs, root)
    finally:
        resume_enqueue.set()
        if not updating.done():
            await asyncio.wait_for(updating, 10)
