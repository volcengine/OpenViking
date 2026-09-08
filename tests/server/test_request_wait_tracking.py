# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for request-scoped wait behavior on write APIs."""

import asyncio

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.telemetry.context import bind_telemetry
from openviking.telemetry.operation import OperationTelemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker


class _FakeRequestWaitTracker:
    def __init__(self, queue_status):
        self.queue_status = queue_status
        self.registered_requests = []
        self.wait_calls = []
        self.build_calls = []
        self.cleaned = []

    def register_request(self, telemetry_id: str) -> None:
        self.registered_requests.append(telemetry_id)

    async def wait_for_request(self, telemetry_id: str, timeout, poll_interval=None):
        del poll_interval
        self.wait_calls.append((telemetry_id, timeout))

    def build_queue_status(self, telemetry_id: str):
        self.build_calls.append(telemetry_id)
        return self.queue_status

    def cleanup(self, telemetry_id: str) -> None:
        self.cleaned.append(telemetry_id)


class _ExplodingQueueManager:
    async def wait_complete(self, *args, **kwargs):
        raise AssertionError("global queue wait should not be used")


@pytest.mark.asyncio
async def test_add_skill_wait_uses_request_tracker(service, monkeypatch):
    tracker = _FakeRequestWaitTracker(
        {
            "Semantic": {"processed": 0, "error_count": 0, "errors": []},
            "Embedding": {"processed": 1, "error_count": 0, "errors": []},
        }
    )
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    telemetry = OperationTelemetry(operation="resources.add_skill", enabled=True)

    async def _fake_process_skill(**kwargs):
        del kwargs
        return {"status": "success", "uri": "viking://user/default/skills/demo", "name": "demo"}

    monkeypatch.setattr(service.resources._skill_processor, "process_skill", _fake_process_skill)
    monkeypatch.setattr(
        "openviking.service.resource_service.get_queue_manager",
        lambda: _ExplodingQueueManager(),
    )
    monkeypatch.setattr(
        "openviking.service.resource_service.get_request_wait_tracker",
        lambda: tracker,
        raising=False,
    )

    with bind_telemetry(telemetry):
        result = await service.resources.add_skill(
            data={"name": "demo", "content": "# Demo"},
            ctx=ctx,
            wait=True,
            timeout=9.0,
        )

    assert result["queue_status"] == tracker.queue_status
    assert tracker.registered_requests == [telemetry.telemetry_id]
    assert tracker.wait_calls == [(telemetry.telemetry_id, 9.0)]
    assert tracker.build_calls == [telemetry.telemetry_id]
    assert tracker.cleaned == [telemetry.telemetry_id]


@pytest.mark.asyncio
async def test_add_skill_wait_uses_request_tracker_when_telemetry_disabled(service, monkeypatch):
    tracker = _FakeRequestWaitTracker(
        {
            "Semantic": {"processed": 0, "error_count": 0, "errors": []},
            "Embedding": {"processed": 1, "error_count": 0, "errors": []},
        }
    )
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    telemetry = OperationTelemetry(operation="resources.add_skill", enabled=False)

    async def _fake_process_skill(**kwargs):
        del kwargs
        return {"status": "success", "uri": "viking://user/default/skills/demo", "name": "demo"}

    monkeypatch.setattr(service.resources._skill_processor, "process_skill", _fake_process_skill)
    monkeypatch.setattr(
        "openviking.service.resource_service.get_queue_manager",
        lambda: _ExplodingQueueManager(),
    )
    monkeypatch.setattr(
        "openviking.service.resource_service.get_request_wait_tracker",
        lambda: tracker,
        raising=False,
    )

    with bind_telemetry(telemetry):
        result = await service.resources.add_skill(
            data={"name": "demo", "content": "# Demo"},
            ctx=ctx,
            wait=True,
            timeout=9.0,
        )

    assert result["root_uri"] == "viking://user/default/skills/demo"
    assert result["queue_status"] == tracker.queue_status
    assert tracker.registered_requests == [telemetry.telemetry_id]
    assert tracker.wait_calls == [(telemetry.telemetry_id, 9.0)]
    assert tracker.build_calls == [telemetry.telemetry_id]
    assert tracker.cleaned == [telemetry.telemetry_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("telemetry_enabled", [False, True])
@pytest.mark.parametrize("embedding_fails", [False, True])
async def test_content_write_wait_uses_request_tracker(
    service, monkeypatch, telemetry_enabled, embedding_fails
):
    """wait=True includes downstream indexing even without a registered collector."""
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=service.user, role=Role.USER)
    await service.viking_fs.mkdir(root_uri, ctx=ctx)
    telemetry = OperationTelemetry(operation="content.write", enabled=telemetry_enabled)
    tracker = get_request_wait_tracker()
    embedding_started = asyncio.Event()
    release_embedding = asyncio.Event()
    semantic_finished = asyncio.Event()
    materialize = service.viking_fs.acl_manager.materialize_context_records
    mark_semantic_done = tracker.mark_semantic_done

    async def delayed_materialize(records, ctx):
        if any(record.get("uri") == file_uri for record in records):
            embedding_started.set()
            await release_embedding.wait()
            if embedding_fails:
                raise RuntimeError("test index write failed")
        return await materialize(records, ctx)

    def record_semantic_done(telemetry_id, root_id, processed_delta=1):
        mark_semantic_done(telemetry_id, root_id, processed_delta)
        if telemetry_id == telemetry.telemetry_id:
            semantic_finished.set()

    monkeypatch.setattr(
        service.viking_fs.acl_manager, "materialize_context_records", delayed_materialize
    )
    monkeypatch.setattr(tracker, "mark_semantic_done", record_semantic_done)

    with bind_telemetry(telemetry):
        write_task = asyncio.create_task(
            service.fs.write(
                uri=file_uri,
                content="Request-scoped indexing wait",
                mode="create",
                ctx=ctx,
                wait=True,
                timeout=10.0,
            )
        )

    try:
        await asyncio.wait_for(
            asyncio.gather(embedding_started.wait(), semantic_finished.wait()), timeout=10.0
        )
        assert not tracker.is_complete(telemetry.telemetry_id)
        assert not write_task.done()
        release_embedding.set()
        result = await asyncio.wait_for(write_task, timeout=10.0)
    finally:
        release_embedding.set()
        if not write_task.done():
            await asyncio.wait_for(write_task, timeout=10.0)

    assert result["semantic_status"] == "complete"
    assert result["vector_status"] == ("failed" if embedding_fails else "complete")
    assert result["queue_status"]["Embedding"]["error_count"] == int(embedding_fails)
    assert result["queue_status"]["Embedding"]["processed"] >= (2 if embedding_fails else 3)
