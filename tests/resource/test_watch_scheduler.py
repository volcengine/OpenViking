import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from openviking.resource.feishu_watch_auth import FeishuRefreshedToken
from openviking.resource.watch_manager import WatchManager, WatchTask
from openviking.resource.watch_scheduler import WatchScheduler
from openviking.service.resource_service import ResourceService


class TestWatchSchedulerValidation:
    def test_check_interval_must_be_positive(self):
        rs = ResourceService()
        with pytest.raises(ValueError, match="check_interval must be > 0"):
            WatchScheduler(resource_service=rs, check_interval=0)

    def test_max_concurrency_must_be_positive(self):
        rs = ResourceService()
        with pytest.raises(ValueError, match="max_concurrency must be > 0"):
            WatchScheduler(resource_service=rs, max_concurrency=0)


class TestWatchSchedulerResourceExistence:
    def test_url_like_sources_are_treated_as_existing(self):
        rs = ResourceService()
        scheduler = WatchScheduler(resource_service=rs, check_interval=1)
        assert scheduler._check_resource_exists("http://example.com") is True
        assert scheduler._check_resource_exists("https://example.com") is True
        assert scheduler._check_resource_exists("git@github.com:org/repo.git") is True
        assert scheduler._check_resource_exists("ssh://git@github.com/org/repo.git") is True
        assert scheduler._check_resource_exists("git://github.com/org/repo.git") is True

    @pytest.mark.asyncio
    async def test_missing_target_uri_deactivates_without_add_resource(self, tmp_path):
        from openviking_cli.exceptions import NotFoundError

        class FakeVikingFS:
            async def stat(self, uri, ctx=None):
                raise NotFoundError(uri, "resource")

        class FakeResourceService(ResourceService):
            def __init__(self):
                super().__init__()
                self.calls = []

            async def add_resource(self, **kwargs):
                self.calls.append(kwargs)
                return {"root_uri": kwargs.get("to")}

        source = tmp_path / "source.txt"
        source.write_text("ok")
        resource_service = FakeResourceService()
        scheduler = WatchScheduler(
            resource_service=resource_service,
            viking_fs=FakeVikingFS(),
            check_interval=1,
        )
        manager = WatchManager(viking_fs=None)
        await manager.initialize()
        scheduler._watch_manager = manager
        task = await manager.create_task(
            path=str(source),
            to_uri="viking://resources/codeask/wiki",
            watch_interval=30.0,
        )

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert updated is not None
        assert updated.is_active is False
        assert resource_service.calls == []

    @pytest.mark.asyncio
    async def test_target_uri_check_error_does_not_deactivate_task(self, tmp_path):
        class FakeVikingFS:
            async def stat(self, uri, ctx=None):
                raise RuntimeError("temporary stat failure")

        class FakeResourceService(ResourceService):
            def __init__(self):
                super().__init__()
                self.calls = []

            async def add_resource(self, **kwargs):
                self.calls.append(kwargs)
                return {"root_uri": kwargs.get("to")}

        source = tmp_path / "source.txt"
        source.write_text("ok")
        resource_service = FakeResourceService()
        scheduler = WatchScheduler(
            resource_service=resource_service,
            viking_fs=FakeVikingFS(),
            check_interval=1,
        )
        manager = WatchManager(viking_fs=None)
        await manager.initialize()
        scheduler._watch_manager = manager
        manager.update_execution_time = AsyncMock()
        task = await manager.create_task(
            path=str(source),
            to_uri="viking://resources/codeask/wiki",
            watch_interval=30.0,
        )

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert updated is not None
        assert updated.is_active is True
        assert resource_service.calls and resource_service.calls[0]["to"] == task.to_uri
        manager.update_execution_time.assert_awaited_once_with(
            task.task_id,
            expected_revision=task.revision,
        )

    @pytest.mark.asyncio
    async def test_stale_target_lookup_cannot_deactivate_updated_task(self, tmp_path):
        from openviking_cli.exceptions import NotFoundError

        stat_started = asyncio.Event()
        allow_stat_to_finish = asyncio.Event()

        class BlockingMissingVikingFS:
            async def stat(self, uri, ctx=None):
                stat_started.set()
                await allow_stat_to_finish.wait()
                raise NotFoundError(uri, "resource")

        source = tmp_path / "source.txt"
        source.write_text("ok")
        resource_service = ResourceService()
        scheduler = WatchScheduler(
            resource_service=resource_service,
            viking_fs=BlockingMissingVikingFS(),
            check_interval=1,
        )
        manager = WatchManager(viking_fs=None)
        await manager.initialize()
        scheduler._watch_manager = manager
        task = await manager.create_task(
            path=str(source),
            to_uri="viking://resources/codeask/wiki",
            watch_interval=30.0,
        )
        task.next_execution_time = datetime.now() - timedelta(seconds=1)
        executed_revision = task.revision

        execution = asyncio.create_task(scheduler._execute_task(task))
        await stat_started.wait()
        await manager.update_task(
            task_id=task.task_id,
            account_id=task.account_id,
            user_id=task.user_id,
            role=task.original_role,
            instruction="updated while target lookup was waiting",
        )
        allow_stat_to_finish.set()
        await execution

        updated = await manager.get_task(task.task_id)
        assert updated is not None
        assert updated.revision == executed_revision + 1
        assert updated.is_active is True
        assert updated.instruction == "updated while target lookup was waiting"
        assert [due.task_id for due in await manager.get_due_tasks()] == [task.task_id]


class TestWatchSchedulerFeishuPrecheck:
    class FakeVikingFS:
        def __init__(self):
            self.mod_time = "destination-v1"

        async def stat(self, uri, ctx=None, skip_count=False):
            return {
                "name": uri.rsplit("/", 1)[-1],
                "size": 1,
                "modTime": self.mod_time,
                "isDir": True,
            }

    class FakeResourceService(ResourceService):
        def __init__(self, viking_fs):
            super().__init__()
            self.viking_fs = viking_fs
            self.calls = []
            self.error = None
            self.result_error = False
            self.queue_errors = {}
            self.include_queue_status = True

        async def add_resource(self, **kwargs):
            self.calls.append(kwargs)
            if self.error is not None:
                raise self.error
            if self.result_error:
                return {"status": "error"}
            self.viking_fs.mod_time = f"destination-v{len(self.calls) + 1}"
            result = {"root_uri": kwargs.get("to")}
            if self.include_queue_status:
                result["queue_status"] = {
                    name: {
                        "processed": 1,
                        "error_count": self.queue_errors.get(name, 0),
                        "errors": (
                            [{"message": f"{name} failed"}]
                            if self.queue_errors.get(name, 0)
                            else []
                        ),
                    }
                    for name in ("Semantic", "Embedding")
                }
            return result

    @staticmethod
    async def _setup_task(
        monkeypatch,
        to_uri="viking://resources/feishu-doc",
    ):
        viking_fs = TestWatchSchedulerFeishuPrecheck.FakeVikingFS()
        resource_service = TestWatchSchedulerFeishuPrecheck.FakeResourceService(viking_fs)
        scheduler = WatchScheduler(
            resource_service=resource_service,
            viking_fs=viking_fs,
            check_interval=1,
        )
        manager = WatchManager(viking_fs=None)
        await manager.initialize()
        scheduler._watch_manager = manager
        monkeypatch.setattr(
            "openviking.resource.watch_scheduler.load_feishu_app_credentials",
            lambda: type(
                "Credentials",
                (),
                {
                    "app_id": "app-1",
                    "app_secret": "secret-1",
                    "domain": "https://open.feishu.cn",
                },
            )(),
        )
        task = await manager.create_task(
            path="https://example.feishu.cn/docx/doc_token",
            to_uri=to_uri,
            watch_interval=30.0,
        )
        return resource_service, scheduler, manager, task, viking_fs

    @pytest.mark.asyncio
    async def test_first_sync_records_private_fingerprint_after_completed_sync(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert resource_service.calls[0]["wait"] is True
        fingerprint = updated.sync_state["feishu_sync_fingerprint_v1"]
        assert len(fingerprint) == 64
        assert "secret-1" not in str(updated.sync_state)

    @pytest.mark.asyncio
    async def test_cancellation_during_post_sync_stat_finishes_revision_commit(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        original_fetch = scheduler._fetch_destination_sync_state
        post_sync_stat_started = asyncio.Event()
        allow_post_sync_stat = asyncio.Event()
        fetch_count = 0

        async def block_post_sync_stat(to_uri, ctx):
            nonlocal fetch_count
            fetch_count += 1
            if fetch_count == 2:
                post_sync_stat_started.set()
                await allow_post_sync_stat.wait()
            return await original_fetch(to_uri, ctx)

        scheduler._fetch_destination_sync_state = block_post_sync_stat
        execution = asyncio.create_task(scheduler._execute_task(task))
        await post_sync_stat_started.wait()

        execution.cancel()
        await asyncio.sleep(0)
        assert not execution.done()
        allow_post_sync_stat.set()

        with pytest.raises(asyncio.CancelledError):
            await execution

        updated = await manager.get_task(task.task_id)
        assert updated is not None
        assert updated.last_execution_time is not None
        assert "feishu_sync_fingerprint_v1" in updated.sync_state

    @pytest.mark.asyncio
    async def test_unchanged_source_inputs_and_destination_skip_full_sync(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        await scheduler._execute_task(task)
        resource_service.calls.clear()

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert resource_service.calls == []
        assert updated.last_execution_time is not None
        assert "feishu_sync_fingerprint_v1" in updated.sync_state

    @pytest.mark.asyncio
    async def test_changed_source_runs_sync_and_advances_fingerprint(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        await scheduler._execute_task(task)
        previous = task.sync_state["feishu_sync_fingerprint_v1"]
        resource_service.calls.clear()
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=101)

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert updated.sync_state["feishu_sync_fingerprint_v1"] != previous

    @pytest.mark.asyncio
    async def test_changed_sync_inputs_force_full_sync(self, monkeypatch):
        resource_service, scheduler, _, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        await scheduler._execute_task(task)
        resource_service.calls.clear()
        task.instruction = "new processing instruction"

        await scheduler._execute_task(task)

        assert len(resource_service.calls) == 1

    @pytest.mark.asyncio
    async def test_changed_destination_state_forces_full_sync(self, monkeypatch):
        resource_service, scheduler, _, task, viking_fs = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        await scheduler._execute_task(task)
        resource_service.calls.clear()
        viking_fs.mod_time = "destination-replaced"

        await scheduler._execute_task(task)

        assert len(resource_service.calls) == 1

    @pytest.mark.asyncio
    async def test_changed_auth_context_forces_full_sync(self, monkeypatch):
        resource_service, scheduler, _, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        await scheduler._execute_task(task)
        resource_service.calls.clear()
        monkeypatch.setattr(
            "openviking.resource.watch_scheduler.load_feishu_app_credentials",
            lambda: type(
                "Credentials",
                (),
                {
                    "app_id": "app-2",
                    "app_secret": "secret-2",
                    "domain": "https://open.feishu.cn",
                },
            )(),
        )

        await scheduler._execute_task(task)

        assert len(resource_service.calls) == 1

    @pytest.mark.asyncio
    async def test_precheck_failure_falls_back_to_full_sync(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=None)

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert updated.sync_state == {}

    @pytest.mark.asyncio
    async def test_pre_destination_stat_failure_cannot_baseline_async_sync(self, monkeypatch):
        resource_service, scheduler, manager, task, viking_fs = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        original_stat = viking_fs.stat
        failed_precheck = False

        async def fail_first_sync_state_stat(uri, ctx=None, skip_count=False):
            nonlocal failed_precheck
            if skip_count and not failed_precheck:
                failed_precheck = True
                raise RuntimeError("transient destination stat failure")
            return await original_stat(uri, ctx=ctx, skip_count=skip_count)

        viking_fs.stat = fail_first_sync_state_stat

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert failed_precheck is True
        assert resource_service.calls[0]["wait"] is False
        assert updated.sync_state == {}

    @pytest.mark.asyncio
    async def test_accessor_construction_failure_falls_back_to_full_sync(self, monkeypatch):
        resource_service, scheduler, _, task, _ = await self._setup_task(monkeypatch)
        monkeypatch.setattr(
            scheduler,
            "_get_feishu_accessor",
            lambda: (_ for _ in ()).throw(RuntimeError("accessor unavailable")),
        )

        await scheduler._execute_task(task)

        assert len(resource_service.calls) == 1

    @pytest.mark.asyncio
    async def test_failed_sync_does_not_advance_fingerprint(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        await scheduler._execute_task(task)
        previous = task.sync_state["feishu_sync_fingerprint_v1"]
        resource_service.calls.clear()
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=101)
        resource_service.error = RuntimeError("sync failed")

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert updated.sync_state["feishu_sync_fingerprint_v1"] == previous

    @pytest.mark.asyncio
    async def test_error_result_does_not_advance_fingerprint(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        await scheduler._execute_task(task)
        previous = task.sync_state["feishu_sync_fingerprint_v1"]
        resource_service.calls.clear()
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=101)
        resource_service.result_error = True

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert updated.sync_state["feishu_sync_fingerprint_v1"] == previous

    @pytest.mark.asyncio
    async def test_missing_queue_status_does_not_advance_fingerprint(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        await scheduler._execute_task(task)
        previous = task.sync_state["feishu_sync_fingerprint_v1"]
        resource_service.calls.clear()
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=101)
        resource_service.include_queue_status = False

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert updated.sync_state["feishu_sync_fingerprint_v1"] == previous

    @pytest.mark.asyncio
    @pytest.mark.parametrize("queue_name", ["Semantic", "Embedding"])
    async def test_queue_error_does_not_advance_fingerprint(self, monkeypatch, queue_name):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        await scheduler._execute_task(task)
        previous = task.sync_state["feishu_sync_fingerprint_v1"]
        resource_service.calls.clear()
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=101)
        resource_service.queue_errors[queue_name] = 1

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert updated.sync_state["feishu_sync_fingerprint_v1"] == previous

    @pytest.mark.asyncio
    async def test_missing_target_uri_disables_fingerprint_skip(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(
            monkeypatch,
            to_uri=None,
        )
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)

        await scheduler._execute_task(task)
        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 2
        assert all(call["wait"] is False for call in resource_service.calls)
        assert updated.sync_state == {}

    @pytest.mark.asyncio
    async def test_concurrent_task_update_cannot_rewrite_completed_sync_fingerprint(
        self, monkeypatch
    ):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        add_started = asyncio.Event()
        allow_add_to_finish = asyncio.Event()
        original_add_resource = resource_service.add_resource

        async def blocking_add_resource(**kwargs):
            add_started.set()
            await allow_add_to_finish.wait()
            return await original_add_resource(**kwargs)

        resource_service.add_resource = blocking_add_resource
        task.next_execution_time = datetime.now() - timedelta(seconds=1)
        executed_revision = task.revision
        execution = asyncio.create_task(scheduler._execute_task(task))
        await add_started.wait()

        await manager.update_task(
            task_id=task.task_id,
            account_id=task.account_id,
            user_id=task.user_id,
            role=task.original_role,
            path="https://example.feishu.cn/docx/changed_token",
            to_uri="viking://resources/changed-target",
            instruction="changed while synchronization was waiting",
            build_index=False,
        )
        allow_add_to_finish.set()
        await execution

        first_call = resource_service.calls[0]
        assert first_call["path"] == "https://example.feishu.cn/docx/doc_token"
        assert first_call["to"] == "viking://resources/feishu-doc"
        assert first_call["instruction"] == ""
        assert first_call["build_index"] is True

        updated = await manager.get_task(task.task_id)
        assert updated.revision == executed_revision + 1
        assert updated.last_execution_time is None
        assert updated.sync_state == {}
        assert [due.task_id for due in await manager.get_due_tasks()] == [task.task_id]
        resource_service.calls.clear()
        await scheduler._execute_task(updated)

        assert len(resource_service.calls) == 1
        assert resource_service.calls[0]["path"] == updated.path
        assert resource_service.calls[0]["to"] == updated.to_uri
        assert resource_service.calls[0]["instruction"] == updated.instruction
        assert resource_service.calls[0]["build_index"] is False

    @pytest.mark.asyncio
    async def test_app_auth_context_is_captured_once_per_execution(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)
        credentials = [
            type(
                "Credentials",
                (),
                {
                    "app_id": "app-old",
                    "app_secret": "secret-old",
                    "domain": "https://open.feishu.cn",
                },
            )(),
            type(
                "Credentials",
                (),
                {
                    "app_id": "app-new",
                    "app_secret": "secret-new",
                    "domain": "https://open.feishu.cn",
                },
            )(),
        ]
        credential_reads = 0

        def rotating_credentials():
            nonlocal credential_reads
            value = credentials[min(credential_reads, len(credentials) - 1)]
            credential_reads += 1
            return value

        monkeypatch.setattr(
            "openviking.resource.watch_scheduler.load_feishu_app_credentials",
            rotating_credentials,
        )

        await scheduler._execute_task(task)

        assert credential_reads == 1
        first_fingerprint = (await manager.get_task(task.task_id)).sync_state[
            "feishu_sync_fingerprint_v1"
        ]
        resource_service.calls.clear()

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert credential_reads == 2
        assert len(resource_service.calls) == 1
        assert updated.sync_state["feishu_sync_fingerprint_v1"] != first_fingerprint

    @pytest.mark.asyncio
    async def test_stale_oauth_refresh_cannot_overwrite_rotated_credentials(self, monkeypatch):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        old_auth = {
            "provider": "feishu",
            "access_token": "access-old",
            "refresh_token": "refresh-old",
            "expires_at": None,
        }
        task = await manager.update_task(
            task_id=task.task_id,
            account_id=task.account_id,
            user_id=task.user_id,
            role=task.original_role,
            auth_state=old_auth,
        )
        task.next_execution_time = datetime.now() - timedelta(seconds=1)
        executed_revision = task.revision
        refresh_started = asyncio.Event()
        allow_refresh_to_finish = asyncio.Event()

        class BlockingOAuthClient:
            async def refresh_user_access_token(self, refresh_token):
                assert refresh_token == "refresh-old"
                refresh_started.set()
                await allow_refresh_to_finish.wait()
                return FeishuRefreshedToken(
                    access_token="access-stale",
                    refresh_token="refresh-stale",
                    expires_in=3600,
                )

        scheduler._feishu_oauth_client = BlockingOAuthClient()
        execution = asyncio.create_task(scheduler._execute_task(task))
        await refresh_started.wait()
        rotated_auth = {
            "provider": "feishu",
            "access_token": "access-rotated",
            "refresh_token": "refresh-rotated",
            "expires_at": "2999-01-01T00:00:00+00:00",
        }
        await manager.update_task(
            task_id=task.task_id,
            account_id=task.account_id,
            user_id=task.user_id,
            role=task.original_role,
            auth_state=rotated_auth,
        )
        allow_refresh_to_finish.set()
        await execution

        updated = await manager.get_task(task.task_id)
        assert updated is not None
        assert updated.revision == executed_revision + 1
        assert updated.auth_state == rotated_auth
        assert resource_service.calls == []
        assert [due.task_id for due in await manager.get_due_tasks()] == [task.task_id]

    @pytest.mark.asyncio
    async def test_successful_oauth_refresh_revision_is_used_for_execution_commit(
        self, monkeypatch
    ):
        resource_service, scheduler, manager, task, _ = await self._setup_task(monkeypatch)
        task = await manager.update_task(
            task_id=task.task_id,
            account_id=task.account_id,
            user_id=task.user_id,
            role=task.original_role,
            auth_state={
                "provider": "feishu",
                "access_token": "access-old",
                "refresh_token": "refresh-old",
                "expires_at": None,
            },
        )
        executed_revision = task.revision
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)

        class SuccessfulOAuthClient:
            async def refresh_user_access_token(self, refresh_token):
                return FeishuRefreshedToken(
                    access_token="access-new",
                    refresh_token="refresh-new",
                    expires_in=3600,
                )

        scheduler._feishu_oauth_client = SuccessfulOAuthClient()
        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert updated is not None
        assert updated.revision == executed_revision + 1
        assert updated.auth_state["access_token"] == "access-new"
        assert updated.last_execution_time is not None
        assert "feishu_sync_fingerprint_v1" in updated.sync_state

    def test_sync_state_is_private_but_persisted(self):
        task = WatchTask(
            path="https://example.feishu.cn/docx/doc_token",
            sync_state={"feishu_sync_fingerprint_v1": "abc123"},
            revision=7,
        )

        assert "sync_state" not in task.to_dict()
        assert "revision" not in task.to_dict()
        restored = WatchTask.from_dict(task.to_storage_dict())
        assert restored.sync_state == {"feishu_sync_fingerprint_v1": "abc123"}
        assert restored.revision == 7
