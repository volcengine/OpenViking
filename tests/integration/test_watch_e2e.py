# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end tests for resource watch functionality."""

from functools import partial
from pathlib import Path

import pytest
import pytest_asyncio

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking_cli.exceptions import ConflictError
from openviking_cli.session.user_id import UserIdentifier


async def get_watch_task(service, ctx: RequestContext, to_uri: str):
    watch_manager = service.resources._watch_scheduler.watch_manager
    return await watch_manager.get_task_by_uri(
        to_uri=to_uri,
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        role=str(ctx.role),
    )


@pytest_asyncio.fixture(scope="function")
async def e2e_service(service, request_context: RequestContext, monkeypatch):
    """End-to-end service with watch support."""
    monkeypatch.setattr(
        service.resources,
        "add_resource",
        partial(
            service.resources._execute_resource_ingestion,
            defer_post_processing=False,
        ),
    )
    yield service, request_context


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
    async def test_create_resource_with_watch(self, e2e_service, watch_test_file: Path):
        """Test creating a resource with watch enabled."""
        service, ctx = e2e_service

        to_uri = "viking://resources/watch_e2e_test"

        result = await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=to_uri,
            reason="E2E watch test",
            instruction="Monitor for changes",
            watch_interval=60.0,
        )

        assert result is not None
        assert "root_uri" in result
        assert result["root_uri"] == to_uri

        task = await get_watch_task(service, ctx, to_uri)
        assert task is not None
        assert task.is_active is True
        assert task.watch_interval == 60.0
        assert task.task_id is not None
        assert task.next_execution_time is not None

    @pytest.mark.asyncio
    async def test_query_watch_status(self, e2e_service, watch_test_file: Path):
        """Test querying watch status for resources."""
        service, ctx = e2e_service

        watched_uri = "viking://resources/watched_resource"
        unwatched_uri = "viking://resources/unwatched_resource"

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=watched_uri,
            watch_interval=30.0,
        )

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=unwatched_uri,
            watch_interval=0,
        )

        watched_task = await get_watch_task(service, ctx, watched_uri)
        assert watched_task is not None
        assert watched_task.is_active is True
        assert watched_task.watch_interval == 30.0

        unwatched_task = await get_watch_task(service, ctx, unwatched_uri)
        assert unwatched_task is None

    @pytest.mark.asyncio
    async def test_update_watch_interval(self, e2e_service, watch_test_file: Path):
        """Test updating watch interval."""
        service, ctx = e2e_service

        to_uri = "viking://resources/update_interval_test"

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=30.0,
        )

        task = await get_watch_task(service, ctx, to_uri)
        assert task is not None
        assert task.watch_interval == 30.0
        task_id = task.task_id

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=0,
        )

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=120.0,
        )

        task = await get_watch_task(service, ctx, to_uri)
        assert task is not None
        assert task.is_active is True
        assert task.watch_interval == 120.0
        assert task.task_id == task_id

    @pytest.mark.asyncio
    async def test_cancel_watch(self, e2e_service, watch_test_file: Path):
        """Test cancelling watch by setting interval to 0 or negative."""
        service, ctx = e2e_service

        to_uri = "viking://resources/cancel_test"

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=30.0,
        )

        task = await get_watch_task(service, ctx, to_uri)
        assert task is not None
        assert task.is_active is True

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=0,
        )

        task = await get_watch_task(service, ctx, to_uri)
        assert task is not None
        assert task.is_active is False


class TestWatchE2EConflictDetection:
    """End-to-end tests for conflict detection."""

    @pytest.mark.asyncio
    async def test_conflict_when_active_watch_exists(self, e2e_service, watch_test_file: Path):
        """Test that conflict is raised when trying to watch an already watched URI."""
        service, ctx = e2e_service

        to_uri = "viking://resources/conflict_test"

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=30.0,
        )

        with pytest.raises(ConflictError) as exc_info:
            await service.resources.add_resource(
                ctx=ctx,
                path=str(watch_test_file),
                to=to_uri,
                watch_interval=60.0,
            )

        assert "already being monitored" in str(exc_info.value)
        assert to_uri in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_reactivate_inactive_watch(self, e2e_service, watch_test_file: Path):
        """Test reactivating an inactive watch task."""
        service, ctx = e2e_service

        to_uri = "viking://resources/reactivate_test"

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=to_uri,
            reason="Initial reason",
            watch_interval=30.0,
        )

        task = await get_watch_task(service, ctx, to_uri)
        assert task is not None
        task_id = task.task_id

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=to_uri,
            watch_interval=0,
        )

        task = await get_watch_task(service, ctx, to_uri)
        assert task is not None
        assert task.is_active is False

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=to_uri,
            reason="Reactivated reason",
            watch_interval=45.0,
        )

        task = await get_watch_task(service, ctx, to_uri)
        assert task is not None
        assert task.is_active is True
        assert task.watch_interval == 45.0
        assert task.task_id == task_id


class TestWatchE2ESchedulerExecution:
    """End-to-end tests for scheduler execution."""


class TestWatchE2EMultipleResources:
    """End-to-end tests for multiple resources."""

    @pytest.mark.asyncio
    async def test_multiple_watched_resources(self, e2e_service, watch_test_file: Path):
        """Test managing multiple watched resources."""
        service, ctx = e2e_service

        uris = [
            "viking://resources/multi_test_1",
            "viking://resources/multi_test_2",
            "viking://resources/multi_test_3",
        ]

        intervals = [30.0, 60.0, 120.0]

        for uri, interval in zip(uris, intervals, strict=True):
            await service.resources.add_resource(
                ctx=ctx,
                path=str(watch_test_file),
                to=uri,
                watch_interval=interval,
            )

        for uri, expected_interval in zip(uris, intervals, strict=True):
            task = await get_watch_task(service, ctx, uri)
            assert task is not None
            assert task.is_active is True
            assert task.watch_interval == expected_interval

        for uri in uris:
            await service.resources.add_resource(
                ctx=ctx,
                path=str(watch_test_file),
                to=uri,
                watch_interval=0,
            )

        for uri in uris:
            task = await get_watch_task(service, ctx, uri)
            assert task is not None
            assert task.is_active is False

    @pytest.mark.asyncio
    async def test_independent_watch_tasks(self, e2e_service, watch_test_file: Path):
        """Test that watch tasks are independent."""
        service, ctx = e2e_service

        uri1 = "viking://resources/independent_1"
        uri2 = "viking://resources/independent_2"

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=uri1,
            watch_interval=30.0,
        )

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=uri2,
            watch_interval=60.0,
        )

        task1 = await get_watch_task(service, ctx, uri1)
        task2 = await get_watch_task(service, ctx, uri2)
        assert task1 is not None
        assert task2 is not None
        assert task1.task_id != task2.task_id

        await service.resources.add_resource(
            ctx=ctx,
            path=str(watch_test_file),
            to=uri1,
            watch_interval=0,
        )

        task1_after = await get_watch_task(service, ctx, uri1)
        task2_after = await get_watch_task(service, ctx, uri2)
        assert task1_after is not None
        assert task1_after.is_active is False
        assert task2_after is not None
        assert task2_after.is_active is True


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
            user=UserIdentifier("test_account", "test_user"),
            role=Role.USER,
        )

        result = await resource_service._execute_resource_ingestion(
            path=str(watch_test_file),
            ctx=ctx,
            defer_post_processing=False,
            to="viking://resources/no_watch_test",
            watch_interval=30.0,
        )

        assert result is not None
        assert "root_uri" in result

    @pytest.mark.asyncio
    async def test_watch_task_nonexistent_resource(self, e2e_service):
        service, ctx = e2e_service
        task = await get_watch_task(service, ctx, "viking://resources/nonexistent")
        assert task is None
