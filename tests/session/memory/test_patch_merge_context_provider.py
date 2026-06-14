# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.session.memory.dataclass import MemoryFile, MemoryTypeSchema
from openviking.session.memory.patch_merge_context_provider import (
    PatchMergeContextProvider,
    PatchMergePatch,
)


def _memory_file(
    *,
    name: str,
    uri: str | None,
    content: str,
    memory_type: str = "experiences",
) -> MemoryFile:
    return MemoryFile(
        uri=uri,
        content=content,
        memory_type=memory_type,
        extra_fields={
            "memory_type": memory_type,
            "experience_name": name,
            "status": "production",
        },
    )


@pytest.mark.asyncio
async def test_patch_merge_context_provider_prefetch_reads_originals_and_renders_patch():
    uri = "viking://user/u/memories/experiences/booking.md"
    provider = PatchMergeContextProvider(
        memory_type="experiences",
        required_file_uris=[uri],
        patches=[
            PatchMergePatch(
                before_file=_memory_file(name="booking", uri=uri, content="old line\nkeep line"),
                after_file=_memory_file(name="booking", uri=uri, content="new line\nkeep line"),
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
    assert messages[1]["content"].startswith("# Memory File Patches")
    assert "## Memory Patch 1" in messages[1]["content"]
    assert "target_uri: viking://user/u/memories/experiences/booking.md" in messages[1]["content"]
    assert "### Field Diff: content" in messages[1]["content"]
    assert "--- content.before" in messages[1]["content"]
    assert "+++ content.after" in messages[1]["content"]
    assert "-old line" in messages[1]["content"]
    assert "+new line" in messages[1]["content"]
    assert " keep line" not in messages[1]["content"]
    assert "### Field Diff: status" not in messages[1]["content"]


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
                before_file=None,
                after_file=_memory_file(
                    name="books",
                    uri="viking://user/u/memories/experiences/books.md",
                    content="用户喜欢阅读科幻书籍，尤其是太空歌剧。",
                ),
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
    assert messages[-1]["content"].startswith("# Memory File Patches")


@pytest.mark.asyncio
async def test_patch_merge_context_provider_caps_extra_candidate_reads_at_ten():
    schema = MemoryTypeSchema(
        memory_type="experiences",
        description="Experiences",
        directory="viking://user/{{ user_space }}/memories/experiences",
        filename_template="{{ experience_name }}.md",
        fields=[],
    )
    required_uris = [
        f"viking://user/u/memories/experiences/required_{idx}.md" for idx in range(12)
    ]
    provider = PatchMergeContextProvider(
        memory_type="experiences",
        required_file_uris=required_uris,
        patches=[
            PatchMergePatch(
                before_file=None,
                after_file=_memory_file(
                    name="books",
                    uri="viking://user/u/memories/experiences/books.md",
                    content="用户喜欢阅读科幻书籍，尤其是太空歌剧。",
                ),
            )
        ],
    )
    provider._registry = SimpleNamespace(get=lambda name: schema if name == "experiences" else None)
    provider._ctx = SimpleNamespace(user=SimpleNamespace(user_id="u"))
    provider.search_files = AsyncMock(
        return_value=[
            *required_uris,
            *[
                f"viking://user/u/memories/experiences/candidate_{idx}.md"
                for idx in range(20)
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

    await provider.prefetch()

    _, search_kwargs = provider.search_files.await_args
    assert search_kwargs["limit"] == 20
    assert provider.read_file.await_count == 22
    read_uris = [call.args[0] for call in provider.read_file.await_args_list]
    assert required_uris[-1] in read_uris
    assert "viking://user/u/memories/experiences/candidate_9.md" in read_uris
    assert "viking://user/u/memories/experiences/candidate_10.md" not in read_uris


@pytest.mark.asyncio
async def test_patch_merge_context_provider_renders_compact_patch_metadata():
    provider = PatchMergeContextProvider(
        memory_type="experiences",
        required_file_uris=[],
        patches=[
            PatchMergePatch(
                before_file=None,
                after_file=_memory_file(
                    name="new_booking",
                    uri=None,
                    content="created line",
                ),
                metadata={
                    "base_version": 3,
                    "rationale": "useful reason",
                    "confidence": 0.9,
                    "links": [{"to_uri": "viking://user/u/memories/trajectories/t.md"}],
                    "memory_fields": {"content": "created line"},
                    "uris": ["viking://user/u/memories/experiences/new_booking.md"],
                    "gradient_metadata": {
                        "trajectory_outcome": "success",
                        "rubric_passed": True,
                        "training_category": "tau2:airline:train:1",
                        "memory_fields": {"content": "duplicated"},
                    },
                },
            )
        ],
    )

    messages = await provider.prefetch()
    content = messages[0]["content"]

    assert 'metadata: {"base_version": 3' in content
    assert '"rationale": "useful reason"' in content
    assert '"trajectory_outcome": "success"' in content
    assert "links" not in content
    assert "memory_fields" not in content
    assert "duplicated" not in content


@pytest.mark.asyncio
async def test_patch_merge_context_provider_renders_create_patch_from_dev_null():
    provider = PatchMergeContextProvider(
        memory_type="experiences",
        required_file_uris=[],
        patches=[
            PatchMergePatch(
                before_file=None,
                after_file=_memory_file(name="new_booking", uri=None, content="created line"),
            )
        ],
    )

    messages = await provider.prefetch()

    assert len(messages) == 1
    assert "target_name: new_booking" in messages[0]["content"]
    assert "### Field Diff: content" in messages[0]["content"]
    assert "--- content.before" in messages[0]["content"]
    assert "+++ content.after" in messages[0]["content"]
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
        required_file_uris=[],
        patches=[],
    )
    provider._registry = SimpleNamespace(get=lambda name: schema if name == "experiences" else None)

    assert provider.get_memory_schemas(ctx=None) == [schema]


def test_patch_merge_context_provider_get_memory_schema_raises_for_missing_type():
    provider = PatchMergeContextProvider(
        memory_type="missing",
        required_file_uris=[],
        patches=[],
    )
    provider._registry = SimpleNamespace(get=lambda name: None)

    with pytest.raises(ValueError, match="Memory schema not found or disabled: missing"):
        provider.get_memory_schemas(ctx=None)


def test_patch_merge_context_provider_instruction_mentions_path_field_normalization():
    provider = PatchMergeContextProvider(
        memory_type="entities",
        required_file_uris=[],
        patches=[],
    )

    instruction = provider.instruction()

    assert "independent extraction patch proposals" in instruction
    assert "merge duplicate/overlapping\nmemories into one canonical file patch" in instruction
    assert "directory/filename fields" in instruction
    assert "schema identifiers" in instruction
    assert "book not books" in instruction
    assert "Chinese" in instruction
    assert "书籍 not 书/图书" in instruction
    assert "put it in delete_ids" in instruction


def test_patch_merge_context_provider_detects_language_from_patch_content(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    provider = PatchMergeContextProvider(
        memory_type="preferences",
        required_file_uris=[],
        patches=[
            PatchMergePatch(
                before_file=None,
                after_file=MemoryFile(
                    uri=None,
                    content="User prefers concise implementation and minimal fallback logic.",
                    memory_type="preferences",
                    extra_fields={
                        "memory_type": "preferences",
                        "user": "alice",
                        "topic": "code_style",
                    },
                ),
            )
        ],
    )

    assert provider.get_output_language() == "en"
    assert "All memory content must be written in en." in provider.instruction()


def test_patch_merge_context_provider_empty_patches_fallback_to_english(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    provider = PatchMergeContextProvider(
        memory_type="preferences",
        required_file_uris=[],
        patches=[],
    )

    assert provider.get_output_language() == "en"


def test_patch_merge_context_provider_ignores_before_file_language(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    provider = PatchMergeContextProvider(
        memory_type="preferences",
        required_file_uris=[],
        patches=[
            PatchMergePatch(
                before_file=MemoryFile(
                    uri="viking://user/u/memories/preferences/old.md",
                    content="用户偏好简洁实现。",
                    memory_type="preferences",
                    extra_fields={"memory_type": "preferences", "topic": "代码风格"},
                ),
                after_file=MemoryFile(
                    uri="viking://user/u/memories/preferences/old.md",
                    content="User prefers concise implementation.",
                    memory_type="preferences",
                    extra_fields={"memory_type": "preferences", "topic": "code_style"},
                ),
            )
        ],
    )

    assert provider.get_output_language() == "en"
