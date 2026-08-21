import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.resource.uri_mutation_coordinator import UriMutationCoordinator
from openviking.resource.watch_manager import WatchManager
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

            async def refresh_resource(self, **kwargs):
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

            async def refresh_resource(self, **kwargs):
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

    @pytest.mark.asyncio
    async def test_execute_task_uses_stable_target_and_options(self, tmp_path):
        class FakeResourceService(ResourceService):
            def __init__(self):
                super().__init__()
                self.calls = []

            async def refresh_resource(self, **kwargs):
                self.calls.append(kwargs)
                return {"root_uri": kwargs.get("to")}

        source = tmp_path / "source.txt"
        source.write_text("ok")
        coordinator = UriMutationCoordinator()
        resource_service = FakeResourceService()
        scheduler = WatchScheduler(
            resource_service=resource_service,
            uri_mutation_coordinator=coordinator,
            check_interval=1,
        )
        manager = WatchManager(uri_mutation_coordinator=coordinator)
        await manager.initialize()
        scheduler._watch_manager = manager
        manager.update_execution_time = AsyncMock()
        old_uri = "viking://resources/codeask/wiki"
        new_uri = "viking://resources/codeask/wiki-renamed"
        task = await manager.create_task(
            path=str(source),
            to_uri=old_uri,
            watch_interval=30.0,
            processing_mode="vectors_only",
        )

        async with coordinator.mutation(task.account_id, [old_uri, new_uri]):
            execution = asyncio.create_task(scheduler._execute_task(task.model_copy(deep=True)))
            await asyncio.sleep(0)
            assert resource_service.calls == []
            await manager.rewrite_target_prefix_internal(
                old_uri,
                new_uri,
                account_id=task.account_id,
            )

        await asyncio.wait_for(execution, timeout=1)

        assert len(resource_service.calls) == 1
        assert resource_service.calls[0]["to"] == new_uri
        assert resource_service.calls[0]["processing_mode"] == "vectors_only"
        assert resource_service.calls[0]["enforce_public_remote_targets"] is True
