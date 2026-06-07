# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.patch_merge_context_provider import (
    PatchMergeContextProvider,
    PatchMergePatch,
)


@pytest.mark.asyncio
async def test_patch_merge_context_provider_prefetch_reads_originals_and_renders_patch():
    provider = PatchMergeContextProvider(
        memory_type="experiences",
        original_file_uris=["viking://user/u/memories/experiences/booking.md"],
        patches=[
            PatchMergePatch(
                target_name="booking",
                target_uri="viking://user/u/memories/experiences/booking.md",
                before_content="old line\nkeep line",
                after_content="new line\nkeep line",
            )
        ],
    )
    provider.read_file = AsyncMock(
        return_value={
            "memory_type": "experiences",
            "experience_name": "booking",
            "content": "1\told line\n2\tkeep line",
        }
    )

    messages = await provider.prefetch()

    assert provider.get_tools() == []
    assert provider.read_file.await_count == 1
    read_message = json.loads(messages[0]["content"])
    assert read_message["tool_call_name"] == "read"
    assert read_message["args"] == {"uri": "viking://user/u/memories/experiences/booking.md"}
    assert read_message["result"]["experience_name"] == "booking"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"].startswith("```diff")
    assert "diff --git a/viking://user/u/memories/experiences/booking.md" in messages[1]["content"]
    assert "--- a/viking://user/u/memories/experiences/booking.md" in messages[1]["content"]
    assert "+++ b/viking://user/u/memories/experiences/booking.md" in messages[1]["content"]
    assert "-old line" in messages[1]["content"]
    assert "+new line" in messages[1]["content"]


@pytest.mark.asyncio
async def test_patch_merge_context_provider_prefetch_searches_and_reads_extra_candidates():
    schema = MemoryTypeSchema(
        memory_type="experiences",
        description="Experiences",
        directory="viking://user/{{ user_space }}/memories/experiences",
        filename_template="{{ experience_name }}.md",
        fields=[],
    )
    provider = PatchMergeContextProvider(
        memory_type="experiences",
        required_file_uris=["viking://user/u/memories/experiences/book.md"],
        patches=[
            PatchMergePatch(
                target_name="books",
                target_uri="viking://user/u/memories/experiences/books.md",
                before_content=None,
                after_content="用户喜欢阅读科幻书籍，尤其是太空歌剧。",
            )
        ],
    )
    provider._registry = SimpleNamespace(get=lambda name: schema if name == "experiences" else None)
    provider._ctx = SimpleNamespace(user=SimpleNamespace(user_id="u"))
    provider.search_files = AsyncMock(
        return_value=[
            "viking://user/u/memories/experiences/book.md",
            *[
                f"viking://user/u/memories/experiences/candidate_{idx}.md"
                for idx in range(10)
            ],
        ]
    )
    provider.read_file = AsyncMock(
        return_value={
            "memory_type": "experiences",
            "experience_name": "candidate",
            "content": "candidate content",
        }
    )

    messages = await provider.prefetch()

    provider.search_files.assert_awaited_once()
    _, search_kwargs = provider.search_files.await_args
    assert search_kwargs["search_uris"] == ["viking://user/u/memories/experiences"]
    assert search_kwargs["limit"] == 10
    assert provider.read_file.await_count == 6
    read_uris = [call.args[0] for call in provider.read_file.await_args_list]
    assert read_uris[0] == "viking://user/u/memories/experiences/book.md"
    assert "viking://user/u/memories/experiences/candidate_0.md" in read_uris
    assert "viking://user/u/memories/experiences/candidate_4.md" in read_uris
    assert "viking://user/u/memories/experiences/candidate_5.md" not in read_uris
    assert messages[-1]["content"].startswith("```diff")


@pytest.mark.asyncio
async def test_patch_merge_context_provider_renders_create_patch_from_dev_null():
    provider = PatchMergeContextProvider(
        memory_type="experiences",
        original_file_uris=[],
        patches=[
            PatchMergePatch(
                target_name="new_booking",
                target_uri=None,
                before_content=None,
                after_content="created line",
            )
        ],
    )

    messages = await provider.prefetch()

    assert len(messages) == 1
    assert "diff --git /dev/null b/new_booking" in messages[0]["content"]
    assert "--- /dev/null" in messages[0]["content"]
    assert "+++ b/new_booking" in messages[0]["content"]
    assert "+created line" in messages[0]["content"]


def test_patch_merge_context_provider_get_memory_schema_single_type(monkeypatch):
    schema = MemoryTypeSchema(
        memory_type="experiences",
        description="Experiences",
        directory="viking://user/{{ user_space }}/memories/experiences",
        filename_template="{{ experience_name }}.md",
        fields=[],
    )
    provider = PatchMergeContextProvider(
        memory_type="experiences",
        original_file_uris=[],
        patches=[],
    )
    provider._registry = SimpleNamespace(get=lambda name: schema if name == "experiences" else None)

    assert provider.get_memory_schemas(ctx=None) == [schema]


def test_patch_merge_context_provider_get_memory_schema_raises_for_missing_type():
    provider = PatchMergeContextProvider(
        memory_type="missing",
        original_file_uris=[],
        patches=[],
    )
    provider._registry = SimpleNamespace(get=lambda name: None)

    with pytest.raises(ValueError, match="Memory schema not found or disabled: missing"):
        provider.get_memory_schemas(ctx=None)
