# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Service-level tests for content write coordination."""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.utils.content import deserialize_full, serialize_with_metadata
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
        user=UserIdentifier(owner_ctx.account_id, "other_user", owner_ctx.user.agent_id),
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
    full_content = serialize_with_metadata({**metadata, "content": "Original preference"})
    expected_metadata = deserialize_full(full_content).memory_fields
    await service.viking_fs.write_file(memory_uri, full_content, ctx=ctx)

    await service.fs.write(
        memory_uri,
        content="Updated preference",
        ctx=ctx,
        mode="replace",
    )

    stored = await service.viking_fs.read_file(memory_uri, ctx=ctx)
    stored_result = deserialize_full(stored)

    assert stored_result.plain_content == "Updated preference"
    assert stored_result.memory_fields == expected_metadata


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
    full_content = serialize_with_metadata({**metadata, "content": "Original preference"})
    expected_metadata = deserialize_full(full_content).memory_fields
    await service.viking_fs.write_file(memory_uri, full_content, ctx=ctx)

    await service.fs.write(
        memory_uri,
        content="\nUpdated preference",
        ctx=ctx,
        mode="append",
    )

    stored = await service.viking_fs.read_file(memory_uri, ctx=ctx)
    stored_result = deserialize_full(stored)

    assert stored_result.plain_content == "Original preference\nUpdated preference"
    assert stored_result.memory_fields == expected_metadata


@pytest.mark.asyncio
async def test_memory_write_vector_refresh_includes_generated_summary(monkeypatch):
    file_uri = "viking://user/default/memories/preferences/theme.md"
    root_uri = "viking://user/default/memories/preferences"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    coordinator = ContentWriteCoordinator(
        viking_fs=_FakeVikingFS(file_uri=file_uri, root_uri=root_uri)
    )

    captured = {}

    async def _fake_generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        del self, llm_sem, ctx
        return {"name": "theme.md", "summary": f"summary for {file_path}"}

    async def _fake_vectorize_file(
        *,
        file_path,
        summary_dict,
        parent_uri,
        context_type,
        ctx,
        semantic_msg_id=None,
        use_summary=False,
        preserve_existing_created_at=False,
    ):
        del ctx, semantic_msg_id, use_summary, preserve_existing_created_at
        captured["file_path"] = file_path
        captured["summary_dict"] = summary_dict
        captured["parent_uri"] = parent_uri
        captured["context_type"] = context_type

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticProcessor._generate_single_file_summary",
        _fake_generate_single_file_summary,
    )
    monkeypatch.setattr(
        "openviking.storage.content_write.vectorize_file",
        _fake_vectorize_file,
    )

    await coordinator._vectorize_single_file(file_uri, context_type="memory", ctx=ctx)

    assert captured["file_path"] == file_uri
    assert captured["parent_uri"] == root_uri
    assert captured["context_type"] == "memory"
    assert captured["summary_dict"] == {
        "name": "theme.md",
        "summary": f"summary for {file_uri}",
    }


class _FakeHandle:
    def __init__(self, handle_id: str):
        self.id = handle_id


class _FakeLockManager:
    def __init__(self):
        self.handle = _FakeHandle("lock-1")
        self.release_calls = []

    def create_handle(self):
        return self.handle

    async def acquire_subtree(self, handle, path):
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

    async def stat(self, uri: str, ctx=None):
        del ctx
        if uri == self._file_uri:
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


@pytest.mark.asyncio
async def test_write_timeout_after_enqueue_does_not_release_resource_lock(monkeypatch):
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

    assert lock_manager.release_calls == []
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
    assert lock_manager.release_calls == []


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
async def test_memory_write_timeout_after_enqueue_does_not_release_lock(monkeypatch):
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

    async def _fake_vectorize_single_file(uri, *, context_type, ctx):
        del uri, context_type, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_request(*, telemetry_id, timeout):
        del telemetry_id
        raise DeadlineExceededError("queue processing", timeout)

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_vectorize_single_file", _fake_vectorize_single_file)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
    monkeypatch.setattr(coordinator, "_wait_for_request", _fake_wait_for_request)

    with pytest.raises(DeadlineExceededError):
        await coordinator.write(
            uri=file_uri,
            content="updated",
            ctx=ctx,
            wait=True,
        )

    assert lock_manager.release_calls == []


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

    async def _fake_vectorize_single_file(uri, *, context_type, ctx):
        del uri, context_type, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_vectorize_single_file", _fake_vectorize_single_file)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    result = await coordinator.write(
        uri=file_uri, content="new content", mode="create", ctx=ctx, wait=True
    )

    assert result["mode"] == "create"
    assert write_calls == [(file_uri, "new content")]


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

    async def _fake_vectorize_single_file(uri, *, context_type, ctx):
        del uri, context_type, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_vectorize_single_file", _fake_vectorize_single_file)
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

    async def _fake_vectorize_single_file(uri, *, context_type, ctx):
        del uri, context_type, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_vectorize_single_file", _fake_vectorize_single_file)
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

    async def _fake_vectorize_single_file(uri, *, context_type, ctx):
        del uri, context_type, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_vectorize_single_file", _fake_vectorize_single_file)
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

        async def _fake_vectorize_single_file(uri, *, context_type, ctx):
            del uri, context_type, ctx
            return None

        async def _fake_enqueue_memory_refresh(**kwargs):
            del kwargs
            return None

        async def _fake_wait_for_queues(*, timeout):
            del timeout
            return None

        monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
        monkeypatch.setattr(coordinator, "_vectorize_single_file", _fake_vectorize_single_file)
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

    async def _fake_vectorize_single_file(uri, *, context_type, ctx):
        # Verify memory-scope URIs take the memory write path
        assert context_type == "memory"
        del uri, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_vectorize_single_file", _fake_vectorize_single_file)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    result = await coordinator.write(
        uri=file_uri, content="content", mode="create", ctx=ctx, wait=True
    )
    assert result["context_type"] == "memory"


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

    async def _fake_vectorize_single_file(uri, *, context_type, ctx):
        del uri, context_type, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_vectorize_single_file", _fake_vectorize_single_file)
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

    async def _fake_vectorize_single_file(uri, *, context_type, ctx):
        del uri, context_type, ctx
        return None

    async def _fake_enqueue_memory_refresh(**kwargs):
        del kwargs
        return None

    async def _fake_wait_for_queues(*, timeout):
        del timeout
        return None

    monkeypatch.setattr(coordinator, "_write_in_place", _fake_write_in_place)
    monkeypatch.setattr(coordinator, "_vectorize_single_file", _fake_vectorize_single_file)
    monkeypatch.setattr(coordinator, "_enqueue_memory_refresh", _fake_enqueue_memory_refresh)
    monkeypatch.setattr(coordinator, "_wait_for_queues", _fake_wait_for_queues)

    result = await coordinator.write(
        uri=file_uri, content="appended", ctx=ctx, mode="append", wait=True
    )

    assert result["mode"] == "append"
