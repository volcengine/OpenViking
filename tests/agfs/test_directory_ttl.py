# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real filesystem coverage of root policies and immutable event lifetimes."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from openviking.core import ttl
from openviking.storage.directory_ttl import read_directory_fields
from openviking.storage.ttl_view import TTLView
from openviking_cli.utils.config.ttl_config import TTLConfig
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import root_ctx


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
    assert initial["ttl_days"] == 7
    assert "ttl_generation" not in initial
    for name in ["a.md", "b.txt", ".abstract.md", ".overview.md", "nested/c.json"]:
        await fs.write_file(root + "/" + name, "updated", ctx=ctx)
        assert await read_directory_fields(fs, root + "/" + name, ctx=ctx) == initial
    assert (await fs.ttl_registry.get(ctx.account_id, root)).expires_at == initial["expires_at"]
    assert await fs.ttl_registry.get(ctx.account_id, root + "/a.md") is None
    assert (await fs._async_agfs.stat(fs._uri_to_path(root, ctx=ctx)))["expires_at"] == initial[
        "expires_at"
    ]


@pytest.mark.asyncio
async def test_only_new_buckets_inherit_changed_policy(binding_fs, enabled):
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
    assert (await read_directory_fields(fs, root + "/2026/09/29", ctx=ctx))["ttl_days"] == 30
    assert await TTLView(fs, ctx).fields(root + "/2026/09", is_dir=True) == {
        "expires_at": None,
        "ttl_days": None,
    }


@pytest.mark.asyncio
async def test_first_write_failure_and_interrupted_finalization(binding_fs, monkeypatch):
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
    assert await fs.ttl_registry.get(ctx.account_id, root) is None

    async def fail_finalize(path, data, **kwargs):
        if path.endswith("/28/.meta.json") and b"_ttl_pending" not in data:
            raise OSError("finalization failure")
        return await real_write(path, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(fs._async_agfs, "write", fail_finalize)
        with pytest.raises(OSError, match="finalization failure"):
            await fs.write_file(uri, "durable", ctx=ctx)
    assert await fs.read_file(uri, ctx=ctx) == "durable"
    assert (await read_directory_fields(fs, root, ctx=ctx))["ttl_days"] == 7


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
async def test_transfer_bucket_preserves_deadline(binding_fs, operation):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/28"
    dest = "viking://user/default/memories/events/2026/09/29"
    await fs.write_file(root + "/a.md", "hello", ctx=ctx)
    initial = await read_directory_fields(fs, root, ctx=ctx)
    await getattr(fs, operation)(
        root, dest, ctx=ctx, **({"recursive": True} if operation == "cp" else {})
    )
    assert await read_directory_fields(fs, dest, ctx=ctx) == initial
    assert (await fs.ttl_registry.get(ctx.account_id, dest)).expires_at == initial["expires_at"]


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
    assert fields["ttl_days"] == 7
    assert len(await fs.ls(root, ctx=ctx)) == 8


@pytest.mark.asyncio
async def test_native_directory_metadata_patch_preserves_business_fields(binding_fs):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/sessions/metadata"
    await fs.write_file(
        root + "/.meta.json",
        json.dumps({"session_id": "metadata", "config": {"keep": True}}),
        ctx=ctx,
    )
    path = fs._uri_to_path(root, ctx=ctx)
    await asyncio.gather(
        *[fs._async_agfs.update_directory_metadata(path, {f"field_{i}": i}) for i in range(5)]
    )
    await fs._async_agfs.update_directory_metadata(
        path, {"expires_at": "2999-01-01T00:00:00Z", "ttl_days": 7}
    )
    result = await read_directory_fields(fs, root, ctx=ctx)
    assert result["session_id"] == "metadata" and result["config"] == {"keep": True}
    assert all(result[f"field_{i}"] == i for i in range(5))
    assert (await fs._async_agfs.stat(path))["expires_at"] == result["expires_at"]
    await fs._async_agfs.update_directory_metadata(path, {"expires_at": None})
    assert (await fs._async_agfs.stat(path))["expires_at"] is None


@pytest.mark.asyncio
async def test_first_content_in_empty_nested_directory_gets_ttl(binding_fs):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/30"
    await fs.mkdir(root + "/nested", ctx=ctx)
    await fs.write_file(root + "/nested/a.md", "first content", ctx=ctx)
    assert (await read_directory_fields(fs, root, ctx=ctx))["ttl_days"] == 7


@pytest.mark.asyncio
async def test_batch_projection_reads_one_owner_once(binding_fs, monkeypatch):
    from unittest.mock import AsyncMock

    import openviking.storage.ttl_view as module

    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/30"
    await fs.write_file(root + "/a.md", "content", ctx=ctx)
    reader = AsyncMock(wraps=module.read_directory_fields)
    monkeypatch.setattr(module, "read_directory_fields", reader)
    rows = await TTLView(fs, ctx).attach_many([{"uri": f"{root}/{i}.md"} for i in range(100)])
    assert len(rows) == 100 and all(row["expires_at"] == rows[0]["expires_at"] for row in rows)
    reader.assert_awaited_once()
