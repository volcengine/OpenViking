# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ResourceService.get_watch_status functionality."""

from datetime import datetime, timedelta
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from openviking.resource.watch_manager import WatchManager
from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking_cli.session.user_id import UserIdentifier


async def get_task_by_uri(service: ResourceService, to_uri: str, ctx: RequestContext):
    return await service._watch_scheduler.watch_manager.get_task_by_uri(
        to_uri=to_uri,
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        role=ctx.role.value,
    )


class MockResourceProcessor:
    """Mock ResourceProcessor for testing."""

    async def process_resource(self, **kwargs):
        return {"root_uri": kwargs.get("to", "viking://resources/test")}


class MockSkillProcessor:
    """Mock SkillProcessor for testing."""

    async def process_skill(self, **kwargs):
        return {"status": "ok"}


class MockVikingFS:
    """Mock VikingFS for testing."""

    pass


class MockVikingDB:
    """Mock VikingDBManager for testing."""

    pass


@pytest_asyncio.fixture
async def watch_manager() -> AsyncGenerator[WatchManager, None]:
    """Create WatchManager instance without VikingFS for testing."""
    manager = WatchManager(viking_fs=None)
    await manager.initialize()
    yield manager
    await manager.clear_all_tasks()


@pytest_asyncio.fixture
async def resource_service(watch_manager: WatchManager) -> AsyncGenerator[ResourceService, None]:
    """Create ResourceService instance with watch support."""
    from unittest.mock import MagicMock

    scheduler = MagicMock()
    scheduler.watch_manager = watch_manager
    service = ResourceService(
        vikingdb=MockVikingDB(),
        viking_fs=MockVikingFS(),
        resource_processor=MockResourceProcessor(),
        skill_processor=MockSkillProcessor(),
        watch_scheduler=scheduler,
    )
    yield service


@pytest_asyncio.fixture
def request_context() -> RequestContext:
    """Create request context for testing."""
    return RequestContext(
        user=UserIdentifier("test_account", "test_user", "test_agent"),
        role=Role.USER,
    )


class TestGetWatchStatus:
    """Tests for get_watch_status method."""

    @pytest.mark.asyncio
    async def test_get_watch_status_with_active_task(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test getting watch status for a resource with an active watch task."""
        to_uri = "viking://resources/watched_resource"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            reason="Test monitoring",
            instruction="Monitor for changes",
            watch_interval=30.0,
        )

        status = await resource_service.get_watch_status(to_uri, request_context)

        assert status is not None
        assert status["is_watched"] is True
        assert status["watch_interval"] == 30.0
        assert status["task_id"] is not None
        assert status["next_execution_time"] is not None

        next_exec_time = datetime.fromisoformat(status["next_execution_time"])
        assert next_exec_time > datetime.now()

        assert status["last_execution_time"] is None

    @pytest.mark.asyncio
    async def test_get_watch_status_with_execution_history(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test getting watch status for a task with execution history."""
        to_uri = "viking://resources/executed_resource"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=60.0,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None

        await resource_service._watch_scheduler.watch_manager.update_execution_time(task.task_id)

        status = await resource_service.get_watch_status(to_uri, request_context)

        assert status is not None
        assert status["is_watched"] is True
        assert status["last_execution_time"] is not None
        assert status["next_execution_time"] is not None

        last_exec_time = datetime.fromisoformat(status["last_execution_time"])
        next_exec_time = datetime.fromisoformat(status["next_execution_time"])

        assert next_exec_time > last_exec_time
        expected_next = last_exec_time + timedelta(minutes=60.0)
        time_diff = abs((next_exec_time - expected_next).total_seconds())
        assert time_diff < 2

    @pytest.mark.asyncio
    async def test_get_watch_status_for_non_watched_resource(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test getting watch status for a resource without watch task."""
        to_uri = "viking://resources/not_watched"

        status = await resource_service.get_watch_status(to_uri, request_context)

        assert status is None

    @pytest.mark.asyncio
    async def test_get_watch_status_for_inactive_task(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test getting watch status for a resource with inactive watch task."""
        to_uri = "viking://resources/inactive_task"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=30.0,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.is_active is True

        await resource_service._watch_scheduler.watch_manager.update_task(
            task_id=task.task_id,
            account_id=request_context.account_id,
            user_id=request_context.user.user_id,
            role=request_context.role.value,
            is_active=False,
        )

        status = await resource_service.get_watch_status(to_uri, request_context)

        assert status is None

    @pytest.mark.asyncio
    async def test_get_watch_status_without_watch_manager(self):
        """Test getting watch status when watch_manager is None."""
        service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=MockVikingFS(),
            resource_processor=MockResourceProcessor(),
            skill_processor=MockSkillProcessor(),
            watch_scheduler=None,
        )

        ctx = RequestContext(
            user=UserIdentifier("test_account", "test_user", "test_agent"),
            role=Role.USER,
        )

        status = await service.get_watch_status("viking://resources/any", ctx)

        assert status is None

    @pytest.mark.asyncio
    async def test_get_watch_status_returns_correct_fields(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test that get_watch_status returns all required fields."""
        to_uri = "viking://resources/fields_test"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=45.0,
        )

        status = await resource_service.get_watch_status(to_uri, request_context)

        assert status is not None
        assert "is_watched" in status
        assert "watch_interval" in status
        assert "next_execution_time" in status
        assert "last_execution_time" in status
        assert "task_id" in status

        assert isinstance(status["is_watched"], bool)
        assert isinstance(status["watch_interval"], float)
        assert status["next_execution_time"] is None or isinstance(
            status["next_execution_time"], str
        )
        assert status["last_execution_time"] is None or isinstance(
            status["last_execution_time"], str
        )
        assert isinstance(status["task_id"], str)

    @pytest.mark.asyncio
    async def test_get_watch_status_multiple_resources(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test getting watch status for multiple resources."""
        to_uri_1 = "viking://resources/resource_1"
        to_uri_2 = "viking://resources/resource_2"
        to_uri_3 = "viking://resources/resource_3"

        await resource_service.add_resource(
            path="/test/path1",
            ctx=request_context,
            to=to_uri_1,
            watch_interval=30.0,
        )

        await resource_service.add_resource(
            path="/test/path2",
            ctx=request_context,
            to=to_uri_2,
            watch_interval=60.0,
        )

        status_1 = await resource_service.get_watch_status(to_uri_1, request_context)
        status_2 = await resource_service.get_watch_status(to_uri_2, request_context)
        status_3 = await resource_service.get_watch_status(to_uri_3, request_context)

        assert status_1 is not None
        assert status_1["is_watched"] is True
        assert status_1["watch_interval"] == 30.0

        assert status_2 is not None
        assert status_2["is_watched"] is True
        assert status_2["watch_interval"] == 60.0

        assert status_3 is None

        assert status_1["task_id"] != status_2["task_id"]
