# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for watch task recovery after service restart."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_scheduler import WatchScheduler
from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS


class MockVikingFS:
    """Mock VikingFS for testing."""

    def __init__(self, root_path: str):
        self.agfs = MockLocalAGFS(root_path=root_path)

    async def read_file(self, uri: str, ctx=None) -> str:
        """Read file from storage."""
        path = self._uri_to_path(uri)
        content = self.agfs.read(path)
        if isinstance(content, bytes):
            return content.decode("utf-8")
        return content

    async def write_file(self, uri: str, content: str, ctx=None) -> None:
        """Write file to storage."""
        path = self._uri_to_path(uri)
        self.agfs.write(path, content.encode("utf-8"))

    def _uri_to_path(self, uri: str) -> str:
        """Convert URI to path."""
        if uri.startswith("viking://"):
            return uri.replace("viking://", "/local/default/")
        return uri


class MockResourceProcessor:
    """Mock ResourceProcessor for testing."""

    def __init__(self):
        self.call_count = 0
        self.processed_paths = []

    async def process_resource(self, **kwargs):
        self.call_count += 1
        self.processed_paths.append(kwargs.get("path"))
        return {"root_uri": kwargs.get("to", "viking://resources/test")}


class MockSkillProcessor:
    """Mock SkillProcessor for testing."""

    async def process_skill(self, **kwargs):
        return {"status": "ok"}


class MockVikingDB:
    """Mock VikingDBManager for testing."""

    pass


@pytest_asyncio.fixture
async def temp_storage(tmp_path: Path) -> AsyncGenerator[Path, None]:
    """Create temporary storage directory."""
    storage_dir = tmp_path / "watch_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    yield storage_dir


@pytest_asyncio.fixture
async def mock_viking_fs(temp_storage: Path) -> MockVikingFS:
    """Create mock VikingFS instance."""
    return MockVikingFS(root_path=str(temp_storage))


@pytest_asyncio.fixture
async def request_context() -> RequestContext:
    """Create request context for testing."""
    return RequestContext(
        user=UserIdentifier.the_default_user(),
        role=Role.ROOT,
    )


class TestServiceRestartRecovery:
    """Tests for watch task recovery after service restart."""

    @pytest.mark.asyncio
    async def test_tasks_persisted_and_reloaded_after_restart(
        self, mock_viking_fs: MockVikingFS, temp_storage: Path
    ):
        """Test that tasks are persisted and correctly reloaded after service restart."""
        manager1 = WatchManager(viking_fs=mock_viking_fs)
        await manager1.initialize()

        task1 = await manager1.create_task(
            path="/test/path1",
            to_uri="viking://resources/test1",
            reason="Task 1",
            watch_interval=30.0,
        )
        task2 = await manager1.create_task(
            path="/test/path2",
            to_uri="viking://resources/test2",
            reason="Task 2",
            watch_interval=60.0,
        )

        task1_id = task1.task_id
        task2_id = task2.task_id

        manager2 = WatchManager(viking_fs=mock_viking_fs)
        await manager2.initialize()

        loaded_task1 = await manager2.get_task(task1_id)
        loaded_task2 = await manager2.get_task(task2_id)

        assert loaded_task1 is not None
        assert loaded_task1.path == "/test/path1"
        assert loaded_task1.to_uri == "viking://resources/test1"
        assert loaded_task1.reason == "Task 1"
        assert loaded_task1.watch_interval == 30.0
        assert loaded_task1.is_active is True

        assert loaded_task2 is not None
        assert loaded_task2.path == "/test/path2"
        assert loaded_task2.to_uri == "viking://resources/test2"
        assert loaded_task2.watch_interval == 60.0

    @pytest.mark.asyncio
    async def test_tasks_recovered_from_backup_when_primary_missing(
        self, mock_viking_fs: MockVikingFS, temp_storage: Path
    ):
        """Test that tasks can be recovered from backup storage when primary is missing."""
        task_data = {
            "task_id": "backup-task-id",
            "path": "/test/backup",
            "to_uri": "viking://resources/backup",
            "reason": "Backup task",
            "instruction": "",
            "watch_interval": 60.0,
            "created_at": datetime.now().isoformat(),
            "last_execution_time": None,
            "next_execution_time": None,
            "is_active": True,
        }

        storage_uri = WatchManager.STORAGE_URI
        storage_path = mock_viking_fs._uri_to_path(storage_uri)
        assert mock_viking_fs.agfs.exists(storage_path) is False

        bak_uri = WatchManager.STORAGE_BAK_URI
        bak_path = mock_viking_fs._uri_to_path(bak_uri)
        data = {"tasks": [task_data], "updated_at": datetime.now().isoformat()}
        mock_viking_fs.agfs.write(bak_path, json.dumps(data).encode("utf-8"))

        manager = WatchManager(viking_fs=mock_viking_fs)
        await manager.initialize()

        loaded_task = await manager.get_task("backup-task-id")
        assert loaded_task is not None
        assert loaded_task.path == "/test/backup"
        assert loaded_task.to_uri == "viking://resources/backup"

        assert mock_viking_fs.agfs.exists(storage_path) is True

    @pytest.mark.asyncio
    async def test_expired_tasks_executed_on_startup(
        self, mock_viking_fs: MockVikingFS, temp_storage: Path, request_context: RequestContext
    ):
        """Test that tasks with next_execution_time in the past are executed on startup."""
        manager = WatchManager(viking_fs=mock_viking_fs)
        await manager.initialize()

        past_time = datetime.now() - timedelta(minutes=10)
        task_data = {
            "task_id": "expired-task-id",
            "path": "/test/expired",
            "to_uri": "viking://resources/expired",
            "reason": "Expired task",
            "instruction": "",
            "watch_interval": 60.0,
            "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
            "last_execution_time": (datetime.now() - timedelta(hours=1)).isoformat(),
            "next_execution_time": past_time.isoformat(),
            "is_active": True,
        }

        storage_uri = WatchManager.STORAGE_URI
        path = mock_viking_fs._uri_to_path(storage_uri)
        data = {"tasks": [task_data], "updated_at": datetime.now().isoformat()}
        mock_viking_fs.agfs.write(path, json.dumps(data).encode("utf-8"))

        manager2 = WatchManager(viking_fs=mock_viking_fs)
        await manager2.initialize()

        due_tasks = await manager2.get_due_tasks()

        assert len(due_tasks) == 1
        assert due_tasks[0].task_id == "expired-task-id"

    @pytest.mark.asyncio
    async def test_future_tasks_not_executed_on_startup(
        self, mock_viking_fs: MockVikingFS, temp_storage: Path
    ):
        """Test that tasks with future next_execution_time are not executed immediately."""
        manager = WatchManager(viking_fs=mock_viking_fs)
        await manager.initialize()

        future_time = datetime.now() + timedelta(hours=1)
        task_data = {
            "task_id": "future-task-id",
            "path": "/test/future",
            "to_uri": "viking://resources/future",
            "reason": "Future task",
            "instruction": "",
            "watch_interval": 60.0,
            "created_at": datetime.now().isoformat(),
            "last_execution_time": None,
            "next_execution_time": future_time.isoformat(),
            "is_active": True,
        }

        storage_uri = WatchManager.STORAGE_URI
        path = mock_viking_fs._uri_to_path(storage_uri)
        data = {"tasks": [task_data], "updated_at": datetime.now().isoformat()}
        mock_viking_fs.agfs.write(path, json.dumps(data).encode("utf-8"))

        manager2 = WatchManager(viking_fs=mock_viking_fs)
        await manager2.initialize()

        due_tasks = await manager2.get_due_tasks()

        assert len(due_tasks) == 0

    @pytest.mark.asyncio
    async def test_inactive_tasks_not_executed_after_restart(
        self, mock_viking_fs: MockVikingFS, temp_storage: Path
    ):
        """Test that inactive tasks are not executed after restart."""
        manager = WatchManager(viking_fs=mock_viking_fs)
        await manager.initialize()

        past_time = datetime.now() - timedelta(minutes=10)
        task_data = {
            "task_id": "inactive-task-id",
            "path": "/test/inactive",
            "to_uri": "viking://resources/inactive",
            "reason": "Inactive task",
            "instruction": "",
            "watch_interval": 60.0,
            "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
            "last_execution_time": None,
            "next_execution_time": past_time.isoformat(),
            "is_active": False,
        }

        storage_uri = WatchManager.STORAGE_URI
        path = mock_viking_fs._uri_to_path(storage_uri)
        data = {"tasks": [task_data], "updated_at": datetime.now().isoformat()}
        mock_viking_fs.agfs.write(path, json.dumps(data).encode("utf-8"))

        manager2 = WatchManager(viking_fs=mock_viking_fs)
        await manager2.initialize()

        due_tasks = await manager2.get_due_tasks()

        assert len(due_tasks) == 0


class TestResourceExistenceCheck:
    """Tests for resource existence checking during task execution."""

    @pytest.mark.asyncio
    async def test_task_deactivated_when_resource_deleted(
        self, temp_storage: Path, request_context: RequestContext
    ):
        """Test that task is deactivated when resource no longer exists."""
        resource_processor = MockResourceProcessor()

        resource_service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=MockVikingFS(root_path=str(temp_storage)),
            resource_processor=resource_processor,
            skill_processor=MockSkillProcessor(),
            watch_scheduler=None,
        )

        scheduler = WatchScheduler(
            resource_service=resource_service,
            viking_fs=None,
        )
        await scheduler.start()

        watch_manager = scheduler.watch_manager

        task = await watch_manager.create_task(
            path="/nonexistent/path/to/resource",
            to_uri="viking://resources/deleted",
            reason="Test deleted resource",
            watch_interval=30.0,
        )

        assert task.is_active is True

        await scheduler._execute_task(task)

        updated_task = await watch_manager.get_task(task.task_id)
        assert updated_task is not None
        assert updated_task.is_active is False

    @pytest.mark.asyncio
    async def test_task_continues_when_resource_exists(
        self, temp_storage: Path, request_context: RequestContext
    ):
        """Test that task continues normally when resource exists."""
        test_file = temp_storage / "test_resource.txt"
        test_file.write_text("test content")

        resource_processor = MockResourceProcessor()

        resource_service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=MockVikingFS(root_path=str(temp_storage)),
            resource_processor=resource_processor,
            skill_processor=MockSkillProcessor(),
            watch_scheduler=None,
        )

        scheduler = WatchScheduler(
            resource_service=resource_service,
            viking_fs=None,
        )
        await scheduler.start()

        watch_manager = scheduler.watch_manager

        task = await watch_manager.create_task(
            path=str(test_file),
            to_uri="viking://resources/existing",
            reason="Test existing resource",
            watch_interval=30.0,
        )

        await scheduler._execute_task(task)

        updated_task = await watch_manager.get_task(task.task_id)
        assert updated_task is not None
        assert updated_task.is_active is True
        assert updated_task.last_execution_time is not None

    @pytest.mark.asyncio
    async def test_url_resources_always_considered_existing(
        self, temp_storage: Path, request_context: RequestContext
    ):
        """Test that URL resources are always considered existing."""
        resource_processor = MockResourceProcessor()

        resource_service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=MockVikingFS(root_path=str(temp_storage)),
            resource_processor=resource_processor,
            skill_processor=MockSkillProcessor(),
            watch_scheduler=None,
        )

        scheduler = WatchScheduler(
            resource_service=resource_service,
            viking_fs=None,
        )
        await scheduler.start()

        watch_manager = scheduler.watch_manager

        task = await watch_manager.create_task(
            path="https://example.com/resource",
            to_uri="viking://resources/url",
            reason="Test URL resource",
            watch_interval=30.0,
        )

        await scheduler._execute_task(task)

        updated_task = await watch_manager.get_task(task.task_id)
        assert updated_task is not None
        assert updated_task.is_active is True
        assert resource_processor.call_count == 1


class TestSchedulerIntegration:
    """Integration tests for WatchScheduler with WatchManager."""

    @pytest.mark.asyncio
    async def test_scheduler_processes_due_tasks_after_restart(
        self, temp_storage: Path, request_context: RequestContext
    ):
        """Test that scheduler processes due tasks after service restart."""
        test_file = temp_storage / "test.txt"
        test_file.write_text("test content")

        resource_processor = MockResourceProcessor()

        resource_service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=MockVikingFS(root_path=str(temp_storage)),
            resource_processor=resource_processor,
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

        await watch_manager.create_task(
            path=str(test_file),
            to_uri="viking://resources/test",
            reason="Test task",
            watch_interval=0.001,
        )

        await asyncio.sleep(0.2)

        await scheduler.stop()

        assert resource_processor.call_count >= 1

    @pytest.mark.asyncio
    async def test_scheduler_handles_multiple_tasks_after_restart(
        self, temp_storage: Path, request_context: RequestContext
    ):
        """Test that scheduler handles multiple tasks after restart."""
        test_file1 = temp_storage / "test1.txt"
        test_file2 = temp_storage / "test2.txt"
        test_file1.write_text("test content 1")
        test_file2.write_text("test content 2")

        resource_processor = MockResourceProcessor()

        resource_service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=MockVikingFS(root_path=str(temp_storage)),
            resource_processor=resource_processor,
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

        await watch_manager.create_task(
            path=str(test_file1),
            to_uri="viking://resources/test1",
            reason="Task 1",
            watch_interval=0.001,
        )
        await watch_manager.create_task(
            path=str(test_file2),
            to_uri="viking://resources/test2",
            reason="Task 2",
            watch_interval=0.001,
        )

        await asyncio.sleep(0.3)

        await scheduler.stop()

        assert resource_processor.call_count >= 2

    @pytest.mark.asyncio
    async def test_scheduler_skips_inactive_tasks_after_restart(
        self, temp_storage: Path, request_context: RequestContext
    ):
        """Test that scheduler skips inactive tasks after restart."""
        test_file = temp_storage / "test.txt"
        test_file.write_text("test content")

        resource_processor = MockResourceProcessor()

        resource_service = ResourceService(
            vikingdb=MockVikingDB(),
            viking_fs=MockVikingFS(root_path=str(temp_storage)),
            resource_processor=resource_processor,
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
            path=str(test_file),
            to_uri="viking://resources/test",
            reason="Inactive task",
            watch_interval=0.001,
        )

        await watch_manager.update_task(
            task_id=task.task_id,
            account_id=task.account_id,
            user_id=task.user_id,
            role="ROOT",
            is_active=False,
        )

        await asyncio.sleep(0.2)

        await scheduler.stop()

        assert resource_processor.call_count == 0


class TestTaskExecutionTimeRecovery:
    """Tests for task execution time handling after restart."""

    @pytest.mark.asyncio
    async def test_execution_times_preserved_after_restart(
        self, mock_viking_fs: MockVikingFS, temp_storage: Path
    ):
        """Test that execution times are preserved after restart."""
        manager1 = WatchManager(viking_fs=mock_viking_fs)
        await manager1.initialize()

        task = await manager1.create_task(
            path="/test/path",
            to_uri="viking://resources/test",
            watch_interval=30.0,
        )

        await manager1.update_execution_time(task.task_id)

        task_after_exec = await manager1.get_task(task.task_id)
        assert task_after_exec is not None
        original_last_exec = task_after_exec.last_execution_time
        original_next_exec = task_after_exec.next_execution_time

        manager2 = WatchManager(viking_fs=mock_viking_fs)
        await manager2.initialize()

        loaded_task = await manager2.get_task(task.task_id)
        assert loaded_task is not None
        assert loaded_task.last_execution_time is not None
        assert abs((loaded_task.last_execution_time - original_last_exec).total_seconds()) < 1
        assert loaded_task.next_execution_time is not None
        assert abs((loaded_task.next_execution_time - original_next_exec).total_seconds()) < 1

    @pytest.mark.asyncio
    async def test_next_execution_time_calculated_correctly_after_restart(
        self, mock_viking_fs: MockVikingFS, temp_storage: Path
    ):
        """Test that next execution time is calculated correctly for loaded tasks."""
        manager = WatchManager(viking_fs=mock_viking_fs)
        await manager.initialize()

        last_exec = datetime.now() - timedelta(minutes=15)
        task_data = {
            "task_id": "test-task-id",
            "path": "/test/path",
            "to_uri": "viking://resources/test",
            "reason": "Test",
            "instruction": "",
            "watch_interval": 30.0,
            "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
            "last_execution_time": last_exec.isoformat(),
            "next_execution_time": (last_exec + timedelta(minutes=30)).isoformat(),
            "is_active": True,
        }

        storage_uri = WatchManager.STORAGE_URI
        path = mock_viking_fs._uri_to_path(storage_uri)
        data = {"tasks": [task_data], "updated_at": datetime.now().isoformat()}
        mock_viking_fs.agfs.write(path, json.dumps(data).encode("utf-8"))

        manager2 = WatchManager(viking_fs=mock_viking_fs)
        await manager2.initialize()

        loaded_task = await manager2.get_task("test-task-id")
        assert loaded_task is not None
        assert loaded_task.watch_interval == 30.0
        assert loaded_task.last_execution_time is not None

        expected_next = loaded_task.last_execution_time + timedelta(minutes=30)
        assert abs((loaded_task.next_execution_time - expected_next).total_seconds()) < 1
