from unittest.mock import AsyncMock

import pytest

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
        manager.update_execution_time.assert_awaited_once_with(task.task_id)


class TestWatchSchedulerFeishuPrecheck:
    class FakeResourceService(ResourceService):
        def __init__(self):
            super().__init__()
            self.calls = []
            self.error = None

        async def add_resource(self, **kwargs):
            self.calls.append(kwargs)
            if self.error is not None:
                raise self.error
            return {"root_uri": kwargs.get("to")}

    @staticmethod
    async def _setup_task():
        resource_service = TestWatchSchedulerFeishuPrecheck.FakeResourceService()
        scheduler = WatchScheduler(resource_service=resource_service, check_interval=1)
        manager = WatchManager(viking_fs=None)
        await manager.initialize()
        scheduler._watch_manager = manager
        task = await manager.create_task(
            path="https://example.feishu.cn/docx/doc_token",
            to_uri=None,
            watch_interval=30.0,
        )
        return resource_service, scheduler, manager, task

    @pytest.mark.asyncio
    async def test_first_sync_records_modify_time(self):
        resource_service, scheduler, manager, task = await self._setup_task()
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert updated.sync_state == {"feishu_latest_modify_time": 100}
        assert "feishu_latest_modify_time" not in resource_service.calls[0]

    @pytest.mark.asyncio
    async def test_unchanged_source_skips_full_sync(self):
        resource_service, scheduler, manager, task = await self._setup_task()
        task.sync_state["feishu_latest_modify_time"] = 100
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=100)

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert resource_service.calls == []
        assert updated.last_execution_time is not None
        assert updated.sync_state == {"feishu_latest_modify_time": 100}

    @pytest.mark.asyncio
    async def test_changed_source_runs_sync_and_advances_baseline(self):
        resource_service, scheduler, manager, task = await self._setup_task()
        task.sync_state["feishu_latest_modify_time"] = 100
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=101)

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert updated.sync_state == {"feishu_latest_modify_time": 101}

    @pytest.mark.asyncio
    async def test_precheck_failure_falls_back_to_full_sync(self):
        resource_service, scheduler, manager, task = await self._setup_task()
        task.sync_state["feishu_latest_modify_time"] = 100
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=None)

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert updated.sync_state == {"feishu_latest_modify_time": 100}

    @pytest.mark.asyncio
    async def test_failed_sync_does_not_advance_baseline(self):
        resource_service, scheduler, manager, task = await self._setup_task()
        task.sync_state["feishu_latest_modify_time"] = 100
        scheduler._fetch_feishu_latest_modify_time = AsyncMock(return_value=101)
        resource_service.error = RuntimeError("sync failed")

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert len(resource_service.calls) == 1
        assert updated.sync_state == {"feishu_latest_modify_time": 100}

    def test_sync_state_is_private_but_persisted(self):
        task = WatchTask(
            path="https://example.feishu.cn/docx/doc_token",
            sync_state={"feishu_latest_modify_time": 100},
        )

        assert "sync_state" not in task.to_dict()
        restored = WatchTask.from_dict(task.to_storage_dict())
        assert restored.sync_state == {"feishu_latest_modify_time": 100}
