# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openviking.core.namespace import canonical_user_root
from openviking.message import TextPart, ToolPart
from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server import dependencies
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import get_task_tracker, set_task_tracker
from openviking.storage import viking_fs as viking_fs_module
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture
def embedding_inputs():
    return []


@pytest_asyncio.fixture
async def archive_service(test_data_dir, request_context, monkeypatch, embedding_inputs):
    class FakeEmbedder(DenseEmbedderBase):
        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            if not is_query:
                embedding_inputs.append(text)
            return EmbedResult(dense_vector=[0.1] * 1024)

        def get_dimension(self) -> int:
            return 1024

    monkeypatch.setattr(
        EmbeddingConfig, "get_embedder", lambda self: FakeEmbedder(model_name="test-embedder")
    )
    previous_fs = viking_fs_module._instance
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "enable_watch_scheduler": False,
            "storage": {"workspace": str(test_data_dir)},
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "test-embedder",
                    "api_key": "test-key",
                    "dimension": 1024,
                }
            },
        }
    )
    service = OpenVikingService(path=str(test_data_dir), user=request_context.user)
    try:
        await service.initialize()
        yield service
    finally:
        await service.close()
        set_task_tracker(None)
        viking_fs_module._instance = previous_fs
        OpenVikingConfigSingleton.reset_instance()


async def _commit(session):
    receipt = await session.commit_async()
    tracker = get_task_tracker()
    for _ in range(300):
        task = await tracker.get(receipt["task_id"])
        if task and task.status.value in {"completed", "failed"}:
            assert task.status.value == "completed", task.to_dict()
            return receipt["archive_uri"]
        await asyncio.sleep(0.05)
    pytest.fail(f"Commit did not complete: {receipt['task_id']}")


@pytest.mark.parametrize("delete_session", [True, False])
async def test_archive_sources_are_searchable_without_extraction(
    archive_service, request_context, monkeypatch, delete_session
):
    service = archive_service
    cfg = get_openviking_config().memory
    monkeypatch.setattr(cfg, "archive_source_index_enabled", True)
    monkeypatch.setattr(cfg, "extraction_enabled", False)
    ctx = request_context
    session = await service.sessions.create(
        ctx, memory_policy={"working_memory": {"enabled": False}}
    )
    extract = AsyncMock(side_effect=AssertionError("unexpected memory extraction"))
    monkeypatch.setattr(session._session_compressor, "extract_long_term_memories", extract)
    original = "Fee schedule\u2028the previous reporting cutoff"
    await session.add_message_async("user", [TextPart(original)])
    await session.add_message_async(
        "assistant", [ToolPart(tool_id="hidden", tool_output="tool output")]
    )
    await session.add_message_async("assistant", [TextPart("The cutoff is 17 November.")])

    archive = await _commit(session)
    extract.assert_not_awaited()
    index_root = f"{canonical_user_root(ctx)}/memories/archive_sources"
    directory = f"{index_root}/{session.session_id}/{archive.rsplit('/', 1)[1]}"
    found = await service.search.find("reporting cutoff", ctx, target_uri=index_root)
    assert {item.uri for item in found.memories} == {
        f"{directory}/message_000001.md",
        f"{directory}/message_000003.md",
    }
    locator = await service.fs.read(f"{directory}/message_000003.md", ctx)
    assert f"`{archive}/messages.jsonl`" in locator
    assert "- Line: 3\n" in locator
    source = json.loads(await service.fs.read(f"{archive}/messages.jsonl", ctx, offset=2, limit=1))
    assert source["parts"][0]["text"] == "The cutoff is 17 November."

    if delete_session:
        await service.sessions.delete(session.session_id, ctx)
    else:
        await service.fs.rm(f"{archive}/messages.jsonl", ctx)
    assert not await session._viking_fs.exists(directory, ctx=ctx)
    found = await service.search.find("reporting cutoff", ctx, target_uri=index_root)
    assert not found.memories


async def test_archive_source_embeds_only_persisted_excerpt_on_commit_and_reindex(
    archive_service, request_context, monkeypatch, embedding_inputs
):
    service = archive_service
    ctx = request_context
    cfg = get_openviking_config().memory
    monkeypatch.setattr(cfg, "archive_source_index_enabled", True)
    monkeypatch.setattr(cfg, "extraction_enabled", False)
    monkeypatch.setattr(dependencies, "_service", service)

    original = "Settlement terms\n\nAccount reference: XYZ-0123"
    excerpt = "Settlement terms\n\nAccount reference: [redacted]"

    async def sanitize(content, *, ctx, uri):
        return content.replace(original, excerpt)

    monkeypatch.setattr(service.privacy_sanitizer, "sanitize_for_write", sanitize)
    session = await service.sessions.create(ctx)
    await session.add_message_async("user", [TextPart(original)])
    archive = await _commit(session)

    root = f"{canonical_user_root(ctx)}/memories/archive_sources"
    entry = f"{root}/{session.session_id}/{archive.rsplit('/', 1)[1]}/message_000001.md"
    assert [value for value in embedding_inputs if "Settlement terms" in value] == [excerpt]
    locator = await service.fs.read(entry, ctx)
    assert f"- Source: `{archive}/messages.jsonl`" in locator
    assert "- Line: 1\n" in locator
    assert locator.endswith(f"{excerpt}\n")

    result = await service.reindex(uri=entry, ctx=ctx, wait=True)
    assert result["rebuilt_records"] == 1, result
    assert [value for value in embedding_inputs if "Settlement terms" in value] == [
        excerpt,
        excerpt,
    ]

    found = await service.search.find("settlement", ctx, target_uri=entry)
    assert len(found.memories) == 1
    assert f"- Source: `{archive}/messages.jsonl`" in found.memories[0].abstract


@pytest.mark.parametrize("index_enabled,self_enabled", [(False, True), (True, False)])
async def test_archive_sources_respect_enablement(
    archive_service, request_context, monkeypatch, index_enabled, self_enabled
):
    service = archive_service
    cfg = get_openviking_config().memory
    monkeypatch.setattr(cfg, "archive_source_index_enabled", index_enabled)
    monkeypatch.setattr(cfg, "extraction_enabled", False)
    ctx = request_context
    session = await service.sessions.create(
        ctx, memory_policy={"self": {"enabled": self_enabled}, "working_memory": {"enabled": False}}
    )
    await session.add_message_async("user", [TextPart("An archived source")])

    archive = await _commit(session)
    assert await session._viking_fs.exists(f"{archive}/messages.jsonl", ctx=ctx)
    root = f"{canonical_user_root(ctx)}/memories/archive_sources"
    assert not await session._viking_fs.exists(root, ctx=ctx)
