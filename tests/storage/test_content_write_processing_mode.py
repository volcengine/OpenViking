# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core.context import ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking.storage import content_write as content_write_module
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.semantic_sidecar import (
    parse_semantic_sidecar,
    render_semantic_sidecar,
)
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class _FakePathLock:
    """Mock for _async_agfs pathlock operations."""

    def __init__(self):
        self._lease = SimpleNamespace(id="lock-1")
        self.release_calls = []

    async def pathlock_acquire_exact(self, lock_path):
        del lock_path
        return self._lease

    async def pathlock_acquire_tree(self, lock_path):
        del lock_path
        return self._lease

    async def pathlock_release(self, lease):
        self.release_calls.append(lease.id)


class _FakeVikingFS:
    def __init__(self):
        self.write_file = AsyncMock()
        self.read_file = AsyncMock(return_value="previous")
        self.read_file_bytes = AsyncMock(return_value=b"previous")
        self.stat = AsyncMock(return_value={"isDir": False})
        self._async_agfs = _FakePathLock()

    def _uri_to_path(self, uri, ctx=None):
        return f"/fake/{uri}"

    def _ensure_mutable_access(self, uri, ctx):
        del uri, ctx


def _sidecar(level=ContextLevel.ABSTRACT, body="Original body."):
    return render_semantic_sidecar(
        level,
        "viking://resources/demo",
        body,
        {
            "generated_by": {"component": "test", "trigger": "test"},
            "freshness": {
                "total_entries": 1,
                "sampled_entries": 1,
                "unsampled_entries": 0,
                "pending_child_changes": 0,
            },
        },
    )


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier("account-1", "user-1"), role=Role.USER)


@pytest.mark.parametrize("name", [".abstract.md", ".overview.md"])
@pytest.mark.asyncio
async def test_public_create_rejects_generated_semantic_sidecars(ctx, name):
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())

    with pytest.raises(InvalidArgumentError, match="cannot create generated semantic sidecar"):
        await coordinator.write(
            uri=f"viking://resources/demo/{name}",
            content="attempted metadata mutation",
            ctx=ctx,
            mode="create",
        )


@pytest.mark.asyncio
async def test_public_sidecar_write_preserves_metadata_and_skips_regeneration(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    current = _sidecar()
    fake_fs.read_file.side_effect = [
        current,
        current,
        _sidecar(ContextLevel.OVERVIEW, "Overview."),
    ]
    vectorize_directory = AsyncMock()
    monkeypatch.setattr(content_write_module, "vectorize_directory_meta", vectorize_directory)
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._enqueue_semantic_refresh = AsyncMock(
        side_effect=AssertionError("sidecar body writes must not regenerate semantics")
    )

    result = await coordinator.write(
        uri="viking://resources/demo/.abstract.md",
        content="Updated body only.",
        mode="replace",
        wait=False,
        ctx=ctx,
    )

    written = fake_fs.write_file.await_args.args[1]
    assert parse_semantic_sidecar(written).body == "Updated body only.\n"
    assert parse_semantic_sidecar(written).metadata == parse_semantic_sidecar(current).metadata
    coordinator._enqueue_semantic_refresh.assert_not_awaited()
    vectorize_directory.assert_awaited_once()
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "queued"


@pytest.mark.parametrize(
    ("existing_name", "level", "body"),
    [
        (".abstract.md", ContextLevel.ABSTRACT, "Only abstract."),
        (".overview.md", ContextLevel.OVERVIEW, "Only overview."),
    ],
)
@pytest.mark.asyncio
async def test_vectorize_semantic_directory_only_indexes_existing_level(
    monkeypatch, ctx, existing_name, level, body
):
    directory_uri = "viking://resources/demo"
    existing_uri = f"{directory_uri}/{existing_name}"
    existing = _sidecar(level, body)
    fake_fs = _FakeVikingFS()

    async def read_file(uri, ctx=None):
        del ctx
        if uri == existing_uri:
            return existing
        raise NotFoundError(uri, "file")

    fake_fs.read_file.side_effect = read_file
    vectorize_directory = AsyncMock()
    monkeypatch.setattr(content_write_module, "vectorize_directory_meta", vectorize_directory)
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)

    await coordinator._vectorize_semantic_directory(directory_uri=directory_uri, ctx=ctx)

    vectorize_directory.assert_awaited_once_with(
        uri=directory_uri,
        abstract=existing if level == ContextLevel.ABSTRACT else "",
        overview=existing if level == ContextLevel.OVERVIEW else "",
        context_type="resource",
        ctx=ctx,
        include_abstract=level == ContextLevel.ABSTRACT,
        include_overview=level == ContextLevel.OVERVIEW,
    )


@pytest.mark.asyncio
async def test_vectorize_semantic_directory_propagates_non_not_found_read_errors(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    fake_fs.read_file.side_effect = RuntimeError("storage unavailable")
    vectorize_directory = AsyncMock()
    monkeypatch.setattr(content_write_module, "vectorize_directory_meta", vectorize_directory)
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await coordinator._vectorize_semantic_directory(
            directory_uri="viking://resources/demo", ctx=ctx
        )

    vectorize_directory.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_sidecar_write_rejects_changed_metadata(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    current = _sidecar()
    fake_fs.read_file.return_value = current
    changed = render_semantic_sidecar(
        ContextLevel.ABSTRACT,
        "viking://resources/demo",
        "Updated.",
        {"generated_by": {"component": "attacker", "trigger": "test"}},
    )
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)

    with pytest.raises(InvalidArgumentError, match="cannot modify protected.*metadata"):
        await coordinator._write_direct_with_refresh(
            uri="viking://resources/demo/.abstract.md",
            root_uri="viking://resources/demo",
            content=changed,
            mode="replace",
            context_type="resource",
            wait=False,
            timeout=None,
            ctx=ctx,
            written_bytes=len(changed.encode()),
            telemetry_id="",
        )

    fake_fs.write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_sidecar_write_uses_protected_direct_path(ctx):
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())
    coordinator._safe_stat = AsyncMock(return_value={"isDir": False})
    coordinator._resolve_root_uri = AsyncMock(
        return_value="viking://user/account-1/user-1/memories/preferences"
    )
    coordinator._write_direct_with_refresh = AsyncMock(return_value={"ok": True})
    coordinator._write_memory_with_refresh = AsyncMock(
        side_effect=AssertionError("memory sidecars must not use MemoryUpdater writes")
    )

    result = await coordinator.write(
        uri=("viking://user/account-1/user-1/memories/preferences/.abstract.md"),
        content="Updated body.",
        ctx=ctx,
    )

    assert result == {"ok": True}
    coordinator._write_direct_with_refresh.assert_awaited_once()
    coordinator._write_memory_with_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_sidecar_write_inherits_metadata_and_only_reindexes(ctx):
    uri = "viking://resources/demo/.overview.md"
    current = _sidecar(ContextLevel.OVERVIEW, "Original overview.")
    fake_fs = _FakeVikingFS()

    async def stat(target, ctx=None):
        del ctx
        return {"isDir": target == "viking://resources/demo"}

    fake_fs.stat.side_effect = stat
    fake_fs.read_file_bytes.return_value = current.encode()
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._vectorize_semantic_directory = AsyncMock()
    coordinator._refresh_batch = AsyncMock(
        side_effect=AssertionError("sidecar batch writes must not regenerate semantics")
    )

    result = await coordinator.batch_write(
        root_uri="viking://resources/demo",
        operations=[
            {
                "uri": uri,
                "content": "Updated overview body only.",
                "precondition": {
                    "kind": "replace_if_hash",
                    "base_hash": coordinator._content_hash(current),
                },
            }
        ],
        ctx=ctx,
        wait=False,
    )

    written = fake_fs.write_file.await_args.args[1]
    assert parse_semantic_sidecar(written).body == "Updated overview body only.\n"
    assert parse_semantic_sidecar(written).metadata == parse_semantic_sidecar(current).metadata
    coordinator._vectorize_semantic_directory.assert_awaited_once_with(
        directory_uri="viking://resources/demo", ctx=ctx
    )
    coordinator._refresh_batch.assert_not_awaited()
    assert result["updated"] == [uri]


@pytest.mark.asyncio
async def test_batch_cannot_create_generated_semantic_sidecar(ctx):
    fake_fs = _FakeVikingFS()

    async def stat(target, ctx=None):
        del ctx
        if target == "viking://resources/demo":
            return {"isDir": True}
        from openviking_cli.exceptions import NotFoundError

        raise NotFoundError(target, "file")

    fake_fs.stat.side_effect = stat
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)

    with pytest.raises(InvalidArgumentError, match="cannot create generated semantic sidecar"):
        await coordinator.batch_write(
            root_uri="viking://resources/demo",
            operations=[
                {
                    "uri": "viking://resources/demo/.abstract.md",
                    "content": "No trusted metadata.",
                    "precondition": {"kind": "create_if_absent"},
                }
            ],
            ctx=ctx,
            wait=False,
        )

    fake_fs.write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_vectors_only_write_skips_semantic_refresh_and_vectorizes_file(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    vectorize_file = AsyncMock(return_value=True)
    semantic_refresh = AsyncMock(side_effect=AssertionError("semantic refresh should not run"))
    monkeypatch.setattr(content_write_module, "vectorize_file", vectorize_file, raising=False)
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._enqueue_semantic_refresh = semantic_refresh

    result = await coordinator._write_direct_with_refresh(
        uri="viking://resources/demo.md",
        root_uri="viking://resources",
        content="updated",
        mode="replace",
        context_type="resource",
        wait=False,
        timeout=None,
        ctx=ctx,
        written_bytes=7,
        telemetry_id="",
        processing_mode="vectors_only",
    )

    semantic_refresh.assert_not_awaited()
    vectorize_file.assert_awaited_once()
    assert vectorize_file.await_args.kwargs["file_path"] == "viking://resources/demo.md"
    assert vectorize_file.await_args.kwargs["parent_uri"] == "viking://resources"
    assert vectorize_file.await_args.kwargs["summary_dict"] == {
        "name": "demo.md",
        "summary": "",
    }
    assert "register_request_wait" not in vectorize_file.await_args.kwargs
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "queued"


@pytest.mark.asyncio
async def test_vectors_only_write_wait_reports_embedding_status(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    queue_status = {
        "Embedding": {"processed": 1, "error_count": 0, "errors": []},
        "Semantic": {"processed": 0, "error_count": 0, "errors": []},
    }
    monkeypatch.setattr(
        content_write_module, "vectorize_file", AsyncMock(return_value=True), raising=False
    )
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._wait_for_request = AsyncMock(return_value=queue_status)

    result = await coordinator._write_direct_with_refresh(
        uri="viking://resources/demo.md",
        root_uri="viking://resources",
        content="updated",
        mode="replace",
        context_type="resource",
        wait=True,
        timeout=3.0,
        ctx=ctx,
        written_bytes=7,
        telemetry_id="tm-test",
        processing_mode="vectors_only",
    )

    assert result["queue_status"] == queue_status
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "complete"


@pytest.mark.asyncio
async def test_vectors_only_write_wait_reports_skipped_when_nothing_enqueued(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(
        content_write_module, "vectorize_file", AsyncMock(return_value=False), raising=False
    )
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._wait_for_request = AsyncMock(return_value=None)

    result = await coordinator._write_direct_with_refresh(
        uri="viking://resources/obsolete.md",
        root_uri="viking://resources",
        content="",
        mode="replace",
        context_type="resource",
        wait=True,
        timeout=3.0,
        ctx=ctx,
        written_bytes=0,
        telemetry_id="tm-test",
        processing_mode="vectors_only",
    )

    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "skipped"


@pytest.mark.asyncio
async def test_memory_write_accepts_processing_mode_without_switching_refresh(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(
        content_write_module.MemoryUpdater,
        "refresh_schema_overview",
        AsyncMock(),
    )
    monkeypatch.setattr(
        content_write_module.MemoryUpdater,
        "refresh_file_embedding",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        content_write_module.MemoryUpdater,
        "memory_type_from_uri",
        lambda uri: "user",
    )
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._write_in_place = AsyncMock()

    result = await coordinator._write_memory_with_refresh(
        uri="viking://user/memories/demo.md",
        root_uri="viking://user/memories",
        content="updated",
        mode="replace",
        wait=True,
        timeout=3.0,
        ctx=ctx,
        written_bytes=7,
        telemetry_id="tm-test",
        processing_mode="vectors_only",
    )

    content_write_module.MemoryUpdater.refresh_schema_overview.assert_awaited_once()
    content_write_module.MemoryUpdater.refresh_file_embedding.assert_awaited_once()
    assert result["context_type"] == "memory"
    assert result["semantic_status"] == "skipped"
    assert result["overview_status"] == "complete"
