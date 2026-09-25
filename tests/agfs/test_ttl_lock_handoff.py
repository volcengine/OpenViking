# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise TTL producer/consumer handoffs with the real native path locks."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.pyagfs import AsyncAGFSClient
from openviking.server.identity import RequestContext, Role
from openviking.session.session import Session
from openviking.session.ttl_fence import StaleSessionGenerationError, session_generation_fence
from openviking.storage.abstract_overview import semantic_body_digest
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.agfs_config import AGFSConfig


@pytest.fixture
async def source_fs(tmp_path):
    client = create_agfs_client(
        RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
    )
    agfs = AsyncAGFSClient(client)
    await agfs.mkdir("/local/default")
    await agfs.mkdir("/local/default/source")
    fs = SimpleNamespace(
        _async_agfs=agfs,
        _uri_to_path=lambda uri, ctx=None: "/local/default/source",
        read_file=AsyncMock(),
    )
    try:
        yield fs
    finally:
        client.close()


async def _handoff(fs, operation, *, before_release=lambda: None):
    producer = await fs._async_agfs.pathlock_acquire_exact(fs._uri_to_path(""))
    consumer = asyncio.create_task(operation())
    try:
        # A zero-timeout consumer fails while the producer still owns the lock.
        # A waiting consumer must not validate or write before that lock drops.
        await asyncio.sleep(0.1)
        assert not consumer.done()
        fs.read_file.assert_not_awaited()
        before_release()
    finally:
        await fs._async_agfs.pathlock_release(producer)
        try:
            result = await asyncio.wait_for(consumer, timeout=5)
        finally:
            if not consumer.done():
                consumer.cancel()
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", ["generation-1", "generation-2"])
async def test_session_fence_waits_and_rechecks_after_producer(source_fs, generation):
    fs = source_fs
    fence = session_generation_fence(
        fs, object(), session_uri="viking://user/default/sessions/s1", generation="generation-1"
    )
    writes = []

    async def consume():
        async with fence.lock():
            writes.append("written")

    def publish():
        fs.read_file.return_value = json.dumps(
            {"ttl_generation": generation, "expires_at": "2999-01-01T00:00:00.000Z"}
        )

    if generation == "generation-1":
        await _handoff(fs, consume, before_release=publish)
        assert writes == ["written"]
    else:
        with pytest.raises(StaleSessionGenerationError):
            await _handoff(fs, consume, before_release=publish)
        assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source_state", ["live", "recreated", "expired"])
async def test_event_embedding_waits_and_revalidates_source(source_fs, monkeypatch, source_state):
    fs = source_fs
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    handler = object.__new__(TextEmbeddingHandler)
    message = EmbeddingMsg(
        "body",
        {"uri": "viking://user/default/memories/events/e.md", "ttl_generation": "g1"},
    )
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    write = AsyncMock(return_value="vector-id")

    def publish():
        fields = {
            "ttl_generation": "g2" if source_state == "recreated" else "g1",
            "expires_at": (
                "2000-01-01T00:00:00.000Z"
                if source_state == "expired"
                else "2999-01-01T00:00:00.000Z"
            ),
        }
        fs.read_file.return_value = "<!-- MEMORY_FIELDS " + json.dumps(fields) + " -->\nbody"

    result = await _handoff(
        fs,
        lambda: handler._write_ttl_vector_if_current(message, ctx, write),
        before_release=publish,
    )
    assert result == ("vector-id" if source_state == "live" else None)
    assert write.await_count == int(source_state == "live")


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["original", "updated"])
async def test_summary_embedding_waits_and_rechecks_digest(source_fs, monkeypatch, body):
    fs = source_fs
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    handler = object.__new__(TextEmbeddingHandler)
    write = AsyncMock(return_value="vector-id")
    result = await _handoff(
        fs,
        lambda: handler._write_directory_vector_if_current(
            "viking://user/default/memories/events/.abstract.md",
            semantic_body_digest("original"),
            object(),
            write,
        ),
        before_release=lambda: setattr(fs.read_file, "return_value", body),
    )
    assert result == ("vector-id" if body == "original" else None)
    assert write.await_count == int(body == "original")


@pytest.mark.asyncio
async def test_phase2_waits_for_phase1_before_reconciliation(source_fs, monkeypatch):
    fs = source_fs
    session_uri = "viking://user/default/sessions/s1"
    archive_uri = f"{session_uri}/history/archive_001"
    files = {}

    async def read(uri, **kwargs):
        if uri not in files:
            raise FileNotFoundError(uri)
        return files[uri]

    fs.read_file.side_effect = read
    session = Session(viking_fs=fs, session_id="s1", session_uri=session_uri)
    tracker = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(status=None)))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(session, "_ensure_phase1_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(session, "_can_run_archive", AsyncMock(return_value=True))
    extraction = AsyncMock()
    monkeypatch.setattr(session, "_run_memory_extraction", extraction)
    msg = SessionCommitMsg(
        task_id="phase2-task",
        session_id="s1",
        session_uri=session_uri,
        archive_uri=archive_uri,
        user={"account_id": "default", "user_id": "default"},
        ttl_generation="g1",
    )
    producer = await fs._async_agfs.pathlock_acquire_exact(fs._uri_to_path(""))
    consumer = asyncio.create_task(session.resume_queued_commit(msg))
    try:
        await asyncio.sleep(0.1)
        assert not consumer.done()
        extraction.assert_not_awaited()
        files[f"{session_uri}/.meta.json"] = json.dumps(
            {
                "session_id": "s1",
                "ttl_days": 1,
                "ttl_generation": "g1",
                "received_at": "2998-12-31T00:00:00.000Z",
                "expires_at": "2999-01-01T00:00:00.000Z",
            }
        )
        files[f"{archive_uri}/.meta.json"] = "{}"
        files[f"{archive_uri}/messages.jsonl"] = Message(
            id="m1", role="user", parts=[TextPart("archived message")]
        ).to_jsonl()
    finally:
        await fs._async_agfs.pathlock_release(producer)
    assert await asyncio.wait_for(consumer, timeout=5) is True
    extraction.assert_awaited_once()
