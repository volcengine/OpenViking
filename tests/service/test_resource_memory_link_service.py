# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for resource-memory linking service."""

import re
from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_memory_link_service import (
    _RESOURCE_REASON_SESSION_ID,
    ResourceMemoryLinkService,
)
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, store):
        self.store = store
        self.rm_calls = []
        self.read_calls = []
        self.tree_calls = []

    async def read_file(self, uri, ctx=None):
        self.read_calls.append(uri)
        return self.store[uri]

    async def write_file(self, uri, content, ctx=None):
        self.store[uri] = content

    async def rm(self, uri, recursive=False, ctx=None, lock_handle=None):
        self.rm_calls.append((uri, recursive))
        self.store.pop(uri, None)

    async def tree(self, uri, ctx=None, node_limit=None, level_limit=None):
        self.tree_calls.append(
            {
                "uri": uri,
                "node_limit": node_limit,
                "level_limit": level_limit,
            }
        )
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

    async def grep(
        self,
        uri,
        pattern,
        exclude_uri=None,
        case_insensitive=False,
        node_limit=None,
        level_limit=None,
        ctx=None,
    ):
        del exclude_uri, case_insensitive, level_limit, ctx
        prefix = uri.rstrip("/") + "/"
        matches = [
            {
                "uri": item_uri,
                "line": 1,
                "content": content,
            }
            for item_uri, content in self.store.items()
            if item_uri.startswith(prefix) and re.search(pattern, content)
        ]
        if node_limit is not None:
            matches = matches[:node_limit]
        return {
            "matches": matches,
            "count": len(matches),
            "match_count": len(matches),
            "files_scanned": len(self.store),
        }


class _FakeGrepVikingFS(_FakeVikingFS):
    def __init__(self, store, grep_uris):
        super().__init__(store)
        self.grep_uris = grep_uris
        self.grep_calls = []

    async def grep(
        self,
        uri,
        pattern,
        exclude_uri=None,
        case_insensitive=False,
        node_limit=None,
        level_limit=None,
        ctx=None,
    ):
        self.grep_calls.append(
            {
                "uri": uri,
                "pattern": pattern,
                "exclude_uri": exclude_uri,
                "case_insensitive": case_insensitive,
                "node_limit": node_limit,
                "level_limit": level_limit,
            }
        )
        return {
            "matches": [
                {
                    "uri": match_uri,
                    "line": 1,
                    "content": self.store.get(match_uri, ""),
                }
                for match_uri in self.grep_uris
            ],
            "count": len(self.grep_uris),
            "match_count": len(self.grep_uris),
            "files_scanned": len(self.store),
        }

    async def tree(self, uri, ctx=None, node_limit=None, level_limit=None):
        raise AssertionError("grep path should not fall back to tree")


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
async def test_on_resource_deleted_bridges_through_fixed_session(request_context):
    resource_uri = "viking://resources/images/2026/06/11/yueqian_jpeg"
    memory_uri = "viking://user/alice/memories/entities/photos.md"
    session_service = _FakeSessionService()
    service = ResourceMemoryLinkService(
        viking_fs=_FakeVikingFS({}),
        session_service=session_service,
    )

    result = await service.on_resource_deleted(
        ctx=request_context,
        resource_uri=resource_uri,
        memory_uris=[memory_uri],
        recursive=True,
    )

    assert result["status"] == "success"
    assert result["session_id"] == _RESOURCE_REASON_SESSION_ID
    assert session_service.session.meta.memory_policy == {
        "self": {"enabled": True},
        "peer": {"enabled": False},
        "memory_types": ["entities", "preferences"],
    }
    message_text = session_service.session.messages[0]["parts"][0].text
    assert "## Resource Deletion" in message_text
    assert resource_uri in message_text
    assert memory_uri in message_text
    assert "Do not create a new event" in message_text


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
async def test_find_referencing_memories_uses_grep_candidates_without_tree_scan(request_context):
    memory_uri = "viking://user/alice/memories/entities/wang.md"
    unrelated_uri = "viking://user/alice/memories/entities/unrelated.md"
    overview_uri = "viking://user/alice/memories/entities/.overview.md"
    resource_uri = "viking://resources/docs/id_card.pdf"
    raw = MemoryFileUtils.write(
        MemoryFile(
            uri=memory_uri,
            content="王大锤资料。",
            extra_fields={
                "resource_refs": [
                    {
                        "resource_uri": resource_uri,
                        "reason": "这是王大锤的身份证",
                    }
                ]
            },
        )
    )
    store = {
        memory_uri: raw,
        unrelated_uri: "不会命中的普通记忆",
        overview_uri: f"- [王大锤]({memory_uri})",
    }
    viking_fs = _FakeGrepVikingFS(
        store,
        grep_uris=[memory_uri, memory_uri, overview_uri],
    )
    service = ResourceMemoryLinkService(viking_fs=viking_fs)

    matches = await service._find_referencing_memories(
        ctx=request_context,
        resource_uri=resource_uri,
        recursive=False,
    )

    assert len(matches) == 1
    assert matches[0].memory_uri == memory_uri
    assert viking_fs.grep_calls == [
        {
            "uri": "viking://user/alice/memories",
            "pattern": re.escape(resource_uri),
            "exclude_uri": None,
            "case_insensitive": False,
            "node_limit": None,
            "level_limit": None,
        }
    ]
    assert viking_fs.read_calls == [memory_uri]
    assert viking_fs.tree_calls == []


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
async def test_before_resource_delete_commits_then_unlinks_stale_refs(request_context):
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
    session_service = _FakeSessionService()
    service = ResourceMemoryLinkService(
        viking_fs=_FakeVikingFS({memory_uri: raw}),
        session_service=session_service,
    )

    result = await service.before_resource_delete(
        ctx=request_context,
        resource_uri=resource_uri,
    )

    assert result["status"] == "success"
    assert result["memory_commit"]["status"] == "success"
    assert session_service.committed == [
        {
            "ctx": request_context,
            "session_id": _RESOURCE_REASON_SESSION_ID,
            "keep_recent_count": 0,
        }
    ]
    mf = MemoryFileUtils.read(service._get_viking_fs().store[memory_uri], uri=memory_uri)
    assert "resource_refs" not in mf.extra_fields


@pytest.mark.asyncio
async def test_unlink_memory_reference_keeps_visible_text_and_no_schema_metadata(request_context):
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

    result = await service._unlink_memory_reference(
        ctx=request_context,
        memory_uri=memory_uri,
        memory_file=MemoryFileUtils.read(original_raw, uri=memory_uri),
        resource_uri=resource_uri,
    )

    assert result.edited_uris == [memory_uri]
    mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
    assert mf.content == "今天是清明节。用户保存了一张不二周助的照片"
    assert mf.extra_fields == {}
    assert mf.memory_type is None


@pytest.mark.asyncio
async def test_unlink_memory_reference_does_not_delete_event_memory(
    request_context,
):
    memory_uri = "viking://user/ryoma/memories/events/2026/06/11/越前龙马.md"
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

    result = await service._unlink_memory_reference(
        ctx=request_context,
        memory_uri=memory_uri,
        memory_file=MemoryFileUtils.read(original_raw, uri=memory_uri),
        resource_uri=resource_uri,
    )

    assert memory_uri in store
    assert service._get_viking_fs().rm_calls == []
    assert result.edited_uris == [memory_uri]
    assert result.deleted_uris == []
    mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
    assert mf.content == "用户保存了一张越前龙马的照片"


@pytest.mark.asyncio
async def test_before_resource_delete_cleans_visible_uri_without_resource_refs(
    request_context,
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

    result = await service.before_resource_delete(
        ctx=request_context,
        resource_uri=resource_uri,
    )

    assert result["status"] == "success"
    assert result["memory_uris"] == [memory_uri]
    mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
    assert mf.content == "今天是清明节。\n用户昨晚查看了越前龙马照片，之后可参考该资源。"
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
    assert "用户保存了相册资源。" in mf.content
    assert f"[相册里的子图]({child_uri})" in mf.content
    refs = mf.extra_fields["resource_refs"]
    assert refs == [{"resource_uri": child_uri, "source": "content.write"}]


@pytest.mark.asyncio
async def test_assert_resource_unlinked_propagates_non_not_found_errors(request_context):
    service = ResourceMemoryLinkService(viking_fs=_ReadFailVikingFS())

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await service._assert_resource_unlinked(
            "viking://user/alice/memories/entities/wang.md",
            "viking://resources/id_card.pdf",
            request_context,
        )
