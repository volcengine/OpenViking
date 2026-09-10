# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Transfer regressions using real RAGFS locking and local filesystem I/O."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openviking.pyagfs import get_binding_client
from openviking.resource.watch_manager import WatchManager
from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclAction, AclManager
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.vector_ids import vector_record_id
from openviking.storage.vectordb import engine as vectordb_engine
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking.utils.agfs_utils import RagfsBindingConfig, mount_agfs_backend
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


@pytest.fixture
def binding_fs(tmp_path):
    client_type, _ = get_binding_client()
    if client_type is None:
        pytest.skip("RAGFS native extension is unavailable")
    config = RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
    client = client_type(None, config=config.to_binding_dict())
    mount_agfs_backend(client, config)
    return VikingFS(agfs=client)


def root_ctx():
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role(Role.ROOT))


@pytest_asyncio.fixture
async def indexed_fs(binding_fs, tmp_path):
    if not getattr(vectordb_engine, "PersistStore", None):
        pytest.skip("local persistent vectordb engine is unavailable")
    backend = VikingVectorIndexBackend(
        config=VectorDBBackendConfig(
            backend="local", name="context", dimension=4, path=str(tmp_path / "vectors")
        )
    )
    try:
        assert await backend.create_collection(
            "context", CollectionSchemas.context_collection("context", 4)
        )
        binding_fs.vector_store = backend
        yield binding_fs, backend
    finally:
        await backend.close()


async def seed_vector(backend, uri, content):
    return await backend.upsert(
        {
            "id": uri,
            "uri": uri,
            "level": 2,
            "vector": [0.1, 0.2, 0.3, 0.4],
            "abstract": content,
            "account_id": root_ctx().account_id,
        },
        ctx=root_ctx(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["copy_uri_mapping", "update_uri_mapping"])
@pytest.mark.parametrize("directory", [False, True])
async def test_path_transfer_scans_native_chunks_without_unrelated_subtrees(
    indexed_fs, monkeypatch, operation, directory
):
    _, backend = indexed_fs
    ctx = root_ctx()
    source_file = "viking://resources/source/file.md"
    target_file = "viking://resources/target/file.md"
    source = "viking://resources/source" if directory else source_file
    target = "viking://resources/target" if directory else target_file
    source_uris = [source_file] + [f"{source_file}#chunk_{i:04d}" for i in range(205)]
    old_targets = [target_file] + [f"{target_file}#chunk_{i:04d}" for i in range(206)]
    untouched = [
        "viking://resources/target/keep.md",
        "viking://resources/target/unrelated/deep.md",
    ]
    records = [
        {
            "id": uri,
            "uri": uri,
            "level": 2,
            "vector": [0.1, 0.2, 0.3, 0.4],
            "account_id": ctx.account_id,
        }
        for uri in source_uris + old_targets + untouched
    ]
    await backend._upsert_many_raw(records, ctx=ctx)
    read_ids = set()
    original_page = backend._strict_transfer_page

    async def tracked_page(*args, **kwargs):
        page, cursor = await original_page(*args, **kwargs)
        read_ids.update(record["id"] for record in page)
        return page, cursor

    monkeypatch.setattr(backend, "_strict_transfer_page", tracked_page)
    result = await getattr(backend, operation)(
        ctx,
        source,
        target,
        recursive=directory,
        **({"source_uris": [source, source_file]} if directory else {}),
    )

    assert result.scanned == result.written == 206
    assert result.batches >= 3
    assert set(source_uris + old_targets).issubset(read_ids)
    assert untouched[1] not in read_ids
    expected_targets = [target_file] + [f"{target_file}#chunk_{i:04d}" for i in range(205)]
    target_ids = [vector_record_id(ctx.account_id, uri, 2) for uri in expected_targets]
    copied = await backend.get(target_ids, ctx=ctx)
    assert {record["uri"] for record in copied} == set(expected_targets)
    assert not await backend.get(old_targets, ctx=ctx)
    assert {record["uri"] for record in await backend.get(untouched, ctx=ctx)} == set(untouched)
    remaining_source = await backend.get(source_uris, ctx=ctx)
    assert len(remaining_source) == (206 if operation == "copy_uri_mapping" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,indexed_source", [("cp", False), ("cp", True), ("mv", False)])
async def test_overwrite_preserves_target_acl_with_real_storage(
    indexed_fs, operation, indexed_source
):
    fs, backend = indexed_fs
    ctx = root_ctx()
    source, target = "viking://resources/source.txt", "viking://resources/target.txt"
    await fs.write_file_bytes(source, b"new", ctx=ctx)
    await fs.write_file_bytes(target, b"old private", ctx=ctx)
    if indexed_source:
        await seed_vector(backend, source, "new")
    await backend._upsert_many_raw(
        [
            {
                "id": "private-target",
                "uri": target,
                "account_id": ctx.account_id,
                "level": 2,
                "abstract": "old private",
                "vector": [0.9, 0.8, 0.7, 0.6],
                "acl_mode": "inherit",
                "acl_direct_grants": [f"3:user:{ctx.user.user_id}"],
                "acl_inherited_grants": [],
            }
        ],
        ctx=ctx,
    )
    acl = AclManager(backend)
    acl.set_enabled(ctx.account_id, True)
    fs.acl_manager = backend.acl_manager = acl

    await getattr(fs, operation)(source, target, ctx=ctx)

    assert await fs.read_file_bytes(target, ctx=ctx) == b"new"
    effective = await acl.resolve(target, ctx)
    assert effective.enabled
    assert effective.direct.principals_for(AclAction.WRITE) == {f"user:{ctx.user.user_id}"}
    records = await backend.get_context_by_uri(target, ctx=ctx)
    records = await backend.get([record["id"] for record in records], ctx=ctx)
    if indexed_source:
        assert [record["abstract"] for record in records] == ["new"]
    else:
        assert [record["id"] for record in records] == ["private-target"]
        assert [record["abstract"] for record in records] == ["old private"]
        assert records[0]["vector"] == pytest.approx([0.9, 0.8, 0.7, 0.6])


@pytest.mark.asyncio
async def test_chunk_only_copy_preserves_private_target_main_record(indexed_fs):
    fs, backend = indexed_fs
    ctx = root_ctx()
    source, target = "viking://resources/source", "viking://resources/target"
    target_file = f"{target}/file.md"
    await fs.write_file_bytes(f"{source}/file.md", b"new", ctx=ctx)
    await fs.write_file_bytes(target_file, b"old", ctx=ctx)
    await seed_vector(backend, f"{source}/file.md#chunk_0000", "new chunk")
    await seed_vector(backend, f"{target_file}#chunk_old", "obsolete chunk")
    old_main = {
        "id": "private-target",
        "uri": target_file,
        "account_id": ctx.account_id,
        "level": 2,
        "abstract": "old",
        "vector": [0.1] * 4,
        "acl_mode": "inherit",
        "acl_direct_grants": [f"7:user:{ctx.user.user_id}"],
        "acl_inherited_grants": [],
    }
    await backend._upsert_many_raw([old_main], ctx=ctx)
    acl = AclManager(backend)
    acl.set_enabled(ctx.account_id, True)
    fs.acl_manager = backend.acl_manager = acl
    outsider = RequestContext(user=UserIdentifier(ctx.account_id, "outsider"), role=Role(Role.USER))
    with pytest.raises(PermissionDeniedError):
        await fs.read_file_bytes(target_file, ctx=outsider)

    await fs.cp(source, target, recursive=True, ctx=ctx)

    assert await fs.read_file_bytes(target_file, ctx=ctx) == b"new"
    with pytest.raises(PermissionDeniedError):
        await fs.read_file_bytes(target_file, ctx=outsider)
    main = await backend.get(["private-target"], ctx=ctx)
    assert main[0]["abstract"] == "old"
    assert main[0]["acl_direct_grants"] == old_main["acl_direct_grants"]
    assert await backend.get_context_by_uri(f"{target_file}#chunk_0000", ctx=ctx)
    assert not await backend.get_context_by_uri(f"{target_file}#chunk_old", ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cp", "mv"])
@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("filename", ["C#notes.md", "file.md#chunk_0001"])
async def test_transfer_preserves_literal_hash_filenames(
    indexed_fs, operation, directory, filename
):
    fs, backend = indexed_fs
    ctx = root_ctx()
    source_dir, target_dir = "viking://resources/source", "viking://resources/target"
    source_file, target_file = f"{source_dir}/{filename}", f"{target_dir}/{filename}"
    await fs.write_file_bytes(source_file, b"new", ctx=ctx)
    await fs.write_file_bytes(target_file, b"old", ctx=ctx)
    await seed_vector(backend, source_file, "new")
    await seed_vector(backend, target_file, "old")
    source, target = (source_dir, target_dir) if directory else (source_file, target_file)

    kwargs = {"recursive": directory} if operation == "cp" else {}
    await getattr(fs, operation)(source, target, ctx=ctx, **kwargs)

    assert await fs.read_file_bytes(target_file, ctx=ctx) == b"new"
    copied = await backend.get_context_by_uri(target_file, ctx=ctx)
    copied = await backend.get([record["id"] for record in copied], ctx=ctx)
    assert [record["abstract"] for record in copied] == ["new"]
    assert bool(await backend.get_context_by_uri(source_file, ctx=ctx)) == (operation == "cp")
    if operation == "cp":
        assert await fs.read_file_bytes(source_file, ctx=ctx) == b"new"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cp", "mv"])
@pytest.mark.parametrize("directory,incoming_chunk", [(False, False), (True, False), (True, True)])
async def test_transfer_protects_chunk_shaped_target_file(
    indexed_fs, operation, directory, incoming_chunk
):
    fs, backend = indexed_fs
    ctx = root_ctx()
    source, target = "viking://resources/source", "viking://resources/target"
    source_file = f"{source}/file.md" if directory else source
    target_file = f"{target}/file.md" if directory else target
    sibling = target_file + "#chunk_0001"
    await fs.write_file_bytes(source_file, b"new", ctx=ctx)
    await fs.write_file_bytes(sibling, b"private sibling", ctx=ctx)
    await seed_vector(backend, source_file, "new")
    if directory:
        await seed_vector(backend, source, "source directory")
    if incoming_chunk:
        await seed_vector(backend, source_file + "#chunk_0001", "incoming chunk")
    sibling_id = vector_record_id(ctx.account_id, sibling, 2)
    await backend._upsert_many_raw(
        [
            {
                "id": sibling_id,
                "uri": sibling,
                "account_id": ctx.account_id,
                "level": 2,
                "abstract": "private sibling",
                "vector": [0.1] * 4,
                "acl_mode": "inherit",
                "acl_direct_grants": [f"7:user:{ctx.user.user_id}"],
                "acl_inherited_grants": [],
            }
        ],
        ctx=ctx,
    )
    acl = AclManager(backend)
    acl.set_enabled(ctx.account_id, True)
    fs.acl_manager = backend.acl_manager = acl
    outsider = RequestContext(user=UserIdentifier(ctx.account_id, "outsider"), role=Role(Role.USER))
    with pytest.raises(PermissionDeniedError):
        await fs.read_file_bytes(sibling, ctx=outsider)
    kwargs = {"recursive": directory} if operation == "cp" else {}
    if incoming_chunk:
        with pytest.raises(InvalidArgumentError, match="chunk.*existing filesystem entry"):
            await getattr(fs, operation)(source, target, ctx=ctx, **kwargs)
        assert await fs.read_file_bytes(source_file, ctx=ctx) == b"new"
        assert await backend.get_context_by_uri(source_file, ctx=ctx)
        # Keep the approved weak rollback: vector failure removes the target
        # filesystem subtree, but must not delete or overwrite the sibling ACL.
        assert not await fs.exists(target, ctx=ctx)
    else:
        await getattr(fs, operation)(source, target, ctx=ctx, **kwargs)
        assert await fs.read_file_bytes(target_file, ctx=ctx) == b"new"
        assert await fs.read_file_bytes(sibling, ctx=ctx) == b"private sibling"
        with pytest.raises(PermissionDeniedError):
            await fs.read_file_bytes(sibling, ctx=outsider)
    saved = await backend.get([sibling_id], ctx=ctx)
    assert saved[0]["abstract"] == "private sibling"
    assert saved[0]["acl_direct_grants"] == [f"7:user:{ctx.user.user_id}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cp", "mv"])
async def test_directory_merge_replaces_only_affected_file_vectors(
    indexed_fs, operation, monkeypatch
):
    fs, backend = indexed_fs
    ctx = root_ctx()
    source, target = "viking://resources/source", "viking://resources/target"
    for base, name, content in (
        (source, "indexed.txt", b"new"),
        (source, "unindexed.txt", b"new raw"),
        (source, "tasks/indexed.txt", b"new task"),
        (target, "indexed.txt", b"old"),
        (target, "unindexed.txt", b"old raw"),
        (target, "tasks/indexed.txt", b"old task"),
        (target, "only.txt", b"keep"),
    ):
        await fs.write_file_bytes(f"{base}/{name}", content, ctx=ctx)
    await seed_vector(backend, f"{source}/indexed.txt", "new")
    await seed_vector(backend, f"{source}/tasks/indexed.txt", "new task")
    await seed_vector(backend, f"{target}/indexed.txt", "old")
    await seed_vector(backend, f"{target}/indexed.txt#chunk-9", "old chunk")
    await seed_vector(backend, f"{target}/unindexed.txt", "old raw")
    await seed_vector(backend, f"{target}/tasks/indexed.txt", "old task")
    await seed_vector(backend, f"{target}/only.txt", "keep")

    fetched_ids: list[str] = []
    original_get = backend._strict_transfer_get

    async def counted_get(ctx, ids):
        fetched_ids.extend(ids)
        return await original_get(ctx, ids)

    monkeypatch.setattr(backend, "_strict_transfer_get", counted_get)
    kwargs = {"recursive": True} if operation == "cp" else {}
    await getattr(fs, operation)(source, target, ctx=ctx, **kwargs)

    # Path queries may scan sibling IDs/URIs, but discard unrelated entries
    # before loading their full vector records.
    assert f"{target}/only.txt" not in fetched_ids
    assert f"{target}/unindexed.txt" not in fetched_ids
    copied = await backend.get_context_by_uri(f"{target}/indexed.txt", ctx=ctx)
    copied = await backend.get([record["id"] for record in copied], ctx=ctx)
    assert [record["abstract"] for record in copied] == ["new"]
    tasks = await backend.get_context_by_uri(f"{target}/tasks/indexed.txt", ctx=ctx)
    tasks = await backend.get([record["id"] for record in tasks], ctx=ctx)
    assert [record["abstract"] for record in tasks] == ["new task"]
    assert not await backend.get_context_by_uri(f"{target}/indexed.txt#chunk-9", ctx=ctx)
    stale = await backend.get_context_by_uri(f"{target}/unindexed.txt", ctx=ctx)
    stale = await backend.get([record["id"] for record in stale], ctx=ctx)
    assert [record["abstract"] for record in stale] == ["old raw"]
    assert await fs.read_file_bytes(f"{target}/unindexed.txt", ctx=ctx) == b"new raw"
    kept = await backend.get_context_by_uri(f"{target}/only.txt", ctx=ctx)
    kept = await backend.get([record["id"] for record in kept], ctx=ctx)
    assert [record["abstract"] for record in kept] == ["keep"]
    assert await fs.read_file_bytes(f"{target}/only.txt", ctx=ctx) == b"keep"
    assert bool(await backend.get_context_by_uri(f"{source}/indexed.txt", ctx=ctx)) == (
        operation == "cp"
    )
    assert bool(await backend.get_context_by_uri(f"{source}/tasks/indexed.txt", ctx=ctx)) == (
        operation == "cp"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cp", "mv"])
@pytest.mark.parametrize("kind", ["same_file", "child_directory", "ancestor_directory"])
async def test_transfer_rejects_overlapping_backend_paths(binding_fs, operation, kind):
    fs, ctx = binding_fs, root_ctx()
    file_uri = "viking://resources/source/file.txt"
    await fs.write_file_bytes(file_uri, b"must survive", ctx=ctx)
    if kind == "same_file":
        source, target = file_uri, "viking://resources//source/file.txt"
    elif kind == "child_directory":
        source, target = "viking://resources/source", "viking://resources//source/child"
    else:
        source, target = "viking://resources/source", "viking://resources/"
    kwargs = {"recursive": True} if operation == "cp" else {}
    with pytest.raises(InvalidArgumentError):
        await getattr(fs, operation)(source, target, ctx=ctx, **kwargs)
    assert await fs.read_file_bytes(file_uri, ctx=ctx) == b"must survive"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cp", "mv"])
@pytest.mark.parametrize("alias_side", ["source", "target"])
async def test_transfer_normalizes_uri_for_files_and_vectors(indexed_fs, operation, alias_side):
    fs, backend = indexed_fs
    ctx = root_ctx()
    source, target = "viking://resources/source.txt", "viking://resources/target.txt"
    await fs.write_file_bytes(source, b"new", ctx=ctx)
    await fs.write_file_bytes(target, b"old", ctx=ctx)
    await seed_vector(backend, source, "new")
    await seed_vector(backend, target, "old")
    source_arg = source.replace("resources/", "resources//") if alias_side == "source" else source
    target_arg = target.replace("resources/", "resources//") if alias_side == "target" else target

    await getattr(fs, operation)(source_arg, target_arg, ctx=ctx)

    assert await fs.read_file_bytes(target, ctx=ctx) == b"new"
    records = await backend.get_context_by_uri(target, ctx=ctx)
    assert records
    records = await backend.get([record["id"] for record in records], ctx=ctx)
    assert [record["abstract"] for record in records] == ["new"]
    alias_id = vector_record_id(ctx.account_id, target.replace("resources/", "resources//"), 2)
    assert not await backend.get([alias_id], ctx=ctx)
    assert bool(await backend.get_context_by_uri(source, ctx=ctx)) == (operation == "cp")


@pytest.mark.asyncio
async def test_acl_failure_restores_only_moved_vectors_after_directory_merge(
    indexed_fs, monkeypatch
):
    fs, backend = indexed_fs
    ctx = root_ctx()
    source, target = "viking://resources/source", "viking://resources/target"
    await fs.write_file_bytes(f"{source}/file.txt", b"new", ctx=ctx)
    await fs.write_file_bytes(f"{target}/only.txt", b"keep", ctx=ctx)
    await seed_vector(backend, f"{source}/file.txt", "new")
    await seed_vector(backend, f"{target}/only.txt", "keep")
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(
        fs,
        "acl_manager",
        SimpleNamespace(
            is_enabled=lambda _: True,
            refresh_context_subtree=AsyncMock(side_effect=RuntimeError("injected ACL failure")),
        ),
    )

    # Trailing slashes must not corrupt the reverse transfer manifest.
    with pytest.raises(RuntimeError, match="injected ACL failure"):
        await fs.mv(source + "/", target, ctx=ctx)

    assert await fs.read_file_bytes(f"{source}/file.txt", ctx=ctx) == b"new"
    assert await backend.get_context_by_uri(f"{source}/file.txt", ctx=ctx)
    assert not await backend.get_context_by_uri(f"{source}/only.txt", ctx=ctx)
    assert await backend.get_context_by_uri(f"{target}/only.txt", ctx=ctx)
    assert not await fs.exists(target, ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cp", "mv"])
@pytest.mark.parametrize("target_exists", [False, True])
async def test_directory_transfer_allows_sibling_lock_and_releases_own_locks(
    binding_fs, operation, target_exists
):
    fs = binding_fs
    ctx = root_ctx()
    source = "viking://resources/source"
    target = "viking://resources/target"
    await fs.write_file_bytes(f"{source}/file.bin", b"new", ctx=ctx)
    if target_exists:
        await fs.write_file_bytes(f"{target}/file.bin", b"old", ctx=ctx)
        await fs.write_file_bytes(f"{target}/only.bin", b"keep", ctx=ctx)
    sibling = fs._uri_to_path("viking://resources/sibling", ctx=ctx)
    sibling_lease = await fs._async_agfs.pathlock_acquire_tree(sibling)
    try:
        kwargs = {"recursive": True} if operation == "cp" else {}
        await getattr(fs, operation)(source, target, ctx=ctx, **kwargs)
        assert await fs.read_file_bytes(f"{target}/file.bin", ctx=ctx) == b"new"
        if target_exists:
            assert await fs.read_file_bytes(f"{target}/only.bin", ctx=ctx) == b"keep"
        assert await fs.exists(source, ctx=ctx) == (operation == "cp")
        # A new owner can lock the completed target; no lease is leaked.
        lease = await fs._async_agfs.pathlock_acquire_tree(fs._uri_to_path(target, ctx=ctx))
        await fs._async_agfs.pathlock_release(lease)
    finally:
        await fs._async_agfs.pathlock_release(sibling_lease)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cp", "mv"])
async def test_directory_transfer_holds_both_subtrees_through_vector_phase(binding_fs, operation):
    fs = binding_fs
    ctx = root_ctx()
    source = "viking://resources/source"
    target = "viking://resources/target"
    await fs.write_file_bytes(f"{source}/file.bin", b"new", ctx=ctx)

    class VectorBoundary:
        async def copy_uri_mapping(self, **kwargs):
            await self.check_locks()

        async def update_uri_mapping(self, **kwargs):
            await self.check_locks()

        async def check_locks(self):
            for uri in (f"{source}/file.bin", f"{target}/file.bin"):
                # No owner lease is supplied: this represents a concurrent writer.
                with pytest.raises(Exception, match="lock|timed out"):
                    await fs._async_agfs.pathlock_acquire_exact(fs._uri_to_path(uri, ctx=ctx))
            lease = await fs._async_agfs.pathlock_acquire_exact(
                fs._uri_to_path("viking://resources/unrelated.txt", ctx=ctx)
            )
            await fs._async_agfs.pathlock_release(lease)

    fs.vector_store = VectorBoundary()
    kwargs = {"recursive": True} if operation == "cp" else {}
    await getattr(fs, operation)(source, target, ctx=ctx, **kwargs)
    assert await fs.read_file_bytes(f"{target}/file.bin", ctx=ctx) == b"new"


@pytest.mark.asyncio
async def test_watch_persistence_overwrites_after_backup_removal_failure(binding_fs, monkeypatch):
    fs = binding_fs
    manager = WatchManager(viking_fs=fs)
    ctx = root_ctx()
    await manager._save_tasks()
    await manager._save_tasks()
    assert await fs.exists(manager.STORAGE_BAK_URI, ctx=ctx)
    real_rm = fs.rm

    async def fail_backup_removal(uri, **kwargs):
        if uri == manager.STORAGE_BAK_URI:
            raise RuntimeError("injected backup removal failure")
        return await real_rm(uri, **kwargs)

    monkeypatch.setattr(fs, "rm", fail_backup_removal)
    task = await manager.create_task(path="https://example.com/doc", watch_interval=60)
    stored = json.loads(await fs.read_file(manager.STORAGE_URI, ctx=ctx))
    assert [item["task_id"] for item in stored["tasks"]] == [task.task_id]
    backup = json.loads(await fs.read_file(manager.STORAGE_BAK_URI, ctx=ctx))
    assert backup["tasks"] == []
    assert not await fs.exists(manager.STORAGE_TMP_URI, ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic", [False, True], ids=["sync-tree", "semantic-sync"])
@pytest.mark.parametrize(
    "source_files,target_files,operation",
    [
        (["item.md"], [], "mv"),
        (["item/file.md"], [], "mv"),
        (["item.md"], ["item.md"], "mv"),
        (["item.md"], ["item.md"], "rm"),
        ([], ["item.md"], "rm"),
        ([], ["item/file.md"], "rm"),
        (["item"], ["item/file.md"], "rm"),
        (["item/file.md"], ["item"], "rm"),
    ],
    ids=[
        "add-file",
        "add-dir",
        "update-move",
        "update-remove",
        "delete-file",
        "delete-dir",
        "file-over-dir",
        "dir-over-file",
    ],
)
async def test_sync_failure_preserves_staged_content(
    binding_fs, monkeypatch, semantic, source_files, target_files, operation
):
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    fs, ctx = binding_fs, root_ctx()
    source, target = "viking://temp/import", "viking://resources/target"
    await fs.mkdir(source, ctx=ctx)
    await fs.mkdir(target, ctx=ctx)
    for name in source_files:
        await fs.write_file_bytes(f"{source}/{name}", b"new payload", ctx=ctx)
    for name in target_files:
        await fs.write_file_bytes(f"{target}/{name}", b"old", ctx=ctx)
    sidecar = f"{source}/.image_mappings.json"
    await fs.write_file_bytes(sidecar, b"{}", ctx=ctx)
    error = OSError("injected storage failure")
    monkeypatch.setattr(fs, operation, AsyncMock(side_effect=error))
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fs)

    with pytest.raises(OSError) as caught:
        if semantic:
            await SemanticProcessor()._sync_topdown_recursive(source, target, ctx=ctx)
        else:
            await fs.sync_tree(source, target, ctx=ctx, delete_temp_after=True)

    assert caught.value is error
    assert await fs.read_file_bytes(sidecar, ctx=ctx) == b"{}"
    for name in source_files:
        assert await fs.read_file_bytes(f"{source}/{name}", ctx=ctx) == b"new payload"


@pytest.mark.asyncio
async def test_successful_sync_reports_applied_changes_and_cleans_temp(binding_fs):
    fs, ctx = binding_fs, root_ctx()
    source, target = "viking://temp/import", "viking://resources/target"
    for name in ("added.md", "updated.md", "newdir/file.md"):
        await fs.write_file_bytes(f"{source}/{name}", b"new payload", ctx=ctx)
    for name in ("updated.md", "removed.md", "olddir/file.md"):
        await fs.write_file_bytes(f"{target}/{name}", b"old", ctx=ctx)

    diff = await fs.sync_tree(source, target, ctx=ctx, delete_temp_after=True)

    assert diff.to_changes() == {
        "added": [f"{target}/added.md", f"{target}/newdir"],
        "modified": [f"{target}/updated.md"],
        "deleted": [f"{target}/removed.md", f"{target}/olddir"],
    }
    assert not await fs.exists(source, ctx=ctx)
    for name in ("added.md", "updated.md", "newdir/file.md"):
        assert await fs.read_file_bytes(f"{target}/{name}", ctx=ctx) == b"new payload"
    for name in ("removed.md", "olddir"):
        assert not await fs.exists(f"{target}/{name}", ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("lock_error", [False, True], ids=["storage-error", "lock-error"])
async def test_partial_source_sync_fails_task_without_replaying_moves(
    binding_fs, monkeypatch, lock_error
):
    from openviking.storage.errors import LockAcquisitionError
    from openviking.storage.queuefs.semantic_msg import SemanticMsg
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    fs, ctx = binding_fs, root_ctx()
    source, target = "viking://temp/import", "viking://resources/target"
    await fs.mkdir(target, ctx=ctx)
    for name in ("a.md", "z.md"):
        await fs.write_file_bytes(f"{source}/{name}", b"payload", ctx=ctx)
    real_mv = fs.mv
    error = (
        LockAcquisitionError("injected lock failure") if lock_error else OSError("storage failed")
    )

    async def fail_second_move(src, dst, **kwargs):
        if src == f"{source}/z.md":
            raise error
        return await real_mv(src, dst, **kwargs)

    monkeypatch.setattr(fs, "mv", fail_second_move)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    tracker = MagicMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_request_wait_tracker", lambda: tracker
    )
    processor = SemanticProcessor()
    processor._reenqueue_semantic_msg = AsyncMock()
    processor.report_success = MagicMock()
    msg = SemanticMsg(
        uri=source, target_uri=target, context_type="resource", telemetry_id="sync-fail"
    )

    await processor.on_dequeue(msg.to_dict())

    tracker.mark_semantic_failed.assert_called_once_with(msg.telemetry_id, msg.id, str(error))
    tracker.mark_semantic_done.assert_not_called()
    processor._reenqueue_semantic_msg.assert_not_awaited()
    processor.report_success.assert_not_called()
    assert await fs.read_file_bytes(f"{target}/a.md", ctx=ctx) == b"payload"
    assert await fs.read_file_bytes(f"{source}/z.md", ctx=ctx) == b"payload"
