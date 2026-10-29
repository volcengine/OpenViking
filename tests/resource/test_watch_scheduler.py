# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Leader-polled WatchScheduler over the file Watch store and real PathLocks."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openviking.resource.feishu_watch_auth import (
    FeishuOAuthClient,
    FeishuRefreshedToken,
    FeishuTokenRefreshError,
)
from openviking.resource.git_watch_auth import create_git_http_auth_state
from openviking.resource.watch_scheduler import WatchScheduler
from openviking.resource.watch_store_file import FileWatchStore
from openviking.utils.git_auth import GIT_AUTH_FAILED, GitHttpAuthConfig
from tests.utils.watch_fixtures import shape_watch

TARGET = "viking://resources/doc"
CONNECTOR_AUTH = {"provider": "connector_encrypted", "ciphertext": "unused"}


class FakeRecord:
    def __init__(self, task_id, status="running", result=None, error=None):
        self.task_id = task_id
        self.status = SimpleNamespace(value=status)
        self.result = result
        self.error = error

    def is_terminal(self):
        return self.status.value in {"completed", "failed", "cancelled"}


class FakeTracker:
    """Task records by ID, as every node would see them in the shared Task store."""

    def __init__(self):
        self.records: dict[str, FakeRecord] = {}
        self.cancelled: list[str] = []
        self.finished: dict[str, asyncio.Event] = {}

    def put(self, task_id, status="running", **values):
        self.records[task_id] = FakeRecord(task_id, status, **values)
        if self.records[task_id].is_terminal():
            self.finished.setdefault(task_id, asyncio.Event()).set()

    async def get(self, task_id, *, account_id, user_id):
        return self.records.get(task_id)

    async def wait(self, task_id, *, account_id, user_id, timeout=None):
        if task_id not in self.records:
            raise KeyError(task_id)
        await asyncio.wait_for(self.finished.setdefault(task_id, asyncio.Event()).wait(), timeout)
        return self.records[task_id]

    async def cancel(self, task_id, *, account_id, user_id):
        self.cancelled.append(task_id)
        self.put(task_id, "cancelled")


@pytest.fixture
def tracker(monkeypatch):
    fake = FakeTracker()
    monkeypatch.setattr("openviking.resource.watch_scheduler.get_task_tracker", lambda: fake)
    return fake


@pytest.fixture
def service():
    return SimpleNamespace(refresh_resource=AsyncMock(), _connector=None)


@pytest_asyncio.fixture
async def make_scheduler(watch_vfs):
    """Schedulers alive together share one process's store; a later one reloads the file."""
    created = []
    store = None

    async def factory(service, **kwargs):
        nonlocal store
        if store is None or not any(scheduler.is_running for scheduler in created):
            store = FileWatchStore(watch_vfs)
        kwargs.setdefault("leader_enabled", False)
        scheduler = WatchScheduler(service, store, viking_fs=watch_vfs, **kwargs)
        await scheduler.start()
        created.append(scheduler)
        return scheduler

    yield factory
    for scheduler in created:
        await scheduler.stop()


async def create_watch(scheduler, uri=TARGET, *, due=True, **values):
    await scheduler._viking_fs.mkdir(uri, exist_ok=True)
    values.setdefault("path", "https://example.org/doc")
    task = await scheduler.watch_manager.create_task(to_uri=uri, watch_interval=5, **values)
    if due:
        await shape_watch(
            scheduler.watch_manager.store,
            task.task_id,
            next_run_at=(datetime.now() - timedelta(minutes=1)).timestamp(),
        )
    return task


async def _occupy(scheduler, task, run_id):
    await shape_watch(
        scheduler.watch_manager.store,
        task.task_id,
        current_run_id=run_id,
        run_started_at=datetime.now().timestamp(),
    )


async def state_of(scheduler, task_id):
    return await scheduler.watch_manager.store.get_state(task_id)


async def drain(scheduler):
    while scheduler._background:
        await asyncio.gather(*list(scheduler._background), return_exceptions=True)


class TestLeadership:
    @pytest.mark.asyncio
    async def test_leader_loop_dispatches_due_watches(self, make_scheduler, service, tracker):
        submitted = asyncio.Event()

        async def refresh(**kwargs):
            submitted.set()
            return {"status": "completed"}

        service.refresh_resource.side_effect = refresh
        scheduler = await make_scheduler(
            service, leader_enabled=True, poll_interval=0.05, leader_ttl=1
        )
        task = await create_watch(scheduler)

        await asyncio.wait_for(submitted.wait(), 2)

        assert scheduler.is_leader
        assert service.refresh_resource.await_count == 1
        await drain(scheduler)
        state = await state_of(scheduler, task.task_id)
        assert state.current_run_id is None
        assert state.task.last_status == "completed"


class TestDispatch:
    @pytest.mark.asyncio
    async def test_async_run_occupies_watch_until_its_task_settles(
        self, make_scheduler, service, tracker
    ):
        async def refresh(**kwargs):
            tracker.put(kwargs["task_id"])
            return {"status": "queued", "task_id": kwargs["task_id"]}

        service.refresh_resource.side_effect = refresh
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler)

        assert await scheduler.schedule_task(task.task_id)
        run_id = (await state_of(scheduler, task.task_id)).current_run_id
        assert run_id is not None
        assert await scheduler.dispatch_due() == 0
        assert not await scheduler.schedule_task(task.task_id)

        tracker.put(run_id, "completed", result={"root_uri": TARGET})
        await drain(scheduler)

        state = await state_of(scheduler, task.task_id)
        assert state.current_run_id is None
        assert state.task.last_status == "completed"
        assert state.task.last_task_id == run_id

    @pytest.mark.asyncio
    async def test_busy_watches_do_not_hold_capacity(self, make_scheduler, service, tracker):
        service.refresh_resource.return_value = {"status": "completed"}
        scheduler = await make_scheduler(service, max_concurrency=1)
        scheduler.DUE_BATCH = 2
        busy = [await create_watch(scheduler, f"viking://resources/busy{i}") for i in range(3)]
        free = await create_watch(scheduler, "viking://resources/free")
        locks = scheduler.watch_manager.locks

        async with (
            locks.tree("default", busy[0].to_uri),
            locks.tree("default", busy[1].to_uri),
            locks.tree("default", busy[2].to_uri),
        ):
            assert not await scheduler.schedule_task(busy[0].task_id)
            assert (await state_of(scheduler, busy[0].task_id)).current_run_id is None
            assert await scheduler.dispatch_due() == 1
            await drain(scheduler)

        assert [call.kwargs["to"] for call in service.refresh_resource.await_args_list] == [
            free.to_uri
        ]

    @pytest.mark.asyncio
    async def test_dispatch_is_bounded_by_concurrency(self, make_scheduler, service, tracker):
        release = asyncio.Event()

        async def refresh(**kwargs):
            await release.wait()
            return {"status": "completed"}

        service.refresh_resource.side_effect = refresh
        scheduler = await make_scheduler(service, max_concurrency=2)
        for index in range(3):
            await create_watch(scheduler, f"viking://resources/doc{index}")

        assert await scheduler.dispatch_due() == 2
        assert await scheduler.dispatch_due() == 0
        release.set()
        await drain(scheduler)
        assert await scheduler.dispatch_due() == 1

    @pytest.mark.asyncio
    async def test_manual_trigger_runs_paused_watch(self, make_scheduler, service, tracker):
        service.refresh_resource.return_value = {"status": "completed"}
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler, due=False, is_active=False)

        assert await scheduler.schedule_task(task.task_id)

        state = await state_of(scheduler, task.task_id)
        assert state.task.last_status == "completed"
        assert state.task.is_active is False
        assert state.task.next_execution_time is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("task_created", [False, True])
    async def test_submission_failure_uses_the_created_task_as_authority(
        self, make_scheduler, service, tracker, task_created
    ):
        async def refresh(**kwargs):
            if task_created:
                tracker.put(kwargs["task_id"])
            raise RuntimeError("enqueue reply lost")

        service.refresh_resource.side_effect = refresh
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler)

        await scheduler.schedule_task(task.task_id)
        if task_created:
            run_id = (await state_of(scheduler, task.task_id)).current_run_id
            assert run_id is not None
            tracker.put(run_id, "failed", error="worker failed")
        await drain(scheduler)
        state = await state_of(scheduler, task.task_id)
        assert state.current_run_id is None
        assert state.task.last_status == "failed"
        assert state.task.last_error == ("worker failed" if task_created else "enqueue reply lost")
        assert state.task.is_active is True

    @pytest.mark.asyncio
    async def test_missing_target_deactivates_without_refresh(
        self, make_scheduler, service, tracker
    ):
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler)
        await scheduler._viking_fs.rm(TARGET, recursive=True)

        await scheduler.dispatch_due()
        await drain(scheduler)

        service.refresh_resource.assert_not_awaited()
        state = await state_of(scheduler, task.task_id)
        assert state.task.is_active is False
        assert "does not exist" in state.task.last_error


class TestSweep:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("cancel_result", "expected_status"),
        [
            ("running", "failed"),
            ("completed", "completed"),
        ],
    )
    async def test_first_sweep_after_release_deadline_requests_cancel_before_release(
        self, make_scheduler, service, tracker, cancel_result, expected_status
    ):
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler, due=False)
        await _occupy(scheduler, task, "late-run")
        await shape_watch(
            scheduler.watch_manager.store,
            task.task_id,
            run_started_at=(
                datetime.now().timestamp() - scheduler._run_timeout - scheduler._cancel_grace - 1
            ),
        )
        tracker.put("late-run")

        async def cancel(task_id, **_kwargs):
            assert (await state_of(scheduler, task.task_id)).current_run_id == task_id
            tracker.cancelled.append(task_id)
            tracker.put(task_id, cancel_result)

        tracker.cancel = cancel
        await scheduler.sweep()
        state = await state_of(scheduler, task.task_id)
        assert tracker.cancelled == ["late-run"]
        assert state.current_run_id is None
        assert state.task.last_status == expected_status
        assert state.task.last_task_id == "late-run"

    @pytest.mark.asyncio
    async def test_terminal_task_is_settled_by_the_sweep(self, make_scheduler, service, tracker):
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler, due=False)
        await _occupy(scheduler, task, "run-1")
        tracker.put("run-1", "failed", error="boom")

        await scheduler.sweep()

        state = await state_of(scheduler, task.task_id)
        assert state.current_run_id is None
        assert state.task.last_error == "boom"

    @pytest.mark.asyncio
    async def test_missing_task_is_settled_after_grace(self, make_scheduler, service, tracker):
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler, due=False)
        await _occupy(scheduler, task, "lost-run")

        await scheduler.sweep()
        assert (await state_of(scheduler, task.task_id)).current_run_id == "lost-run"

        await shape_watch(
            scheduler.watch_manager.store,
            task.task_id,
            run_started_at=datetime.now().timestamp() - scheduler._task_missing_grace - 1,
        )
        await scheduler.sweep()
        state = await state_of(scheduler, task.task_id)
        assert state.current_run_id is None
        assert state.task.last_status == "error"
        assert state.task.next_execution_time is not None

    @pytest.mark.asyncio
    async def test_missing_task_sweep_keeps_a_task_created_before_its_fence(
        self, make_scheduler, service, tracker, monkeypatch
    ):
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler, due=False)
        await _occupy(scheduler, task, "slow-submit")
        store = scheduler.watch_manager.store
        await shape_watch(
            store,
            task.task_id,
            run_started_at=datetime.now().timestamp() - scheduler._task_missing_grace - 1,
        )
        fence = store.fence_run

        async def created_before_fencing(task_id, run_id):
            tracker.put(run_id)
            return await fence(task_id, run_id)

        monkeypatch.setattr(store, "fence_run", created_before_fencing)
        await scheduler.sweep()

        assert (await state_of(scheduler, task.task_id)).current_run_id == "slow-submit"
        tracker.put("slow-submit", "completed")
        await scheduler.sweep()
        state = await state_of(scheduler, task.task_id)
        assert state.current_run_id is None
        assert state.task.last_status == "completed"

    @pytest.mark.asyncio
    async def test_overdue_run_stays_occupied_until_its_task_stops(
        self, make_scheduler, service, tracker
    ):
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler, due=False)
        await _occupy(scheduler, task, "slow-run")
        tracker.put("slow-run")

        await scheduler.sweep()
        assert tracker.cancelled == []
        await shape_watch(
            scheduler.watch_manager.store,
            task.task_id,
            run_started_at=datetime.now().timestamp() - scheduler._run_timeout - 1,
        )
        await scheduler.sweep()

        assert tracker.cancelled == ["slow-run"]
        assert (await state_of(scheduler, task.task_id)).current_run_id == "slow-run"
        await scheduler.sweep()
        state = await state_of(scheduler, task.task_id)
        assert state.current_run_id is None
        assert state.task.last_status == "cancelled"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("past_release_deadline", [False, True])
    async def test_failed_timeout_cancellation_is_retried_before_release(
        self, make_scheduler, service, tracker, past_release_deadline
    ):
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler, due=False)
        await _occupy(scheduler, task, "retry-run")
        age = scheduler._run_timeout + 1
        if past_release_deadline:
            age += scheduler._cancel_grace
        await shape_watch(
            scheduler.watch_manager.store,
            task.task_id,
            run_started_at=datetime.now().timestamp() - age,
        )
        tracker.put("retry-run")
        cancel = tracker.cancel
        attempts = 0

        async def retry_cancel(task_id, **owner):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("Cancellation storage unavailable")
            await cancel(task_id, **owner)

        tracker.cancel = retry_cancel
        await scheduler.sweep()
        assert (await state_of(scheduler, task.task_id)).current_run_id == "retry-run"
        assert tracker.records["retry-run"].status.value == "running"

        await scheduler.sweep()
        await scheduler.sweep()
        state = await state_of(scheduler, task.task_id)
        assert attempts == 2
        assert state.current_run_id is None
        assert state.task.last_status == "cancelled"

    @pytest.mark.asyncio
    async def test_overdue_run_that_does_not_stop_is_released_after_the_cancel_grace(
        self, make_scheduler, service, tracker
    ):
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler, due=False)
        await _occupy(scheduler, task, "stuck-run")
        tracker.put("stuck-run")
        tracker.cancel = AsyncMock(side_effect=ValueError("not cancellable"))
        await shape_watch(
            scheduler.watch_manager.store,
            task.task_id,
            run_started_at=datetime.now().timestamp() - scheduler._run_timeout - 1,
        )

        await scheduler.sweep()
        await scheduler.sweep()

        assert tracker.cancel.await_count == 1
        assert (await state_of(scheduler, task.task_id)).current_run_id == "stuck-run"
        await shape_watch(
            scheduler.watch_manager.store,
            task.task_id,
            run_started_at=(
                datetime.now().timestamp() - scheduler._run_timeout - scheduler._cancel_grace - 1
            ),
        )
        await scheduler.sweep()
        state = await state_of(scheduler, task.task_id)
        assert state.current_run_id is None
        assert state.task.last_status == "failed"
        assert "timed out" in state.task.last_error

    @pytest.mark.asyncio
    async def test_delete_tasks_cancels_running_runs(self, make_scheduler, service, tracker):
        scheduler = await make_scheduler(service)
        busy = await create_watch(scheduler, due=False)
        idle = await create_watch(scheduler, "viking://resources/idle", due=False)
        await _occupy(scheduler, busy, "run-1")
        tracker.put("run-1")

        await scheduler.delete_tasks("default", "default")

        assert tracker.cancelled == ["run-1"]
        assert await state_of(scheduler, busy.task_id) is None
        assert await state_of(scheduler, idle.task_id) is None

    @pytest.mark.asyncio
    async def test_delete_tasks_fails_a_run_task_that_cannot_be_cancelled(
        self, make_scheduler, service, tracker
    ):
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler, due=False, auth_state=CONNECTOR_AUTH)
        await _occupy(scheduler, task, "connector-run")
        tracker.put("connector-run")
        tracker.cancel = AsyncMock(side_effect=ValueError("not cancellable"))

        async def fail(task_id, error, *, account_id, user_id):
            tracker.put(task_id, "failed", error=error)

        tracker.fail = AsyncMock(side_effect=fail)

        await scheduler.delete_tasks("default", "default")

        tracker.fail.assert_awaited_once()
        assert tracker.records["connector-run"].status.value == "failed"
        assert await state_of(scheduler, task.task_id) is None


class TestRestartRecovery:
    @pytest.mark.asyncio
    async def test_restart_runs_only_watches_that_came_due(self, make_scheduler, service, tracker):
        service.refresh_resource.return_value = {"status": "completed"}
        first = await make_scheduler(service)
        overdue = await create_watch(first, "viking://resources/overdue")
        future = await create_watch(first, "viking://resources/future", due=False)
        paused = await create_watch(first, "viking://resources/paused", is_active=False)
        await first.stop()

        second = await make_scheduler(service)
        listed = await second.watch_manager.get_all_tasks("default", "default", "root")
        assert {task.task_id for task in listed} == {
            overdue.task_id,
            future.task_id,
            paused.task_id,
        }
        assert await second.dispatch_due() == 1
        await drain(second)

        assert service.refresh_resource.await_args.kwargs["to"] == "viking://resources/overdue"
        ran = (await state_of(second, overdue.task_id)).task
        assert ran.last_status == "completed"
        expected = ran.last_execution_time + timedelta(minutes=5)
        assert abs((ran.next_execution_time - expected).total_seconds()) < 1
        await second.stop()

        third = await make_scheduler(service)
        reloaded = (await state_of(third, overdue.task_id)).task
        assert reloaded.last_execution_time == ran.last_execution_time
        assert reloaded.next_execution_time == ran.next_execution_time
        assert await third.dispatch_due() == 0

    @pytest.mark.asyncio
    async def test_missing_local_source_deactivates_without_refresh(
        self, make_scheduler, service, tracker, tmp_path
    ):
        scheduler = await make_scheduler(service)
        task = await create_watch(scheduler, path=str(tmp_path / "gone.txt"))

        await scheduler.dispatch_due()
        await drain(scheduler)

        service.refresh_resource.assert_not_awaited()
        state = await state_of(scheduler, task.task_id)
        assert state.current_run_id is None
        assert state.task.is_active is False
        assert "does not exist" in state.task.last_error


class FakeFeishuOAuthClient:
    def __init__(self):
        self.calls = []

    async def refresh_user_access_token(self, refresh_token):
        self.calls.append(refresh_token)
        return FeishuRefreshedToken(access_token="u-new", refresh_token="r-new", expires_in=7200)


def _feishu_runtime_config():
    async def resolve_account(_account_id, resolver):
        from openviking_cli.utils.config.parser_config import FeishuConfig

        return resolver(
            SimpleNamespace(
                account=SimpleNamespace(feishu=None),
                cluster=SimpleNamespace(feishu=FeishuConfig(domain="https://open.feishu.cn")),
            )
        )

    return SimpleNamespace(resolve_account=resolve_account)


FEISHU_AUTH = {
    "provider": "feishu",
    "access_token": "u-old",
    "refresh_token": "r-old",
    "expires_at": None,
    "app_id": "cli-test",
    "app_secret": "secret-test",
}


class TestAuthRefresh:
    @pytest.mark.asyncio
    async def test_feishu_token_refreshes_before_submission(
        self, monkeypatch, make_scheduler, service, tracker
    ):
        client = FakeFeishuOAuthClient()
        monkeypatch.setattr(
            FeishuOAuthClient,
            "from_auth_state",
            classmethod(lambda _cls, _auth_state, *, config=None: client),
            raising=False,
        )
        service.refresh_resource.return_value = {"status": "completed"}
        scheduler = await make_scheduler(service, runtime_config_manager=_feishu_runtime_config())
        task = await create_watch(
            scheduler,
            "viking://resources/feishu",
            path="https://example.feishu.cn/docx/doc_token",
            auth_state=dict(FEISHU_AUTH),
        )

        assert await scheduler.schedule_task(task.task_id)

        assert client.calls == ["r-old"]
        assert service.refresh_resource.await_args.kwargs["feishu_access_token"] == "u-new"
        updated = await scheduler.watch_manager.get_task(task.task_id)
        assert updated.auth_state["access_token"] == "u-new"
        assert updated.auth_state["refresh_token"] == "r-new"

    @pytest.mark.asyncio
    async def test_permanent_feishu_refresh_failure_deactivates(
        self, monkeypatch, make_scheduler, service, tracker
    ):
        class FailingClient:
            async def refresh_user_access_token(self, _token):
                raise FeishuTokenRefreshError("invalid client credentials", permanent=True)

        monkeypatch.setattr(
            FeishuOAuthClient,
            "from_auth_state",
            classmethod(lambda _cls, _auth_state, *, config=None: FailingClient()),
            raising=False,
        )
        scheduler = await make_scheduler(service, runtime_config_manager=_feishu_runtime_config())
        task = await create_watch(
            scheduler,
            "viking://resources/feishu",
            path="https://example.feishu.cn/docx/doc_token",
            auth_state=dict(FEISHU_AUTH),
        )

        assert await scheduler.schedule_task(task.task_id)

        service.refresh_resource.assert_not_awaited()
        updated = await scheduler.watch_manager.get_task(task.task_id)
        assert updated.is_active is False
        assert updated.last_error == "invalid client credentials"

    @pytest.mark.asyncio
    async def test_git_token_is_restored_for_the_refresh(self, make_scheduler, service, tracker):
        repo_url = "https://git.example/org/private.git"
        service.refresh_resource.return_value = {"status": "completed"}
        scheduler = await make_scheduler(service)
        task = await create_watch(
            scheduler,
            path=repo_url,
            auth_state=create_git_http_auth_state(
                GitHttpAuthConfig(username="git-user", token="git-secret"), repo_url
            ),
        )

        assert await scheduler.schedule_task(task.task_id)

        call = service.refresh_resource.await_args.kwargs
        assert call["auth_config"] == {"username": "git-user", "token": "git-secret"}
        assert "git-secret" not in str(
            (await scheduler.watch_manager.get_task(task.task_id)).to_dict()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_failure", [True, False])
async def test_git_watch_pauses_only_on_auth_failure(
    make_scheduler, service, tracker, auth_failure
):
    error = "Git authentication failed." if auth_failure else "Connection timed out"
    code = GIT_AUTH_FAILED if auth_failure else "UNKNOWN"

    async def refresh(**kwargs):
        tracker.put(kwargs["task_id"], "failed", error=error, result={"code": code})
        return {"status": "queued", "task_id": kwargs["task_id"]}

    service.refresh_resource.side_effect = refresh
    scheduler = await make_scheduler(service)
    task = await create_watch(scheduler, path="https://github.com/org/repo.git")

    await scheduler.schedule_task(task.task_id)
    await drain(scheduler)

    updated = await scheduler.watch_manager.get_task(task.task_id)
    assert updated.last_status == "failed"
    assert updated.last_error == error
    assert updated.is_active is (not auth_failure)
    assert (updated.next_execution_time is None) is auth_failure
