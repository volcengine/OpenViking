# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Check update lock continuity using the real filesystem and HTTP endpoints."""

import asyncio

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import skills as skills_router
from openviking.storage.errors import LockAcquisitionError
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_api_skills import _add_skill, _skill_md
from tests.server.test_api_skills import _stub_mcp_endpoint as _stub_mcp_endpoint


def _ctx():
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


async def _assert_locked(fs, root):
    other_lease = None
    try:
        with pytest.raises(LockAcquisitionError):
            other_lease = await fs._async_agfs.pathlock_acquire_tree(
                fs._uri_to_path(root, ctx=_ctx()), timeout_secs=0.01
            )
    finally:
        if other_lease is not None:
            await fs._async_agfs.pathlock_release(other_lease)


@pytest.mark.parametrize("competing_operation", ["add", "update", "delete"])
async def test_update_lock_blocks_same_package_until_failed_update_finishes(
    client, service, monkeypatch, competing_operation
):
    name = f"update-lock-{competing_operation}"
    added = await _add_skill(client, name, "Original")
    root = added["root_uri"]
    fs = service.viking_fs
    hidden_uri = f"{root}/.custom.json"
    await fs.write_file(hidden_uri, '{"original": true}', ctx=_ctx())
    original_definition = await fs.read_file_bytes(f"{root}/SKILL.md", ctx=_ctx())
    paused = asyncio.Event()
    resume = asyncio.Event()
    original_clear = skills_router.clear_skill_package_contents

    async def fail_after_clear(*args, **kwargs):
        await original_clear(*args, **kwargs)
        paused.set()
        await resume.wait()
        raise RuntimeError("injected preparation failure after clearing the package")

    monkeypatch.setattr(skills_router, "clear_skill_package_contents", fail_after_clear)
    updating = asyncio.create_task(
        client.put(
            f"/api/v1/skills/{name}",
            json={"data": _skill_md(name, "Failed replacement"), "wait": True},
        )
    )
    try:
        await asyncio.wait_for(paused.wait(), 5)
        assert await fs.exists(root, ctx=_ctx())
        assert await fs.read_file_bytes(f"{root}/SKILL.md", ctx=_ctx()) == original_definition
        assert not await fs.exists(hidden_uri, ctx=_ctx())
        entries = await fs._async_agfs.ls(fs._uri_to_path(root.rsplit("/", 1)[0], ctx=_ctx()))
        assert not any(".update-backup-" in entry["name"] for entry in entries)
        await _assert_locked(fs, root)
        if competing_operation == "delete":
            competing = await client.delete(f"/api/v1/skills/{name}")
        else:
            method = client.post if competing_operation == "add" else client.put
            endpoint = (
                "/api/v1/skills" if competing_operation == "add" else f"/api/v1/skills/{name}"
            )
            competing = await asyncio.wait_for(
                method(endpoint, json={"data": _skill_md(name, "Concurrent"), "wait": True}),
                5,
            )
        assert competing.status_code != 200, competing.text
        # Holding this package must not lock the entire skills namespace.
        await _add_skill(client, f"unrelated-{competing_operation}", "Independent")
        resume.set()
        failed = await asyncio.wait_for(updating, 10)
        assert failed.status_code == 500, failed.text
        assert await fs.read_file_bytes(f"{root}/SKILL.md", ctx=_ctx()) == original_definition
        assert not await fs.exists(hidden_uri, ctx=_ctx())
        assert await fs.vector_store.get_context_by_uri(root, level=0, ctx=_ctx()) == []
        assert (
            await fs.vector_store.get_context_by_uri(f"{root}/SKILL.md", level=2, ctx=_ctx()) == []
        )
        # Both the request and background references must have been released.
        lease = await fs._async_agfs.pathlock_acquire_tree(fs._uri_to_path(root, ctx=_ctx()))
        await fs._async_agfs.pathlock_release(lease)
    finally:
        resume.set()
        if not updating.done():
            await asyncio.wait_for(updating, 10)
