# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Check Skill configuration isolation with real storage locks and HTTP requests."""

import asyncio
import threading

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import skills as skills_router
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


@pytest.mark.parametrize("accepted_values", [{}, {"api_key": "accepted-value"}])
async def test_rejected_add_preserves_background_update_privacy(
    client, service, monkeypatch, accepted_values
):
    name = "concurrent-add-privacy"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    fs = service.viking_fs
    hidden_uri = f"{root}/.old-source.json"
    await fs.write_file(hidden_uri, "old metadata", ctx=ctx)
    endpoint = f"/api/v1/privacy-configs/skill/{name}"
    seeded = await client.post(endpoint, json={"values": {"api_key": "old-value"}})
    assert seeded.status_code == 200, seeded.text

    async def prepare_privacy(self, skill_dict, ctx):
        values = (
            accepted_values
            if skill_dict["description"] == "Accepted update"
            else {"api_key": "rejected-value"}
        )
        return skill_dict, values

    resume = threading.Event()
    original_summary = SemanticProcessor._generate_single_file_summary

    async def hold_summary(self, *args, **kwargs):
        while not resume.is_set():
            await asyncio.sleep(0.01)
        return await original_summary(self, *args, **kwargs)

    monkeypatch.setattr(SkillProcessor, "prepare_skill_privacy", prepare_privacy)
    monkeypatch.setattr(SemanticProcessor, "_generate_single_file_summary", hold_summary)
    try:
        accepted = await client.put(
            f"/api/v1/skills/{name}",
            json={"data": _skill_md(name, "Accepted update"), "wait": False},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["result"]["task_id"]
        assert not accepted.json()["result"].get("warnings")
        await _assert_locked(fs, root)
        assert not await fs.exists(hidden_uri, ctx=ctx)
        before = await _privacy_snapshot(client, endpoint)
        assert before[0] == (200 if accepted_values else 404)
        if accepted_values:
            assert before[1]["current"]["values"] == accepted_values
        else:
            privacy_root = service.privacy_configs.get_config_root(ctx, "skill", name)
            assert not await fs.exists(privacy_root, ctx=ctx)

        rejected = await client.post(
            "/api/v1/skills",
            json={"data": _skill_md(name, "Rejected add"), "wait": True},
        )
        assert rejected.status_code == 409, rejected.text
        assert await _privacy_snapshot(client, endpoint) == before
        shown = await client.get(f"/api/v1/skills/{name}")
        assert shown.status_code == 200, shown.text
        assert shown.json()["result"]["description"] == "Accepted update"
        # After wait=false responds, configuration editing must be available
        # even while the background task still owns the package lock.
        saved = await asyncio.wait_for(
            client.post(endpoint, json={"values": {"api_key": "concurrent-value"}}), 5
        )
        assert saved.status_code == 200, saved.text
        await _assert_locked(service.viking_fs, root)
        _, current, _ = await _privacy_snapshot(client, endpoint)
        assert current["current"]["values"] == {"api_key": "concurrent-value"}
    finally:
        resume.set()
        await get_queue_manager().wait_complete(timeout=5)
    await _assert_unlocked(fs, root, ctx)


async def test_privacy_history_restore_serializes_concurrent_save(client, service, monkeypatch):
    name = "privacy-history-restore-lock"
    await _add_skill(client, name, "Original")
    endpoint = f"/api/v1/privacy-configs/skill/{name}"
    for revision, value in enumerate(("older-value", "old-value"), start=1):
        seeded = await client.post(
            endpoint,
            json={
                "values": {"api_key": value},
                "change_reason": f"seed version {revision}",
                "labels": {"revision": revision},
            },
        )
        assert seeded.status_code == 200, seeded.text
    old_snapshot = await _privacy_snapshot(client, endpoint)
    _, _, old_history = old_snapshot
    assert len(old_history) == 2
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    fs = service.viking_fs
    privacy = service.privacy_configs
    root = privacy.get_config_root(ctx, "skill", name)
    paused = asyncio.Event()
    resume = asyncio.Event()
    save_started = _observe_save_start(privacy, monkeypatch)
    metadata_failed = False
    original_write = fs.write_file_bytes
    original_restore = skills_router._restore_skill_privacy
    restored_snapshot = None

    async def observe_restoration(*args, **kwargs):
        nonlocal restored_snapshot
        await original_restore(*args, **kwargs)
        # Inspect the restored current value and metadata before the waiting
        # save can replace them; the update still owns the configuration lock.
        restored_snapshot = await _privacy_snapshot(client, endpoint)

    async def empty_privacy(self, skill_dict, ctx):
        return skill_dict, {}

    async def fail_metadata(*args, **kwargs):
        nonlocal metadata_failed
        assert (await client.get(endpoint)).status_code == 404
        await _assert_locked(fs, root)
        metadata_failed = True
        raise RuntimeError("injected source metadata failure after privacy deletion")

    async def pause_restore_write(uri, *args, **kwargs):
        # Pause at the first restoration write, independently of how cleanup
        # removes stale files. The configuration lock must remain held here.
        if metadata_failed and uri.startswith(f"{root}/") and not paused.is_set():
            paused.set()
            await resume.wait()
        return await original_write(uri, *args, **kwargs)

    monkeypatch.setattr(SkillProcessor, "prepare_skill_privacy", empty_privacy)
    monkeypatch.setattr(
        "openviking.server.skill_source_metadata.write_skill_source_metadata", fail_metadata
    )
    monkeypatch.setattr(fs, "write_file_bytes", pause_restore_write)
    monkeypatch.setattr(skills_router, "_restore_skill_privacy", observe_restoration)
    updating = asyncio.create_task(
        client.put(
            f"/api/v1/skills/{name}",
            json={"data": _skill_md(name, "Replacement without private fields"), "wait": True},
        )
    )
    saving = None
    try:
        await asyncio.wait_for(paused.wait(), 5)
        await _assert_locked(fs, root)
        unrelated = await asyncio.wait_for(
            client.post(
                "/api/v1/privacy-configs/skill/unrelated-configuration",
                json={"values": {"api_key": "independent-value"}},
            ),
            5,
        )
        assert unrelated.status_code == 200, unrelated.text
        saving = asyncio.create_task(
            client.post(endpoint, json={"values": {"api_key": "concurrent-value"}})
        )
        await asyncio.wait_for(save_started.wait(), 5)
        assert not saving.done(), "A concurrent save must wait until restoration finishes"
        resume.set()
        failed = await asyncio.wait_for(updating, 10)
        assert failed.status_code == 500, failed.text
        assert restored_snapshot == old_snapshot
        saved = await asyncio.wait_for(saving, 10)
        assert saved.status_code == 200, saved.text

        status, current, history = await _privacy_snapshot(client, endpoint)
        assert status == 200
        assert current["current"]["values"] == {"api_key": "concurrent-value"}
        assert len(history) == len(old_history) + 1
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


async def test_config_lock_failure_preserves_skill_and_releases_package_lock(
    client, service, monkeypatch
):
    name = "privacy-lock-acquisition-failure"
    root = (await _add_skill(client, name, "Original"))["root_uri"]
    endpoint = f"/api/v1/privacy-configs/skill/{name}"
    seeded = await client.post(endpoint, json={"values": {"api_key": "old-value"}})
    assert seeded.status_code == 200, seeded.text
    old_privacy = await _privacy_snapshot(client, endpoint)
    old_skill = (await client.get(f"/api/v1/skills/{name}")).json()["result"]
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    fs = service.viking_fs
    privacy_root = service.privacy_configs.get_config_root(ctx, "skill", name)
    privacy_path = fs._uri_to_path(privacy_root, ctx=ctx)
    agfs = fs._async_agfs
    original_acquire = agfs.pathlock_acquire_tree
    competing_lease = await original_acquire(privacy_path, timeout_secs=0.01)

    async def acquire_with_short_contention_timeout(path, *args, **kwargs):
        if path == privacy_path and kwargs.get("owner_lease_ref") is None:
            kwargs["timeout_secs"] = 0.01
        return await original_acquire(path, *args, **kwargs)

    monkeypatch.setattr(agfs, "pathlock_acquire_tree", acquire_with_short_contention_timeout)
    try:
        failed = await client.put(
            f"/api/v1/skills/{name}",
            json={"data": _skill_md(name, "Rejected replacement"), "wait": True},
        )
        assert failed.status_code == 409, failed.text
        assert await _privacy_snapshot(client, endpoint) == old_privacy
        shown = await client.get(f"/api/v1/skills/{name}")
        assert shown.status_code == 200, shown.text
        assert shown.json()["result"] == old_skill
        await _assert_unlocked(fs, root, ctx)
    finally:
        await agfs.pathlock_release(competing_lease)


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
