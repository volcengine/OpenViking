# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for resource-memory linking service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_memory_link_service import (
    _RESOURCE_REASON_SESSION_ID,
    ResourceMemoryLinkService,
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


class _FakeSession:
    def __init__(self):
        self.messages = []
        self.meta = SimpleNamespace(memory_policy=None)

    def add_messages(self, specs):
        self.messages.extend(specs)


class _FakeSessionService:
    def __init__(self):
        self.session = _FakeSession()
        self.created = []
        self.got = []
        self.committed = []
        self.deleted = []

    async def create(self, ctx, session_id=None, memory_policy=None):
        self.created.append(
            {
                "ctx": ctx,
                "session_id": session_id,
                "memory_policy": memory_policy,
            }
        )
        return self.session

    async def get(self, session_id, ctx, auto_create=False):
        self.got.append(
            {
                "ctx": ctx,
                "session_id": session_id,
                "auto_create": auto_create,
            }
        )
        return self.session

    async def commit_async(self, session_id, ctx, keep_recent_count=0):
        archive_index = len(self.committed) + 1
        self.committed.append(
            {
                "ctx": ctx,
                "session_id": session_id,
                "keep_recent_count": keep_recent_count,
            }
        )
        return {
            "task_id": None,
            "archive_uri": (
                f"viking://user/alice/sessions/{session_id}/history/archive_{archive_index:03d}"
            ),
        }

    async def delete(self, session_id, ctx):
        self.deleted.append({"ctx": ctx, "session_id": session_id})


@pytest.fixture
def request_context():
    return RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=Role.USER,
    )


@pytest.mark.asyncio
async def test_on_resource_added_bridges_reason_through_fixed_session(request_context):
    resource_uri = "viking://resources/images/2026/06/11/yueqian_jpeg"
    session_service = _FakeSessionService()
    service = ResourceMemoryLinkService(
        viking_fs=_FakeVikingFS(
            {"viking://resources/images/2026/06/11/.abstract.md": "动漫角色照片合集"}
        ),
        session_service=session_service,
    )

    result = await service.on_resource_added(
        ctx=request_context,
        resource_uri=resource_uri,
        reason="这是越前龙马的照片",
        source_name="yueqian.jpeg",
    )

    session_id = result["session_id"]
    assert result["status"] == "success"
    assert session_id == _RESOURCE_REASON_SESSION_ID
    assert session_service.got == [
        {
            "ctx": request_context,
            "session_id": session_id,
            "auto_create": True,
        }
    ]
    assert session_service.created == []
    assert session_service.session.meta.memory_policy == {
        "self": {"enabled": True},
        "peer": {"enabled": False},
        "memory_types": ["entities", "events", "preferences"],
    }
    assert session_service.committed == [
        {
            "ctx": request_context,
            "session_id": session_id,
            "keep_recent_count": 0,
        }
    ]
    assert session_service.deleted == []
    message_text = session_service.session.messages[0]["parts"][0].text
    assert resource_uri in message_text
    assert "这是越前龙马的照片" in message_text
    assert "yueqian.jpeg" in message_text
    assert "动漫角色照片合集" in message_text


@pytest.mark.asyncio
async def test_on_resource_added_reuses_same_reason_session(request_context):
    session_service = _FakeSessionService()
    service = ResourceMemoryLinkService(
        viking_fs=_FakeVikingFS({}),
        session_service=session_service,
    )

    first = await service.on_resource_added(
        ctx=request_context,
        resource_uri="viking://resources/images/ryoma.jpeg",
        reason="这是越前龙马的照片",
        source_name="ryoma.jpeg",
    )
    second = await service.on_resource_added(
        ctx=request_context,
        resource_uri="viking://resources/images/fuji.jpeg",
        reason="这是不二周助的照片",
        source_name="fuji.jpeg",
    )

    assert first["session_id"] == _RESOURCE_REASON_SESSION_ID
    assert second["session_id"] == _RESOURCE_REASON_SESSION_ID
    assert [call["session_id"] for call in session_service.got] == [
        _RESOURCE_REASON_SESSION_ID,
        _RESOURCE_REASON_SESSION_ID,
    ]
    assert [call["session_id"] for call in session_service.committed] == [
        _RESOURCE_REASON_SESSION_ID,
        _RESOURCE_REASON_SESSION_ID,
    ]
    assert session_service.deleted == []
    messages = [item["parts"][0].text for item in session_service.session.messages]
    assert "这是越前龙马的照片" in messages[0]
    assert "这是不二周助的照片" in messages[1]


@pytest.mark.asyncio
async def test_on_resource_added_routes_reason_to_actor_peer(request_context):
    peer_ctx = RequestContext(
        user=request_context.user,
        role=request_context.role,
        actor_peer_id="web-visitor-alice",
    )
    session_service = _FakeSessionService()
    service = ResourceMemoryLinkService(
        viking_fs=_FakeVikingFS({}),
        session_service=session_service,
    )

    result = await service.on_resource_added(
        ctx=peer_ctx,
        resource_uri="viking://resources/images/ryoma.jpeg",
        reason="这是越前龙马的照片",
        source_name="ryoma.jpeg",
    )

    assert result["session_id"] == _RESOURCE_REASON_SESSION_ID
    assert session_service.session.meta.memory_policy == {
        "self": {"enabled": False},
        "peer": {"enabled": True},
        "memory_types": ["entities", "events", "preferences"],
    }
    assert session_service.session.messages[0]["peer_id"] == "web-visitor-alice"
    assert session_service.committed == [
        {
            "ctx": peer_ctx,
            "session_id": _RESOURCE_REASON_SESSION_ID,
            "keep_recent_count": 0,
        }
    ]


@pytest.mark.asyncio
async def test_on_resource_added_routes_peer_resource_uri_to_peer(request_context):
    resource_uri = "viking://user/alice/peers/web-visitor-alice/resources/images/ryoma.jpeg"
    session_service = _FakeSessionService()
    service = ResourceMemoryLinkService(
        viking_fs=_FakeVikingFS({}),
        session_service=session_service,
    )

    await service.on_resource_added(
        ctx=request_context,
        resource_uri=resource_uri,
        reason="这是越前龙马的照片",
        source_name="ryoma.jpeg",
    )

    assert session_service.session.meta.memory_policy == {
        "self": {"enabled": False},
        "peer": {"enabled": True},
        "memory_types": ["entities", "events", "preferences"],
    }
    assert session_service.session.messages[0]["peer_id"] == "web-visitor-alice"


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
async def test_find_referencing_memories_scans_actor_peer_memory(request_context):
    peer_ctx = RequestContext(
        user=request_context.user,
        role=request_context.role,
        actor_peer_id="web-visitor-alice",
    )
    memory_uri = "viking://user/alice/peers/web-visitor-alice/memories/entities/wang.md"
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
        ctx=peer_ctx,
        resource_uri=resource_uri,
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
    refresh_overview = AsyncMock()
    monkeypatch.setattr(
        "openviking.service.resource_memory_link_service.MemoryUpdater.refresh_schema_overview",
        refresh_overview,
    )

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
async def test_before_resource_delete_cleans_visible_uri_without_resource_refs(
    request_context,
    monkeypatch,
):
    memory_uri = "viking://user/alice/memories/events/2026/06/11/yueqian.md"
    resource_uri = "viking://resources/images/2026/06/12/yueqian_jpeg"
    raw = MemoryFileUtils.write(
        MemoryFile(
            uri=memory_uri,
            content=(
                f"今天是清明节。\n用户昨晚查看了[越前龙马照片]({resource_uri})，之后可参考该资源。"
            ),
            extra_fields={"memory_type": "events"},
        )
    )
    store = {memory_uri: raw}
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS(store))
    refresh_overview = AsyncMock()
    monkeypatch.setattr(
        "openviking.service.resource_memory_link_service.MemoryUpdater.refresh_schema_overview",
        refresh_overview,
    )

    result = await service.before_resource_delete(
        ctx=request_context,
        resource_uri=resource_uri,
    )

    assert result["status"] == "success"
    assert result["memory_uris"] == [memory_uri]
    mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
    assert mf.content == "今天是清明节。"
    assert "resource_refs" not in mf.extra_fields


@pytest.mark.asyncio
async def test_before_resource_delete_exact_keeps_child_resource_refs(
    request_context,
):
    memory_uri = "viking://user/alice/memories/entities/photos.md"
    resource_uri = "viking://resources/images/album"
    child_uri = f"{resource_uri}/child.jpeg"
    raw = MemoryFileUtils.write(
        MemoryFile(
            uri=memory_uri,
            content=(
                f"用户保存了[相册资源]({resource_uri})。\n用户保存了[相册里的子图]({child_uri})。"
            ),
            extra_fields={
                "resource_refs": [
                    {"resource_uri": resource_uri, "source": "content.write"},
                    {"resource_uri": child_uri, "source": "content.write"},
                ],
            },
        )
    )
    store = {memory_uri: raw}
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS(store))

    result = await service.before_resource_delete(
        ctx=request_context,
        resource_uri=resource_uri,
        recursive=False,
    )

    assert result["status"] == "success"
    mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
    assert f"[相册资源]({resource_uri})" not in mf.content
    assert f"[相册里的子图]({child_uri})" in mf.content
    refs = mf.extra_fields["resource_refs"]
    assert refs == [{"resource_uri": child_uri, "source": "content.write"}]


@pytest.mark.asyncio
async def test_before_resource_delete_deletes_previous_failed_cleanup_artifact(
    request_context,
    monkeypatch,
):
    memory_uri = "viking://user/alice/memories/events/2026/06/11/yueqian.md"
    resource_uri = "viking://resources/images/2026/06/12/yueqian_jpeg"
    raw = MemoryFileUtils.write(
        MemoryFile(
            uri=memory_uri,
            content=(
                f"Summary: 用户查看了[越前龙马照片]({resource_uri})。\n"
                "None ChatLog:\n"
                f"[[user]: Deleted resource URI:]({resource_uri})\n"
                "Original reason: \n"
                f"Memory URI: {memory_uri}"
            ),
            extra_fields={"memory_type": "events"},
        )
    )
    store = {memory_uri: raw}
    service = ResourceMemoryLinkService(viking_fs=_FakeVikingFS(store))
    refresh_overview = AsyncMock()
    monkeypatch.setattr(
        "openviking.service.resource_memory_link_service.MemoryUpdater.refresh_schema_overview",
        refresh_overview,
    )

    result = await service.before_resource_delete(
        ctx=request_context,
        resource_uri=resource_uri,
    )

    assert result["status"] == "success"
    assert result["deleted_memory_uris"] == [memory_uri]
    assert memory_uri not in store
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
