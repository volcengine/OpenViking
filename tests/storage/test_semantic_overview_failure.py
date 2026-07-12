# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs import semantic_dag as semantic_dag_module
from openviking.storage.queuefs import semantic_processor as semantic_processor_module
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking_cli.session.user_id import UserIdentifier


def _patch_overview_config(monkeypatch, vlm, *, batch_size=1):
    config = SimpleNamespace(
        vlm=vlm,
        semantic=SimpleNamespace(overview_batch_size=batch_size),
    )
    monkeypatch.setattr(semantic_processor_module, "get_openviking_config", lambda: config)


class _EmptyVikingFS:
    def __init__(self):
        self.writes = []

    async def ls(self, _uri, node_limit=None, ctx=None):
        return []

    async def write_file(self, path, content, ctx=None):
        self.writes.append((path, content))

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/account/")


class _FailingOverviewProcessor:
    async def _generate_overview(self, _dir_uri, _file_summaries, _children_abstracts):
        raise TimeoutError("directory overview timed out")

    def _normalize_overview_generation(self, overview):
        return overview, "abstract"


@pytest.mark.asyncio
async def test_single_overview_generation_propagates_vlm_error(monkeypatch):
    error = TimeoutError("VLM request timed out")
    vlm = SimpleNamespace(get_completion_async=AsyncMock(side_effect=error))
    _patch_overview_config(monkeypatch, vlm)
    monkeypatch.setattr(
        semantic_processor_module, "render_prompt", lambda *_args, **_kwargs: "prompt"
    )

    processor = SemanticProcessor()

    with pytest.raises(TimeoutError, match="VLM request timed out"):
        await processor._single_generate_overview(
            "viking://resources/docs",
            "[1] README.md: project overview",
            "None",
            {1: "README.md"},
        )


@pytest.mark.asyncio
async def test_batched_overview_propagates_partial_batch_error(monkeypatch):
    async def get_completion(prompt):
        if "bad.md" in prompt:
            raise TimeoutError("partial batch timed out")
        return "successful partial overview"

    vlm = SimpleNamespace(get_completion_async=get_completion)
    _patch_overview_config(monkeypatch, vlm)
    monkeypatch.setattr(
        semantic_processor_module,
        "render_prompt",
        lambda _name, values: values["file_summaries"],
    )
    processor = SemanticProcessor(max_concurrent_llm=2)

    with pytest.raises(TimeoutError, match="partial batch timed out"):
        await processor._batched_generate_overview(
            "viking://resources/docs",
            [
                {"name": "good.md", "summary": "good"},
                {"name": "bad.md", "summary": "bad"},
            ],
            [],
            {1: "good.md", 2: "bad.md"},
        )


@pytest.mark.asyncio
async def test_batched_overview_propagates_partial_batch_cancellation(monkeypatch):
    async def get_completion(prompt):
        if "cancelled.md" in prompt:
            raise asyncio.CancelledError
        return "successful partial overview"

    vlm = SimpleNamespace(get_completion_async=get_completion)
    _patch_overview_config(monkeypatch, vlm)
    monkeypatch.setattr(
        semantic_processor_module,
        "render_prompt",
        lambda _name, values: values["file_summaries"],
    )
    processor = SemanticProcessor(max_concurrent_llm=2)

    with pytest.raises(asyncio.CancelledError):
        await processor._batched_generate_overview(
            "viking://resources/docs",
            [
                {"name": "good.md", "summary": "good"},
                {"name": "cancelled.md", "summary": "cancelled"},
            ],
            [],
            {1: "good.md", 2: "cancelled.md"},
        )


@pytest.mark.asyncio
async def test_batched_overview_propagates_merge_error(monkeypatch):
    error = TimeoutError("overview merge timed out")
    vlm = SimpleNamespace(
        get_completion_async=AsyncMock(side_effect=["first partial", "second partial", error])
    )
    _patch_overview_config(monkeypatch, vlm)
    monkeypatch.setattr(
        semantic_processor_module, "render_prompt", lambda *_args, **_kwargs: "prompt"
    )
    processor = SemanticProcessor(max_concurrent_llm=2)

    with pytest.raises(TimeoutError, match="overview merge timed out"):
        await processor._batched_generate_overview(
            "viking://resources/docs",
            [
                {"name": "one.md", "summary": "one"},
                {"name": "two.md", "summary": "two"},
            ],
            [],
            {1: "one.md", 2: "two.md"},
        )


@pytest.mark.asyncio
async def test_batched_overview_returns_merged_result_on_success(monkeypatch):
    vlm = SimpleNamespace(
        get_completion_async=AsyncMock(
            side_effect=["first partial", "second partial", "Combined [1] and [2]"]
        )
    )
    _patch_overview_config(monkeypatch, vlm)
    monkeypatch.setattr(
        semantic_processor_module, "render_prompt", lambda *_args, **_kwargs: "prompt"
    )
    processor = SemanticProcessor(max_concurrent_llm=2)

    overview = await processor._batched_generate_overview(
        "viking://resources/docs",
        [
            {"name": "one.md", "summary": "one"},
            {"name": "two.md", "summary": "two"},
        ],
        [],
        {1: "one.md", 2: "two.md"},
    )

    assert overview == "Combined one.md and two.md"


@pytest.mark.asyncio
async def test_semantic_dag_propagates_overview_error_without_writing_sidecars(monkeypatch):
    viking_fs = _EmptyVikingFS()
    monkeypatch.setattr(semantic_dag_module, "get_viking_fs", lambda: viking_fs)
    executor = SemanticDagExecutor(
        processor=_FailingOverviewProcessor(),
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("account", "user"), role=Role.USER),
    )

    with pytest.raises(TimeoutError, match="directory overview timed out"):
        await executor.run("viking://resources/docs")

    assert viking_fs.writes == []
