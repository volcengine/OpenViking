# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real filesystem coverage of root policies and event content-write lifetimes."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from openviking.core import ttl
from openviking.storage.directory_ttl import read_directory_fields
from openviking.storage.ttl_view import TTLView
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.utils.config.ttl_config import TTLConfig
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import root_ctx
from tests.unit.storage.ttl_test_storage import read_record


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    config = TTLConfig.model_validate({"global": {"mode": "days", "ttl_days": 7}})
    monkeypatch.setattr(ttl, "get_openviking_config", lambda: SimpleNamespace(ttl=config))
    return config


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["viking://user/default", "viking://user/default/peers/p"])
async def test_event_deadline_is_frozen_on_first_content_write(binding_fs, prefix):
    fs, ctx = binding_fs, root_ctx()
    root = prefix + "/memories/events/2026/09/28"
    await fs.mkdir(root, ctx=ctx)
    assert await read_directory_fields(fs, root, ctx=ctx) == {}
    await fs.write_file(root + "/a.md", "first", ctx=ctx)
    initial = await read_directory_fields(fs, root, ctx=ctx)
    assert parse_iso_datetime(initial["expires_at"]) - parse_iso_datetime(
        initial["received_at"]
    ) == timedelta(days=7)
    assert set(initial) == {"expires_at", "received_at"}
    for name in ["a.md", "b.txt", ".abstract.md", ".overview.md", "nested/c.json"]:
        await fs.write_file(root + "/" + name, "updated", ctx=ctx)
        assert await read_directory_fields(fs, root + "/" + name, ctx=ctx) == initial
    assert await read_record(fs, ctx.account_id, root + "/a.md") is None
    assert (await TTLView(fs, ctx).fields(root))["expires_at"] == initial["expires_at"]


@pytest.mark.asyncio
async def test_content_writes_do_not_implicitly_reapply_policy(binding_fs, enabled):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events"
    enabled.global_default.mode = "disabled"
    enabled.global_default.ttl_days = None
    await fs.write_file(root + "/2026/09/27/a.md", "legacy", ctx=ctx)
    enabled.global_default.mode = "days"
    enabled.global_default.ttl_days = 7
    await fs.write_file(root + "/2026/09/27/b.md", "new content in old bucket", ctx=ctx)
    assert not (await read_directory_fields(fs, root + "/2026/09/27", ctx=ctx)).get("expires_at")
    await fs.write_file(root + "/2026/09/28/a.md", "new bucket", ctx=ctx)
    initial = await read_directory_fields(fs, root + "/2026/09/28", ctx=ctx)
    enabled.global_default.ttl_days = 30
    await fs.write_file(root + "/2026/09/28/a.md", "update", ctx=ctx)
    assert await read_directory_fields(fs, root + "/2026/09/28", ctx=ctx) == initial
    await fs.write_file(root + "/2026/09/29/a.md", "next bucket", ctx=ctx)
    fields = await read_directory_fields(fs, root + "/2026/09/29", ctx=ctx)
    assert parse_iso_datetime(fields["expires_at"]) - parse_iso_datetime(
        fields["received_at"]
    ) == timedelta(days=30)
    assert await TTLView(fs, ctx).fields(root + "/2026/09", is_dir=True) == {
        "expires_at": None,
    }


@pytest.mark.asyncio
async def test_failed_first_write_does_not_start_lifetime(binding_fs, monkeypatch):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/28"
    uri = root + "/a.md"
    real_write = fs._async_agfs.write

    async def fail_content(path, data, **kwargs):
        if path.endswith("/a.md"):
            raise OSError("content failure")
        return await real_write(path, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(fs._async_agfs, "write", fail_content)
        with pytest.raises(OSError, match="content failure"):
            await fs.write_file(uri, "hello", ctx=ctx)
    assert not (await read_directory_fields(fs, root, ctx=ctx)).get("expires_at")
    assert await read_record(fs, ctx.account_id, root) is None


@pytest.mark.asyncio
async def test_failed_first_write_cannot_clear_successful_sibling_lifetime(binding_fs, monkeypatch):
    """Two commits target one date; the first storage write fails mid-flight."""
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/28"
    entered, sibling_ready, fail_first = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_write = fs._async_agfs.write
    original_lock = fs._async_agfs.pathlock_acquire_exact

    async def write(path, data, **kwargs):
        if path.endswith("/first.md"):
            entered.set()
            await fail_first.wait()
            raise OSError("first body failed")
        if path.endswith("/second.md"):
            sibling_ready.set()
        return await original_write(path, data, **kwargs)

    async def acquire(path, **kwargs):
        if asyncio.current_task().get_name() == "sibling" and path.endswith("/.meta.json"):
            sibling_ready.set()
        return await original_lock(path, **kwargs)

    monkeypatch.setattr(fs._async_agfs, "write", write)
    monkeypatch.setattr(fs._async_agfs, "pathlock_acquire_exact", acquire)
    first = asyncio.create_task(fs.write_file(root + "/first.md", "first", ctx=ctx))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(
            fs.write_file(root + "/second.md", "second", ctx=ctx), name="sibling"
        )
        await asyncio.wait_for(sibling_ready.wait(), 5)
    finally:
        fail_first.set()
        results = await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
    assert isinstance(results[0], OSError)
    assert len(results) == 2 and not isinstance(results[1], BaseException)
    assert await fs.read_file(root + "/second.md", ctx=ctx) == "second"
    fields = await read_directory_fields(fs, root, ctx=ctx)
    assert fields.get("received_at") and fields.get("expires_at")


@pytest.mark.asyncio
async def test_legacy_event_metadata_preserves_existing_deadline(binding_fs):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/28"
    fields = {"expires_at": "2999-01-01T00:00:00Z", "ttl_days": 7, "ttl_generation": "legacy"}
    await fs.write_file(root + "/.ttl.json", json.dumps(fields), ctx=ctx)
    await fs.write_file(root + "/new.md", "legacy bucket", ctx=ctx)
    assert await read_directory_fields(fs, root, ctx=ctx) == fields
    assert (await TTLView(fs, ctx).fields(root + "/new.md"))["expires_at"] == fields["expires_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cp", "mv"])
@pytest.mark.parametrize("metadata", [".meta.json", ".ttl.json"])
async def test_transfer_bucket_preserves_deadline(binding_fs, operation, metadata):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/28"
    dest = "viking://user/default/memories/events/2026/09/29"
    await fs.write_file(root + "/a.md", "hello", ctx=ctx)
    initial = await read_directory_fields(fs, root, ctx=ctx)
    if metadata == ".ttl.json":
        await fs.write_file(root + "/.ttl.json", json.dumps(initial), ctx=ctx)
        await fs._async_agfs.rm(fs._uri_to_path(root + "/.meta.json", ctx=ctx))
    await fs.mkdir("viking://resources", ctx=ctx)
    with pytest.raises(InvalidArgumentError, match="preserve the source TTL owner"):
        await getattr(fs, operation)(root + "/a.md", "viking://resources/detached.md", ctx=ctx)
    await getattr(fs, operation)(
        root, dest, ctx=ctx, **({"recursive": True} if operation == "cp" else {})
    )
    assert await read_directory_fields(fs, dest, ctx=ctx) == initial


@pytest.mark.asyncio
async def test_concurrent_sibling_writes_share_one_lifetime(binding_fs):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/28"
    await asyncio.wait_for(
        asyncio.gather(
            *[fs.write_file(f"{root}/{i}.txt", f"content {i}", ctx=ctx) for i in range(8)]
        ),
        timeout=10,
    )
    fields = await read_directory_fields(fs, root, ctx=ctx)
    assert parse_iso_datetime(fields["expires_at"]) - parse_iso_datetime(
        fields["received_at"]
    ) == timedelta(days=7)
    assert len(await fs.ls(root, ctx=ctx)) == 8


@pytest.mark.asyncio
async def test_first_content_in_empty_nested_directory_gets_ttl(binding_fs):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/30"
    await fs.mkdir(root + "/nested", ctx=ctx)
    await fs.write_file(root + "/nested/a.md", "first content", ctx=ctx)
    fields = await read_directory_fields(fs, root, ctx=ctx)
    assert parse_iso_datetime(fields["expires_at"]) - parse_iso_datetime(
        fields["received_at"]
    ) == timedelta(days=7)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["ls", "tree", "stat", "get_ttl"])
async def test_public_read_shares_owner_deadline_without_caching_next_request(
    binding_fs, monkeypatch, operation
):
    from unittest.mock import AsyncMock

    import openviking.storage.ttl_view as module
    from openviking.service.fs_service import FSService

    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/30"
    for name in ("a.md", "b.md", "c.md"):
        await fs.write_file(root + "/" + name, "content", ctx=ctx)
    service = FSService(viking_fs=fs)
    reader = AsyncMock(wraps=module.read_directory_fields)
    monkeypatch.setattr(module, "read_directory_fields", reader)
    uri = root if operation in {"ls", "tree"} else root + "/a.md"
    result = await getattr(service, operation)(uri, ctx=ctx)
    rows = result.entries if operation in {"ls", "tree"} else [result]
    assert len(rows) == (3 if operation in {"ls", "tree"} else 1)
    assert all(row["expires_at"] == rows[0]["expires_at"] for row in rows)
    reader.assert_awaited_once()
    await fs.write_file(root + "/.meta.json", '{"expires_at":"2000-01-01T00:00:00Z"}', ctx=ctx)
    with pytest.raises(NotFoundError):
        await getattr(service, operation)(uri, ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["memories/events/2026/09/28", "sessions/s1"])
async def test_saved_deadline_hides_reads_but_allows_internal_cleanup_access(binding_fs, owner):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/" + owner
    uri = root + "/body.md"
    await fs.write_file(root + "/.meta.json", '{"expires_at":null}', ctx=ctx)
    await fs.write_file(uri, "body", ctx=ctx)
    assert await fs.read_file(uri, ctx=ctx) == "body"
    await fs.write_file(root + "/.meta.json", '{"expires_at":"2000-01-01T00:00:00Z"}', ctx=ctx)
    for read in (fs.read_file, fs.read_file_bytes, fs.stat):
        with pytest.raises(NotFoundError):
            await read(uri, ctx=ctx)
    assert not await fs.exists(uri, ctx=ctx)
    assert await fs.read_file(uri, ctx=ctx, include_expired=True) == "body"
    assert await fs.read_file_bytes(uri, ctx=ctx, include_expired=True) == b"body"
    assert (await fs.stat(uri, ctx=ctx, include_expired=True))["name"] == "body.md"
    assert await fs.exists(uri, ctx=ctx, include_expired=True)


@pytest.mark.asyncio
async def test_lists_fill_limit_after_hiding_expired_owners(binding_fs):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09"
    for day, year in [("28", 2000), ("29", 2999)]:
        await fs.write_file(f"{root}/{day}/body.md", "body", ctx=ctx)
        await fs.write_file(
            f"{root}/{day}/.meta.json",
            json.dumps({"expires_at": f"{year}-01-01T00:00:00Z"}),
            ctx=ctx,
        )
    for listing in (fs.ls, fs.tree):
        rows = await listing(root, node_limit=1, ctx=ctx)
        assert [row["uri"] for row in rows] == [root + "/29"]
