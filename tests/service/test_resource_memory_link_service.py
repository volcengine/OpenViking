# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for resource-memory linking service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_memory_link_service import (
    ResourceMemoryLinkService,
    _ResourceLinkingProvider,
)
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_updater import MemoryUpdateResult
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
            for item_uri in self.store
            if item_uri.startswith(prefix)
        ]


class _ReadFailVikingFS:
    async def read_file(self, uri, ctx=None):
        raise RuntimeError("storage unavailable")

    async def tree(self, uri, ctx=None, node_limit=None, level_limit=None):
        memory_uri = "viking://user/alice/memories/entities/wang.md"
        return [{"uri": memory_uri, "rel_path": "entities/wang.md", "isDir": False}]


class _FakeCompactor:
    def __init__(self):
        self.marked = None
        self.enqueued = False

    async def mark_managed_memories(self, **kwargs):
        self.marked = kwargs
        return list(kwargs["memory_uris"])

    async def enqueue_check(self, **kwargs):
        self.enqueued = True
        return "msg-1"


@pytest.fixture
def request_context():
    return RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=Role.USER,
    )


@pytest.mark.asyncio
async def test_append_resource_refs_stores_only_memory_metadata(request_context):
    memory_uri = "viking://user/alice/memories/entities/wang.md"
    resource_uri = "viking://resources/id_card.pdf"
    store = {memory_uri: "王大锤的身份证资料。\n"}
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS(store))

    await service._append_resource_refs(
        memory_uris=[memory_uri],
        resource_uri=resource_uri,
        reason="这是王大锤的身份证",
        ctx=request_context,
    )

    mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
    assert mf.extra_fields["resource_refs"][0]["resource_uri"] == resource_uri
    assert mf.extra_fields["resource_refs"][0]["source"] == "add_resource.reason"
    assert mf.extra_fields["resource_refs"][0]["match_text"] == "王大锤"
    assert mf.links == []
    assert f"[王大锤]({resource_uri})" in store[memory_uri]
    assert resource_uri not in store


@pytest.mark.asyncio
async def test_on_resource_added_marks_new_memories_for_compaction(request_context):
    memory_uri = "viking://user/alice/memories/entities/动漫角色/越前龙马.md"
    resource_uri = "viking://resources/images/2026/06/11/yueqian_jpeg"
    store = {
        memory_uri: MemoryFileUtils.write(
            MemoryFile(
                uri=memory_uri,
                content="用户上传了一张越前龙马的照片。",
                memory_type="entities",
                extra_fields={"category": "动漫角色", "name": "越前龙马"},
            )
        )
    }
    compactor = _FakeCompactor()
    service = ResourceMemoryLinkService(
        viking_fs=_FakeVikingFS(store),
        compactor=compactor,
    )
    service._run_extract_loop = AsyncMock(
        return_value=(
            SimpleNamespace(upsert_operations=[object()], delete_file_contents=[], errors=[]),
            object(),
            object(),
        )
    )
    update_result = MemoryUpdateResult()
    update_result.add_written(memory_uri)
    service._apply_memory_operations = AsyncMock(return_value=update_result)

    result = await service.on_resource_added(
        ctx=request_context,
        resource_uri=resource_uri,
        reason="这是越前龙马的照片",
        source_name="yueqian.jpeg",
    )

    assert result["status"] == "success"
    assert result["managed_memory_uris"] == [memory_uri]
    assert result["compaction_msg_id"] == "msg-1"
    assert compactor.enqueued is True
    assert compactor.marked["memory_uris"] == [memory_uri]
    assert compactor.marked["created_at"]


def test_resource_linking_provider_detects_language_from_reason_not_resource_uri():
    provider = _ResourceLinkingProvider(
        resource_uri="viking://resources/images/2026/06/10/yueqian_jpeg",
        reason="这是越前龙马的照片",
        source_name="yueqian.jpeg",
    )

    assert provider.get_output_language() == "zh-CN"


def test_resource_linking_provider_exposes_resource_uri_only_as_metadata():
    resource_uri = "viking://resources/images/2026/06/10/yueqian_jpeg"
    provider = _ResourceLinkingProvider(
        resource_uri=resource_uri,
        reason="这是越前龙马的照片",
        source_name="yueqian.jpeg",
        added_at="2026-06-11T08:00:00+00:00",
        resource_abstract="动漫角色照片合集",
    )

    message_text = "\n".join(
        part.text
        for message in provider.messages
        for part in message.parts
        if getattr(part, "text", None)
    )

    instruction = provider.instruction()
    assert resource_uri in instruction
    assert resource_uri in provider._build_conversation_message()["content"]
    assert resource_uri in provider.get_conversation_text()
    assert resource_uri in message_text
    assert "2026-06-11T08:00:00+00:00" in instruction
    assert "动漫角色照片合集" in instruction
    assert (
        "Added at: 2026-06-11T08:00:00+00:00"
        in provider._build_conversation_message()["content"]
    )
    assert "Resource abstract: 动漫角色照片合集" in message_text
    assert "include the exact Resource URI in the visible memory content" not in instruction
    assert "Use the Resource URI only as resource identity metadata" in instruction
    assert "Do NOT include raw resource URIs" in instruction


def test_resource_linking_prompt_prefers_natural_sentence_over_terse_label():
    provider = _ResourceLinkingProvider(
        resource_uri="viking://resources/reports/gdp_pdf",
        reason="这个 PDF 第 65 页的人均 GDP 数据应为 4 万",
        source_name="gdp.pdf",
    )

    instruction = provider.instruction()
    assert "Create/edit visible memory as durable natural sentences" in instruction
    assert "user intent/judgment" in instruction
    assert "rewrite terse resource labels" in instruction
    assert 'reason "page 3 total should be 42"' in instruction
    assert '"User said page 3 total should be 42"' in instruction
    assert "merge with it" in instruction
    assert "only the newest resource" in instruction
    assert "enumerate/count resources" in instruction
    assert "under 12 Chinese characters" in instruction
    assert "under 8 English words" in instruction
    assert "weak supporting context" in instruction
    assert "short resource descriptor only" in instruction
    assert "adds non-redundant readability" in instruction
    assert "Source name alone is opaque" in instruction
    assert "配置服务项目" in instruction
    assert "merely repeats the subject, media type, or facts" in instruction
    assert "角色照片" not in instruction
    assert "身份证" not in instruction


@pytest.mark.asyncio
async def test_read_resource_directory_abstract_uses_parent_abstract(request_context):
    service = ResourceMemoryLinkService(
        viking_fs=_FakeVikingFS({"viking://resources/images/.abstract.md": "动漫角色照片合集"})
    )

    abstract = await service._read_resource_directory_abstract(
        "viking://resources/images/yueqian.jpeg",
        request_context,
    )

    assert abstract == "动漫角色照片合集"


@pytest.mark.asyncio
async def test_read_resource_directory_abstract_ignores_missing_or_not_ready(
    request_context,
):
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS({}))

    missing = await service._read_resource_directory_abstract(
        "viking://resources/images/yueqian.jpeg",
        request_context,
    )

    assert missing == ""

    service = ResourceMemoryLinkService(
        viking_fs=_FakeVikingFS(
            {
                "viking://resources/images/.abstract.md": (
                    "# viking://resources/images [Directory abstract is not ready]"
                )
            }
        )
    )

    not_ready = await service._read_resource_directory_abstract(
        "viking://resources/images/yueqian.jpeg",
        request_context,
    )

    assert not_ready == ""


@pytest.mark.asyncio
async def test_append_resource_refs_linkifies_memory_entity_name_and_removes_plain_uri(
    request_context,
):
    memory_uri = "viking://user/ryoma/memories/entities/fictional_character/越前龙马.md"
    resource_uri = "viking://resources/images/2026/06/10/yueqian_jpeg"
    raw = MemoryFileUtils.write(
        MemoryFile(
            uri=memory_uri,
            content=f"用户上传了一张越前龙马的照片，资源 URI：{resource_uri}",
            extra_fields={
                "category": "fictional_character",
                "name": "越前龙马",
                "memory_type": "entities",
            },
        )
    )
    store = {memory_uri: raw}
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS(store))

    await service._append_resource_refs(
        memory_uris=[memory_uri],
        resource_uri=resource_uri,
        reason="这是越前龙马的照片",
        ctx=request_context,
    )

    written = store[memory_uri]
    assert f"[越前龙马]({resource_uri})" in written
    assert f"资源 URI：{resource_uri}" not in written
    mf = MemoryFileUtils.read(written, uri=memory_uri)
    assert mf.extra_fields["resource_refs"][0]["match_text"] == "越前龙马"
    assert mf.links == []


@pytest.mark.asyncio
async def test_append_resource_refs_removes_colon_visible_uri_with_markdown_escape(
    request_context,
):
    memory_uri = "viking://user/ryoma/memories/entities/fictional_character/越前龙马.md"
    resource_uri = "viking://resources/images/2026/06/10/yueqian_jpeg"
    visible_uri = "viking://resources/images/2026/06/10/yueqian\\_jpeg"
    raw = MemoryFileUtils.write(
        MemoryFile(
            uri=memory_uri,
            content=f"- 越前龙马的照片资源：{visible_uri}",
            extra_fields={
                "category": "fictional_character",
                "name": "越前龙马",
                "memory_type": "entities",
            },
        )
    )
    store = {memory_uri: raw}
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS(store))

    await service._append_resource_refs(
        memory_uris=[memory_uri],
        resource_uri=resource_uri,
        reason="这是越前龙马的照片",
        ctx=request_context,
    )

    mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
    assert mf.content == f"- [越前龙马]({resource_uri})的照片资源"
    assert visible_uri not in mf.content


@pytest.mark.asyncio
async def test_append_resource_refs_falls_back_to_first_sentence_when_anchor_missing(
    request_context,
):
    memory_uri = "viking://user/ryoma/memories/entities/fictional_character/越前龙马.md"
    resource_uri = "viking://resources/images/2026/06/10/yueqian_jpeg"
    store = {memory_uri: "用户上传了一张角色照片。后续句子不应被链接。"}
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS(store))

    await service._append_resource_refs(
        memory_uris=[memory_uri],
        resource_uri=resource_uri,
        reason="这是越前龙马的照片",
        ctx=request_context,
    )

    written = store[memory_uri]
    assert f"[用户上传了一张角色照片。]({resource_uri})" in written
    assert "后续句子不应被链接。" in written
    mf = MemoryFileUtils.read(written, uri=memory_uri)
    assert mf.extra_fields["resource_refs"][0]["match_text"] == "用户上传了一张角色照片。"


@pytest.mark.asyncio
async def test_find_referencing_memories_uses_memory_refs(request_context):
    memory_uri = "viking://user/alice/memories/entities/wang.md"
    resource_uri = "viking://resources/docs/id_card.pdf"
    raw = (
        "王大锤资料。\n\n"
        "<!-- MEMORY_FIELDS\n"
        "{\n"
        '  "resource_refs": [\n'
        "    {\n"
        f'      "resource_uri": "{resource_uri}",\n'
        '      "reason": "这是王大锤的身份证"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "-->"
    )
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS({memory_uri: raw}))

    matches = await service._find_referencing_memories(
        ctx=request_context,
        resource_uri="viking://resources/docs",
        recursive=True,
    )

    assert len(matches) == 1
    assert matches[0].memory_uri == memory_uri
    assert matches[0].resource_ref["resource_uri"] == resource_uri


@pytest.mark.asyncio
async def test_before_resource_delete_removes_refs_when_cleanup_has_no_changes(request_context):
    memory_uri = "viking://user/alice/memories/entities/wang.md"
    resource_uri = "viking://resources/id_card.pdf"
    raw = (
        "王大锤资料。\n\n"
        "<!-- MEMORY_FIELDS\n"
        "{\n"
        '  "resource_refs": [\n'
        "    {\n"
        f'      "resource_uri": "{resource_uri}",\n'
        '      "reason": "这是王大锤的身份证"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "-->"
    )
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS({memory_uri: raw}))
    service._cleanup_memory_reference = AsyncMock(return_value=MemoryUpdateResult())

    result = await service.before_resource_delete(
        ctx=request_context,
        resource_uri=resource_uri,
    )

    assert result["status"] == "success"
    mf = MemoryFileUtils.read(service._get_viking_fs().store[memory_uri], uri=memory_uri)
    assert "resource_refs" not in mf.extra_fields


@pytest.mark.asyncio
async def test_cleanup_memory_reference_does_not_introduce_schema_metadata(request_context):
    memory_uri = "viking://user/ryoma/memories/entities/动漫角色/不二周助-write-test3.md"
    resource_uri = "viking://resources/images/2026/06/10/不二周助_jpeg"
    original_raw = MemoryFileUtils.write(
        MemoryFile(
            uri=memory_uri,
            content=f"今天是清明节。[用户保存了一张不二周助的照片]({resource_uri})",
            extra_fields={
                "resource_refs": [
                    {
                        "resource_uri": resource_uri,
                        "source": "content.write",
                    }
                ]
            },
        )
    )
    store = {memory_uri: original_raw}
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS(store))
    service._run_extract_loop = AsyncMock(return_value=(object(), object(), object()))

    async def fake_apply_memory_operations(**kwargs):
        store[memory_uri] = MemoryFileUtils.write(
            MemoryFile(
                uri=memory_uri,
                content="今天是清明节。",
                memory_type="entities",
                extra_fields={
                    "category": "anime_character",
                    "name": "不二周助",
                    "user_id": "ryoma",
                    "resource_refs": [
                        {
                            "resource_uri": resource_uri,
                            "source": "content.write",
                        }
                    ],
                },
            )
        )
        result = MemoryUpdateResult()
        result.add_edited(memory_uri)
        return result

    service._apply_memory_operations = AsyncMock(side_effect=fake_apply_memory_operations)

    result = await service._cleanup_memory_reference(
        ctx=request_context,
        memory_uri=memory_uri,
        memory_file=MemoryFileUtils.read(original_raw, uri=memory_uri),
        resource_uri=resource_uri,
        reason="",
    )

    assert result.edited_uris == [memory_uri]
    mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
    assert mf.content == "今天是清明节。"
    assert mf.extra_fields == {}
    assert mf.memory_type is None


@pytest.mark.asyncio
async def test_cleanup_memory_reference_deletes_empty_memory_shell(
    request_context,
    monkeypatch,
):
    memory_uri = "viking://user/ryoma/memories/entities/动漫角色/越前龙马.md"
    resource_uri = "viking://resources/images/2026/06/11/yueqian_jpeg"
    original_raw = MemoryFileUtils.write(
        MemoryFile(
            uri=memory_uri,
            content=f"[用户保存了一张越前龙马的照片]({resource_uri})",
            extra_fields={
                "category": "动漫角色",
                "name": "越前龙马",
                "user_id": "ryoma",
                "memory_type": "entities",
            },
        )
    )
    store = {memory_uri: original_raw}
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS(store))
    service._run_extract_loop = AsyncMock(return_value=(object(), object(), object()))
    refresh_overview = AsyncMock()
    monkeypatch.setattr(
        "openviking.service.resource_memory_link_service.MemoryUpdater.refresh_schema_overview",
        refresh_overview,
    )

    async def fake_apply_memory_operations(**kwargs):
        store[memory_uri] = MemoryFileUtils.write(
            MemoryFile(
                uri=memory_uri,
                content="",
                memory_type="entities",
                extra_fields={
                    "category": "动漫角色",
                    "name": "越前龙马",
                    "user_id": "ryoma",
                    "memory_type": "entities",
                    "resource_refs": [
                        {
                            "resource_uri": resource_uri,
                            "source": "add_resource.reason",
                        }
                    ],
                },
            )
        )
        result = MemoryUpdateResult()
        result.add_edited(memory_uri)
        return result

    service._apply_memory_operations = AsyncMock(side_effect=fake_apply_memory_operations)

    result = await service._cleanup_memory_reference(
        ctx=request_context,
        memory_uri=memory_uri,
        memory_file=MemoryFileUtils.read(original_raw, uri=memory_uri),
        resource_uri=resource_uri,
        reason="这是越前龙马的照片",
    )

    assert memory_uri not in store
    assert service._get_viking_fs().rm_calls == [(memory_uri, False)]
    assert result.edited_uris == []
    assert result.deleted_uris == [memory_uri]
    refresh_overview.assert_awaited_once()


@pytest.mark.asyncio
async def test_assert_resource_unlinked_propagates_non_not_found_errors(request_context):
    service = ResourceMemoryLinkService(viking_fs=_ReadFailVikingFS())

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await service._assert_resource_unlinked(
            "viking://user/alice/memories/entities/wang.md",
            "viking://resources/id_card.pdf",
            request_context,
        )
