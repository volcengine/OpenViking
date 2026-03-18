# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for ResourceService watch functionality."""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openviking.resource.watch_manager import WatchManager
from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking_cli.exceptions import ConflictError
from openviking_cli.exceptions import InvalidArgumentError
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


class TestWatchTaskCreation:
    """Tests for watch task creation in add_resource."""

    @pytest.mark.asyncio
    async def test_create_watch_task_with_positive_interval(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test creating a watch task when watch_interval > 0."""
        to_uri = "viking://resources/test_resource"

        result = await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            reason="Test monitoring",
            instruction="Monitor for changes",
            watch_interval=30.0,
        )

        assert result is not None
        assert "root_uri" in result

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.path == "/test/path"
        assert task.to_uri == to_uri
        assert task.reason == "Test monitoring"
        assert task.instruction == "Monitor for changes"
        assert task.watch_interval == 30.0
        assert task.is_active is True

    @pytest.mark.asyncio
    async def test_watch_interval_requires_to_when_watch_enabled(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        with pytest.raises(InvalidArgumentError, match="requires 'to'"):
            await resource_service.add_resource(
                path="/test/path",
                ctx=request_context,
                to=None,
                watch_interval=30.0,
            )

    @pytest.mark.asyncio
    async def test_watch_task_aligns_processor_params(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        to_uri = "viking://resources/align_processor_params"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=30.0,
            build_index=False,
            summarize=True,
            custom_option="x",
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.build_index is False
        assert task.summarize is True
        assert task.processor_kwargs.get("custom_option") == "x"

    @pytest.mark.asyncio
    async def test_create_watch_task_with_default_interval(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test creating a watch task with default interval."""
        to_uri = "viking://resources/default_interval"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=60.0,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.watch_interval == 60.0

    @pytest.mark.asyncio
    async def test_no_watch_task_created_with_zero_interval(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test that no watch task is created when watch_interval is 0."""
        to_uri = "viking://resources/no_watch"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=0,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is None

    @pytest.mark.asyncio
    async def test_no_watch_task_created_with_negative_interval(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test that no watch task is created when watch_interval is negative."""
        to_uri = "viking://resources/negative_watch"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=-10,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is None


class TestWatchTaskConflict:
    """Tests for watch task conflict detection."""

    @pytest.mark.asyncio
    async def test_conflict_when_active_task_exists(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test that ConflictError is raised when an active task already exists."""
        to_uri = "viking://resources/conflict_test"

        await resource_service.add_resource(
            path="/test/path1",
            ctx=request_context,
            to=to_uri,
            watch_interval=30.0,
        )

        with pytest.raises(ConflictError) as exc_info:
            await resource_service.add_resource(
                path="/test/path2",
                ctx=request_context,
                to=to_uri,
                watch_interval=45.0,
            )

        assert "already being monitored" in str(exc_info.value)
        assert to_uri in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_conflict_when_task_exists_but_hidden_by_permission(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        to_uri = "viking://resources/cross_user_conflict"
        other_user_ctx = RequestContext(
            user=UserIdentifier("test_account", "other_user", "other_agent"),
            role=Role.USER,
        )

        await resource_service.add_resource(
            path="/test/path1",
            ctx=request_context,
            to=to_uri,
            watch_interval=30.0,
        )

        hidden_task = await get_task_by_uri(resource_service, to_uri, other_user_ctx)
        assert hidden_task is None

        with pytest.raises(ConflictError) as exc_info:
            await resource_service.add_resource(
                path="/test/path2",
                ctx=other_user_ctx,
                to=to_uri,
                watch_interval=45.0,
            )

        assert "already used by another task" in str(exc_info.value)
        assert to_uri in str(exc_info.value)

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None

    @pytest.mark.asyncio
    async def test_reactivate_inactive_task(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test reactivating an inactive task."""
        to_uri = "viking://resources/reactivate_test"

        await resource_service.add_resource(
            path="/test/path1",
            ctx=request_context,
            to=to_uri,
            watch_interval=30.0,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        task_id = task.task_id

        await resource_service._watch_scheduler.watch_manager.update_task(
            task_id=task_id,
            account_id=request_context.account_id,
            user_id=request_context.user.user_id,
            role=request_context.role.value,
            is_active=False,
        )

        await resource_service.add_resource(
            path="/test/path2",
            ctx=request_context,
            to=to_uri,
            reason="Updated reason",
            watch_interval=45.0,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.task_id == task_id
        assert task.path == "/test/path2"
        assert task.reason == "Updated reason"
        assert task.watch_interval == 45.0
        assert task.is_active is True


class TestWatchTaskCancellation:
    """Tests for watch task cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_watch_task_with_zero_interval(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test cancelling a watch task by setting watch_interval to 0."""
        to_uri = "viking://resources/cancel_test"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=30.0,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.is_active is True

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=0,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.is_active is False

    @pytest.mark.asyncio
    async def test_cancel_watch_task_with_negative_interval(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test cancelling a watch task by setting watch_interval to negative."""
        to_uri = "viking://resources/cancel_negative"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=30.0,
        )

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=-5,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.is_active is False

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task_no_error(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test that cancelling a nonexistent task does not raise an error."""
        to_uri = "viking://resources/nonexistent"

        result = await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=0,
        )

        assert result is not None


class TestWatchTaskUpdate:
    """Tests for watch task update."""

    @pytest.mark.asyncio
    async def test_update_watch_task_parameters(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Test updating watch task parameters."""
        to_uri = "viking://resources/update_test"

        await resource_service.add_resource(
            path="/test/path1",
            ctx=request_context,
            to=to_uri,
            reason="Original reason",
            instruction="Original instruction",
            watch_interval=30.0,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        original_task_id = task.task_id

        await resource_service._watch_scheduler.watch_manager.update_task(
            task_id=task.task_id,
            account_id=request_context.account_id,
            user_id=request_context.user.user_id,
            role=request_context.role.value,
            is_active=False,
        )

        await resource_service.add_resource(
            path="/test/path2",
            ctx=request_context,
            to=to_uri,
            reason="Updated reason",
            instruction="Updated instruction",
            watch_interval=60.0,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.task_id == original_task_id
        assert task.path == "/test/path2"
        assert task.reason == "Updated reason"
        assert task.instruction == "Updated instruction"
        assert task.watch_interval == 60.0
        assert task.is_active is True


class TestResourceProcessingIndependence:
    """Tests that resource processing is independent of watch task management."""

    @pytest.mark.asyncio
    async def test_resource_added_even_if_watch_fails(self, request_context: RequestContext):
        """Test that resource is added even if watch task creation fails."""
        failing_watch_manager = MagicMock(spec=WatchManager)
        failing_watch_manager.get_task_by_uri = AsyncMock(side_effect=Exception("DB error"))
        scheduler = MagicMock()
        scheduler.watch_manager = failing_watch_manager

        service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=MockVikingFS(),
            resource_processor=MockResourceProcessor(),
            skill_processor=MockSkillProcessor(),
            watch_scheduler=scheduler,
        )

        result = await service.add_resource(
            path="/test/path",
            ctx=request_context,
            to="viking://resources/test",
            watch_interval=30.0,
        )

        assert result is not None
        assert "root_uri" in result

    @pytest.mark.asyncio
    async def test_resource_added_without_watch_manager(self, request_context: RequestContext):
        """Test that resource is added when watch_manager is None."""
        service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=MockVikingFS(),
            resource_processor=MockResourceProcessor(),
            skill_processor=MockSkillProcessor(),
            watch_scheduler=None,
        )

        result = await service.add_resource(
            path="/test/path",
            ctx=request_context,
            to="viking://resources/test",
            watch_interval=30.0,
        )

        assert result is not None
        assert "root_uri" in result
