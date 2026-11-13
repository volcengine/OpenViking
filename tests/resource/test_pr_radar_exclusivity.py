# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Directory exclusivity over the real file Watch store and RAGFS locks."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

import openviking.resource.pr_radar_runner as runner_module
from openviking.resource.pr_radar_runner import PrRadarRunner
from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_types import WatchAuthority, WatchTask
from openviking_cli.exceptions import ConflictError, FailedPreconditionError, NotFoundError
from tests.resource.test_pr_radar_runner import FakeGh

TARGET = "viking://resources/team/radar"
OWNER_FILE = ".radar-owner.json"
ROOT = WatchAuthority("default", "default", "root")


async def create(manager, name, *, target=TARGET, **kwargs):
    return await manager.create_task(
        path=f"https://github.com/org/{name}",
        to_uri=f"viking://resources/repos/{name}",
        original_role="root",
        processor_kwargs={"pr_radar": {"enabled": True, "target_uri": target}},
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [TARGET, TARGET + "/", TARGET.replace("/team/", "//team//")])
async def test_creation_rejects_directory_reuse(watch_manager, target):
    first = await create(watch_manager, "a")
    with pytest.raises(ConflictError, match="radar"):
        await create(watch_manager, "b", target=target)
    assert [task.task_id for task in await watch_manager.store.list_tasks(ROOT)] == [first.task_id]


@pytest.mark.asyncio
async def test_concurrent_managers_allow_only_one_directory_owner(watch_manager, watch_vfs):
    peer = WatchManager(watch_manager.store, viking_fs=watch_vfs)
    results = await asyncio.gather(
        create(watch_manager, "a"), create(peer, "b"), return_exceptions=True,
    )
    assert sum(isinstance(result, WatchTask) for result in results) == 1
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    assert len(await watch_manager.store.list_tasks(ROOT)) == 1


@pytest.mark.asyncio
async def test_same_directory_is_isolated_by_account(watch_manager):
    a = await create(watch_manager, "a")
    b = await create(watch_manager, "b", account_id="other")
    assert (a.account_id, b.account_id) == ("default", "other")


@pytest.mark.asyncio
async def test_failed_creation_does_not_reserve_directory(watch_manager, monkeypatch):
    with monkeypatch.context() as mp:
        mp.setattr(watch_manager.store, "create_task", AsyncMock(side_effect=OSError("disk")))
        with pytest.raises(OSError, match="disk"):
            await create(watch_manager, "a")
    second = await create(watch_manager, "b")
    assert [task.task_id for task in await watch_manager.store.list_tasks(ROOT)] == [second.task_id]


@pytest.mark.asyncio
async def test_old_conflicting_watches_are_blocked_before_collection(
    watch_manager, watch_vfs, monkeypatch,
):
    # Direct store insertion represents Watches created before exclusivity.
    tasks = [
        WatchTask(
            path=f"https://github.com/org/{name}", to_uri=f"viking://resources/repos/{name}",
            original_role="root",
            processor_kwargs={"pr_radar": {"enabled": True, "target_uri": TARGET}},
        )
        for name in ("a", "b")
    ]
    for task in tasks:
        await watch_manager.store.create_task(task)
    monkeypatch.setattr(runner_module, "have_gh", lambda: True)
    gh = FakeGh([])
    gh.api_get_json = AsyncMock(wraps=gh.api_get_json)
    runner = PrRadarRunner(watch_vfs, gh=gh, watch_store=watch_manager.store)
    for task in tasks:
        result = await runner.maybe_run(task, WatchManager.context(task))
        assert result.status == "skipped"
        assert "radar" in result.reason
    gh.api_get_json.assert_not_called()
    with pytest.raises((FileNotFoundError, NotFoundError)):
        await watch_vfs.read_file(f"{TARGET}/{OWNER_FILE}")


@pytest.mark.asyncio
async def test_owner_retries_but_deleted_watch_history_cannot_be_reassigned(
    watch_manager, watch_vfs, monkeypatch,
):
    task = await create(watch_manager, "a")
    monkeypatch.setattr(runner_module, "have_gh", lambda: True)
    runner = PrRadarRunner(watch_vfs, gh=FakeGh([]), watch_store=watch_manager.store)
    ctx = WatchManager.context(task)
    assert (await runner.maybe_run(task, ctx)).status == "empty"
    assert (await runner.maybe_run(task, ctx)).status == "empty"
    owner = json.loads(await watch_vfs.read_file(f"{TARGET}/{OWNER_FILE}"))
    assert owner == {"watch_id": task.task_id, "repo": "org/a"}
    await watch_manager.store.delete_task(task.task_id, ROOT)
    with pytest.raises(ConflictError, match="radar"):
        await create(watch_manager, "b")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ['{"watch_id": "other", "repo": "org/a"}', "{bad"])
async def test_creation_rejects_foreign_or_corrupt_owner(watch_manager, watch_vfs, payload):
    await watch_vfs.write_file(f"{TARGET}/{OWNER_FILE}", payload)
    with pytest.raises(ConflictError, match="radar"):
        await create(watch_manager, "a")
    assert await watch_manager.store.list_tasks(ROOT) == []


@pytest.mark.asyncio
async def test_withdrawn_creation_releases_directory(watch_manager):
    watch_manager.set_deletion_guard(AsyncMock(side_effect=[False, True]))
    with pytest.raises(FailedPreconditionError):
        await create(watch_manager, "a", initial_run_id="first-run")
    assert await watch_manager.store.list_tasks(ROOT) == []
    assert await watch_manager.store.running() == []
    watch_manager.set_deletion_guard(None)
    task = await create(watch_manager, "b")
    assert [row.task_id for row in await watch_manager.store.list_tasks(ROOT)] == [task.task_id]


@pytest.mark.asyncio
async def test_cancelled_creation_releases_directory(watch_manager, monkeypatch):
    entered, resume = asyncio.Event(), asyncio.Event()
    original = watch_manager.store.create_task

    async def persist_then_pause(task, **kwargs):
        await original(task, **kwargs)
        entered.set()
        await resume.wait()

    with monkeypatch.context() as mp:
        mp.setattr(watch_manager.store, "create_task", persist_then_pause)
        running = asyncio.create_task(create(watch_manager, "a", initial_run_id="first-run"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            running.cancel()
        finally:
            resume.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 2)
    assert await watch_manager.store.list_tasks(ROOT) == []
    assert await watch_manager.store.running() == []
    task = await create(watch_manager, "b")
    assert [row.task_id for row in await watch_manager.store.list_tasks(ROOT)] == [task.task_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", [".radar-state.json", ".radar-signals.jsonl", "latest.md"])
async def test_unowned_history_cannot_be_silently_adopted(
    watch_manager, watch_vfs, monkeypatch, filename,
):
    task = await create(watch_manager, "a")
    await watch_vfs.write_file(f"{TARGET}/{filename}", "{}")
    monkeypatch.setattr(runner_module, "have_gh", lambda: True)
    gh = FakeGh([])
    gh.api_get_json = AsyncMock(wraps=gh.api_get_json)
    runner = PrRadarRunner(watch_vfs, gh=gh, watch_store=watch_manager.store)
    result = await runner.maybe_run(task, WatchManager.context(task))
    assert result.status == "skipped"
    assert "unverified history" in result.reason
    gh.api_get_json.assert_not_called()
    assert await watch_vfs.read_file(f"{TARGET}/{filename}") == "{}"


@pytest.mark.asyncio
async def test_derived_directories_use_each_watch_owner(watch_manager):
    # Different users may watch the same source resource through Connector Watches.
    connector = {"provider": "connector_encrypted", "ciphertext": "unused"}
    tasks = []
    for user in ("alice", "bob"):
        tasks.append(await watch_manager.create_task(
            path="https://github.com/org/repo", to_uri="viking://resources/repo",
            user_id=user, original_role="root", auth_state=connector,
            processor_kwargs={"pr_radar": {"enabled": True}},
        ))
    assert [task.user_id for task in tasks] == ["alice", "bob"]


@pytest.mark.asyncio
async def test_owner_read_failure_is_not_treated_as_unclaimed(watch_manager, watch_vfs, monkeypatch):
    original = watch_vfs.read_file

    async def read(uri, *args, **kwargs):
        if uri.endswith(OWNER_FILE):
            raise OSError("owner store unavailable")
        return await original(uri, *args, **kwargs)

    monkeypatch.setattr(watch_vfs, "read_file", read)
    with pytest.raises(OSError, match="owner store unavailable"):
        await create(watch_manager, "a")
    assert await watch_manager.store.list_tasks(ROOT) == []


@pytest.mark.asyncio
async def test_reports_only_history_rejects_creation(watch_manager, watch_vfs):
    await watch_vfs.write_file(f"{TARGET}/reports/old.md", "old report")
    with pytest.raises(ConflictError, match="radar"):
        await create(watch_manager, "a")
    assert await watch_manager.store.list_tasks(ROOT) == []


@pytest.mark.asyncio
async def test_move_rejects_derived_directory_collision(watch_manager):
    a = await watch_manager.create_task(
        path="https://github.com/org/a", to_uri="viking://resources/a",
        user_id="alice", original_role="root",
        processor_kwargs={"pr_radar": {"enabled": True}},
    )
    await create(
        watch_manager, "c", user_id="alice",
        target="viking://user/alice/resources/radar/b",
    )
    with pytest.raises(ConflictError, match="radar"):
        await watch_manager.move(
            account_id="default", from_uri="viking://resources/a",
            to_uri="viking://resources/b",
        )
    row = await watch_manager.store.get_task(a.task_id, ROOT)
    assert row.to_uri == "viking://resources/a"


@pytest.mark.asyncio
async def test_move_then_rollback_keeps_watch_usable(watch_manager, watch_vfs, monkeypatch):
    task = await watch_manager.create_task(
        path="https://github.com/org/a", to_uri="viking://resources/a",
        original_role="root", processor_kwargs={"pr_radar": {"enabled": True}},
    )
    monkeypatch.setattr(runner_module, "have_gh", lambda: True)
    runner = PrRadarRunner(watch_vfs, gh=FakeGh([]), watch_store=watch_manager.store)
    assert (await runner.maybe_run(task, WatchManager.context(task))).status == "empty"
    moved = await watch_manager.move(
        account_id="default", from_uri="viking://resources/a", to_uri="viking://resources/b",
    )
    await watch_manager.move(
        account_id="default", from_uri="viking://resources/b", to_uri="viking://resources/a",
        task_ids=moved.task_ids,
    )
    row = await watch_manager.store.get_task(task.task_id, ROOT)
    assert (await runner.maybe_run(row, WatchManager.context(row))).status == "empty"


@pytest.mark.asyncio
async def test_creation_and_move_cannot_reserve_same_final_directory(watch_manager, watch_vfs):
    await watch_manager.create_task(
        path="https://github.com/org/a", to_uri="viking://resources/a",
        original_role="root", processor_kwargs={"pr_radar": {"enabled": True}},
    )
    peer = WatchManager(watch_manager.store, viking_fs=watch_vfs)
    results = await asyncio.gather(
        watch_manager.move(
            account_id="default", from_uri="viking://resources/a", to_uri="viking://resources/b",
        ),
        create(peer, "c", target="viking://user/default/resources/radar/b"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1


@pytest.mark.asyncio
async def test_move_preserves_original_directory_until_filesystem_rollback(watch_manager):
    task = await watch_manager.create_task(
        path="https://github.com/org/a", to_uri="viking://resources/a",
        original_role="root", processor_kwargs={"pr_radar": {"enabled": True}},
    )
    moved = await watch_manager.move(
        account_id="default", from_uri="viking://resources/a", to_uri="viking://resources/b",
    )
    with pytest.raises(ConflictError, match="radar"):
        await create(
            watch_manager, "c", target="viking://user/default/resources/radar/a",
        )
    await watch_manager.move(
        account_id="default", from_uri="viking://resources/b", to_uri="viking://resources/a",
        task_ids=moved.task_ids,
    )
    row = await watch_manager.store.get_task(task.task_id, ROOT)
    assert row.to_uri == "viking://resources/a"
