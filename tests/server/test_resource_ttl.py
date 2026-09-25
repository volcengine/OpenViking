# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Public resource TTL through the real import pipeline and runtime policy store."""

import asyncio
from datetime import timedelta

import pytest

from openviking.service.task_tracker import get_task_tracker
from openviking.storage.resource_ttl import prepare_resource_ttl, resource_ttl_fields
from openviking.utils.content_hash import content_md5
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils.config.open_viking_config import (
    get_openviking_config,
    set_openviking_config,
)
from tests.storage.test_transfer_merge_binding import root_ctx

ROOT = "viking://user/default/resources"


@pytest.fixture(autouse=True)
def restore_cluster_config():
    # Runtime PATCH publishes a process-wide singleton; keep later tests isolated.
    original = get_openviking_config()
    yield
    set_openviking_config(original)


@pytest.mark.asyncio
@pytest.mark.parametrize("parse_mode", ["default", "no_split"])
@pytest.mark.parametrize("root", [ROOT, "viking://resources"])
async def test_import_assigns_per_file_ttl_and_updates_use_file_mtime(
    client, service, upload_temp_dir, parse_mode, root
):
    source = upload_temp_dir / "ttl-resource.md"
    source.write_text("# Guide\n\nAn imported resource with a frozen lifetime.\n")
    await service.viking_fs.mkdir(root, exist_ok=True, ctx=root_ctx())
    request = {
        "temp_file_id": source.name,
        **({"parent": root} if parse_mode == "no_split" else {"to": root + "/ttl-resource"}),
        "ttl_relative": 7,
        "wait": True,
        "args": {"parse_mode": parse_mode},
    }
    if root == "viking://resources":
        await service.runtime_config_manager.patch_account("default", {"acl": {"enabled": True}})
        request["acl"] = {
            "acl_mode": "restricted",
            "entries": [
                {"principal": "user:default", "level": "manage"},
                {"principal": "user:reader", "level": "read"},
            ],
        }
    response = await asyncio.wait_for(client.post("/api/v1/resources", json=request), 25)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    uri = result["root_uri"]
    if "acl" in request:
        acl = await service.viking_fs.get_acl(uri, ctx=root_ctx())
        assert acl["acl_mode"] == "restricted"
        assert acl["direct_entries"] == request["acl"]["entries"]
    if parse_mode == "no_split":
        file_uri = uri
    else:
        entries = await service.viking_fs.tree(
            uri, node_limit=None, level_limit=None, ctx=root_ctx()
        )
        file_uri = next(
            entry["uri"]
            for entry in entries
            if not entry.get("isDir") and not entry.get("name", "").startswith(".")
        )
    response = await client.get("/api/v1/resources/ttl", params={"uri": file_uri})
    assert response.status_code == 200, response.text
    frozen = response.json()["result"]
    assert parse_iso_datetime(frozen["expires_at"]) - parse_iso_datetime(
        frozen["received_at"]
    ) == timedelta(days=7)
    record = await service.viking_fs.ttl_registry.get("default", file_uri)
    assert record.object_type == "resource_file"
    assert (await resource_ttl_fields(service.viking_fs, file_uri, ctx=root_ctx()))[
        "content_md5"
    ]
    root_record = await service.viking_fs.ttl_registry.get("default", uri)
    if file_uri == uri:
        assert root_record == record
    else:
        assert root_record is None
    await asyncio.sleep(0.01)
    source.write_text("# Guide\n\nUpdated resource body.\n")
    request["ttl_relative"] = 30
    if parse_mode == "no_split":
        result = await asyncio.wait_for(
            service.resources.refresh_resource(
                path=str(source),
                to=uri,
                to_is_directory=False,
                ctx=root_ctx(),
                ttl_relative=30,
                args={"parse_mode": "no_split"},
            ),
            25,
        )
        assert result["root_uri"] == uri
        task = await get_task_tracker().wait(
            result["task_id"], account_id="default", user_id="default", timeout=25
        )
        assert task.status.value == "completed", task.error
    else:
        response = await asyncio.wait_for(client.post("/api/v1/resources", json=request), 25)
        assert response.status_code == 200, response.text
    response = await client.get("/api/v1/resources/ttl", params={"uri": file_uri})
    renewed = response.json()["result"]
    assert renewed["ttl_days"] == 7
    assert renewed["ttl_generation"] == frozen["ttl_generation"]
    assert parse_iso_datetime(renewed["received_at"]) > parse_iso_datetime(
        frozen["received_at"]
    )
    assert parse_iso_datetime(renewed["expires_at"]) - parse_iso_datetime(
        renewed["received_at"]
    ) == timedelta(days=7)


@pytest.mark.asyncio
@pytest.mark.parametrize("root", [ROOT, "viking://resources"])
async def test_directory_policy_is_incremental_and_keeps_cluster_defaults(client, service, root):
    fs, ctx = service.viking_fs, root_ctx()
    manager = service._runtime_config_manager
    await manager.patch_cluster({"ttl": {"resources": {"mode": "days", "ttl_days": 30}}})
    policy_uri = root + "/policy"
    response = await client.patch(
        "/api/v1/resources/config", json={"uri": policy_uri, "ttl_relative": 7}
    )
    assert response.status_code == 200, response.text
    first = await prepare_resource_ttl(
        fs, policy_uri + "/one.txt", is_dir=False, existing=False, ctx=ctx, lease_ref=None
    )
    outside = await prepare_resource_ttl(
        fs, root + "/outside.txt", is_dir=False, existing=False, ctx=ctx, lease_ref=None
    )
    assert first["ttl_days"] == 7
    assert outside["ttl_days"] == 30
    response = await client.patch(
        "/api/v1/resources/config", json={"uri": policy_uri, "ttl_relative": 14}
    )
    assert response.status_code == 200, response.text
    unchanged = await resource_ttl_fields(fs, policy_uri + "/one.txt", ctx=ctx)
    new = await prepare_resource_ttl(
        fs, policy_uri + "/two.txt", is_dir=False, existing=False, ctx=ctx, lease_ref=None
    )
    assert unchanged == first
    assert new["ttl_days"] == 14
    response = await client.patch("/api/v1/resources/config", json={"uri": policy_uri})
    assert response.status_code == 200, response.text
    disabled = await prepare_resource_ttl(
        fs, policy_uri + "/three.txt", is_dir=False, existing=False, ctx=ctx, lease_ref=None
    )
    assert disabled == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ttl",
    [{"ttl_relative": 0}, {"ttl_relative": True}, {"ttl_relative": 1, "ttl_absolute": 2000000000}],
)
async def test_api_rejects_invalid_ttl_before_import(client, ttl):
    response = await client.post(
        "/api/v1/resources", json={"path": "https://example.com/a.md", **ttl}
    )
    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_parent_watch_updates_live_files_without_restoring_unchanged_expired_file(
    service, upload_temp_dir
):
    fs, ctx = service.viking_fs, root_ctx()
    source = upload_temp_dir / "source"
    source.mkdir()
    expired_source = source / "expired.md"
    live_source = source / "live.md"
    expired_body = "# Expired\n\nSource retained outside OpenViking."
    expired_source.write_text(expired_body)
    live_source.write_text("# Live\n\nVersion one.")
    root = ROOT + "/watched"
    await fs.mkdir(ROOT, exist_ok=True, ctx=ctx)
    await service.resources.add_resource(
        str(source), ctx=ctx, to=root, wait=True, processing_mode="vectors_only", build_index=False
    )
    await service.runtime_config_manager.patch_account(
        ctx.account_id,
        {
            "ttl": {
                "resources": {"mode": "days", "ttl_days": 7},
                "directories": {root: {"mode": "days", "ttl_days": 7}},
            }
        },
    )
    entries = await fs.tree(root, node_limit=None, level_limit=None, ctx=ctx)
    expired_uri = next(
        entry["uri"] for entry in entries if entry["uri"].endswith("/expired.md")
    )
    live_uri = next(entry["uri"] for entry in entries if entry["uri"].endswith("/live.md"))
    expired_fields = await prepare_resource_ttl(
        fs, expired_uri, is_dir=False, existing=False, ctx=ctx, lease_ref=None
    )
    await prepare_resource_ttl(
        fs, live_uri, is_dir=False, existing=False, ctx=ctx, lease_ref=None
    )
    expired_fields["expires_at"] = "2000-01-01T00:00:00.000Z"
    expired_fields["content_md5"] = content_md5(expired_body.encode())
    from openviking.storage.resource_ttl import write_resource_fields

    await write_resource_fields(
        fs, "resource_file", expired_uri, expired_fields, ctx=ctx, lease_ref=None
    )
    await fs.rm(expired_uri, strict=True, preserve_summaries=True, ctx=ctx)
    await fs.ttl_registry.remove_if_generation(
        ctx.account_id, expired_uri, expired_fields["ttl_generation"]
    )
    live_source.write_text("# Live\n\nVersion two.")
    result = await service.resources.refresh_resource(
        str(source), ctx=ctx, to=root, processing_mode="vectors_only", build_index=False
    )
    task = await get_task_tracker().wait(
        result["task_id"], account_id=ctx.account_id, user_id=ctx.user.user_id, timeout=25
    )
    assert task.status.value == "completed", task.error
    assert not await fs.exists(expired_uri, ctx=ctx)
    assert await fs.read_file(live_uri, ctx=ctx) == "# Live\n\nVersion two."

    expired_source.write_text("# Expired\n\nA new source version.")
    result = await service.resources.refresh_resource(
        str(source), ctx=ctx, to=root, processing_mode="vectors_only", build_index=False
    )
    task = await get_task_tracker().wait(
        result["task_id"], account_id=ctx.account_id, user_id=ctx.user.user_id, timeout=25
    )
    assert task.status.value == "completed", task.error
    assert await fs.read_file(expired_uri, ctx=ctx) == "# Expired\n\nA new source version."


@pytest.mark.asyncio
async def test_single_file_watch_only_restores_expired_file_after_source_changes(
    client, service, upload_temp_dir
):
    fs, ctx = service.viking_fs, root_ctx()
    source = upload_temp_dir / "single.md"
    first_body = "# Watched file\n\nVersion one."
    source.write_text(first_body)
    await fs.mkdir(ROOT, exist_ok=True, ctx=ctx)
    response = await client.post(
        "/api/v1/resources",
        json={
            "temp_file_id": source.name,
            "parent": ROOT,
            "ttl_relative": 7,
            "wait": True,
            "args": {"parse_mode": "no_split"},
            "processing_mode": "vectors_only",
        },
    )
    assert response.status_code == 200, response.text
    uri = response.json()["result"]["root_uri"]
    assert not (await fs.stat(uri, ctx=ctx))["isDir"]
    stored_body = await fs.read_file(uri, ctx=ctx)
    fields = await resource_ttl_fields(fs, uri, ctx=ctx)
    fields["expires_at"] = "2000-01-01T00:00:00.000Z"
    fields["content_md5"] = content_md5(stored_body.encode())
    from openviking.storage.resource_ttl import write_resource_fields

    await write_resource_fields(fs, "resource_file", uri, fields, ctx=ctx, lease_ref=None)
    await fs.remove_files(uri, ctx=ctx)
    await fs.ttl_registry.remove_if_generation(ctx.account_id, uri, fields["ttl_generation"])

    result = await service.resources.refresh_resource(
        str(source),
        ctx=ctx,
        to=uri,
        to_is_directory=False,
        processing_mode="vectors_only",
        build_index=False,
        args={"parse_mode": "no_split"},
    )
    task = await get_task_tracker().wait(
        result["task_id"], account_id=ctx.account_id, user_id=ctx.user.user_id, timeout=25
    )
    assert task.status.value == "completed", task.error
    with pytest.raises(NotFoundError):
        await fs.read_file(uri, ctx=ctx)

    second_body = "# Watched file\n\nVersion two."
    source.write_text(second_body)
    result = await service.resources.refresh_resource(
        str(source),
        ctx=ctx,
        to=uri,
        to_is_directory=False,
        processing_mode="vectors_only",
        build_index=False,
        args={"parse_mode": "no_split"},
    )
    task = await get_task_tracker().wait(
        result["task_id"], account_id=ctx.account_id, user_id=ctx.user.user_id, timeout=25
    )
    assert task.status.value == "completed", task.error
    assert await fs.read_file(uri, ctx=ctx) == second_body
