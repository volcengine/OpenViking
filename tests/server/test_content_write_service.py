# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Service-level tests for content write coordination."""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils import MemoryFileUtils
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.errors import ResourceBusyError
from openviking_cli.exceptions import (
    AlreadyExistsError,
    DeadlineExceededError,
    InvalidArgumentError,
    NotFoundError,
)
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_write_updates_memory_file_and_parent_overview(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_dir = f"viking://user/{ctx.user.user_space_name()}/memories/preferences"
    memory_uri = f"{memory_dir}/theme.md"

    await service.viking_fs.write_file(memory_uri, "Original preference", ctx=ctx)

    result = await service.fs.write(
        memory_uri,
        content="Updated preference",
        ctx=ctx,
        mode="replace",
        wait=True,
    )

    assert result["context_type"] == "memory"
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "complete"
    assert result["overview_status"] == "complete"
    assert result["queue_status"]["Embedding"]["processed"] >= 1
    assert await service.viking_fs.read_file(memory_uri, ctx=ctx) == "Updated preference"
    assert await service.viking_fs.read_file(f"{memory_dir}/.overview.md", ctx=ctx)
    with pytest.raises(NotFoundError):
        await service.viking_fs.read_file(f"{memory_dir}/.abstract.md", ctx=ctx)


@pytest.mark.asyncio
async def test_write_denies_foreign_user_memory_space(service):
    owner_ctx = RequestContext(user=service.user, role=Role.USER)
    memory_uri = (
        f"viking://user/{owner_ctx.user.user_space_name()}/memories/preferences/private-note.md"
    )
    await service.viking_fs.write_file(memory_uri, "Owner note", ctx=owner_ctx)

    foreign_ctx = RequestContext(
        user=UserIdentifier(owner_ctx.account_id, "other_user"),
        role=Role.USER,
    )

    with pytest.raises(NotFoundError):
        await service.fs.write(
            memory_uri,
            content="Intruder update",
            ctx=foreign_ctx,
        )


@pytest.mark.asyncio
async def test_memory_replace_preserves_metadata(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_uri = f"viking://user/{ctx.user.user_space_name()}/memories/preferences/theme.md"
    metadata = {
        "tags": ["ui", "preference"],
        "created_at": "2026-04-01T10:00:00",
        "updated_at": "2026-04-01T10:05:00",
        "fields": {"topic": "theme"},
    }
    original_mf = MemoryFile(content="Original preference", extra_fields=metadata)
    full_content = MemoryFileUtils.write(original_mf)
    expected_mf = MemoryFileUtils.read(full_content)
    await service.viking_fs.write_file(memory_uri, full_content, ctx=ctx)

    await service.fs.write(
        memory_uri,
        content="Updated preference",
        ctx=ctx,
        mode="replace",
    )

    stored = await service.viking_fs.read_file(memory_uri, ctx=ctx)
    stored_result = MemoryFileUtils.read(stored)

    assert stored_result.content == "Updated preference"
    assert stored_result.extra_fields == expected_mf.extra_fields


@pytest.mark.asyncio
async def test_memory_append_preserves_metadata(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_uri = f"viking://user/{ctx.user.user_space_name()}/memories/preferences/theme.md"
    metadata = {
        "tags": ["ui", "preference"],
        "created_at": "2026-04-01T10:00:00",
        "updated_at": "2026-04-01T10:05:00",
        "fields": {"topic": "theme"},
    }
    original_mf = MemoryFile(content="Original preference", extra_fields=metadata)
    full_content = MemoryFileUtils.write(original_mf)
    expected_mf = MemoryFileUtils.read(full_content)
    await service.viking_fs.write_file(memory_uri, full_content, ctx=ctx)

    await service.fs.write(
        memory_uri,
        content="\nUpdated preference",
        ctx=ctx,
        mode="append",
    )

    stored = await service.viking_fs.read_file(memory_uri, ctx=ctx)
    stored_result = MemoryFileUtils.read(stored)

    assert stored_result.content == "Original preference\nUpdated preference"
    assert stored_result.extra_fields == expected_mf.extra_fields


@pytest.mark.asyncio
async def test_memory_write_adds_resource_refs_for_markdown_resource_link(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_uri = f"viking://user/{ctx.user.user_space_name()}/memories/entities/ryoma.md"
    resource_uri = "viking://resources/images/2026/06/10/yueqian_jpeg_1"
    content = f"用户上传了一张[越前龙马]({resource_uri})的照片"
    await service.viking_fs.write_file(memory_uri, "Original", ctx=ctx)

    await service.fs.write(memory_uri, content=content, ctx=ctx, mode="replace")

    stored = await service.viking_fs.read_file(memory_uri, ctx=ctx)
    mf = MemoryFileUtils.read(stored, uri=memory_uri)
    refs = mf.extra_fields["resource_refs"]
    assert mf.content == content
    assert refs[0]["resource_uri"] == resource_uri
    assert refs[0]["source"] == "content.write"
    assert refs[0]["match_text"] == "越前龙马"
    assert mf.links == []


@pytest.mark.parametrize(
    "resource_uri",
    [
        "viking://user/test_user/resources/images/2026/06/10/yueqian_jpeg",
        "viking://user/test_user/peers/fuji/resources/images/2026/06/10/yueqian_jpeg",
    ],
)
@pytest.mark.asyncio
async def test_memory_write_adds_resource_refs_for_user_scoped_resource_links(
    service,
    resource_uri,
):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_uri = f"viking://user/{ctx.user.user_space_name()}/memories/entities/ryoma.md"
    content = f"用户上传了一张[越前龙马]({resource_uri})的照片"
    await service.viking_fs.write_file(memory_uri, "Original", ctx=ctx)

    await service.fs.write(memory_uri, content=content, ctx=ctx, mode="replace")

    stored = await service.viking_fs.read_file(memory_uri, ctx=ctx)
    mf = MemoryFileUtils.read(stored, uri=memory_uri)
    refs = mf.extra_fields["resource_refs"]
    assert mf.content == content
    assert refs[0]["resource_uri"] == resource_uri
    assert refs[0]["source"] == "content.write"
    assert refs[0]["match_text"] == "越前龙马"


@pytest.mark.asyncio
async def test_memory_write_linkifies_bare_resource_uri_previous_sentence(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_uri = f"viking://user/{ctx.user.user_space_name()}/memories/entities/ryoma.md"
    resource_uri = "viking://resources/images/2026/06/10/yueqian_jpeg_1"
    await service.viking_fs.write_file(memory_uri, "Original", ctx=ctx)

    await service.fs.write(
        memory_uri,
        content=f"用户上传了一张越前龙马的照片 {resource_uri}",
        ctx=ctx,
        mode="replace",
    )

    stored = await service.viking_fs.read_file(memory_uri, ctx=ctx)
    mf = MemoryFileUtils.read(stored, uri=memory_uri)
    assert mf.content == f"[用户上传了一张越前龙马的照片]({resource_uri})"
    refs = mf.extra_fields["resource_refs"]
    assert refs[0]["resource_uri"] == resource_uri
    assert refs[0]["source"] == "content.write"
    assert refs[0]["match_text"] == "用户上传了一张越前龙马的照片"
    assert mf.links == []


@pytest.mark.asyncio
async def test_memory_write_linkifies_resource_uri_marker_with_readable_anchor(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_uri = f"viking://user/{ctx.user.user_space_name()}/memories/entities/ryoma.md"
    resource_uri = "viking://resources/images/2026/06/12/yueqian_jpeg"
    await service.viking_fs.write_file(memory_uri, "Original", ctx=ctx)

    await service.fs.write(
        memory_uri,
        content=f"2026-06-12，用户保存了粉丝创作的越前龙马动漫插画资源，资源URI为{resource_uri}。",
        ctx=ctx,
        mode="replace",
    )

    stored = await service.viking_fs.read_file(memory_uri, ctx=ctx)
    mf = MemoryFileUtils.read(stored, uri=memory_uri)
    assert mf.content == f"2026-06-12，[用户保存了粉丝创作的越前龙马动漫插画资源]({resource_uri})。"
    refs = mf.extra_fields["resource_refs"]
    assert refs[0]["resource_uri"] == resource_uri
    assert refs[0]["source"] == "content.write"
    assert refs[0]["match_text"] == "用户保存了粉丝创作的越前龙马动漫插画资源"
    assert mf.links == []


@pytest.mark.asyncio
async def test_memory_write_ignores_resource_uri_in_inline_code(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_uri = f"viking://user/{ctx.user.user_space_name()}/memories/entities/ryoma.md"
    resource_uri = "viking://resources/images/2026/06/10/yueqian_jpeg_1"
    content = f"调试示例：`{resource_uri}`"
    await service.viking_fs.write_file(memory_uri, "Original", ctx=ctx)

    await service.fs.write(memory_uri, content=content, ctx=ctx, mode="replace")

    stored = await service.viking_fs.read_file(memory_uri, ctx=ctx)
    mf = MemoryFileUtils.read(stored, uri=memory_uri)
    assert mf.content == content
    assert "resource_refs" not in mf.extra_fields
    assert mf.links == []


@pytest.mark.asyncio
async def test_memory_create_refreshes_nested_schema_overview(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_dir = f"viking://user/{ctx.user.user_space_name()}/memories/entities/动漫角色"
    memory_uri = f"{memory_dir}/不二周助-link-test.md"

    result = await service.fs.write(
        memory_uri,
        content="用户保存了一张[不二周助](viking://resources/images/2026/06/10/不二周助_jpeg)的照片",
        ctx=ctx,
        mode="create",
        wait=False,
    )

    overview = await service.viking_fs.read_file(f"{memory_dir}/.overview.md", ctx=ctx)
    assert result["root_uri"] == memory_dir
    assert "[不二周助-link-test](./不二周助-link-test.md)" in overview


@pytest.mark.asyncio
async def test_memory_rm_refreshes_nested_schema_overview(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_dir = f"viking://user/{ctx.user.user_space_name()}/memories/entities/动漫角色"
    deleted_uri = f"{memory_dir}/不二周助-delete-test.md"
    kept_uri = f"{memory_dir}/越前龙马-keep-test.md"

    await service.fs.write(
        deleted_uri,
        content="用户保存了一张不二周助的照片",
        ctx=ctx,
        mode="create",
    )
    await service.fs.write(
        kept_uri,
        content="用户保存了一张越前龙马的照片",
        ctx=ctx,
        mode="create",
    )

    before = await service.viking_fs.read_file(f"{memory_dir}/.overview.md", ctx=ctx)
    assert "[不二周助-delete-test](./不二周助-delete-test.md)" in before
    assert "[越前龙马-keep-test](./越前龙马-keep-test.md)" in before

    await service.fs.rm(deleted_uri, ctx=ctx)

    after = await service.viking_fs.read_file(f"{memory_dir}/.overview.md", ctx=ctx)
    assert "不二周助-delete-test" not in after
    assert "[越前龙马-keep-test](./越前龙马-keep-test.md)" in after


class _FakeHandle:
    def __init__(self, handle_id: str):
        self.id = handle_id


class _FakeLockManager:
    def __init__(
        self,
        *,
        acquire_exact_path_result: bool = True,
        acquire_tree_result: bool = True,
    ):
        self.handle = _FakeHandle("lock-1")
        self.acquire_exact_path_result = acquire_exact_path_result
        self.acquire_tree_result = acquire_tree_result
        self.release_calls = []

    def create_handle(self):
        return self.handle

    async def acquire_tree(self, handle, path):
        del handle, path
        return self.acquire_tree_result

    async def acquire_exact_path(self, handle, path):
        del handle, path
        return self.acquire_exact_path_result

    async def release(self, handle):
        self.release_calls.append(handle.id)


class _FakeVikingFS:
    def __init__(self, file_uri: str, root_uri: str):
        self._file_uri = file_uri
        self._root_uri = root_uri
        self.delete_temp_calls = []
        self.write_file_calls = []
        self.rm_calls = []
        self.content = {file_uri: "original"}
        self.vector_store = None
        self.tree_entries = []

    async def stat(self, uri: str, ctx=None):
        del ctx
        if uri == self._file_uri or uri in self.content:
            return {"isDir": False}
        if uri == self._root_uri:
            return {"isDir": True}
        raise AssertionError(f"unexpected stat uri: {uri}")

    def _uri_to_path(self, uri: str, ctx=None):
        del ctx
        return f"/fake/{uri.replace('://', '/').strip('/')}"

    def _ensure_mutable_access(self, uri: str, ctx):
        del uri, ctx

    async def delete_temp(self, temp_uri: str, ctx=None):
        del ctx
        self.delete_temp_calls.append(temp_uri)

    async def read_file(self, uri: str, ctx=None):
        del ctx
        return self.content[uri]

    async def write_file(self, uri: str, content: str, ctx=None, lock_handle=None):
        del ctx
        self.write_file_calls.append((uri, content, lock_handle))
        self.content[uri] = content

    async def rm(self, uri: str, ctx=None, lock_handle=None):
        del ctx, lock_handle
        self.rm_calls.append(uri)
        self.content.pop(uri, None)

    async def tree(
        self,
        uri: str,
        ctx=None,
        output: str = "original",
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        level_limit: int = 3,
        abs_limit: int = 256,
    ):
        del ctx, output, show_all_hidden, node_limit, level_limit, abs_limit
        assert uri == self._root_uri
        return list(self.tree_entries)

    def _get_vector_store(self):
        return self.vector_store


class _FakeSemanticQueue:
    def __init__(self):
        self.messages = []

    async def enqueue(self, msg):
        self.messages.append(msg)
        return "queued-id"


class _FakeQueueManager:
    SEMANTIC = "semantic"

    def __init__(self, queue):
        self.queue = queue

    def get_queue(self, name, allow_create=False):
        del allow_create
        assert name == self.SEMANTIC
        return self.queue


@pytest.mark.asyncio
async def test_resource_write_semantic_refresh_uses_coalesce_key(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    queue = _FakeSemanticQueue()
    coordinator = ContentWriteCoordinator(
        viking_fs=_FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    )

    monkeypatch.setattr(
        "openviking.storage.content_write.get_queue_manager",
        lambda: _FakeQueueManager(queue),
    )

    await coordinator._enqueue_semantic_refresh(
        root_uri=root_uri,
        changed_uri=file_uri,
        context_type="resource",
        ctx=ctx,
    )

    assert len(queue.messages) == 1
    assert queue.messages[0].coalesce_key == (
        "resource|default|default|default|viking://resources/demo"
    )
    assert queue.messages[0].lock_handoff is None


@pytest.mark.asyncio
async def test_write_timeout_after_enqueue_releases_resource_lock(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: lock_manager,
    )

    async def _fake_enqueue_semantic_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_request(*, telemetry_id, timeout):
        del telemetry_id
        raise DeadlineExceededError("queue processing", timeout)

    monkeypatch.setattr(coordinator, "_enqueue_semantic_refresh", _fake_enqueue_semantic_refresh)
    monkeypatch.setattr(coordinator, "_wait_for_request", _fake_wait_for_request)

    with pytest.raises(DeadlineExceededError):
        await coordinator.write(
            uri=file_uri,
            content="updated",
            ctx=ctx,
            wait=True,
        )

    assert lock_manager.release_calls == ["lock-1"]
    assert viking_fs.delete_temp_calls == []
    assert viking_fs.content[file_uri] == "updated"


@pytest.mark.asyncio
async def test_resource_write_lock_conflict_raises_resource_busy(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager(acquire_exact_path_result=False)

    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: lock_manager,
    )

    with pytest.raises(ResourceBusyError) as exc_info:
        await coordinator.write(
            uri=file_uri,
            content="updated",
            ctx=ctx,
        )

    assert exc_info.value.uri == file_uri
    assert lock_manager.release_calls == ["lock-1"]
    assert viking_fs.content[file_uri] == "original"


@pytest.mark.asyncio
async def test_memory_write_lock_conflict_raises_resource_busy(monkeypatch):
    file_uri = "viking://user/default/memories/preferences/theme.md"
    root_uri = "viking://user/default/memories/preferences"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager(acquire_exact_path_result=False)

    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: lock_manager,
    )

    with pytest.raises(ResourceBusyError) as exc_info:
        await coordinator.write(
            uri=file_uri,
            content="updated",
            ctx=ctx,
        )

    assert exc_info.value.uri == file_uri
    assert lock_manager.release_calls == ["lock-1"]
    assert viking_fs.content[file_uri] == "original"


@pytest.mark.asyncio
async def test_write_direct_reuses_outer_lock_handle_for_viking_fs(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    async def _fake_enqueue_semantic_refresh(**kwargs):
        del kwargs
        return None

    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: lock_manager,
    )
    monkeypatch.setattr(coordinator, "_enqueue_semantic_refresh", _fake_enqueue_semantic_refresh)

    result = await coordinator._write_direct_with_refresh(
        uri=file_uri,
        root_uri=root_uri,
        content="updated",
        mode="replace",
        context_type="resource",
        wait=False,
        timeout=None,
        ctx=ctx,
        written_bytes=len("updated".encode("utf-8")),
        telemetry_id="",
    )

    assert result["uri"] == file_uri
    assert viking_fs.write_file_calls == [(file_uri, "updated", lock_manager.handle)]


@pytest.mark.asyncio
async def test_resource_write_updates_target_and_queues_refresh_before_return(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()
    captured_enqueue = {}

    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: lock_manager,
    )

    async def _fake_enqueue_semantic_refresh(**kwargs):
        captured_enqueue.update(kwargs)

    monkeypatch.setattr(coordinator, "_enqueue_semantic_refresh", _fake_enqueue_semantic_refresh)

    result = await coordinator.write(
        uri=file_uri,
        content="updated",
        ctx=ctx,
        mode="replace",
        wait=False,
    )

    assert viking_fs.content[file_uri] == "updated"
    assert result["content_updated"] is True
    assert result["semantic_status"] == "queued"
    assert result["vector_status"] == "queued"
    assert captured_enqueue["root_uri"] == root_uri
    assert captured_enqueue["changed_uri"] == file_uri
    assert captured_enqueue["change_type"] == "modified"
    assert viking_fs.delete_temp_calls == []
    assert lock_manager.release_calls == ["lock-1"]


@pytest.mark.asyncio
async def test_resource_write_rolls_back_replace_when_enqueue_fails(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: lock_manager,
    )

    async def _fail_enqueue(**kwargs):
        del kwargs
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(coordinator, "_enqueue_semantic_refresh", _fail_enqueue)

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await coordinator.write(
            uri=file_uri,
            content="updated",
            ctx=ctx,
            mode="replace",
        )

    assert viking_fs.content[file_uri] == "original"
    assert viking_fs.write_file_calls == [
        (file_uri, "updated", lock_manager.handle),
        (file_uri, "original", lock_manager.handle),
    ]
    assert lock_manager.release_calls == ["lock-1"]


@pytest.mark.asyncio
async def test_resource_write_rolls_back_create_when_enqueue_fails(monkeypatch):
    file_uri = "viking://resources/demo/new.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=False)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: lock_manager,
    )

    async def _fail_enqueue(**kwargs):
        del kwargs
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(coordinator, "_enqueue_semantic_refresh", _fail_enqueue)

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await coordinator.write(
            uri=file_uri,
            content="new content",
            ctx=ctx,
            mode="create",
        )

    assert file_uri not in viking_fs.content
    assert viking_fs.rm_calls == [file_uri]
    assert lock_manager.release_calls == ["lock-1"]


@pytest.mark.asyncio
async def test_memory_write_wait_skips_semantic_queue_and_releases_write_lock(monkeypatch):
    file_uri = "viking://user/default/memories/preferences/theme.md"
    root_uri = "viking://user/default/memories/preferences"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: lock_manager,
    )

    async def _fake_write_in_place(uri, content, *, mode, ctx, lock_handle=None):
        del uri, content, mode, ctx, lock_handle
        return None

    async def _fail_wait_for_request(*, telemetry_id, timeout):
        del telemetry_id, timeout
        raise AssertionError("memory write should not wait for semantic refresh")

    async def _fake_refresh_schema_overview(**kwargs):
        del kwargs
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_wait_for_request", _fail_wait_for_request)
    monkeypatch.setattr(
        "openviking.storage.content_write.MemoryUpdater.refresh_schema_overview",
        _fake_refresh_schema_overview,
    )

    result = await coordinator.write(
        uri=file_uri,
        content="updated",
        ctx=ctx,
        wait=True,
    )

    assert lock_manager.release_calls == ["lock-1"]
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "skipped"
    assert result["overview_status"] == "complete"
    assert result["queue_status"] is None


# Create-mode test helpers


class _FakeVikingFSForCreate:
    """Variant of _FakeVikingFS that supports 'file doesn't exist' scenarios."""

    def __init__(
        self,
        file_uri: str,
        root_uri: str,
        file_exists: bool = True,
        existing_dirs: set[str] | None = None,
    ):
        self._file_uri = file_uri
        self._root_uri = root_uri
        self._file_exists = file_exists
        self.delete_temp_calls = []
        self.write_file_calls = []
        self.rm_calls = []
        self.content = {}
        self.existing_dirs = set({root_uri} if existing_dirs is None else existing_dirs)

    async def stat(self, uri: str, ctx=None):
        del ctx
        if uri == self._file_uri:
            if self._file_exists:
                return {"isDir": False}
            raise NotFoundError(uri, "file")
        if uri in self.existing_dirs:
            return {"isDir": True}
        # Parent directories should exist for creation
        if uri != self._root_uri and uri.startswith(self._root_uri) and uri != self._file_uri:
            return {"isDir": True}
        raise NotFoundError(uri, "path")

    def _uri_to_path(self, uri: str, ctx=None):
        del ctx
        return f"/fake/{uri.replace('://', '/').strip('/')}"

    def _ensure_mutable_access(self, uri: str, ctx):
        del uri, ctx

    async def delete_temp(self, temp_uri: str, ctx=None):
        del ctx
        self.delete_temp_calls.append(temp_uri)

    async def write_file(self, uri: str, content: str, *, ctx=None, lock_handle=None):
        del ctx
        self.write_file_calls.append((uri, content, lock_handle))
        self.content[uri] = content
        parent = uri.rsplit("/", 1)[0]
        while parent.startswith("viking://") and parent not in self.existing_dirs:
            self.existing_dirs.add(parent)
            if "/" not in parent.removeprefix("viking://"):
                break
            parent = parent.rsplit("/", 1)[0]

    async def rm(self, uri: str, *, ctx=None, lock_handle=None):
        del ctx, lock_handle
        self.rm_calls.append(uri)
        self.content.pop(uri, None)


# Create-mode tests


@pytest.mark.asyncio
async def test_create_mode_new_file_success(monkeypatch):
    file_uri = "viking://user/default/memories/new_file.md"
    root_uri = "viking://user/default/memories"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=False)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr("openviking.storage.content_write.get_lock_manager", lambda: lock_manager)

    write_calls = []

    async def _fake_write_in_place(uri, content, *, mode, ctx, lock_handle=None):
        del mode, ctx, lock_handle
        write_calls.append((uri, content))
        return content

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    result = await coordinator.write(
        uri=file_uri, content="new content", mode="create", ctx=ctx, wait=True
    )

    assert result["mode"] == "create"
    assert write_calls == [(file_uri, "new content")]


@pytest.mark.asyncio
async def test_create_mode_canonicalizes_user_shorthand_memory_uri(monkeypatch):
    input_uri = "viking://user/memories/new_file.md"
    canonical_uri = "viking://user/default/memories/new_file.md"
    root_uri = "viking://user/default/memories"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(
        file_uri=canonical_uri,
        root_uri=root_uri,
        file_exists=False,
    )
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr("openviking.storage.content_write.get_lock_manager", lambda: lock_manager)

    write_calls = []
    refresh_calls = []

    async def _fake_write_in_place(uri, content, *, mode, ctx, lock_handle=None):
        del mode, ctx, lock_handle
        write_calls.append((uri, content))
        return content

    async def _fake_refresh_schema_overview(**kwargs):
        refresh_calls.append(kwargs)
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)
    monkeypatch.setattr(
        "openviking.storage.content_write.MemoryUpdater.refresh_schema_overview",
        _fake_refresh_schema_overview,
    )

    result = await coordinator.write(
        uri=input_uri, content="new content", mode="create", ctx=ctx, wait=True
    )

    assert result["uri"] == canonical_uri
    assert result["root_uri"] == root_uri
    assert result["context_type"] == "memory"
    assert write_calls == [(canonical_uri, "new content")]
    assert refresh_calls[0]["directory_uri"] == root_uri


@pytest.mark.asyncio
async def test_create_mode_existing_file_raises_409(monkeypatch):
    file_uri = "viking://user/default/memories/existing.md"
    root_uri = "viking://user/default/memories"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=True)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)

    async def _fake_write_in_place(uri, content, *, mode, ctx, lock_handle=None):
        del uri, content, mode, ctx, lock_handle
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    with pytest.raises(AlreadyExistsError):
        await coordinator.write(uri=file_uri, content="content", mode="create", ctx=ctx, wait=True)


@pytest.mark.asyncio
async def test_create_mode_invalid_extension_raises_400(monkeypatch):
    file_uri = "viking://user/default/memories/test.exe"
    root_uri = "viking://user/default/memories"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=False)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)

    async def _fake_write_in_place(uri, content, *, mode, ctx, lock_handle=None):
        del uri, content, mode, ctx, lock_handle
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    with pytest.raises(InvalidArgumentError):
        await coordinator.write(uri=file_uri, content="content", mode="create", ctx=ctx, wait=True)


@pytest.mark.asyncio
async def test_create_mode_parent_dirs_auto_created(monkeypatch):
    file_uri = "viking://user/default/memories/new_subdir/test.md"
    root_uri = "viking://user/default/memories"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=False)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr("openviking.storage.content_write.get_lock_manager", lambda: lock_manager)

    write_calls = []

    async def _fake_write_in_place(uri, content, *, mode, ctx, lock_handle=None):
        del mode, ctx, lock_handle
        write_calls.append((uri, content))
        return content

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    result = await coordinator.write(
        uri=file_uri, content="nested content", mode="create", ctx=ctx, wait=True
    )

    assert result["mode"] == "create"
    assert write_calls == [(file_uri, "nested content")]


@pytest.mark.asyncio
async def test_create_mode_valid_extensions_pass(monkeypatch):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)

    # Test a representative set of valid extensions
    valid_extensions = [".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".ts"]

    for ext in valid_extensions:
        file_uri = f"viking://user/default/memories/test{ext}"
        root_uri = "viking://user/default/memories"
        viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=False)
        coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
        lock_manager = _FakeLockManager()

        _captured_lock = lock_manager

        monkeypatch.setattr(
            "openviking.storage.content_write.get_lock_manager", lambda _l=_captured_lock: _l
        )

        async def _fake_write_in_place(uri, content, *, mode, ctx, lock_handle=None):
            del uri, mode, ctx, lock_handle
            return content

        async def _fake_wait_for_queues(*, timeout):
            del timeout
            return None

        monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
        monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

        result = await coordinator.write(
            uri=file_uri, content="content", mode="create", ctx=ctx, wait=True
        )
        assert result["mode"] == "create"


@pytest.mark.asyncio
async def test_create_mode_memory_scope(monkeypatch):
    file_uri = "viking://user/default/memories/test.md"
    root_uri = "viking://user/default/memories"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=False)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr("openviking.storage.content_write.get_lock_manager", lambda: lock_manager)

    async def _fake_write_in_place(uri, content, *, mode, ctx, lock_handle=None):
        del uri, mode, ctx, lock_handle
        return content

    refresh_calls = []

    async def _fake_refresh_schema_overview(**kwargs):
        refresh_calls.append(kwargs)
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)
    monkeypatch.setattr(
        "openviking.storage.content_write.MemoryUpdater.refresh_schema_overview",
        _fake_refresh_schema_overview,
    )

    result = await coordinator.write(
        uri=file_uri, content="content", mode="create", ctx=ctx, wait=True
    )
    assert result["context_type"] == "memory"
    assert refresh_calls[0]["directory_uri"] == root_uri


@pytest.mark.asyncio
async def test_create_mode_resource_scope(monkeypatch):
    file_uri = "viking://resources/demo/test.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=False)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr("openviking.storage.content_write.get_lock_manager", lambda: lock_manager)

    async def _fake_enqueue_semantic_refresh(**kwargs):
        # Verify resource-scope URIs take the resource write path
        assert kwargs["root_uri"] == root_uri
        assert kwargs["changed_uri"] == file_uri
        assert kwargs["context_type"] == "resource"
        assert kwargs["change_type"] == "added"
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_enqueue_semantic_refresh", _fake_enqueue_semantic_refresh)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    result = await coordinator.write(
        uri=file_uri, content="content", mode="create", ctx=ctx, wait=True
    )
    assert result["context_type"] == "resource"
    assert viking_fs.content[file_uri] == "content"


class _AnyDirVikingFS:
    """Stats the given file uri as a file and every other uri as a directory."""

    def __init__(self, file_uri: str):
        self._file_uri = file_uri

    async def stat(self, uri: str, ctx=None):
        del ctx
        return {"isDir": uri != self._file_uri}


@pytest.mark.asyncio
async def test_resource_write_anchors_nested_file_to_direct_parent():
    """A resource content write anchors the semantic refresh at the written file's
    direct parent directory (anchor_to_parent=True), so the changed file is a direct
    child of the DAG run root: its own L2 vector and the parent's L0/L1 are generated
    from a single-directory run instead of a recursive walk of the whole project subtree.
    set_tags keeps the project-root collapse (the default), which the derived
    ``.abstract.md`` sidecar mapping relies on."""
    file_uri = "viking://resources/dir1/sub_dir/sub_dir_1/test.md"
    parent_uri = "viking://resources/dir1/sub_dir/sub_dir_1"
    project_root = "viking://resources/dir1"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    coordinator = ContentWriteCoordinator(viking_fs=_AnyDirVikingFS(file_uri))

    write_anchor = await coordinator._resolve_root_uri(file_uri, ctx=ctx, anchor_to_parent=True)
    set_tags_anchor = await coordinator._resolve_root_uri(file_uri, ctx=ctx)

    assert write_anchor == parent_uri
    assert set_tags_anchor == project_root


@pytest.mark.asyncio
async def test_create_mode_regression_replace_unchanged(monkeypatch):
    file_uri = "viking://user/default/memories/theme.md"
    root_uri = "viking://user/default/memories"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=True)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr("openviking.storage.content_write.get_lock_manager", lambda: lock_manager)

    async def _fake_write_in_place(uri, content, *, mode, ctx, lock_handle=None):
        # Verify mode="replace" still works
        assert mode == "replace"
        del uri, content, ctx, lock_handle
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    result = await coordinator.write(
        uri=file_uri, content="updated", ctx=ctx, mode="replace", wait=True
    )

    assert result["mode"] == "replace"


@pytest.mark.asyncio
async def test_create_mode_regression_append_unchanged(monkeypatch):
    file_uri = "viking://user/default/memories/theme.md"
    root_uri = "viking://user/default/memories"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=True)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr("openviking.storage.content_write.get_lock_manager", lambda: lock_manager)

    async def _fake_write_in_place(uri, content, *, mode, ctx, lock_handle=None):
        # Verify mode="append" still works
        assert mode == "append"
        del uri, content, ctx, lock_handle
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    result = await coordinator.write(
        uri=file_uri, content="appended", ctx=ctx, mode="append", wait=True
    )

    assert result["mode"] == "append"


@pytest.mark.asyncio
async def test_set_tags_updates_vector_record(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def update_search_tags(self, uri: str, tags, *, mode: str, levels=None, ctx=None):
            del ctx
            if levels is None:
                self.update_calls.append((uri, list(tags), mode))
                return [{"uri": uri}]
            self.update_calls.append((uri, list(tags), mode, list(levels)))
            return []

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    result = await coordinator.set_tags(
        uri=file_uri,
        tags=["Env=Prod", " env=prod "],
        ctx=ctx,
    )

    assert result["tags"] == ["env=prod"]
    assert result["tags_updated"] is True
    assert "semantic_status" not in result
    assert "vector_status" not in result
    assert "queue_status" not in result
    assert fake_store.update_calls == [(file_uri, ["env=prod"], "replace")]


@pytest.mark.asyncio
async def test_set_tags_uses_store_update_api_without_fetch(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def fetch_by_uri(self, uri: str, ctx=None):
            del uri, ctx
            raise AssertionError("set_tags should not depend on fetch_by_uri")

        async def update_search_tags(self, uri: str, tags, *, mode: str, levels=None, ctx=None):
            del ctx
            assert levels is None
            self.update_calls.append((uri, list(tags), mode))
            return [{"uri": uri}]

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    result = await coordinator.set_tags(
        uri=file_uri,
        tags=["Env=Prod"],
        mode="replace",
        ctx=ctx,
    )

    assert result["success_count"] == 1
    assert result["skipped_count"] == 0
    assert result["failed_count"] == 0
    assert result["root_uri"] == root_uri
    assert fake_store.update_calls == [(file_uri, ["env=prod"], "replace")]


@pytest.mark.asyncio
async def test_set_tags_user_scope_resource_leaf_returns_parent_root_uri(monkeypatch):
    file_uri = "viking://user/default/resources/demo/doc.md"
    root_uri = "viking://user/default/resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def update_search_tags(self, uri: str, tags, *, mode: str, levels=None, ctx=None):
            del ctx
            assert levels is None
            self.update_calls.append((uri, list(tags), mode))
            return [{"uri": uri}]

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store

    result = await coordinator.set_tags(
        uri=file_uri,
        tags=["team=search"],
        mode="replace",
        ctx=ctx,
    )

    assert result["success_count"] == 1
    assert result["root_uri"] == root_uri
    assert result["context_type"] == "resource"
    assert fake_store.update_calls == [(file_uri, ["team=search"], "replace")]


@pytest.mark.asyncio
async def test_set_tags_derived_abstract_maps_to_parent_level_zero(monkeypatch):
    file_uri = "viking://resources/demo/doc.md/.abstract.md"
    root_uri = "viking://resources/demo"
    updated_uri = "viking://resources/demo/doc.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def update_search_tags(self, uri: str, tags, *, mode: str, levels=None, ctx=None):
            del ctx
            self.update_calls.append((uri, list(tags), mode, levels))
            return [{"uri": uri}]

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store

    result = await coordinator.set_tags(
        uri=file_uri,
        tags=["team=test"],
        mode="replace",
        ctx=ctx,
    )

    assert result["success_count"] == 1
    assert result["skipped_count"] == 0
    assert result["updated_uris"] == [updated_uri]
    assert fake_store.update_calls == [(updated_uri, ["team=test"], "replace", [0])]


@pytest.mark.asyncio
async def test_set_tags_append_merges_existing_tags(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def fetch_by_uri(self, uri: str, ctx=None):
            del uri, ctx
            raise AssertionError("append should be handled inside store update API")

        async def update_search_tags(self, uri: str, tags, *, mode: str, levels=None, ctx=None):
            del ctx
            assert levels is None
            self.update_calls.append((uri, list(tags), mode))
            return [{"uri": uri}]

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    result = await coordinator.set_tags(
        uri=file_uri,
        tags=["Env=Prod", " team=search "],
        mode="append",
        ctx=ctx,
    )

    assert result["mode"] == "append"
    assert "recursive" not in result
    assert result["success_count"] == 1
    assert result["skipped_count"] == 0
    assert result["failed_count"] == 0
    assert fake_store.update_calls == [(file_uri, ["env=prod", "team=search"], "append")]


@pytest.mark.asyncio
async def test_set_tags_rejects_non_kv_tags(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        async def update_search_tags(self, uri: str, tags, *, mode: str, levels=None, ctx=None):
            raise AssertionError("invalid tags must fail before store update")

    fake_vfs.vector_store = _FakeVectorStore()
    with pytest.raises(InvalidArgumentError, match="k=v"):
        await coordinator.set_tags(uri=file_uri, tags=["project-a"], ctx=ctx)


@pytest.mark.asyncio
async def test_set_tags_recursive_directory_updates_descendants(monkeypatch):
    root_uri = "viking://resources/demo"
    file_uri = f"{root_uri}/doc.md"
    abstract_uri = f"{root_uri}/.abstract.md"
    overview_uri = f"{root_uri}/.overview.md"
    nested_dir_uri = f"{root_uri}/nested"
    nested_abstract_uri = f"{nested_dir_uri}/.abstract.md"
    nested_overview_uri = f"{nested_dir_uri}/.overview.md"
    nested_file_uri = f"{nested_dir_uri}/note.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    fake_vfs.tree_entries = [
        {"uri": abstract_uri, "isDir": False},
        {"uri": overview_uri, "isDir": False},
        {"uri": file_uri, "isDir": False},
        {"uri": nested_dir_uri, "isDir": True},
        {"uri": nested_abstract_uri, "isDir": False},
        {"uri": nested_overview_uri, "isDir": False},
        {"uri": nested_file_uri, "isDir": False},
    ]
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []
            self.directory_update_calls = []

        async def fetch_by_uri(self, uri: str, ctx=None):
            del uri, ctx
            raise AssertionError("recursive tag updates should use store update API")

        async def update_search_tags(self, uri: str, tags, *, mode: str, levels=None, ctx=None):
            del ctx
            if levels is None:
                self.update_calls.append((uri, list(tags), mode))
                return [{"uri": uri}]
            self.directory_update_calls.append((uri, list(tags), mode, list(levels)))
            return [{"uri": uri}]

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    result = await coordinator.set_tags(
        uri=root_uri,
        tags=["env=prod"],
        mode="append",
        recursive=True,
        ctx=ctx,
    )

    assert result["mode"] == "append"
    assert "recursive" not in result
    assert result["success_count"] == 4
    assert result["skipped_count"] == 0
    assert result["failed_count"] == 0
    assert set(result["updated_uris"]) == {
        root_uri,
        file_uri,
        nested_dir_uri,
        nested_file_uri,
    }
    assert sorted(fake_store.update_calls) == sorted(
        [
            (file_uri, ["env=prod"], "append"),
            (nested_file_uri, ["env=prod"], "append"),
        ]
    )
    assert sorted(fake_store.directory_update_calls) == sorted(
        [
            (root_uri, ["env=prod"], "append", [0, 1]),
            (nested_dir_uri, ["env=prod"], "append", [0, 1]),
        ]
    )
    assert nested_dir_uri in result["updated_uris"]


@pytest.mark.asyncio
async def test_set_tags_recursive_directory_all_missing_vector_records_returns_zero_counts(
    monkeypatch,
):
    root_uri = "viking://resources/demo"
    file_uri = f"{root_uri}/doc.md"
    abstract_uri = f"{root_uri}/.abstract.md"
    overview_uri = f"{root_uri}/.overview.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    fake_vfs.tree_entries = [
        {"uri": abstract_uri, "isDir": False},
        {"uri": overview_uri, "isDir": False},
        {"uri": file_uri, "isDir": False},
    ]
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def update_search_tags(self, uri: str, tags, *, mode: str, levels=None, ctx=None):
            del ctx
            if levels is None:
                self.update_calls.append((uri, list(tags), mode))
                return []
            self.update_calls.append((uri, list(tags), mode, list(levels)))
            return []

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    result = await coordinator.set_tags(
        uri=root_uri,
        tags=["env=prod"],
        mode="replace",
        recursive=True,
        ctx=ctx,
    )

    assert result["success_count"] == 0
    assert result["skipped_count"] == 3
    assert result["failed_count"] == 0
    assert result["updated_uris"] == []
    assert result["tags_updated"] is False


@pytest.mark.asyncio
async def test_set_tags_non_recursive_directory_all_missing_vector_records_returns_zero_counts(
    monkeypatch,
):
    root_uri = "viking://resources/demo"
    file_uri = f"{root_uri}/doc.md"
    abstract_uri = f"{root_uri}/.abstract.md"
    overview_uri = f"{root_uri}/.overview.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    fake_vfs.content[abstract_uri] = "abstract"
    fake_vfs.content[overview_uri] = "overview"
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def update_search_tags(self, uri: str, tags, *, mode: str, levels=None, ctx=None):
            del ctx
            if levels is None:
                self.update_calls.append((uri, list(tags), mode))
                return []
            self.update_calls.append((uri, list(tags), mode, list(levels)))
            return []

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    result = await coordinator.set_tags(
        uri=root_uri,
        tags=["env=prod"],
        mode="replace",
        recursive=False,
        ctx=ctx,
    )

    assert result["success_count"] == 0
    assert result["skipped_count"] == 1
    assert result["failed_count"] == 0
    assert result["updated_uris"] == []
    assert result["tags_updated"] is False
    assert fake_store.update_calls == [(root_uri, ["env=prod"], "replace", [0, 1])]


@pytest.mark.asyncio
async def test_set_tags_single_uri_missing_vector_record_returns_zero_counts(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def update_search_tags(self, uri: str, tags, *, mode: str, ctx=None):
            del ctx
            self.update_calls.append((uri, list(tags), mode))
            return False

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store

    result = await coordinator.set_tags(
        uri=file_uri,
        tags=["env=prod"],
        mode="replace",
        ctx=ctx,
    )

    assert result["success_count"] == 0
    assert result["skipped_count"] == 1
    assert result["failed_count"] == 0
    assert result["updated_uris"] == []
    assert result["root_uri"] == root_uri
    assert result["tags_updated"] is False


@pytest.mark.asyncio
async def test_set_tags_does_not_return_write_queue_fields(monkeypatch):
    file_uri = "viking://resources/demo/doc.md"
    root_uri = "viking://resources/demo"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    fake_vfs = _FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    coordinator = ContentWriteCoordinator(viking_fs=fake_vfs)

    class _FakeVectorStore:
        async def update_search_tags(self, uri: str, tags, *, mode: str, ctx=None):
            del ctx
            assert uri == file_uri
            assert list(tags) == ["env=prod"]
            assert mode == "replace"
            return True

    fake_vfs.vector_store = _FakeVectorStore()

    result = await coordinator.set_tags(
        uri=file_uri,
        tags=["env=prod"],
        mode="replace",
        ctx=ctx,
    )

    assert result["tags_updated"] is True
    assert "semantic_status" not in result
    assert "vector_status" not in result
    assert "queue_status" not in result
