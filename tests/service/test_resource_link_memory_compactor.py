# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for resource-link memory compaction."""

from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_link_memory_compactor import (
    RESOURCE_LINK_MANAGED_FIELD,
    RESOURCE_LINK_MEMORY_TYPE,
    ResourceLinkMemoryCompactor,
    _CompactedMemory,
    _CompactionResponse,
)
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, store):
        self.store = store
        self.rm_calls = []

    async def read_file(self, uri, ctx=None):
        return self.store[uri]

    async def write_file(self, uri, content, ctx=None):
        self.store[uri] = content

    async def rm(self, uri, recursive=False, ctx=None, lock_handle=None):
        self.rm_calls.append((uri, recursive))
        self.store.pop(uri, None)

    async def tree(self, uri, ctx=None, node_limit=None, level_limit=None):
        prefix = uri.rstrip("/") + "/"
        return [
            {
                "uri": item_uri,
                "rel_path": item_uri.removeprefix(prefix),
                "isDir": False,
            }
            for item_uri in list(self.store)
            if item_uri.startswith(prefix)
        ]


@pytest.fixture
def request_context():
    return RequestContext(
        user=UserIdentifier("acct", "ryoma"),
        role=Role.USER,
    )


def _managed_memory(uri: str, resource_uri: str, index: int) -> str:
    return MemoryFileUtils.write(
        MemoryFile(
            uri=uri,
            content=f"用户上传了一张角色{index}的照片。",
            memory_type="entities",
            extra_fields={
                "category": "动漫角色",
                "name": f"角色{index}",
                RESOURCE_LINK_MANAGED_FIELD: True,
                "resource_refs": [
                    {
                        "resource_uri": resource_uri,
                        "reason": f"这是角色{index}的照片",
                        "source": "add_resource.reason",
                        "created_at": f"2026-06-11T00:00:{index:02d}+00:00",
                    }
                ],
            },
        )
    )


def _aggregate_memory(uri: str, resource_uri: str, item_count: int = 10) -> str:
    return MemoryFileUtils.write(
        MemoryFile(
            uri=uri,
            content="用户保存过一组全球地标风景照片。",
            memory_type=RESOURCE_LINK_MEMORY_TYPE,
            extra_fields={
                "topic": "全球地标风景照片",
                "resource_link_state": {"item_count": item_count},
                "resource_refs": [
                    {
                        "resource_uri": resource_uri,
                        "source": "resource_link.compaction",
                    }
                ],
            },
        )
    )


@pytest.mark.asyncio
async def test_compact_if_needed_writes_aggregate_and_deletes_managed_inputs(
    request_context,
    monkeypatch,
):
    store = {}
    for index in range(10):
        memory_uri = f"viking://user/ryoma/memories/entities/动漫角色/角色{index}.md"
        resource_uri = f"viking://resources/images/2026/06/11/role_{index}"
        store[memory_uri] = _managed_memory(memory_uri, resource_uri, index)

    fake_fs = _FakeVikingFS(store)
    compactor = ResourceLinkMemoryCompactor(viking_fs=fake_fs)
    compactor._call_model = AsyncMock(
        return_value=(
            '{"memories":[{"title":"动漫角色照片",'
            '"content":"用户上传过一组动漫角色照片，代表资源包括'
            '[角色0](viking://resources/images/2026/06/11/role_0)。",'
            '"resource_uris":["viking://resources/images/2026/06/11/role_0"],'
            '"item_count":10}]}'
        )
    )
    refresh_embedding = AsyncMock(return_value=True)
    refresh_overview = AsyncMock()
    monkeypatch.setattr(
        "openviking.service.resource_link_memory_compactor.MemoryUpdater.refresh_file_embedding",
        refresh_embedding,
    )
    monkeypatch.setattr(
        "openviking.service.resource_link_memory_compactor.MemoryUpdater.refresh_schema_overview",
        refresh_overview,
    )

    result = await compactor.compact_if_needed(ctx=request_context)

    aggregate_uri = "viking://user/ryoma/memories/resource_link_memories/动漫角色照片.md"
    assert result["status"] == "success"
    assert result["written_uris"] == [aggregate_uri]
    assert len(result["deleted_uris"]) == 10
    assert aggregate_uri in store
    assert all("memories/entities/动漫角色/角色" not in uri for uri in store)

    aggregate = MemoryFileUtils.read(store[aggregate_uri], uri=aggregate_uri)
    assert aggregate.memory_type == RESOURCE_LINK_MEMORY_TYPE
    assert aggregate.extra_fields["topic"] == "动漫角色照片"
    assert aggregate.extra_fields["resource_link_state"]["item_count"] == 10
    assert aggregate.extra_fields["resource_refs"][0]["resource_uri"].endswith("/role_0")
    refresh_embedding.assert_awaited_once()
    refresh_overview.assert_awaited()


@pytest.mark.asyncio
async def test_compact_if_needed_skips_below_threshold(request_context):
    store = {}
    for index in range(9):
        memory_uri = f"viking://user/ryoma/memories/entities/动漫角色/角色{index}.md"
        resource_uri = f"viking://resources/images/2026/06/11/role_{index}"
        store[memory_uri] = _managed_memory(memory_uri, resource_uri, index)

    compactor = ResourceLinkMemoryCompactor(viking_fs=_FakeVikingFS(store))
    compactor._call_model = AsyncMock()

    result = await compactor.compact_if_needed(ctx=request_context)

    assert result == {
        "status": "skipped",
        "reason": "below_threshold",
        "single_count": 9,
        "aggregate_count": 0,
        "total_memory_count": 9,
    }
    compactor._call_model.assert_not_called()


@pytest.mark.asyncio
async def test_compact_if_needed_counts_existing_aggregates_toward_threshold(
    request_context,
    monkeypatch,
):
    store = {}
    aggregate_uri = "viking://user/ryoma/memories/resource_link_memories/全球地标风景照片.md"
    store[aggregate_uri] = _aggregate_memory(
        aggregate_uri,
        "viking://resources/images/2026/06/11/landmark_0",
        item_count=10,
    )
    for index in range(9):
        memory_uri = f"viking://user/ryoma/memories/entities/照片资源/风景{index}.md"
        resource_uri = f"viking://resources/images/2026/06/11/scene_{index}"
        store[memory_uri] = _managed_memory(memory_uri, resource_uri, index)

    fake_fs = _FakeVikingFS(store)
    compactor = ResourceLinkMemoryCompactor(viking_fs=fake_fs)
    compactor._call_model = AsyncMock(
        return_value=(
            '{"memories":[{"title":"风景照片集合",'
            '"content":"用户保存过一组风景照片，代表资源包括'
            '[风景0](viking://resources/images/2026/06/11/scene_0)。",'
            '"resource_uris":["viking://resources/images/2026/06/11/scene_0"],'
            '"item_count":19}]}'
        )
    )
    monkeypatch.setattr(
        "openviking.service.resource_link_memory_compactor.MemoryUpdater.refresh_file_embedding",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "openviking.service.resource_link_memory_compactor.MemoryUpdater.refresh_schema_overview",
        AsyncMock(),
    )

    result = await compactor.compact_if_needed(ctx=request_context)

    assert result["status"] == "success"
    assert aggregate_uri in result["deleted_uris"]
    compactor._call_model.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_managed_memories_marks_only_memory_files(request_context):
    memory_uri = "viking://user/ryoma/memories/entities/动漫角色/越前龙马.md"
    resource_uri = "viking://resources/images/2026/06/11/yueqian_jpeg"
    store = {
        memory_uri: MemoryFileUtils.write(
            MemoryFile(
                uri=memory_uri,
                content=f"用户上传了一张[越前龙马]({resource_uri})的照片。",
                memory_type="entities",
                extra_fields={
                    "category": "动漫角色",
                    "name": "越前龙马",
                    "resource_refs": [{"resource_uri": resource_uri}],
                },
            )
        )
    }
    compactor = ResourceLinkMemoryCompactor(viking_fs=_FakeVikingFS(store))

    marked = await compactor.mark_managed_memories(
        ctx=request_context,
        memory_uris=[memory_uri, "viking://resources/images/2026/06/11/yueqian_jpeg"],
        created_at="2026-06-11T00:00:00+00:00",
    )

    assert marked == [memory_uri]
    mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
    assert mf.extra_fields[RESOURCE_LINK_MANAGED_FIELD] is True
    assert mf.extra_fields["resource_link_source"] == "add_resource.reason"
    assert mf.extra_fields["resource_link_created_at"] == "2026-06-11T00:00:00+00:00"


def test_clean_title_removes_upload_date_and_user_prefix():
    title = ResourceLinkMemoryCompactor._clean_title(
        "2026年6月11日用户上传的全球知名地标风景照片合集",
        1,
    )

    assert title == "全球知名地标风景照片合集"
    assert len(title) <= 24


@pytest.mark.asyncio
async def test_write_aggregate_memories_truncates_long_content(request_context, monkeypatch):
    fake_fs = _FakeVikingFS({})
    compactor = ResourceLinkMemoryCompactor(viking_fs=fake_fs)
    monkeypatch.setattr(
        "openviking.service.resource_link_memory_compactor.MemoryUpdater.refresh_file_embedding",
        AsyncMock(return_value=True),
    )
    long_content = "用户保存了一组风景照片。" + ("很长的补充信息" * 100)

    written = await compactor._write_aggregate_memories(
        ctx=request_context,
        aggregate_dir_uri="viking://user/ryoma/memories/resource_link_memories",
        response=_CompactionResponse(
            memories=[
                _CompactedMemory(
                    title="风景照片集合",
                    content=long_content,
                    resource_uris=["viking://resources/images/2026/06/11/scene_0"],
                    item_count=10,
                )
            ]
        ),
        input_item_count=10,
    )

    mf = MemoryFileUtils.read(fake_fs.store[written[0]], uri=written[0])
    assert len(mf.content) <= 360
    assert mf.content.endswith("...")
