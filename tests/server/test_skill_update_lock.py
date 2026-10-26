# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Check update lock continuity using the real filesystem and HTTP endpoints."""

import asyncio

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.errors import LockAcquisitionError
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_api_skills import _add_skill, _skill_md
from tests.server.test_api_skills import _stub_mcp_endpoint as _stub_mcp_endpoint
from tests.server.test_skill_update_cancellation import _download, _indexed_record


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
async def test_update_lock_blocks_same_package_through_backup_and_rollback(
    client, service, monkeypatch, competing_operation
):
    name = f"update-lock-{competing_operation}"
    added = await _add_skill(client, name, "Original")
    root = added["root_uri"]
    fs = service.viking_fs
    hidden_uri = f"{root}/.custom.json"
    await fs.write_file(hidden_uri, '{"original": true}', ctx=_ctx())
    old_content = await _download(client, f"{root}/SKILL.md")
    old_index = await _indexed_record(client, root, 0)
    paused = asyncio.Event()
    resume = asyncio.Event()
    original_apply = SkillProcessor.apply_skill_privacy

    async def fail_after_backup(self, *args, **kwargs):
        if kwargs.get("change_reason") == "auto-extracted from update_skill":
            paused.set()
            await resume.wait()
            raise RuntimeError("injected preparation failure after backup")
        return await original_apply(self, *args, **kwargs)

    monkeypatch.setattr(SkillProcessor, "apply_skill_privacy", fail_after_backup)
    updating = asyncio.create_task(
        client.put(
            f"/api/v1/skills/{name}",
            json={"data": _skill_md(name, "Failed replacement"), "wait": True},
        )
    )
    try:
        await asyncio.wait_for(paused.wait(), 5)
        assert await fs.exists(root, ctx=_ctx())
        assert not await fs.exists(f"{root}/SKILL.md", ctx=_ctx())
        assert not await fs.exists(hidden_uri, ctx=_ctx())
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
        assert await _download(client, f"{root}/SKILL.md") == old_content
        assert await _download(client, hidden_uri) == b'{"original": true}'
        assert await _indexed_record(client, root, 0) == old_index
        # Both the request and background references must have been released.
        lease = await fs._async_agfs.pathlock_acquire_tree(fs._uri_to_path(root, ctx=_ctx()))
        await fs._async_agfs.pathlock_release(lease)
    finally:
        resume.set()
        if not updating.done():
            await asyncio.wait_for(updating, 10)


async def test_restore_index_failure_preserves_live_lock_and_backup(client, service, monkeypatch):
    name = "restore-index-lock"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    fs = service.viking_fs
    old_content = await _download(client, f"{root}/SKILL.md")
    original_transfer = fs._update_vector_store_uris
    backup = None

    async def fail_new_source(*args, **kwargs):
        raise RuntimeError("injected new source write failure")

    async def fail_restore_index(source, target, **kwargs):
        nonlocal backup
        if ".update-backup-" in source and target == root:
            backup = source
            await _assert_locked(fs, root)
            assert await _download(client, f"{root}/SKILL.md") == old_content
            raise RuntimeError("injected index restoration failure")
        return await original_transfer(source, target, **kwargs)

    monkeypatch.setattr(
        "openviking.server.skill_source_metadata.write_skill_source_metadata", fail_new_source
    )
    monkeypatch.setattr(fs, "_update_vector_store_uris", fail_restore_index)
    response = await client.put(
        f"/api/v1/skills/{name}",
        json={"data": _skill_md(name, "Replacement"), "wait": True},
    )
    assert response.status_code == 500, response.text
    assert "injected index restoration failure" in response.text
    assert backup is not None and backup in response.text
    # Generic cp/mv cleanup would remove the live root when vector restore fails.
    assert await _download(client, f"{root}/SKILL.md") == old_content
    assert await _download(client, f"{backup}/SKILL.md") == old_content
    # Internal backups are hidden from public search; inspect their stored vectors.
    assert await fs.vector_store.get_context_by_uri(backup, level=0, ctx=_ctx())
