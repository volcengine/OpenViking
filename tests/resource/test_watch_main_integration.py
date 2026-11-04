# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Main's Git gate and credential lifecycle on both distributed Watch stores."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.connector.delegate import ConnectorDelegate
from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_types import WatchPatch, WatchRunResult, WatchTask
from tests.resource.test_watch_store import (
    CONNECTOR_AUTH,
    OWNER,
    create,
)
from tests.resource.test_watch_store import (
    make_store as make_store,
)
from tests.resource.test_watch_store import (
    store as store,
)
from tests.utils.watch_fixtures import reserve_watch

REPO = "https://github.com/volcengine/OpenViking.git"
SHA = "a" * 40
CHECKPOINT = {"kind": "git", "head_sha": SHA, "instruction": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize("connector", [False, True])
@pytest.mark.parametrize("mutation", [None, "pause", "delete", "invalidate"])
async def test_running_summary_settles_checkpoint_and_connector_state(
    store, watch_vfs, watch_tracker, connector, mutation
):
    owner = {"account_id": OWNER.account_id, "user_id": OWNER.user_id}
    record = await watch_tracker.create("add_resource", resource_id="scan", **owner)
    manager = WatchManager(store, viking_fs=watch_vfs, tracker=watch_tracker)
    task = await create(
        store,
        initial_run_id=record.task_id,
        auth_state=CONNECTOR_AUTH if connector else {"provider": "git", "token": "private"},
        connector_states={"old": "state"},
        source_checkpoint=CHECKPOINT,
        instruction="scan instruction 中文",
    )
    state = (await store.running())[0]
    assert (state.account_id, state.user_id) == (OWNER.account_id, OWNER.user_id)
    assert state.connector is connector
    assert state.instruction == task.instruction
    assert not await manager.settle_running(state, record)
    if mutation == "pause":
        await store.patch_task(task.task_id, WatchPatch({"is_active": False}), OWNER)
    elif mutation == "delete":
        await store.delete_task(task.task_id, OWNER)
    elif mutation == "invalidate":
        await store.patch_task(task.task_id, WatchPatch({"instruction": "new"}), OWNER)
    # Paused and deleted rows still participate in run observation.
    current = (await store.running())[0]
    assert (current.task_id, current.run_started_at) == (task.task_id, state.run_started_at)
    assert current.instruction == ("new" if mutation == "invalidate" else task.instruction)
    assert current.connector is connector
    await watch_tracker.complete(
        record.task_id,
        {"connector_states": {"new": "state"}, "meta": {"resolved_head_sha": "b" * 40}},
        **owner,
    )
    record = await watch_tracker.get(record.task_id, **owner)
    assert await manager.settle_running(current, record)
    assert not await manager.settle_running(state, record)
    assert await store.running() == []
    if mutation == "delete":
        assert await store.get_state(task.task_id) is None
    else:
        saved = await store.get_task(task.task_id, OWNER)
        assert saved.last_status == "completed"
        assert saved.last_task_id == record.task_id
        assert saved.auth_state == task.auth_state
        assert saved.connector_states == ({"new": "state"} if connector else {"old": "state"})
        assert saved.source_checkpoint == (
            None
            if mutation == "invalidate"
            else {"kind": "git", "head_sha": "b" * 40, "instruction": task.instruction}
        )
        assert saved.is_active is (mutation != "pause")


@pytest.mark.asyncio
async def test_due_snapshot_survives_edits_pause_and_recovery(store, watch_vfs, watch_tracker):
    manager = WatchManager(store, viking_fs=watch_vfs, tracker=watch_tracker)
    task = await create(
        store,
        due=True,
        instruction="selected instruction",
        processor_kwargs={"nested": {"value": "selected"}},
        source_checkpoint=CHECKPOINT,
    )
    runs = await manager.due(1)
    assert len(runs) == 1
    run = runs[0]
    assert await manager.has_running_overlap(OWNER.account_id, [task.to_uri])
    await store.patch_task(
        task.task_id,
        WatchPatch(
            {
                "path": "https://example.org/next",
                "instruction": "next instruction",
                "processor_kwargs": {"nested": {"value": "next"}},
                "watch_interval": 10,
                "is_active": False,
            }
        ),
        OWNER,
    )
    assert run.task.path == task.path
    assert run.task.instruction == "selected instruction"
    assert run.task.processor_kwargs == {"nested": {"value": "selected"}}
    assert run.task.watch_interval == task.watch_interval
    assert run.task.source_checkpoint == CHECKPOINT
    assert await manager.due(1) == []

    owner = {"account_id": OWNER.account_id, "user_id": OWNER.user_id}
    record = await watch_tracker.create("add_resource", resource_id=task.to_uri, **owner)
    assert await store.attach_task(task.task_id, record.task_id, started_at=run.started_at)
    await watch_tracker.complete(record.task_id, {"meta": {"resolved_head_sha": "b" * 40}}, **owner)
    recovered = WatchManager(store, viking_fs=watch_vfs, tracker=watch_tracker)
    assert await recovered.settle(task.task_id, record.task_id)
    saved = await store.get_task(task.task_id, OWNER)
    assert saved.source_checkpoint is None
    assert saved.instruction == "next instruction"
    assert saved.last_status == "completed"
    assert not saved.is_active and saved.next_execution_time is None


@pytest.mark.asyncio
async def test_stale_running_summary_cannot_settle_reused_run_id(store, watch_vfs, watch_tracker):
    owner = {"account_id": OWNER.account_id, "user_id": OWNER.user_id}
    record = await watch_tracker.create("add_resource", resource_id="scan", **owner)
    manager = WatchManager(store, viking_fs=watch_vfs, tracker=watch_tracker)
    task = await create(store, initial_run_id=record.task_id)
    state = (await store.running())[0]
    await watch_tracker.complete(record.task_id, {}, **owner)
    record = await watch_tracker.get(record.task_id, **owner)
    assert await manager.settle_running(state, record)
    run = await reserve_watch(
        store, task.task_id, run_id=record.task_id, target_uri=task.to_uri, manual=True
    )
    assert run.started_at != state.run_started_at
    assert not await manager.settle_running(state, record)
    assert (await store.running())[0].run_started_at == run.started_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "unchanged",
        "commit",
        "token",
        "instruction",
        "changed",
        "error",
        "timeout",
        "non_git",
        "sync",
        "failed",
    ],
)
async def test_git_gate(store, watch_vfs, watch_tracker, monkeypatch, scenario):
    tracker = watch_tracker
    owner = {"account_id": OWNER.account_id, "user_id": OWNER.user_id}
    imported_sha = "c" * 40
    probe = AsyncMock(return_value=("", SHA))
    if scenario == "changed":
        probe.return_value = ("refs/heads/main", "b" * 40)
    elif scenario == "error":
        probe.side_effect = RuntimeError("probe failed")
    elif scenario == "timeout":

        async def blocked(*args, **kwargs):
            await asyncio.Event().wait()

        probe.side_effect = blocked
    monkeypatch.setattr(
        "openviking.parse.accessors.git_accessor.GitAccessor.probe_git_remote_head", probe
    )

    async def refresh(**kwargs):
        if scenario == "sync":
            return {"status": "completed", "meta": {"resolved_head_sha": imported_sha}}
        record = await tracker.create(
            "add_resource", task_id="refresh-1", resource_id=kwargs["to"], **owner
        )
        await kwargs["on_task_created"](record)
        return {"status": "success", "task_id": record.task_id}

    resources = SimpleNamespace(
        refresh_resource=AsyncMock(side_effect=refresh),
        _resource_processor=SimpleNamespace(github_token_for=AsyncMock(return_value=None)),
    )
    if scenario == "token":
        resources._resource_processor.github_token_for.return_value = "account-token"
    manager = WatchManager(store, viking_fs=watch_vfs, tracker=tracker, resource_service=resources)
    manager.DEFAULT_GIT_PROBE_TIMEOUT = 0.01 if scenario == "timeout" else 30
    target = "viking://resources/git"
    task = WatchTask(
        account_id=OWNER.account_id,
        user_id=OWNER.user_id,
        to_uri=target,
        path=REPO if scenario != "non_git" else "https://example.org/doc",
        source_checkpoint=None if scenario in {"sync", "failed"} else CHECKPOINT,
        last_task_id="previous-import",
        instruction="new" if scenario == "instruction" else "",
        processor_kwargs={"commit": SHA} if scenario == "commit" else {},
    )
    task.next_execution_time = task.calculate_next_execution_time()
    await watch_vfs.mkdir(target, ctx=manager.context(task), exist_ok=True)
    await store.create_task(task)
    receipt = await manager.trigger(task.task_id)
    state = await store.get_state(task.task_id)
    if scenario in {"unchanged", "commit", "token"}:
        assert receipt is None
        resources.refresh_resource.assert_not_awaited()
        assert state.task.last_task_id == "previous-import"
        assert state.task.last_status == "completed"
        assert state.run_started_at is None
        assert state.task.next_execution_time is not None
    elif scenario == "sync":
        assert receipt is None
        assert state.task.last_status == "completed"
        assert state.task.source_checkpoint == {
            "kind": "git",
            "head_sha": imported_sha,
            "instruction": "",
        }
        assert state.run_started_at is None
    else:
        assert receipt.task_id == "refresh-1"
        assert state.task.source_checkpoint == task.source_checkpoint
        assert state.current_run_id == receipt.task_id
        if scenario == "failed":
            await tracker.fail(receipt.task_id, "import failed", **owner)
        else:
            await tracker.complete(
                receipt.task_id, {"meta": {"resolved_head_sha": imported_sha}}, **owner
            )
        assert await manager.settle(task.task_id, receipt.task_id)
        assert not await manager.settle(task.task_id, receipt.task_id)
        state = await store.get_state(task.task_id)
        assert state.task.source_checkpoint == (
            None
            if scenario == "failed"
            else {
                "kind": "git",
                "head_sha": imported_sha,
                "instruction": task.instruction,
            }
        )
    if scenario == "non_git":
        probe.assert_not_awaited()
    elif scenario == "token":
        probe.assert_awaited_once_with(REPO, auth_config={"token": "account-token"})
        assert "auth_config" not in state.task.processor_kwargs
    assert "source_checkpoint" not in state.task.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidated", [False, True])
async def test_checkpoint_settlement_is_fenced(store, invalidated):
    task = await create(store, initial_run_id="first")
    outcome = WatchRunResult(status="completed", source_checkpoint=CHECKPOINT)
    if invalidated:
        await store.patch_task(task.task_id, WatchPatch({}, invalidate_source_checkpoint=True))
    assert not await store.finish_run(task.task_id, "stale", outcome)
    assert await store.finish_run(task.task_id, "first", outcome)
    assert (await store.get_task(task.task_id)).source_checkpoint == (
        None if invalidated else CHECKPOINT
    )
    run = await reserve_watch(
        store, task.task_id, run_id="second", target_uri=task.to_uri, manual=True
    )
    assert not await store.finish_run(task.task_id, "first", outcome)
    assert await store.finish_run(task.task_id, "second", outcome, started_at=run.started_at)
    assert (await store.get_task(task.task_id)).source_checkpoint == CHECKPOINT


def credential_state(version=8, authorized=False, **values):
    return {
        "account_id": "volcano-account",
        "resource_id": "resource",
        "user_id": "volcano-user",
        "ov_account_id": OWNER.account_id,
        "ov_user_id": OWNER.user_id,
        "type": "oauth",
        "platform": "dingtalk",
        "version": version,
        "authorized": authorized,
        **values,
    }


async def connector_task(store, watch_vfs, *, active=True, encrypted=False, legacy=False):
    manager = WatchManager(store, viking_fs=watch_vfs)
    account_id = "default" if legacy else OWNER.account_id
    reference = credential_state(ov_account_id=account_id)
    del reference["version"], reference["authorized"]
    reference["binding_version"] = 3
    if legacy:
        reference.pop("ov_account_id")
    request = {
        "api_key": "test-key",
        "account_id": account_id,
        "add_type": "dingtalk",
        "path": "dingtalk://node",
        "connector_args": {},
        "connector_auth_config": {"openviking_credential_ref": reference},
    }
    auth = {"provider": "connector_plaintext", "request": request}
    if encrypted:

        class Encryptor:
            async def encrypt(self, account_id, plaintext):
                return b"OVE1" + plaintext[::-1]

            async def decrypt(self, account_id, ciphertext):
                return ciphertext[4:][::-1]

        watch_vfs._encryptor = Encryptor()
        delegate = ConnectorDelegate(viking_fs=watch_vfs)
        auth = await delegate.create_watch_auth_state(**request)
    task = await create(
        store,
        account_id=account_id,
        auth_state=auth,
        is_active=active,
        last_status="failed",
        last_error="previous",
    )
    task = await store.patch_task(task.task_id, WatchPatch({"path": request["path"]}))
    return manager, task


async def notify(manager, task, state):
    return await manager.update_task(
        task.task_id, task.account_id, task.user_id, OWNER.role, credential_state=state
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["plaintext", "encrypted", "legacy"])
async def test_credential_lifecycle_and_stale_events(store, watch_vfs, mode):
    manager, task = await connector_task(
        store, watch_vfs, encrypted=mode == "encrypted", legacy=mode == "legacy"
    )

    def state(version=8, authorized=False):
        return credential_state(version, authorized, ov_account_id=task.account_id)

    revoked = await notify(manager, task, state())
    assert not revoked.is_active and revoked.credential_paused
    assert revoked.next_execution_time is None
    await store.close()
    await store.initialize()
    persisted_pause = await store.get_task(task.task_id)
    assert persisted_pause.credential_paused and persisted_pause.credential_version == 8
    resumed = await notify(manager, task, state(10, True))
    assert resumed.is_active and not resumed.credential_paused
    assert resumed.next_execution_time is not None
    await asyncio.gather(
        *(
            notify(manager, task, state(version, version % 2 == 0))
            for version in [11, 14, 12, 13, 14]
        )
    )
    saved = await store.get_task(task.task_id)
    assert saved.is_active and saved.credential_version == 14
    assert saved.auth_state == task.auth_state
    assert (saved.last_status, saved.last_error) == ("failed", "previous")
    persisted = WatchTask.from_dict(saved.to_storage_dict())
    assert persisted.credential_version == 14
    assert "credential_version" not in persisted.to_dict()
    assert "credential_paused" not in persisted.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "account_id",
        "resource_id",
        "user_id",
        "ov_account_id",
        "ov_user_id",
        "type",
        "platform",
    ],
)
async def test_credential_owner_fence(store, watch_vfs, field):
    manager, task = await connector_task(store, watch_vfs)
    result = await notify(manager, task, credential_state(**{field: "other"}))
    assert result.is_active and result.credential_version == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("already_paused", [False, True])
async def test_user_pause_survives_reauthorization(store, watch_vfs, already_paused):
    manager, task = await connector_task(store, watch_vfs, active=not already_paused)
    await notify(manager, task, credential_state())
    if not already_paused:
        await manager.update_task(
            task.task_id, OWNER.account_id, OWNER.user_id, OWNER.role, is_active=False
        )
    result = await notify(manager, task, credential_state(10, True))
    assert not result.is_active and not result.credential_paused
    assert result.next_execution_time is None


@pytest.mark.asyncio
async def test_credential_reference_change_during_notification(store, watch_vfs, monkeypatch):
    manager, task = await connector_task(store, watch_vfs)
    original_patch = store.patch_task

    async def replace_reference(task_id, patch, *args, **kwargs):
        await original_patch(task_id, WatchPatch({"auth_state": None}))
        return await original_patch(task_id, patch, *args, **kwargs)

    monkeypatch.setattr(store, "patch_task", replace_reference)
    result = await notify(manager, task, credential_state())
    assert result.is_active and result.credential_version == 0
