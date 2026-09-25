# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource TTL ownership and transfer with native AGFS leases and I/O."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core import ttl
from openviking.service.ttl_cleanup import TTLCleanupService
from openviking.storage.errors import StorageException
from openviking.storage.ovpack.operations import export_ovpack, import_ovpack
from openviking.storage.resource_ttl import prepare_resource_ttl, resource_ttl_fields
from openviking.utils.content_hash import content_md5
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils.config.ttl_config import TTLConfig
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import root_ctx
from tests.unit.service.test_ttl_cleanup import _cleanup_once

ROOT = "viking://user/default/resources"
PAST = "2000-01-01T00:00:00.000Z"
FUTURE = "2999-01-01T00:00:00.000Z"


@pytest.fixture(autouse=True)
def disabled_policy(monkeypatch):
    monkeypatch.setattr(ttl, "get_openviking_config", lambda: SimpleNamespace(ttl=TTLConfig()))


async def set_expiry(fs, uri, *, is_dir=False, expiry=FUTURE, generation="g1"):
    fields = {
        "expires_at": expiry,
        "received_at": "2020-01-01T00:00:00.000Z",
        "ttl_generation": generation,
    }
    kind = ttl.OBJECT_TYPE_RESOURCE if is_dir else ttl.OBJECT_TYPE_RESOURCE_FILE
    await fs.write_file(ttl.ttl_metadata_uri(kind, uri), json.dumps(fields), ctx=root_ctx())
    return fields


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cp", "mv"])
@pytest.mark.parametrize("is_dir", [False, True])
async def test_transfer_keeps_each_file_deadline(binding_fs, operation, is_dir):
    fs, ctx = binding_fs, root_ctx()
    source = ROOT + ("/source" if is_dir else "/source.txt")
    target = ROOT + "/target"
    source_files = [source + "/part.txt", source + "/nested/other.bin"] if is_dir else [source]
    for index, payload in enumerate(source_files):
        await fs.write_file_bytes(payload, f"original {index}".encode(), ctx=ctx)
        await set_expiry(
            fs,
            payload,
            expiry=f"299{9 - index}-01-01T00:00:00.000Z",
            generation=f"g{index + 1}",
        )
    expected = {uri: await resource_ttl_fields(fs, uri, ctx=ctx) for uri in source_files}
    await getattr(fs, operation)(
        source, target, ctx=ctx, **({"recursive": is_dir} if operation == "cp" else {})
    )
    for index, source_file in enumerate(source_files):
        target_file = target + source_file[len(source) :]
        assert await fs.read_file_bytes(target_file, ctx=ctx) == f"original {index}".encode()
        assert await resource_ttl_fields(fs, target_file, ctx=ctx) == expected[source_file]
        record = await fs.ttl_registry.get(ctx.account_id, target_file)
        assert record.expires_at == expected[source_file]["expires_at"]
        source_record = await fs.ttl_registry.get(ctx.account_id, source_file)
        if operation == "mv":
            assert source_record is None
        else:
            assert source_record is not None
    if is_dir:
        assert await resource_ttl_fields(fs, target, ctx=ctx) == {}
        assert await fs.ttl_registry.get(ctx.account_id, target) is None
    assert await fs.exists(source, ctx=ctx) == (operation == "cp")


@pytest.mark.asyncio
async def test_partial_cleanup_blocks_recreation_and_retry_removes_only_file(
    binding_fs, monkeypatch
):
    fs, ctx = binding_fs, root_ctx()
    uri, sibling = ROOT + "/expired", ROOT + "/keep"
    await fs.write_file_bytes(uri, b"delete", ctx=ctx)
    await fs.write_file_bytes(sibling, b"keep", ctx=ctx)
    await set_expiry(fs, uri, expiry=PAST)
    record = await fs.ttl_registry.get(ctx.account_id, uri)
    cleanup = TTLCleanupService(
        service=SimpleNamespace(viking_fs=fs, fs=SimpleNamespace(rm=fs.rm)),
        service_loop=asyncio.get_running_loop(),
    )
    original = fs._confirm_fs_scope_cleared
    monkeypatch.setattr(
        fs,
        "_confirm_fs_scope_cleared",
        AsyncMock(side_effect=RuntimeError("confirmation unavailable")),
    )
    with pytest.raises(StorageException, match="confirmation unavailable"):
        await _cleanup_once(cleanup, record)
    assert await fs.ttl_registry.get(ctx.account_id, uri) is not None
    with pytest.raises(NotFoundError):
        await prepare_resource_ttl(fs, uri, is_dir=False, existing=False, ctx=ctx, lease_ref=None)
    monkeypatch.setattr(fs, "_confirm_fs_scope_cleared", original)
    assert (await _cleanup_once(cleanup, record))["deleted"]
    assert await fs.ttl_registry.get(ctx.account_id, uri) is None
    assert await fs.read_file_bytes(sibling, ctx=ctx) == b"keep"
    metadata = ttl.ttl_metadata_uri("resource_file", uri)
    # The hidden sidecar survives as a Watch tombstone. It is removed by an
    # explicit recreate/delete and never makes the expired source visible.
    assert await fs._async_agfs.stat(fs._uri_to_path(metadata, ctx=ctx))
    assert (await resource_ttl_fields(fs, uri, ctx=ctx))["content_md5"] == content_md5(
        b"delete"
    )
    assert not await fs.exists(uri, ctx=ctx)


@pytest.mark.asyncio
async def test_legacy_directory_expiry_retires_only_metadata(binding_fs):
    fs, ctx = binding_fs, root_ctx()
    uri = ROOT + "/legacy"
    child = uri + "/still-live.txt"
    await fs.write_file_bytes(child, b"keep", ctx=ctx)
    await set_expiry(fs, uri, is_dir=True, expiry=PAST)
    record = await fs.ttl_registry.get(ctx.account_id, uri)
    cleanup = TTLCleanupService(
        service=SimpleNamespace(viking_fs=fs, fs=SimpleNamespace(rm=fs.rm)),
        service_loop=asyncio.get_running_loop(),
    )

    result = await cleanup._cleanup_record(record)

    assert result == {"deleted": False, "skipped": "legacy_resource_directory"}
    assert await fs.read_file_bytes(child, ctx=ctx) == b"keep"
    assert await fs.exists(uri, ctx=ctx)
    assert not await fs.exists(ttl.ttl_metadata_uri("resource", uri), ctx=ctx)
    assert await fs.ttl_registry.get(ctx.account_id, uri) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_directory", [False, True])
async def test_ordinary_delete_removes_exact_file_ttl_projections(
    binding_fs, delete_directory
):
    fs, ctx = binding_fs, root_ctx()
    root = ROOT + "/delete"
    files = [root + "/one.txt", root + "/nested/two.txt"] if delete_directory else [root]
    for index, uri in enumerate(files):
        await fs.write_file_bytes(uri, f"body {index}".encode(), ctx=ctx)
        await set_expiry(fs, uri, generation=f"delete-{index}")
        assert await fs.ttl_registry.get(ctx.account_id, uri) is not None

    await fs.rm(root, recursive=delete_directory, ctx=ctx)

    for uri in files:
        assert await fs.ttl_registry.get(ctx.account_id, uri) is None
        assert not await fs.exists(ttl.ttl_metadata_uri("resource_file", uri), ctx=ctx)


@pytest.mark.asyncio
async def test_ovpack_round_trips_each_file_deadline_and_cleanup_cannot_interleave(
    binding_fs, tmp_path, monkeypatch
):
    fs, ctx = binding_fs, root_ctx()
    source = ROOT + "/owner/child"
    source_files = [source + "/part.txt", source + "/nested/other.txt"]
    for index, uri in enumerate(source_files):
        await fs.write_file_bytes(uri, f"original {index}".encode(), ctx=ctx)
        await set_expiry(fs, uri, generation=f"g{index + 1}")
    expected = {uri: await resource_ttl_fields(fs, uri, ctx=ctx) for uri in source_files}
    archive = await export_ovpack(fs, source, str(tmp_path / "child.ovpack"), ctx)
    parent = ROOT + "/restored"
    await fs.mkdir(parent, ctx=ctx)
    target = parent + "/child"
    real_write = fs.write_file_bytes
    attempted_cleanup = []

    async def write(uri, data, **kwargs):
        await real_write(uri, data, **kwargs)
        if uri == target + "/.part.txt.ttl.json":
            from openviking.storage.errors import LockAcquisitionError

            with pytest.raises(LockAcquisitionError):
                await fs._async_agfs.pathlock_acquire_tree(fs._uri_to_path(target, ctx=ctx))
            attempted_cleanup.append(True)

    monkeypatch.setattr(fs, "write_file_bytes", write)
    monkeypatch.setattr(
        "openviking.storage.ovpack.operations._enqueue_direct_vectorization", AsyncMock()
    )
    assert await import_ovpack(fs, archive, parent, ctx) == target
    assert attempted_cleanup
    for index, source_file in enumerate(source_files):
        target_file = target + source_file[len(source) :]
        assert await resource_ttl_fields(fs, target_file, ctx=ctx) == expected[source_file]
        assert await fs.read_file_bytes(target_file, ctx=ctx) == f"original {index}".encode()
        assert (await fs.ttl_registry.get(ctx.account_id, target_file)).generation == expected[
            source_file
        ]["ttl_generation"]
    assert await resource_ttl_fields(fs, target, ctx=ctx) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["copy", "vectors"])
async def test_failed_subtree_copy_keeps_only_published_ttl_records(
    binding_fs, monkeypatch, failure
):
    fs, ctx = binding_fs, root_ctx()
    source, target = ROOT + "/owner/child", ROOT + "/copied"
    await fs.write_file_bytes(source, b"body", ctx=ctx)
    await set_expiry(fs, source)
    if failure == "copy":
        original = fs._copy_agfs_entry

        async def fail_after_copy(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("copy interrupted")

        monkeypatch.setattr(fs, "_copy_agfs_entry", fail_after_copy)
    else:
        monkeypatch.setattr(
            fs,
            "_copy_vector_store_uris",
            AsyncMock(side_effect=RuntimeError("vectors unavailable")),
        )
    with pytest.raises(RuntimeError):
        await fs.cp(source, target, ctx=ctx)
    record = await fs.ttl_registry.get(ctx.account_id, target)
    if failure == "copy":
        assert record is not None
        assert await fs.read_file_bytes(target, ctx=ctx) == b"body"
    else:
        assert record is None
        assert await resource_ttl_fields(fs, target, ctx=ctx) == {}
        assert not await fs.exists(target, ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["live", "expired", "recreated", "updated"])
async def test_late_resource_embedding_validates_source_generation_and_content(
    binding_fs, monkeypatch, state
):
    from openviking.storage.collection_schemas import TextEmbeddingHandler
    from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
    from openviking.utils.content_hash import content_md5

    fs, ctx = binding_fs, root_ctx()
    uri = ROOT + "/document"
    await fs.write_file_bytes(uri, b"updated" if state == "updated" else b"original", ctx=ctx)
    await set_expiry(fs, uri, is_dir=False, expiry=PAST if state == "expired" else FUTURE)
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    message = EmbeddingMsg(
        "original",
        {
            "uri": uri,
            "ttl_generation": "old" if state == "recreated" else "g1",
            "md5": content_md5(b"original"),
        },
    )
    write = AsyncMock(return_value="vector-id")
    result = await object.__new__(TextEmbeddingHandler)._write_ttl_vector_if_current(
        message, ctx, write
    )
    assert result == ("vector-id" if state == "live" else None)
    assert write.await_count == int(state == "live")
