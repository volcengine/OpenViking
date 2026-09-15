# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Final Skill cleanup must preserve the completed update's result or error."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.routers import skills as skills_router
from openviking.server.temp_upload_store import ResolvedTempUpload, TempUploadStore
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.exceptions import InvalidArgumentError
from tests.server.test_api_skills import _add_skill, _skill_md
from tests.server.test_api_skills import _stub_mcp_endpoint as _stub_mcp_endpoint
from tests.server.test_skill_privacy_lock import _assert_unlocked
from tests.server.test_skill_update_lock import _assert_locked, _ctx


async def _empty_privacy(self, skill_dict, ctx):
    return skill_dict, {}


def _inject_final_cleanup_failure(service, monkeypatch, name, root, failure_stage):
    """Arm the fault after backup deletion, once commit or rollback has finished."""
    fs = service.viking_fs
    privacy = service.privacy_configs
    privacy_root = privacy.get_config_root(_ctx(), "skill", name)
    backup_prefix = f"{root.rsplit('/', 1)[0]}/.{name}.update-backup-"
    state = {"cleanup_started": False, "failed": False}
    original_rm = fs.rm

    def fail_once():
        if state["cleanup_started"] and not state["failed"]:
            state["failed"] = True
            raise RuntimeError(f"injected final privacy cleanup {failure_stage} failure")

    async def observe_cleanup(uri, *args, **kwargs):
        if failure_stage == "rm" and uri == privacy_root:
            fail_once()
        result = await original_rm(uri, *args, **kwargs)
        if uri.startswith(backup_prefix) and "/" not in uri[len(backup_prefix) :]:
            state["cleanup_started"] = True
        return result

    monkeypatch.setattr(fs, "rm", observe_cleanup)
    if failure_stage != "rm":
        original_read = getattr(privacy, failure_stage)

        async def fail_cleanup_read(*args, **kwargs):
            fail_once()
            return await original_read(*args, **kwargs)

        monkeypatch.setattr(privacy, failure_stage, fail_cleanup_read)
    return privacy_root, state


@pytest.mark.parametrize("wait,failure_stage", [(False, "get_meta"), (True, "rm")])
async def test_final_privacy_cleanup_failure_preserves_success_and_releases_locks(
    client, service, monkeypatch, wait, failure_stage
):
    name = "cleanup-success"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    monkeypatch.setattr(SkillProcessor, "prepare_skill_privacy", _empty_privacy)
    privacy_root, fault = _inject_final_cleanup_failure(
        service, monkeypatch, name, root, failure_stage
    )
    resume = threading.Event()
    original_summary = SemanticProcessor._generate_single_file_summary

    async def hold_background(self, *args, **kwargs):
        while not resume.is_set():
            await asyncio.sleep(0.01)
        return await original_summary(self, *args, **kwargs)

    if not wait:
        monkeypatch.setattr(SemanticProcessor, "_generate_single_file_summary", hold_background)
    try:
        response = await client.put(
            f"/api/v1/skills/{name}",
            json={"data": _skill_md(name, "Replacement"), "wait": wait},
        )
        assert fault["failed"], "The injected failure must happen during final cleanup"
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["action"] == "update"
        assert result["root_uri"] == root
        assert privacy_root in str(result.get("warnings", []))
        if wait:
            assert "queue_status" in result
        else:
            assert result["task_id"]
            await _assert_locked(service.viking_fs, root)
        await _assert_unlocked(service.viking_fs, privacy_root, _ctx())
        shown = await client.get(f"/api/v1/skills/{name}")
        assert shown.status_code == 200, shown.text
        assert shown.json()["result"]["description"] == "Replacement"
        assert (await client.get(f"/api/v1/privacy-configs/skill/{name}")).status_code == 404

        resume.set()
        await get_queue_manager().wait_complete(timeout=5)
        await _assert_unlocked(service.viking_fs, root, _ctx())
        if not wait:
            task = await client.get(f"/api/v1/tasks/{result['task_id']}")
            assert task.status_code == 200, task.text
            assert task.json()["result"]["status"] not in {"failed", "cancelled", "cancelling"}
    finally:
        resume.set()
        await get_queue_manager().wait_complete(timeout=5)


async def test_secondary_cleanup_failure_preserves_original_error_and_restored_skill(
    client, service, monkeypatch
):
    name = "cleanup-after-rollback"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    before = (await client.get(f"/api/v1/skills/{name}")).json()["result"]
    monkeypatch.setattr(SkillProcessor, "prepare_skill_privacy", _empty_privacy)
    privacy_root, fault = _inject_final_cleanup_failure(service, monkeypatch, name, root, "rm")

    async def fail_source_metadata(*args, **kwargs):
        raise InvalidArgumentError("original update validation failure")

    monkeypatch.setattr(
        "openviking.server.skill_source_metadata.write_skill_source_metadata", fail_source_metadata
    )
    response = await client.put(
        f"/api/v1/skills/{name}",
        json={"data": _skill_md(name, "Rejected replacement"), "wait": True},
    )
    assert fault["failed"], "The secondary cleanup fault must happen after rollback"
    assert response.status_code == 400, response.text
    assert response.json()["error"]["message"] == "original update validation failure"
    shown = await client.get(f"/api/v1/skills/{name}")
    assert shown.status_code == 200, shown.text
    for field in ("abstract", "overview", "content", "description"):
        assert shown.json()["result"][field] == before[field]
    assert (await client.get(f"/api/v1/privacy-configs/skill/{name}")).status_code == 404
    await _assert_unlocked(service.viking_fs, root, _ctx())
    await _assert_unlocked(service.viking_fs, privacy_root, _ctx())


@pytest.mark.parametrize("update_fails", [False, True])
async def test_backup_discard_failure_preserves_update_outcome(
    client, service, monkeypatch, update_fails
):
    name = "backup-cleanup-failure"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    fs = service.viking_fs
    original_content = await fs.read_file(f"{root}/SKILL.md", ctx=_ctx())
    backup_prefix = f"{root.rsplit('/', 1)[0]}/.{name}.update-backup-"
    original_rm = fs.rm
    backup = None
    warning = Mock(wraps=skills_router.logger.warning)

    async def retain_backup(uri, *args, **kwargs):
        nonlocal backup
        if uri.startswith(backup_prefix) and "/" not in uri[len(backup_prefix) :]:
            backup = uri
            raise RuntimeError("injected final backup deletion failure")
        return await original_rm(uri, *args, **kwargs)

    async def fail_source_metadata(*args, **kwargs):
        raise InvalidArgumentError("original update validation failure")

    monkeypatch.setattr(SkillProcessor, "prepare_skill_privacy", _empty_privacy)
    monkeypatch.setattr(fs, "rm", retain_backup)
    monkeypatch.setattr(skills_router.logger, "warning", warning)
    if update_fails:
        monkeypatch.setattr(
            "openviking.server.skill_source_metadata.write_skill_source_metadata",
            fail_source_metadata,
        )
    response = await client.put(
        f"/api/v1/skills/{name}",
        json={"data": _skill_md(name, "Replacement"), "wait": False},
    )
    assert backup is not None
    assert await fs.read_file(f"{backup}/SKILL.md", ctx=_ctx()) == original_content
    if update_fails:
        assert response.status_code == 400, response.text
        assert response.json()["error"]["message"] == "original update validation failure"
        assert any(backup in str(call.args) for call in warning.call_args_list)
    else:
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["task_id"]
        assert backup in str(result.get("warnings", []))
    shown = await client.get(f"/api/v1/skills/{name}")
    assert shown.status_code == 200, shown.text
    assert shown.json()["result"]["description"] == ("Original" if update_fails else "Replacement")
    await get_queue_manager().wait_complete(timeout=5)
    await _assert_unlocked(fs, root, _ctx())
    await _assert_unlocked(fs, backup, _ctx())


async def test_temp_upload_cleanup_failure_preserves_success(
    client, service, monkeypatch, tmp_path
):
    name = "upload-cleanup-failure"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    uploaded = tmp_path / "SKILL.md"
    uploaded.write_text(_skill_md(name, "Replacement"), encoding="utf-8")
    resolved = ResolvedTempUpload(
        mode="shared",
        temp_file_id="cleanup-test-upload",
        original_filename="SKILL.md",
        local_path=str(uploaded),
    )
    cleanup = AsyncMock(side_effect=PermissionError("injected temporary upload cleanup failure"))
    monkeypatch.setattr(resolved, "cleanup", cleanup)
    store = SimpleNamespace(resolve_for_consume=AsyncMock(return_value=resolved))
    monkeypatch.setattr(TempUploadStore, "build", lambda _config: store)
    monkeypatch.setattr(SkillProcessor, "prepare_skill_privacy", _empty_privacy)

    response = await client.put(
        f"/api/v1/skills/{name}",
        json={"temp_file_id": resolved.temp_file_id, "wait": True},
    )
    assert response.status_code == 200, response.text
    assert str(uploaded) in str(response.json()["result"].get("warnings", []))
    cleanup.assert_awaited_once()
    assert uploaded.exists()
    shown = await client.get(f"/api/v1/skills/{name}")
    assert shown.status_code == 200, shown.text
    assert shown.json()["result"]["description"] == "Replacement"
    await _assert_unlocked(service.viking_fs, root, _ctx())
