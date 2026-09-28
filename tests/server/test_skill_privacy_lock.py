# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Check Skill configuration isolation with real storage locks and HTTP requests."""

import asyncio
import threading

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_api_skills import _add_skill, _skill_md
from tests.server.test_api_skills import _stub_mcp_endpoint as _stub_mcp_endpoint
from tests.server.test_skill_update_cancellation import _wait_until
from tests.server.test_skill_update_lock import _assert_locked


async def _privacy_snapshot(client, endpoint):
    current = await client.get(endpoint)
    assert current.status_code in (200, 404), current.text
    versions = await client.get(f"{endpoint}/versions")
    if current.status_code == 404:
        assert versions.status_code == 404, versions.text
        return 404, None, {}
    assert versions.status_code == 200, versions.text
    history = {}
    for version in versions.json()["result"]:
        response = await client.get(f"{endpoint}/versions/{version}")
        assert response.status_code == 200, response.text
        history[version] = response.json()["result"]
    return current.status_code, current.json().get("result"), history


def _observe_save_start(privacy, monkeypatch):
    started = asyncio.Event()
    original_upsert = privacy.upsert

    async def observe_save(*args, **kwargs):
        if kwargs.get("values") == {"api_key": "concurrent-value"}:
            started.set()
        return await original_upsert(*args, **kwargs)

    monkeypatch.setattr(privacy, "upsert", observe_save)
    return started


async def _assert_unlocked(fs, root, ctx):
    lease = await fs._async_agfs.pathlock_acquire_tree(
        fs._uri_to_path(root, ctx=ctx), timeout_secs=0.01
    )
    await fs._async_agfs.pathlock_release(lease)


@pytest.mark.parametrize("had_privacy", [False, True])
async def test_update_rollback_preserves_waiting_privacy_save(
    client, service, monkeypatch, had_privacy
):
    name = "privacy-update-rollback"
    await _add_skill(client, name, "Original")
    endpoint = f"/api/v1/privacy-configs/skill/{name}"
    if had_privacy:
        seeded = await client.post(endpoint, json={"values": {"api_key": "old-value"}})
        assert seeded.status_code == 200, seeded.text
    _, _, old_history = await _privacy_snapshot(client, endpoint)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    privacy = service.privacy_configs
    root = privacy.get_config_root(ctx, "skill", name)
    paused = asyncio.Event()
    resume = asyncio.Event()
    save_started = _observe_save_start(privacy, monkeypatch)

    async def prepare_privacy(self, skill_dict, ctx):
        return skill_dict, {"api_key": "replacement-value"}

    async def fail_metadata(*args, **kwargs):
        paused.set()
        await resume.wait()
        raise RuntimeError("injected metadata failure after configuration change")

    monkeypatch.setattr(SkillProcessor, "prepare_skill_privacy", prepare_privacy)
    monkeypatch.setattr(
        "openviking.server.skill_source_metadata.write_skill_source_metadata", fail_metadata
    )
    updating = asyncio.create_task(
        client.put(
            f"/api/v1/skills/{name}",
            json={"data": _skill_md(name, "Failed replacement"), "wait": True},
        )
    )
    saving = None
    try:
        await asyncio.wait_for(paused.wait(), 5)
        await _assert_locked(service.viking_fs, root)
        saving = asyncio.create_task(
            client.post(endpoint, json={"values": {"api_key": "concurrent-value"}})
        )
        await asyncio.wait_for(save_started.wait(), 5)
        assert not saving.done(), "Configuration writes must wait through update and rollback"
        resume.set()
        failed = await asyncio.wait_for(updating, 10)
        assert failed.status_code == 500, failed.text
        saved = await asyncio.wait_for(saving, 10)
        assert saved.status_code == 200, saved.text
        status, current, history = await _privacy_snapshot(client, endpoint)
        assert status == 200
        assert current["current"]["values"] == {"api_key": "concurrent-value"}
        for version, snapshot in old_history.items():
            assert history[version] == snapshot
        shown = await client.get(f"/api/v1/skills/{name}")
        assert shown.status_code == 200, shown.text
        assert shown.json()["result"]["description"] == "Original"
    finally:
        resume.set()
        pending = [task for task in (updating, saving) if task is not None and not task.done()]
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending), 10)


async def test_update_timeout_restores_privacy_and_releases_both_locks(
    client, service, monkeypatch
):
    name = "privacy-update-timeout"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    endpoint = f"/api/v1/privacy-configs/skill/{name}"
    seeded = await client.post(endpoint, json={"values": {"api_key": "old-value"}})
    assert seeded.status_code == 200, seeded.text
    _, _, old_history = await _privacy_snapshot(client, endpoint)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    fs = service.viking_fs
    privacy_root = service.privacy_configs.get_config_root(ctx, "skill", name)
    started = threading.Event()
    cancelled = threading.Event()
    resume = threading.Event()
    original_summary = SemanticProcessor._generate_single_file_summary

    async def replacement_privacy(self, skill_dict, ctx):
        return skill_dict, {"api_key": "replacement-value"}

    async def hold_summary(self, *args, **kwargs):
        started.set()
        try:
            while not resume.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return await original_summary(self, *args, **kwargs)

    monkeypatch.setattr(SkillProcessor, "prepare_skill_privacy", replacement_privacy)
    monkeypatch.setattr(SemanticProcessor, "_generate_single_file_summary", hold_summary)
    updating = asyncio.create_task(
        client.put(
            f"/api/v1/skills/{name}",
            json={"data": _skill_md(name, "Timed out replacement"), "wait": True, "timeout": 2},
        )
    )
    try:
        await _wait_until(started.is_set)
        await _assert_locked(fs, root)
        await _assert_locked(fs, privacy_root)
        failed = await asyncio.wait_for(asyncio.shield(updating), 5)
        assert failed.status_code == 504, failed.text
        assert cancelled.is_set(), "The timeout must cancel the actual background summary"
        await _assert_unlocked(fs, root, ctx)
        await _assert_unlocked(fs, privacy_root, ctx)
        status, current, history = await _privacy_snapshot(client, endpoint)
        assert status == 200
        assert current["current"]["values"] == {"api_key": "old-value"}
        for version, snapshot in old_history.items():
            assert history[version] == snapshot
        saved = await asyncio.wait_for(
            client.post(endpoint, json={"values": {"api_key": "concurrent-value"}}), 5
        )
        assert saved.status_code == 200, saved.text
        shown = await client.get(f"/api/v1/skills/{name}")
        assert shown.status_code == 200, shown.text
        assert shown.json()["result"]["description"] == "Original"
    finally:
        resume.set()
        await get_queue_manager().wait_complete(timeout=5)
        if not updating.done():
            updating.cancel()
        await asyncio.gather(updating, return_exceptions=True)
