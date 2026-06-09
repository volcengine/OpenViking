# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Service-level tests for content write coordination."""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils import MemoryFileUtils
from openviking.storage.content_write import ContentWriteCoordinator
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
    assert await service.viking_fs.read_file(memory_uri, ctx=ctx) == "Updated preference"
    assert await service.viking_fs.read_file(f"{memory_dir}/.overview.md", ctx=ctx)
    assert await service.viking_fs.read_file(f"{memory_dir}/.abstract.md", ctx=ctx)


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


class _FakeHandle:
    def __init__(self, handle_id: str):
        self.id = handle_id


class _FakeLockManager:
    def __init__(self):
        self.handle = _FakeHandle("lock-1")
        self.release_calls = []

    def create_handle(self):
        return self.handle

    async def acquire_tree(self, handle, path):
        del handle, path
        return True

    async def acquire_exact_path(self, handle, path):
        del handle, path
        return True

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

    async def delete_temp(self, temp_uri: str, ctx=None):
        del ctx
        self.delete_temp_calls.append(temp_uri)

    async def read_file(self, uri: str, ctx=None):
        del ctx
        return self.content[uri]

    async def write_file(self, uri: str, content: str, ctx=None):
        del ctx
        self.write_file_calls.append((uri, content))
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
async def test_memory_write_timeout_after_enqueue_releases_write_lock(monkeypatch):
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

    async def _fake_write_in_place(uri, content, *, mode, ctx):
        del uri, content, mode, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_request(*, telemetry_id, timeout):
        del telemetry_id
        raise DeadlineExceededError("queue processing", timeout)

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
    monkeypatch.setattr(coordinator, "_wait_for_request", _fake_wait_for_request)

    with pytest.raises(DeadlineExceededError):
        await coordinator.write(
            uri=file_uri,
            content="updated",
            ctx=ctx,
            wait=True,
        )

    assert lock_manager.release_calls == ["lock-1"]


# Create-mode test helpers


class _FakeVikingFSForCreate:
    """Variant of _FakeVikingFS that supports 'file doesn't exist' scenarios."""

    def __init__(self, file_uri: str, root_uri: str, file_exists: bool = True):
        self._file_uri = file_uri
        self._root_uri = root_uri
        self._file_exists = file_exists
        self.delete_temp_calls = []
        self.write_file_calls = []
        self.rm_calls = []
        self.content = {}

    async def stat(self, uri: str, ctx=None):
        del ctx
        if uri == self._file_uri:
            if self._file_exists:
                return {"isDir": False}
            raise NotFoundError(uri, "file")
        if uri == self._root_uri:
            return {"isDir": True}
        # Parent directories should exist for creation
        if uri.startswith(self._root_uri) and uri != self._file_uri:
            return {"isDir": True}
        raise NotFoundError(uri, "path")

    def _uri_to_path(self, uri: str, ctx=None):
        del ctx
        return f"/fake/{uri.replace('://', '/').strip('/')}"

    async def delete_temp(self, temp_uri: str, ctx=None):
        del ctx
        self.delete_temp_calls.append(temp_uri)

    async def write_file(self, uri: str, content: str, *, ctx=None):
        del ctx
        self.write_file_calls.append((uri, content))
        self.content[uri] = content

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

    async def _fake_write_in_place(uri, content, *, mode, ctx):
        del mode, ctx
        write_calls.append((uri, content))
        return content

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
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

    async def _fake_write_in_place(uri, content, *, mode, ctx):
        del mode, ctx
        write_calls.append((uri, content))
        return content

    async def _fake_enqueue_memory_refresh(**kwargs):
        refresh_calls.append(kwargs)
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    result = await coordinator.write(
        uri=input_uri, content="new content", mode="create", ctx=ctx, wait=True
    )

    assert result["uri"] == canonical_uri
    assert result["root_uri"] == root_uri
    assert result["context_type"] == "memory"
    assert write_calls == [(canonical_uri, "new content")]
    assert refresh_calls[0]["root_uri"] == root_uri
    assert refresh_calls[0]["modified_uri"] == canonical_uri


@pytest.mark.asyncio
async def test_create_mode_existing_file_raises_409(monkeypatch):
    file_uri = "viking://user/default/memories/existing.md"
    root_uri = "viking://user/default/memories"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=True)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)

    async def _fake_write_in_place(uri, content, *, mode, ctx):
        del uri, content, mode, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
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

    async def _fake_write_in_place(uri, content, *, mode, ctx):
        del uri, content, mode, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
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

    async def _fake_write_in_place(uri, content, *, mode, ctx):
        del mode, ctx
        write_calls.append((uri, content))
        return content

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
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

        async def _fake_write_in_place(uri, content, *, mode, ctx):
            del uri, mode, ctx
            return content

        async def _fake_enqueue_memory_refresh(**kwargs):
            del kwargs
            return None

        async def _fake_wait_for_queues(*, timeout):
            del timeout
            return None

        monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
        monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
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

    async def _fake_write_in_place(uri, content, *, mode, ctx):
        del uri, mode, ctx
        return content

    refresh_calls = []

    async def _fake_enqueue_memory_refresh(**kwargs):
        refresh_calls.append(kwargs)
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    result = await coordinator.write(
        uri=file_uri, content="content", mode="create", ctx=ctx, wait=True
    )
    assert result["context_type"] == "memory"
    assert refresh_calls[0]["root_uri"] == root_uri
    assert refresh_calls[0]["modified_uri"] == file_uri


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


@pytest.mark.asyncio
async def test_create_mode_regression_replace_unchanged(monkeypatch):
    file_uri = "viking://user/default/memories/theme.md"
    root_uri = "viking://user/default/memories"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    viking_fs = _FakeVikingFSForCreate(file_uri=file_uri, root_uri=root_uri, file_exists=True)
    coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
    lock_manager = _FakeLockManager()

    monkeypatch.setattr("openviking.storage.content_write.get_lock_manager", lambda: lock_manager)

    async def _fake_write_in_place(uri, content, *, mode, ctx):
        # Verify mode="replace" still works
        assert mode == "replace"
        del uri, content, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
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

    async def _fake_write_in_place(uri, content, *, mode, ctx):
        # Verify mode="append" still works
        assert mode == "append"
        del uri, content, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
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

        async def update_search_tags(self, uri: str, tags, *, mode: str, ctx=None):
            del ctx
            self.update_calls.append((uri, list(tags), mode))
            return True

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    monkeypatch.setattr(
        "openviking.storage.content_write.get_queue_manager",
        lambda: _FakeQueueManager(_FakeSemanticQueue()),
    )

    result = await coordinator.set_tags(
        uri=file_uri,
        tags=["Env=Prod", " env=prod "],
        ctx=ctx,
    )

    assert result["tags"] == ["env=prod"]
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

        async def update_search_tags(self, uri: str, tags, *, mode: str, ctx=None):
            del ctx
            self.update_calls.append((uri, list(tags), mode))
            return True

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    monkeypatch.setattr(
        "openviking.storage.content_write.get_queue_manager",
        lambda: _FakeQueueManager(_FakeSemanticQueue()),
    )

    result = await coordinator.set_tags(
        uri=file_uri,
        tags=["Env=Prod"],
        mode="replace",
        ctx=ctx,
    )

    assert result["success_count"] == 1
    assert result["skipped_count"] == 0
    assert result["failed_count"] == 0
    assert fake_store.update_calls == [(file_uri, ["env=prod"], "replace")]


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

        async def update_search_tags(self, uri: str, tags, *, mode: str, ctx=None):
            del ctx
            self.update_calls.append((uri, list(tags), mode))
            return True

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    monkeypatch.setattr(
        "openviking.storage.content_write.get_queue_manager",
        lambda: _FakeQueueManager(_FakeSemanticQueue()),
    )

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
        async def update_search_tags(self, uri: str, tags, *, mode: str, ctx=None):
            raise AssertionError("invalid tags must fail before store update")

    fake_vfs.vector_store = _FakeVectorStore()
    monkeypatch.setattr(
        "openviking.storage.content_write.get_queue_manager",
        lambda: _FakeQueueManager(_FakeSemanticQueue()),
    )

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
    queue = _FakeSemanticQueue()

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def fetch_by_uri(self, uri: str, ctx=None):
            del uri, ctx
            raise AssertionError("recursive tag updates should use store update API")

        async def update_search_tags(self, uri: str, tags, *, mode: str, ctx=None):
            del ctx
            self.update_calls.append((uri, list(tags), mode))
            return uri != nested_overview_uri

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    monkeypatch.setattr(
        "openviking.storage.content_write.get_queue_manager",
        lambda: _FakeQueueManager(queue),
    )

    result = await coordinator.set_tags(
        uri=root_uri,
        tags=["env=prod"],
        mode="append",
        recursive=True,
        ctx=ctx,
    )

    assert result["mode"] == "append"
    assert "recursive" not in result
    assert result["success_count"] == 5
    assert result["skipped_count"] == 1
    assert result["failed_count"] == 0
    assert set(result["updated_uris"]) == {
        abstract_uri,
        overview_uri,
        file_uri,
        nested_abstract_uri,
        nested_file_uri,
    }
    assert nested_overview_uri not in result["updated_uris"]
    assert sorted(fake_store.update_calls) == sorted(
        [
            (abstract_uri, ["env=prod"], "append"),
            (overview_uri, ["env=prod"], "append"),
            (file_uri, ["env=prod"], "append"),
            (nested_abstract_uri, ["env=prod"], "append"),
            (nested_overview_uri, ["env=prod"], "append"),
            (nested_file_uri, ["env=prod"], "append"),
        ]
    )
    assert len(queue.messages) == 1
    assert queue.messages[0].uri == root_uri
    assert queue.messages[0].recursive is True


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
    queue = _FakeSemanticQueue()

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def update_search_tags(self, uri: str, tags, *, mode: str, ctx=None):
            del ctx
            self.update_calls.append((uri, list(tags), mode))
            return False

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    monkeypatch.setattr(
        "openviking.storage.content_write.get_queue_manager",
        lambda: _FakeQueueManager(queue),
    )

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
    assert len(queue.messages) == 1
    assert queue.messages[0].uri == root_uri
    assert queue.messages[0].recursive is True


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
    queue = _FakeSemanticQueue()

    class _FakeVectorStore:
        def __init__(self):
            self.update_calls = []

        async def update_search_tags(self, uri: str, tags, *, mode: str, ctx=None):
            del ctx
            self.update_calls.append((uri, list(tags), mode))
            return False

    fake_store = _FakeVectorStore()
    fake_vfs.vector_store = fake_store
    monkeypatch.setattr(
        "openviking.storage.content_write.get_queue_manager",
        lambda: _FakeQueueManager(queue),
    )

    result = await coordinator.set_tags(
        uri=root_uri,
        tags=["env=prod"],
        mode="replace",
        recursive=False,
        ctx=ctx,
    )

    assert result["success_count"] == 0
    assert result["skipped_count"] == 2
    assert result["failed_count"] == 0
    assert result["updated_uris"] == []
    assert len(queue.messages) == 1
    assert queue.messages[0].uri == root_uri
    assert queue.messages[0].recursive is False
