# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""WatchManager contracts over the file Watch store and real PathLocks."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_store_file import FileWatchStore
from openviking.resource.watch_types import WatchAuthority, WatchRunResult
from openviking.storage.errors import ResourceBusyError
from openviking_cli.exceptions import ConflictError, FailedPreconditionError

TEST_ACCOUNT_ID = "default"
TEST_USER_ID = "default"
TEST_ROLE = "ROOT"
_CONNECTOR_AUTH = {"provider": "connector_encrypted", "ciphertext": "unused"}
_ROOT = WatchAuthority(TEST_ACCOUNT_ID, TEST_USER_ID, "root")


async def _create(manager: WatchManager, uri: str = "viking://resources/test", **kwargs):
    kwargs.setdefault("path", "/test/path")
    return await manager.create_task(to_uri=uri, **kwargs)


class TestWatchManager:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"path": ""}, "Path is required"),
            ({"to_uri": None}, "target URI"),
            ({"watch_interval": 0}, "watch_interval must be > 0"),
        ],
    )
    async def test_create_task_validation(self, watch_manager: WatchManager, kwargs, message):
        values = {"path": "/test/path", "to_uri": "viking://resources/test", **kwargs}
        with pytest.raises(ValueError, match=message):
            await watch_manager.create_task(**values)

    @pytest.mark.asyncio
    async def test_create_is_refused_while_its_identity_is_deleted(
        self, watch_manager: WatchManager
    ):
        watch_manager.set_deletion_guard(AsyncMock(return_value=True))

        with pytest.raises(FailedPreconditionError):
            await _create(watch_manager)

        assert await watch_manager.store.list_tasks(_ROOT) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["deletion", "identity-query", "lock-release"])
    async def test_create_withdraws_a_watch_when_acceptance_fails(
        self, watch_manager: WatchManager, watch_vfs, monkeypatch, failure
    ):
        # The deletion fences the identity and removes its Watches between the
        # check and the insert, so only the creator can still remove this one.
        error = OSError("Watch acceptance unavailable")
        watch_manager.set_deletion_guard(
            AsyncMock(side_effect=[False, error if failure == "identity-query" else True])
        )
        agfs = watch_vfs._async_agfs
        release = agfs.pathlock_release
        if failure == "lock-release":

            async def release_then_fail(lease):
                await release(lease)
                raise error

            monkeypatch.setattr(agfs, "pathlock_release", release_then_fail)

        with pytest.raises(FailedPreconditionError if failure == "deletion" else OSError) as raised:
            await _create(watch_manager, initial_run_id="first-run")
        if failure != "deletion":
            assert raised.value is error

        assert await watch_manager.store.list_tasks(_ROOT) == []
        assert await watch_manager.store.running() == []
        reopened = FileWatchStore(watch_vfs)
        await reopened.initialize()
        assert await reopened.list_tasks(_ROOT) == []
        assert await reopened.running() == []
        await reopened.close()
        watch_manager.set_deletion_guard(None)
        monkeypatch.setattr(agfs, "pathlock_release", release)
        assert await _create(watch_manager)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cancel_at", ["insert", "identity-query", "lock-release"])
    async def test_cancel_during_creation_leaves_no_watch_or_run(
        self, watch_manager, watch_vfs, monkeypatch, cancel_at
    ):
        entered, resume = asyncio.Event(), asyncio.Event()

        async def pause():
            entered.set()
            await resume.wait()

        create = watch_manager.store.create_task

        async def create_then_pause(task, **kwargs):
            await create(task, **kwargs)
            if cancel_at == "insert":
                await pause()

        monkeypatch.setattr(watch_manager.store, "create_task", create_then_pause)
        checks = 0

        async def guard(account_id, user_id):
            nonlocal checks
            checks += 1
            if cancel_at == "identity-query" and checks == 2:
                await pause()
            return False

        watch_manager.set_deletion_guard(guard)
        release = watch_vfs._async_agfs.pathlock_release

        async def release_then_pause(lease):
            await release(lease)
            if cancel_at == "lock-release":
                await pause()

        monkeypatch.setattr(watch_vfs._async_agfs, "pathlock_release", release_then_pause)
        running = asyncio.create_task(_create(watch_manager, initial_run_id="first-run"))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            running.cancel()
            await asyncio.sleep(0)
            running.cancel()
        finally:
            resume.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 1)

        assert await watch_manager.store.list_tasks(_ROOT) == []
        assert await watch_manager.store.running() == []
        reopened = FileWatchStore(watch_vfs)
        await reopened.initialize()
        assert await reopened.list_tasks(_ROOT) == []
        assert await reopened.running() == []
        await reopened.close()

    @pytest.mark.asyncio
    async def test_create_requires_target_tree_lock(self, watch_manager: WatchManager, watch_vfs):
        locks = watch_manager.locks
        async with locks.tree(TEST_ACCOUNT_ID, "viking://resources") as lease:
            with pytest.raises(ResourceBusyError):
                await _create(watch_manager, "viking://resources/test")
            task = await _create(watch_manager, "viking://resources/test", lease_ref=lease)
        assert await watch_manager.get_task(task.task_id) is not None

    @pytest.mark.asyncio
    async def test_create_for_missing_target_locks_existing_ancestor(
        self, watch_manager: WatchManager, watch_vfs, tmp_path
    ):
        await _create(watch_manager, "viking://resources/pending/doc.md")

        assert not await watch_vfs.exists("viking://resources/pending")
        assert not list((tmp_path / "agfs").rglob("pending"))

        await watch_vfs.mkdir("viking://resources/held")
        async with watch_manager.locks.tree(TEST_ACCOUNT_ID, "viking://resources/held"):
            with pytest.raises(ResourceBusyError):
                await _create(watch_manager, "viking://resources/held/new/doc.md")

    @pytest.mark.asyncio
    async def test_finish_run_records_sanitized_result(self, watch_vfs, watch_manager):
        task = await _create(watch_manager, initial_run_id="ingest-task-1")

        applied = await watch_manager.finish_run(
            task.task_id,
            "ingest-task-1",
            WatchRunResult(
                status="failed",
                execution_task_id="ingest-task-1",
                error="pull failed with Bearer secret-token",
            ),
        )

        assert applied is True
        reopened = FileWatchStore(watch_vfs)
        await reopened.initialize()
        try:
            loaded = await reopened.get_task(task.task_id)
        finally:
            await reopened.close()
        assert loaded.last_task_id == "ingest-task-1"
        assert loaded.last_status == "failed"
        assert loaded.last_error == "pull failed with Bearer [REDACTED]"

    @pytest.mark.asyncio
    async def test_secret_state_persisted_and_hidden_from_public_dict(
        self, watch_vfs, watch_manager
    ):
        states = {"documents": {"cursor": {"sync_checkpoint": "2026-09-01T00:00:00+00:00"}}}
        task = await _create(
            watch_manager,
            path="tos://bucket/docs/",
            auth_state=dict(_CONNECTOR_AUTH),
            connector_states=states,
        )

        reopened = FileWatchStore(watch_vfs)
        await reopened.initialize()
        try:
            loaded = await reopened.get_task(task.task_id)
        finally:
            await reopened.close()
        assert loaded.auth_state == _CONNECTOR_AUTH
        assert loaded.connector_states == states
        public = loaded.to_dict()
        assert "auth_state" not in public
        assert "connector_states" not in public

    @pytest.mark.asyncio
    async def test_update_task(self, watch_manager: WatchManager):
        task = await _create(watch_manager, reason="Original reason")

        updated = await watch_manager.update_task(
            task.task_id,
            TEST_ACCOUNT_ID,
            TEST_USER_ID,
            TEST_ROLE,
            reason="Updated reason",
            watch_interval=45.0,
            is_active=False,
        )

        assert updated.reason == "Updated reason"
        assert updated.watch_interval == 45.0
        assert updated.is_active is False
        assert updated.next_execution_time is None

        resumed = await watch_manager.update_task(
            task.task_id, TEST_ACCOUNT_ID, TEST_USER_ID, TEST_ROLE, is_active=True
        )
        expected = resumed.created_at + timedelta(minutes=45)
        assert abs((resumed.next_execution_time - expected).total_seconds()) < 1

    @pytest.mark.asyncio
    async def test_visibility_by_role(self, watch_manager: WatchManager):
        await _create(watch_manager, "viking://resources/a1", user_id="alice")
        await _create(watch_manager, "viking://resources/b1", user_id="bob")
        await _create(watch_manager, "viking://resources/a2", user_id="alice")
        await _create(watch_manager, "viking://resources/o1", account_id="other", user_id="alice")

        root = await watch_manager.get_all_tasks(TEST_ACCOUNT_ID, "root", "root")
        admin = await watch_manager.get_all_tasks(TEST_ACCOUNT_ID, "alice", "admin")
        user = await watch_manager.get_all_tasks(TEST_ACCOUNT_ID, "bob", "user")

        assert len(root) == 4
        assert {task.user_id for task in admin} == {"alice"} and len(admin) == 2
        assert [task.user_id for task in user] == ["bob"]


class TestTargetMoves:
    @pytest.mark.asyncio
    async def test_move_and_delete_watch_subtrees_preserve_account_boundaries(self, watch_manager):
        root = await _create(
            watch_manager,
            "viking://resources/codeask/wiki",
            parent_uri="viking://resources/codeask",
        )
        child = await _create(watch_manager, "viking://resources/codeask/wiki/page")
        sibling = await _create(watch_manager, "viking://resources/codeask/wiki-renamed")
        other = await _create(watch_manager, root.to_uri, account_id="other-account")

        moved = await watch_manager.move(
            account_id=TEST_ACCOUNT_ID,
            from_uri="viking://resources/codeask/wiki",
            to_uri="viking://resources/docs/wiki",
        )

        assert set(moved.task_ids) == {root.task_id, child.task_id}
        root_after = await watch_manager.get_task(root.task_id)
        assert root_after.to_uri == "viking://resources/docs/wiki"
        assert root_after.parent_uri == "viking://resources/docs"
        child_after = await watch_manager.get_task(child.task_id)
        assert child_after.to_uri == "viking://resources/docs/wiki/page"
        deactivated = await watch_manager.deactivate_tasks_under_uri_internal(
            "viking://resources/docs/wiki", TEST_ACCOUNT_ID
        )
        assert {task.task_id for task in deactivated} == {root.task_id, child.task_id}
        assert not (await watch_manager.get_task(root.task_id)).is_active
        assert (await watch_manager.get_task(sibling.task_id)).is_active
        unchanged = await watch_manager.get_task(other.task_id)
        assert unchanged.to_uri == other.to_uri and unchanged.is_active

    @pytest.mark.asyncio
    async def test_move_onto_native_target_is_refused_without_changes(self, watch_manager):
        await _create(watch_manager, "viking://resources/target")
        moving = await _create(watch_manager, "viking://resources/source")

        with pytest.raises(ConflictError, match="already being monitored"):
            await watch_manager.move(
                account_id=TEST_ACCOUNT_ID,
                from_uri="viking://resources/source",
                to_uri="viking://resources/target",
            )
        assert (await watch_manager.get_task(moving.task_id)).to_uri == "viking://resources/source"


class TestSharedConnectorTargets:
    """Connector watches may share a target; native watches are exclusive."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("user_id", "role", "visible_indexes"),
        [
            ("carol", "user", ()),
            ("bob", "user", (2,)),
            ("alice", "user", (0, 1)),
        ],
    )
    async def test_uri_lookup_only_considers_visible_tasks(
        self, watch_manager: WatchManager, user_id, role, visible_indexes
    ):
        to_uri = "viking://resources/shared"
        tasks = []
        for index, owner in enumerate(("alice", "alice", "bob")):
            tasks.append(
                await _create(
                    watch_manager,
                    to_uri,
                    path=f"tos://bucket/{index}/",
                    user_id=owner,
                    auth_state=dict(_CONNECTOR_AUTH),
                )
            )

        if len(visible_indexes) > 1:
            with pytest.raises(ConflictError, match=f"has {len(visible_indexes)} watch tasks"):
                await watch_manager.get_task_by_uri(to_uri, TEST_ACCOUNT_ID, user_id, role)
        else:
            task = await watch_manager.get_task_by_uri(to_uri, TEST_ACCOUNT_ID, user_id, role)
            expected = tasks[visible_indexes[0]].task_id if visible_indexes else None
            assert (task.task_id if task else None) == expected

    @pytest.mark.asyncio
    async def test_connector_move_may_land_on_connector_target(self, watch_manager):
        auth = dict(_CONNECTOR_AUTH)
        await _create(watch_manager, "viking://resources/target", path="tos://a/", auth_state=auth)
        moving = await _create(
            watch_manager, "viking://resources/source", path="tos://b/", auth_state=auth
        )

        await watch_manager.move(
            account_id=TEST_ACCOUNT_ID,
            from_uri="viking://resources/source",
            to_uri="viking://resources/target",
        )

        assert (await watch_manager.get_task(moving.task_id)).to_uri == "viking://resources/target"
