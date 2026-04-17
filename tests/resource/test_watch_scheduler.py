import pytest
from unittest.mock import AsyncMock, MagicMock

from openviking.resource.watch_scheduler import WatchScheduler
from openviking.server.identity import Role


class _DummyResourceService:
    pass


class TestWatchSchedulerValidation:
    def test_check_interval_must_be_positive(self):
        rs = _DummyResourceService()
        with pytest.raises(ValueError, match="check_interval must be > 0"):
            WatchScheduler(resource_service=rs, check_interval=0)

    def test_max_concurrency_must_be_positive(self):
        rs = _DummyResourceService()
        with pytest.raises(ValueError, match="max_concurrency must be > 0"):
            WatchScheduler(resource_service=rs, max_concurrency=0)


class TestWatchSchedulerResourceExistence:
    def test_url_like_sources_are_treated_as_existing(self):
        rs = _DummyResourceService()
        scheduler = WatchScheduler(resource_service=rs, check_interval=1)
        assert scheduler._check_resource_exists("http://example.com") is True
        assert scheduler._check_resource_exists("https://example.com") is True
        assert scheduler._check_resource_exists("git@github.com:org/repo.git") is True
        assert scheduler._check_resource_exists("ssh://git@github.com/org/repo.git") is True
        assert scheduler._check_resource_exists("git://github.com/org/repo.git") is True


class TestWatchSchedulerExecution:
    @pytest.mark.asyncio
    async def test_execute_task_reuses_resource_add_path(self):
        rs = _DummyResourceService()
        rs.add_resource = AsyncMock(return_value={"root_uri": "viking://resources/demo"})
        scheduler = WatchScheduler(resource_service=rs, check_interval=1)
        scheduler._watch_manager = MagicMock()
        scheduler._watch_manager.update_execution_time = AsyncMock()
        scheduler._watch_manager.update_task = AsyncMock()

        task = MagicMock(
            task_id="watch-1",
            path="/tmp/demo",
            to_uri="viking://resources/demo",
            parent_uri="viking://resources",
            reason="watch refresh",
            instruction="keep updated",
            watch_interval=15.0,
            build_index=True,
            summarize=False,
            processor_kwargs={"custom_option": "x"},
            account_id="acc",
            user_id="user",
            agent_id="agent",
            original_role=Role.USER.value,
        )

        scheduler._check_resource_exists = lambda path: path == "/tmp/demo"

        await scheduler._execute_task(task)

        rs.add_resource.assert_awaited_once()
        call = rs.add_resource.await_args
        assert call.kwargs["path"] == "/tmp/demo"
        assert call.kwargs["to"] == "viking://resources/demo"
        assert call.kwargs["parent"] == "viking://resources"
        assert call.kwargs["watch_interval"] == 15.0
        assert call.kwargs["skip_watch_management"] is True
        assert call.kwargs["build_index"] is True
        assert call.kwargs["summarize"] is False
        assert call.kwargs["custom_option"] == "x"
        scheduler._watch_manager.update_execution_time.assert_awaited_once_with("watch-1")
