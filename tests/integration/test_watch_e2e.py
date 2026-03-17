# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for resource watch functionality."""

import asyncio
import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.resource.watch_scheduler import WatchScheduler
from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking_cli.exceptions import ConflictError
from openviking_cli.session.user_id import UserIdentifier


@pytest_asyncio.fixture(scope="function")
async def e2e_client(test_data_dir: Path):
    """End-to-end test client with watch support."""
    await AsyncOpenViking.reset()

    shutil.rmtree(test_data_dir, ignore_errors=True)
    test_data_dir.mkdir(parents=True, exist_ok=True)

    client = AsyncOpenViking(path=str(test_data_dir))
    await client.initialize()

    yield client

    await client.close()
    await AsyncOpenViking.reset()


@pytest_asyncio.fixture(scope="function")
async def watch_test_file(temp_dir: Path) -> Path:
    """Create a test file for watch testing."""
    file_path = temp_dir / "watch_test.md"
    file_path.write_text(
        """# Watch Test Document

## Initial Content
This is the initial content for watch testing.

## Version
Version: 1.0
Last Updated: Initial
"""
    )
    return file_path


class TestWatchE2EBasicFlow:
    """End-to-end tests for basic watch flow."""

    @pytest.mark.asyncio
    async def test_create_resource_with_watch(
        self, e2e_client: AsyncOpenViking, watch_test_file: Path
    ):
        """Test creating a resource with watch enabled."""
        client = e2e_client

        to_uri = "viking://resources/watch_e2e_test"

        result = await client.add_resource(
            path=str(watch_test_file),
            to=to_uri,
            reason="E2E watch test",
            instruction="Monitor for changes",
            watch_interval=60.0,
        )

        assert result is not None
        assert "root_uri" in result
        assert result["root_uri"] == to_uri

        status = await client.get_watch_status(to_uri)
        assert status is not None
        assert status["is_watched"] is True
        assert status["watch_interval"] == 60.0
        assert status["task_id"] is not None
        assert status["next_execution_time"] is not None

    @pytest.mark.asyncio
    async def test_query_watch_status(self, e2e_client: AsyncOpenViking, watch_test_file: Path):
        """Test querying watch status for resources."""
        client = e2e_client

        watched_uri = "viking://resources/watched_resource"
        unwatched_uri = "viking://resources/unwatched_resource"

        await client.add_resource(
            path=str(watch_test_file),
            to=watched_uri,
            watch_interval=30.0,
        )

        await client.add_resource(
            path=str(watch_test_file),
            to=unwatched_uri,
            watch_interval=0,
        )

        watched_status = await client.get_watch_status(watched_uri)
        assert watched_status is not None
        assert watched_status["is_watched"] is True
        assert watched_status["watch_interval"] == 30.0

        unwatched_status = await client.get_watch_status(unwatched_uri)
        assert unwatched_status is None

    @pytest.mark.asyncio
    async def test_update_watch_interval(self, e2e_client: AsyncOpenViking, watch_test_file: Path):
        """Test updating watch interval."""
        client = e2e_client

        to_uri = "viking://resources/update_interval_test"

        await client.add_resource(
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=30.0,
        )

        status = await client.get_watch_status(to_uri)
        assert status is not None
        assert status["watch_interval"] == 30.0
        task_id = status["task_id"]

        await client._service.resources._watch_scheduler.watch_manager.update_task(
            task_id=task_id,
            account_id=client._service.user.account_id,
            user_id=client._service.user.user_id,
            role="ROOT",
            is_active=False,
        )

        await client.add_resource(
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=120.0,
        )

        status = await client.get_watch_status(to_uri)
        assert status is not None
        assert status["watch_interval"] == 120.0
        assert status["task_id"] == task_id

    @pytest.mark.asyncio
    async def test_cancel_watch(self, e2e_client: AsyncOpenViking, watch_test_file: Path):
        """Test cancelling watch by setting interval to 0 or negative."""
        client = e2e_client

        to_uri = "viking://resources/cancel_test"

        await client.add_resource(
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=30.0,
        )

        status = await client.get_watch_status(to_uri)
        assert status is not None
        assert status["is_watched"] is True

        await client.add_resource(
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=0,
        )

        status = await client.get_watch_status(to_uri)
        assert status is None


class TestWatchE2EConflictDetection:
    """End-to-end tests for conflict detection."""

    @pytest.mark.asyncio
    async def test_conflict_when_active_watch_exists(
        self, e2e_client: AsyncOpenViking, watch_test_file: Path
    ):
        """Test that conflict is raised when trying to watch an already watched URI."""
        client = e2e_client

        to_uri = "viking://resources/conflict_test"

        await client.add_resource(
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=30.0,
        )

        with pytest.raises(ConflictError) as exc_info:
            await client.add_resource(
                path=str(watch_test_file),
                to=to_uri,
                watch_interval=60.0,
            )

        assert "already being monitored" in str(exc_info.value)
        assert to_uri in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_reactivate_inactive_watch(
        self, e2e_client: AsyncOpenViking, watch_test_file: Path
    ):
        """Test reactivating an inactive watch task."""
        client = e2e_client

        to_uri = "viking://resources/reactivate_test"

        await client.add_resource(
            path=str(watch_test_file),
            to=to_uri,
            reason="Initial reason",
            watch_interval=30.0,
        )

        status = await client.get_watch_status(to_uri)
        assert status is not None
        task_id = status["task_id"]

        await client.add_resource(
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=0,
        )

        status = await client.get_watch_status(to_uri)
        assert status is None

        await client.add_resource(
            path=str(watch_test_file),
            to=to_uri,
            reason="Reactivated reason",
            watch_interval=45.0,
        )

        status = await client.get_watch_status(to_uri)
        assert status is not None
        assert status["is_watched"] is True
        assert status["watch_interval"] == 45.0
        assert status["task_id"] == task_id


class TestWatchE2ESchedulerExecution:
    """End-to-end tests for scheduler execution."""

    @pytest.mark.asyncio
    async def test_scheduler_executes_watch_task(self, temp_dir: Path, watch_test_file: Path):
        """Test that scheduler executes watch tasks on schedule."""
        execution_count = 0

        class MockResourceProcessor:
            async def process_resource(self, **kwargs):
                nonlocal execution_count
                execution_count += 1
                return {"root_uri": kwargs.get("to", "viking://resources/test")}

        class MockSkillProcessor:
            async def process_skill(self, **kwargs):
                return {"status": "ok"}

        class MockVikingDB:
            pass

        resource_service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=object(),
            resource_processor=MockResourceProcessor(),
            skill_processor=MockSkillProcessor(),
            watch_scheduler=None,
        )
        scheduler = WatchScheduler(
            resource_service=resource_service,
            viking_fs=None,
            check_interval=0.1,
        )
        await scheduler.start()

        watch_manager = scheduler.watch_manager

        task = await watch_manager.create_task(
            path=str(watch_test_file),
            to_uri="viking://resources/scheduler_test",
            reason="Scheduler test",
            watch_interval=0.002,
        )

        assert task.is_active is True

        await asyncio.sleep(0.3)

        await scheduler.stop()

        assert execution_count >= 1

        await watch_manager.clear_all_tasks()

    @pytest.mark.asyncio
    async def test_scheduler_updates_execution_time(self, temp_dir: Path, watch_test_file: Path):
        """Test that scheduler updates execution time after task execution."""

        class MockResourceProcessor:
            async def process_resource(self, **kwargs):
                return {"root_uri": kwargs.get("to", "viking://resources/test")}

        class MockSkillProcessor:
            async def process_skill(self, **kwargs):
                return {"status": "ok"}

        class MockVikingDB:
            pass

        resource_service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=object(),
            resource_processor=MockResourceProcessor(),
            skill_processor=MockSkillProcessor(),
            watch_scheduler=None,
        )

        scheduler = WatchScheduler(
            resource_service=resource_service,
            viking_fs=None,
            check_interval=0.1,
        )
        await scheduler.start()

        watch_manager = scheduler.watch_manager

        task = await watch_manager.create_task(
            path=str(watch_test_file),
            to_uri="viking://resources/execution_time_test",
            reason="Execution time test",
            watch_interval=0.002,
        )

        assert task.last_execution_time is None

        await asyncio.sleep(0.3)

        await scheduler.stop()

        updated_task = await watch_manager.get_task(task.task_id)
        assert updated_task is not None
        assert updated_task.last_execution_time is not None
        assert updated_task.next_execution_time is not None
        assert updated_task.next_execution_time > updated_task.last_execution_time

        await watch_manager.clear_all_tasks()


class TestWatchE2EMultipleResources:
    """End-to-end tests for multiple resources."""

    @pytest.mark.asyncio
    async def test_multiple_watched_resources(
        self, e2e_client: AsyncOpenViking, watch_test_file: Path
    ):
        """Test managing multiple watched resources."""
        client = e2e_client

        uris = [
            "viking://resources/multi_test_1",
            "viking://resources/multi_test_2",
            "viking://resources/multi_test_3",
        ]

        intervals = [30.0, 60.0, 120.0]

        for uri, interval in zip(uris, intervals):
            await client.add_resource(
                path=str(watch_test_file),
                to=uri,
                watch_interval=interval,
            )

        for uri, expected_interval in zip(uris, intervals):
            status = await client.get_watch_status(uri)
            assert status is not None
            assert status["is_watched"] is True
            assert status["watch_interval"] == expected_interval

        for uri in uris:
            await client.add_resource(
                path=str(watch_test_file),
                to=uri,
                watch_interval=0,
            )

        for uri in uris:
            status = await client.get_watch_status(uri)
            assert status is None

    @pytest.mark.asyncio
    async def test_independent_watch_tasks(
        self, e2e_client: AsyncOpenViking, watch_test_file: Path
    ):
        """Test that watch tasks are independent."""
        client = e2e_client

        uri1 = "viking://resources/independent_1"
        uri2 = "viking://resources/independent_2"

        await client.add_resource(
            path=str(watch_test_file),
            to=uri1,
            watch_interval=30.0,
        )

        await client.add_resource(
            path=str(watch_test_file),
            to=uri2,
            watch_interval=60.0,
        )

        status1 = await client.get_watch_status(uri1)
        status2 = await client.get_watch_status(uri2)

        assert status1["task_id"] != status2["task_id"]

        await client.add_resource(
            path=str(watch_test_file),
            to=uri1,
            watch_interval=0,
        )

        status1_after = await client.get_watch_status(uri1)
        status2_after = await client.get_watch_status(uri2)

        assert status1_after is None
        assert status2_after is not None
        assert status2_after["is_watched"] is True


class TestWatchE2EErrorHandling:
    """End-to-end tests for error handling."""

    @pytest.mark.asyncio
    async def test_watch_without_watch_manager(self, temp_dir: Path, watch_test_file: Path):
        """Test that resource can be added without watch manager."""
        class MockResourceProcessor:
            async def process_resource(self, **kwargs):
                return {"root_uri": kwargs.get("to", "viking://resources/test")}

        class MockSkillProcessor:
            async def process_skill(self, **kwargs):
                return {"status": "ok"}

        resource_service = ResourceService(
            vikingdb=object(),
            viking_fs=object(),
            resource_processor=MockResourceProcessor(),
            skill_processor=MockSkillProcessor(),
            watch_scheduler=None,
        )

        ctx = RequestContext(
            user=UserIdentifier("test_account", "test_user", "test_agent"),
            role=Role.USER,
        )

        result = await resource_service.add_resource(
            path=str(watch_test_file),
            ctx=ctx,
            to="viking://resources/no_watch_test",
            watch_interval=30.0,
        )

        assert result is not None
        assert "root_uri" in result

    @pytest.mark.asyncio
    async def test_watch_status_nonexistent_resource(self, e2e_client: AsyncOpenViking):
        """Test getting watch status for nonexistent resource."""
        client = e2e_client

        status = await client.get_watch_status("viking://resources/nonexistent")

        assert status is None
