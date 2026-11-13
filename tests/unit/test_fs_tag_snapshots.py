from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking.storage.collection_schemas import CollectionSchemas
from openviking_cli.exceptions import ConflictError, InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


@pytest.fixture
def snapshot_service():
    store = SimpleNamespace(scroll=AsyncMock(return_value=([], None)))
    vfs = SimpleNamespace(
        _async_agfs=SimpleNamespace(
            pathlock_reacquire_batch=AsyncMock(return_value={"owner_id": "target"}),
            pathlock_release=AsyncMock(),
        ),
        _uri_to_path=lambda uri, ctx: f"/local/{ctx.account_id}/{uri.removeprefix('viking://')}",
        _get_vector_store=lambda: store,
        _ensure_access=AsyncMock(),
        commit=AsyncMock(return_value={"commit_oid": "b" * 40, "result": "created"}),
        log=AsyncMock(return_value=[{"oid": "a" * 40}]),
        show=AsyncMock(return_value=b"restored content"),
        read_file_bytes=AsyncMock(return_value=b"restored content"),
        stat=AsyncMock(return_value={"isDir": True}),
        mkdir=AsyncMock(),
    )
    service = FSService(viking_fs=vfs)
    service._directory_has_compile_tag = AsyncMock(return_value=False)
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    return service, vfs, ctx


async def test_snapshot_uses_content_lease(snapshot_service):
    service, vfs, ctx = snapshot_service
    root = "viking://user/user/resources/output"
    uri = f"{root}/nested/file.md"
    write = AsyncMock(return_value=b"written")
    lease = {"owner": "content"}

    result = await service._snapshot_write([uri], ctx=ctx, lease_ref=lease, write=write)

    assert result == b"written"
    write.assert_awaited_once()
    vfs.commit.assert_awaited_once_with(
        message="content write", paths=[uri], ctx=ctx, lease_ref=lease
    )


async def test_snapshot_failure_is_returned_after_write(snapshot_service):
    service, vfs, ctx = snapshot_service
    vfs.commit.side_effect = OSError("snapshot failed")
    write = AsyncMock()

    with pytest.raises(OSError, match="snapshot failed"):
        await service._snapshot_write(
            ["viking://resources/output/file.md"], ctx=ctx, lease_ref={}, write=write
        )
    write.assert_awaited_once()


async def test_native_versions_page_without_global_metadata_access(snapshot_service):
    service, vfs, ctx = snapshot_service
    cursor = "a" * 40
    vfs.log.return_value = [{"oid": cursor}, {"oid": "b" * 40}, {"oid": "c" * 40}]

    result = await service.file_versions("viking://resources/file.md", ctx, limit=1, before=cursor)

    assert result["versions"] == [{"oid": "b" * 40}]
    assert result["next_before_snapshot"] == "b" * 40
    vfs.show.assert_not_awaited()


@pytest.mark.parametrize("state,count", [("unfixed", 1), ("fixed", 0), (None, 0)])
@pytest.mark.parametrize("stale", ["none", "commit", "content"])
async def test_file_restore_rechecks_commit_under_content_lock(
    snapshot_service, stale, state, count
):
    service, vfs, ctx = snapshot_service
    uri = "viking://resources/ordinary.md"
    current = "c" if stale == "commit" else "a"
    vfs.log.side_effect = [[{"oid": "a" * 40}], [{"oid": current * 40}]]
    write = AsyncMock()
    expected_content = "restored content"
    if state:
        expected_content += f'<!-- CONFLICTS_CONTENT {{"state":"{state}","match_text":"x"}} -->'
    vfs.show.return_value = expected_content.encode()
    vfs.read_file_bytes.return_value = b"edit" if stale == "content" else vfs.show.return_value

    async def locked_write(uri, content, ctx, *, mode, commit_content, tags, tag_mode, **kwargs):
        assert mode == "replace" and content == expected_content
        assert kwargs == {"use_snapshot": True}
        assert tags == [f"has_conflict={str(bool(count)).lower()}", f"conflict_num={count}"]
        assert tag_mode == "append"
        return await commit_content([uri], lease_ref={"owner": "restore"}, write=write)

    service.write = locked_write
    if stale != "none":
        with pytest.raises(ConflictError):
            await service.restore_file_version(uri, "f" * 40, "a" * 40, ctx)
        write.assert_not_awaited()
        vfs.commit.assert_not_awaited()
    else:
        result = await service.restore_file_version(uri, "f" * 40, "a" * 40, ctx)
        assert result == {"uri": uri, "snapshot_id": "b" * 40, "result": "created"}
        write.assert_awaited_once()
        assert vfs.commit.await_args.kwargs["lease_ref"] == {"owner": "restore"}
    service._directory_has_compile_tag.assert_not_awaited()


async def test_file_restore_dry_run_does_not_write(snapshot_service):
    service, vfs, ctx = snapshot_service
    service.write = AsyncMock()
    result = await service.restore_file_version(
        "viking://resources/ordinary.md", "f" * 40, "a" * 40, ctx, dry_run=True
    )
    assert result["result"] == "dry_run"
    service.write.assert_not_awaited()
    vfs.commit.assert_not_awaited()


@pytest.mark.parametrize("dry_run", [False, True])
async def test_file_restore_rejects_invalid_conflict_state(snapshot_service, dry_run):
    service, vfs, ctx = snapshot_service
    vfs.show.return_value = b'<!-- CONFLICTS_CONTENT {"state":"unknown"} -->'
    service.write = AsyncMock()
    with pytest.raises(InvalidArgumentError):
        await service.restore_file_version(
            "viking://resources/page.md", "f" * 40, None, ctx, dry_run=dry_run
        )
    service.write.assert_not_awaited()
    vfs.commit.assert_not_awaited()


@pytest.mark.parametrize("kind", ["missing", "directory", "file"])
async def test_ensure_compile_target_preserves_existing_directory(snapshot_service, kind):
    service, vfs, ctx = snapshot_service
    uri = "viking://resources/output"
    if kind == "missing":
        vfs.stat.side_effect = FileNotFoundError(uri)
    else:
        vfs.stat.return_value = {"isDir": kind == "directory"}
    service._enqueue_compile_target_tag = AsyncMock(return_value={"directory_count": 1})
    service.finalize_compile_directories = AsyncMock(side_effect=AssertionError("inline tagging"))

    @asynccontextmanager
    async def hold_scope(requests, uris, request_ctx):
        assert requests == [{"path": vfs._uri_to_path(uri, ctx=ctx), "kind": "tree"}]
        assert uris == [uri] and request_ctx is ctx
        yield {"owner": "directory"}

    service._hold_watch_scope = hold_scope
    if kind == "file":
        with pytest.raises(InvalidArgumentError):
            await service.ensure_compile_target(uri, ctx)
        service._enqueue_compile_target_tag.assert_not_awaited()
    else:
        await service.ensure_compile_target(uri, ctx)
        service._enqueue_compile_target_tag.assert_awaited_once_with(uri, ctx, ANY)
    service.finalize_compile_directories.assert_not_awaited()
    assert vfs.mkdir.await_count == (kind == "missing")
    vfs.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "relative,level,tag,rejected",
    [
        ("", 0, "compile_target=true", True),
        ("/output/child", 1, "compile_target=true", True),
        ("/output", 0, "compile_target=true", False),
        ("/output-other/child", 0, "compile_target=true", False),
        ("/output/file.md", 2, "compile_target=true", False),
        ("/output/child", 0, "compile_target=false", False),
    ],
)
async def test_compile_target_nesting_uses_directory_tags(
    snapshot_service, vector_backend_factory, tmp_path, relative, level, tag, rejected
):
    service, vfs, ctx = snapshot_service
    store = vector_backend_factory(
        VectorDBBackendConfig(
            backend="local", name="context", dimension=4, path=str(tmp_path / "vectors")
        )
    )
    try:
        await store.create_collection("context", CollectionSchemas.context_collection("context", 4))
        vfs._get_vector_store = lambda: store
        del service._directory_has_compile_tag
        await store._upsert_many_raw(
            [
                {
                    "id": "node",
                    "uri": f"viking://resources/parent{relative}",
                    "account_id": "account",
                    "context_type": "resource",
                    "level": level,
                    "search_tags": [tag],
                    "updated_at": "2026-01-01T00:00:00Z",
                    "vector": [1.0, 0.0, 0.0, 0.0],
                }
            ],
            ctx=ctx,
        )
        if rejected:
            with pytest.raises(
                InvalidArgumentError, match="already a Compile target.*Choose another"
            ):
                await service.ensure_compile_target("viking://resources/parent/output", ctx)
            vfs.mkdir.assert_not_awaited()
        else:
            await service._validate_compile_target_nesting("viking://resources/parent/output", ctx)
    finally:
        await store.close()


@pytest.mark.parametrize("successful_queries", [0, 1])
async def test_compile_target_query_failure_prevents_writes(snapshot_service, successful_queries):
    service, vfs, ctx = snapshot_service
    del service._directory_has_compile_tag
    store = vfs._get_vector_store()
    store.scroll.side_effect = [([], None)] * successful_queries + [OSError("index unavailable")]

    with pytest.raises(OSError, match="index unavailable"):
        await service.ensure_compile_target("viking://resources/output", ctx)

    assert all(call.kwargs["ctx"] is ctx for call in store.scroll.await_args_list)
    vfs.mkdir.assert_not_awaited()
