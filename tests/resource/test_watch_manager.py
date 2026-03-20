# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for WatchManager."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from openviking.resource.watch_manager import PermissionDeniedError, WatchManager, WatchTask
from openviking_cli.exceptions import ConflictError
from tests.utils.mock_agfs import MockLocalAGFS

TEST_ACCOUNT_ID = "default"
TEST_USER_ID = "default"
TEST_AGENT_ID = "default"
OTHER_AGENT_ID = "other-agent"
TEST_ROLE = "ROOT"


class MockVikingFS:
    """Mock VikingFS for testing."""

    def __init__(self, root_path: str):
        self.agfs = MockLocalAGFS(root_path=root_path)
        self._storage_data = {}

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
async def watch_manager(mock_viking_fs: MockVikingFS) -> AsyncGenerator[WatchManager, None]:
    """Create WatchManager instance with mock VikingFS."""
    manager = WatchManager(viking_fs=mock_viking_fs)
    await manager.initialize()
    yield manager
    await manager.clear_all_tasks()


@pytest_asyncio.fixture
async def watch_manager_no_fs() -> AsyncGenerator[WatchManager, None]:
    """Create WatchManager instance without VikingFS."""
    manager = WatchManager(viking_fs=None)
    await manager.initialize()
    yield manager
    await manager.clear_all_tasks()


class TestWatchTask:
    """Tests for WatchTask data model."""

    def test_create_task_with_defaults(self):
        """Test creating a task with default values."""
        task = WatchTask(path="/test/path")

        assert task.path == "/test/path"
        assert task.task_id is not None
        assert task.to_uri is None
        assert task.parent_uri is None
        assert task.reason == ""
        assert task.instruction == ""
        assert task.watch_interval == 60.0
        assert task.is_active is True
        assert task.created_at is not None
        assert task.last_execution_time is None
        assert task.next_execution_time is None

    def test_create_task_with_all_fields(self):
        """Test creating a task with all fields specified."""
        now = datetime.now()
        task = WatchTask(
            task_id="test-task-id",
            path="/test/path",
            to_uri="viking://resources/test",
            parent_uri="viking://resources",
            reason="Test reason",
            instruction="Test instruction",
            watch_interval=30.0,
            created_at=now,
            last_execution_time=now,
            next_execution_time=now + timedelta(minutes=30),
            is_active=False,
        )

        assert task.task_id == "test-task-id"
        assert task.path == "/test/path"
        assert task.to_uri == "viking://resources/test"
        assert task.parent_uri == "viking://resources"
        assert task.reason == "Test reason"
        assert task.instruction == "Test instruction"
        assert task.watch_interval == 30.0
        assert task.is_active is False
        assert task.created_at == now
        assert task.last_execution_time == now

    def test_to_dict(self):
        """Test converting task to dictionary."""
        now = datetime.now()
        task = WatchTask(
            task_id="test-id",
            path="/test/path",
            to_uri="viking://test",
            created_at=now,
        )

        data = task.to_dict()

        assert data["task_id"] == "test-id"
        assert data["path"] == "/test/path"
        assert data["to_uri"] == "viking://test"
        assert data["created_at"] == now.isoformat()
        assert data["is_active"] is True

    def test_from_dict(self):
        """Test creating task from dictionary."""
        now = datetime.now()
        data = {
            "task_id": "test-id",
            "path": "/test/path",
            "to_uri": "viking://test",
            "parent_uri": "viking://parent",
            "reason": "Test",
            "instruction": "Instruction",
            "watch_interval": 45.0,
            "created_at": now.isoformat(),
            "last_execution_time": now.isoformat(),
            "next_execution_time": (now + timedelta(minutes=45)).isoformat(),
            "is_active": False,
        }

        task = WatchTask.from_dict(data)

        assert task.task_id == "test-id"
        assert task.path == "/test/path"
        assert task.to_uri == "viking://test"
        assert task.watch_interval == 45.0
        assert task.is_active is False
        assert task.created_at == now
        assert task.last_execution_time == now

    def test_calculate_next_execution_time(self):
        """Test calculating next execution time."""
        now = datetime.now()
        task = WatchTask(
            path="/test",
            watch_interval=30.0,
            created_at=now,
        )

        next_time = task.calculate_next_execution_time()

        expected = now + timedelta(minutes=30.0)
        assert abs((next_time - expected).total_seconds()) < 1

    def test_calculate_next_execution_time_with_last_execution(self):
        """Test calculating next execution time based on last execution."""
        now = datetime.now()
        last_exec = now - timedelta(minutes=10)
        task = WatchTask(
            path="/test",
            watch_interval=30.0,
            created_at=now - timedelta(hours=1),
            last_execution_time=last_exec,
        )

        next_time = task.calculate_next_execution_time()

        expected = last_exec + timedelta(minutes=30.0)
        assert abs((next_time - expected).total_seconds()) < 1


class TestWatchManager:
    """Tests for WatchManager."""

    @pytest.mark.asyncio
    async def test_create_task(self, watch_manager: WatchManager):
        """Test creating a task."""
        task = await watch_manager.create_task(
            path="/test/path",
            to_uri="viking://resources/test",
            reason="Test task",
            watch_interval=30.0,
        )

        assert task.path == "/test/path"
        assert task.to_uri == "viking://resources/test"
        assert task.reason == "Test task"
        assert task.watch_interval == 30.0
        assert task.is_active is True
        assert task.next_execution_time is not None

    @pytest.mark.asyncio
    async def test_create_task_without_path_raises(self, watch_manager: WatchManager):
        """Test that creating a task without path raises error."""
        with pytest.raises(ValueError, match="Path is required"):
            await watch_manager.create_task(path="")

    @pytest.mark.asyncio
    async def test_create_task_with_conflicting_uri(self, watch_manager: WatchManager):
        """Test that creating a task with conflicting URI raises error."""
        await watch_manager.create_task(
            path="/test/path1",
            to_uri="viking://resources/test",
        )

        with pytest.raises(ConflictError, match="already used by another task"):
            await watch_manager.create_task(
                path="/test/path2",
                to_uri="viking://resources/test",
            )

    @pytest.mark.asyncio
    async def test_update_task(self, watch_manager: WatchManager):
        """Test updating a task."""
        task = await watch_manager.create_task(
            path="/test/path",
            reason="Original reason",
        )

        updated = await watch_manager.update_task(
            task_id=task.task_id,
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
            reason="Updated reason",
            watch_interval=45.0,
            is_active=False,
        )

        assert updated.reason == "Updated reason"
        assert updated.watch_interval == 45.0
        assert updated.is_active is False
        assert updated.next_execution_time is None

    @pytest.mark.asyncio
    async def test_update_task_not_found(self, watch_manager: WatchManager):
        """Test updating a non-existent task."""
        with pytest.raises(ValueError, match="not found"):
            await watch_manager.update_task(
                task_id="non-existent-id",
                account_id=TEST_ACCOUNT_ID,
                user_id=TEST_USER_ID,
                role=TEST_ROLE,
                reason="Updated",
            )

    @pytest.mark.asyncio
    async def test_update_task_with_conflicting_uri(self, watch_manager: WatchManager):
        """Test updating a task with conflicting URI."""
        await watch_manager.create_task(
            path="/test/path1",
            to_uri="viking://resources/test1",
        )
        task2 = await watch_manager.create_task(
            path="/test/path2",
            to_uri="viking://resources/test2",
        )

        with pytest.raises(ConflictError, match="already used by another task"):
            await watch_manager.update_task(
                task_id=task2.task_id,
                account_id=TEST_ACCOUNT_ID,
                user_id=TEST_USER_ID,
                role=TEST_ROLE,
                to_uri="viking://resources/test1",
            )

    @pytest.mark.asyncio
    async def test_delete_task(self, watch_manager: WatchManager):
        """Test deleting a task."""
        task = await watch_manager.create_task(
            path="/test/path",
            to_uri="viking://resources/test",
        )

        result = await watch_manager.delete_task(
            task_id=task.task_id,
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )

        assert result is True

        retrieved = await watch_manager.get_task(task.task_id)
        assert retrieved is None

        uri_task = await watch_manager.get_task_by_uri(
            to_uri="viking://resources/test",
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )
        assert uri_task is None

    @pytest.mark.asyncio
    async def test_delete_task_not_found(self, watch_manager: WatchManager):
        """Test deleting a non-existent task."""
        result = await watch_manager.delete_task(
            task_id="non-existent-id",
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_get_task(self, watch_manager: WatchManager):
        """Test getting a task by ID."""
        task = await watch_manager.create_task(path="/test/path")

        retrieved = await watch_manager.get_task(task.task_id)

        assert retrieved is not None
        assert retrieved.task_id == task.task_id
        assert retrieved.path == "/test/path"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, watch_manager: WatchManager):
        """Test getting a non-existent task."""
        retrieved = await watch_manager.get_task("non-existent-id")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_get_all_tasks(self, watch_manager: WatchManager):
        """Test getting all tasks."""
        await watch_manager.create_task(path="/test/path1")
        await watch_manager.create_task(path="/test/path2")
        await watch_manager.create_task(path="/test/path3")

        tasks = await watch_manager.get_all_tasks(
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )

        assert len(tasks) == 3

    @pytest.mark.asyncio
    async def test_get_all_tasks_active_only(self, watch_manager: WatchManager):
        """Test getting only active tasks."""
        task1 = await watch_manager.create_task(path="/test/path1")
        await watch_manager.create_task(path="/test/path2")

        await watch_manager.update_task(
            task_id=task1.task_id,
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
            is_active=False,
        )

        tasks = await watch_manager.get_all_tasks(
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
            active_only=True,
        )

        assert len(tasks) == 1
        assert tasks[0].is_active is True

    @pytest.mark.asyncio
    async def test_get_task_by_uri(self, watch_manager: WatchManager):
        """Test getting a task by URI."""
        task = await watch_manager.create_task(
            path="/test/path",
            to_uri="viking://resources/test",
        )

        retrieved = await watch_manager.get_task_by_uri(
            to_uri="viking://resources/test",
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )

        assert retrieved is not None
        assert retrieved.task_id == task.task_id

    @pytest.mark.asyncio
    async def test_get_task_by_uri_not_found(self, watch_manager: WatchManager):
        """Test getting a task by non-existent URI."""
        retrieved = await watch_manager.get_task_by_uri(
            to_uri="viking://nonexistent",
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_update_execution_time(self, watch_manager: WatchManager):
        """Test updating execution time."""
        task = await watch_manager.create_task(
            path="/test/path",
            watch_interval=30.0,
        )

        original_next_time = task.next_execution_time

        await asyncio.sleep(0.1)
        await watch_manager.update_execution_time(task.task_id)

        updated = await watch_manager.get_task(task.task_id)
        assert updated is not None
        assert updated.last_execution_time is not None
        assert updated.next_execution_time > original_next_time

    @pytest.mark.asyncio
    async def test_get_due_tasks(self, watch_manager: WatchManager):
        """Test getting due tasks."""
        task1 = await watch_manager.create_task(
            path="/test/path1",
            watch_interval=0.001,
        )
        await watch_manager.create_task(
            path="/test/path2",
            watch_interval=60.0,
        )

        await asyncio.sleep(0.1)

        due_tasks = await watch_manager.get_due_tasks()

        assert len(due_tasks) == 1
        assert due_tasks[0].task_id == task1.task_id

    @pytest.mark.asyncio
    async def test_clear_all_tasks(self, watch_manager: WatchManager):
        """Test clearing all tasks."""
        await watch_manager.create_task(path="/test/path1")
        await watch_manager.create_task(path="/test/path2")

        count = await watch_manager.clear_all_tasks()

        assert count == 2

        tasks = await watch_manager.get_all_tasks(
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )
        assert len(tasks) == 0

    @pytest.mark.asyncio
    async def test_create_task_with_non_positive_interval_raises(self, watch_manager: WatchManager):
        with pytest.raises(ValueError, match="watch_interval must be > 0"):
            await watch_manager.create_task(path="/test/path", watch_interval=0)

    @pytest.mark.asyncio
    async def test_get_next_execution_time(self, watch_manager: WatchManager):
        await watch_manager.create_task(path="/test/path1", watch_interval=60.0)
        await watch_manager.create_task(path="/test/path2", watch_interval=0.001)
        await asyncio.sleep(0.05)
        next_time = await watch_manager.get_next_execution_time()
        assert next_time is not None

    @pytest.mark.asyncio
    async def test_user_cannot_access_other_agent_task(self, watch_manager: WatchManager):
        task = await watch_manager.create_task(
            path="/test/path",
            to_uri="viking://resources/agent-isolation",
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
        )

        by_task_id = await watch_manager.get_task(
            task.task_id,
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role="USER",
            agent_id=OTHER_AGENT_ID,
        )
        by_uri = await watch_manager.get_task_by_uri(
            to_uri="viking://resources/agent-isolation",
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role="USER",
            agent_id=OTHER_AGENT_ID,
        )
        tasks = await watch_manager.get_all_tasks(
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role="USER",
            agent_id=OTHER_AGENT_ID,
        )

        assert by_task_id is None
        assert by_uri is None
        assert tasks == []

    @pytest.mark.asyncio
    async def test_user_cannot_update_or_delete_other_agent_task(self, watch_manager: WatchManager):
        task = await watch_manager.create_task(
            path="/test/path",
            to_uri="viking://resources/agent-update-delete",
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
        )

        with pytest.raises(PermissionDeniedError, match="does not have permission"):
            await watch_manager.update_task(
                task_id=task.task_id,
                account_id=TEST_ACCOUNT_ID,
                user_id=TEST_USER_ID,
                role="USER",
                agent_id=OTHER_AGENT_ID,
                reason="other agent should not update",
            )

        with pytest.raises(PermissionDeniedError, match="does not have permission"):
            await watch_manager.delete_task(
                task_id=task.task_id,
                account_id=TEST_ACCOUNT_ID,
                user_id=TEST_USER_ID,
                role="USER",
                agent_id=OTHER_AGENT_ID,
            )

    @pytest.mark.asyncio
    async def test_admin_can_manage_other_agent_task_in_same_account(
        self, watch_manager: WatchManager
    ):
        task = await watch_manager.create_task(
            path="/test/path",
            to_uri="viking://resources/admin-cross-agent",
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            agent_id=TEST_AGENT_ID,
        )

        updated = await watch_manager.update_task(
            task_id=task.task_id,
            account_id=TEST_ACCOUNT_ID,
            user_id="admin-user",
            role="ADMIN",
            agent_id=OTHER_AGENT_ID,
            reason="admin update",
        )

        assert updated.reason == "admin update"


class TestWatchManagerPersistence:
    """Tests for WatchManager persistence."""

    @pytest.mark.asyncio
    async def test_persistence_save_and_load(
        self, mock_viking_fs: MockVikingFS, temp_storage: Path
    ):
        """Test that tasks are saved and loaded correctly."""
        manager1 = WatchManager(viking_fs=mock_viking_fs)
        await manager1.initialize()

        task = await manager1.create_task(
            path="/test/path",
            to_uri="viking://resources/test",
            reason="Test task",
            watch_interval=45.0,
        )
        task_id = task.task_id

        manager2 = WatchManager(viking_fs=mock_viking_fs)
        await manager2.initialize()

        loaded_task = await manager2.get_task(task_id)

        assert loaded_task is not None
        assert loaded_task.path == "/test/path"
        assert loaded_task.to_uri == "viking://resources/test"
        assert loaded_task.reason == "Test task"
        assert loaded_task.watch_interval == 45.0

    @pytest.mark.asyncio
    async def test_persistence_without_vikingfs(self, watch_manager_no_fs: WatchManager):
        """Test that manager works without VikingFS (no persistence)."""
        task = await watch_manager_no_fs.create_task(
            path="/test/path",
            reason="Test task",
        )

        retrieved = await watch_manager_no_fs.get_task(task.task_id)
        assert retrieved is not None
        assert retrieved.path == "/test/path"

    @pytest.mark.asyncio
    async def test_persistence_after_delete(self, mock_viking_fs: MockVikingFS):
        """Test that deleted tasks are removed from persistence."""
        manager = WatchManager(viking_fs=mock_viking_fs)
        await manager.initialize()

        task = await manager.create_task(
            path="/test/path",
            to_uri="viking://resources/test",
        )

        await manager.delete_task(
            task_id=task.task_id,
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )

        manager2 = WatchManager(viking_fs=mock_viking_fs)
        await manager2.initialize()

        loaded_task = await manager2.get_task(task.task_id)
        assert loaded_task is None

        uri_task = await manager2.get_task_by_uri(
            to_uri="viking://resources/test",
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )
        assert uri_task is None

    @pytest.mark.asyncio
    async def test_persistence_backfill_next_execution_time(
        self, mock_viking_fs: MockVikingFS, temp_storage: Path
    ):
        manager1 = WatchManager(viking_fs=mock_viking_fs)
        await manager1.initialize()
        task = await manager1.create_task(
            path="/test/path",
            to_uri="viking://resources/test_backfill",
            watch_interval=30.0,
        )

        content = await mock_viking_fs.read_file(WatchManager.STORAGE_URI)
        data = json.loads(content)
        for t in data.get("tasks", []):
            if t.get("task_id") == task.task_id:
                t["next_execution_time"] = None
                break
        await mock_viking_fs.write_file(WatchManager.STORAGE_URI, json.dumps(data))

        manager2 = WatchManager(viking_fs=mock_viking_fs)
        await manager2.initialize()
        loaded = await manager2.get_task(task.task_id)
        assert loaded is not None
        assert loaded.is_active is True
        assert loaded.next_execution_time is not None

    @pytest.mark.asyncio
    async def test_persistence_empty_storage_file_is_ignored(self, mock_viking_fs: MockVikingFS):
        path = mock_viking_fs._uri_to_path(WatchManager.STORAGE_URI)
        mock_viking_fs.agfs.write(path, b"")

        manager = WatchManager(viking_fs=mock_viking_fs)
        await manager.initialize()

        tasks = await manager.get_all_tasks(
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )
        assert tasks == []

    @pytest.mark.asyncio
    async def test_persistence_recovers_from_backup_on_corrupt_storage(
        self, mock_viking_fs: MockVikingFS
    ):
        storage_path = mock_viking_fs._uri_to_path(WatchManager.STORAGE_URI)
        bak_path = mock_viking_fs._uri_to_path(WatchManager.STORAGE_BAK_URI)

        mock_viking_fs.agfs.write(storage_path, b"")

        task_data = {
            "task_id": "bak-task-id",
            "path": "/test/bak",
            "to_uri": "viking://resources/bak",
            "reason": "Backup task",
            "instruction": "",
            "watch_interval": 60.0,
            "created_at": datetime.now().isoformat(),
            "last_execution_time": None,
            "next_execution_time": None,
            "is_active": True,
        }
        data = {"tasks": [task_data], "updated_at": datetime.now().isoformat()}
        mock_viking_fs.agfs.write(bak_path, json.dumps(data).encode("utf-8"))

        manager = WatchManager(viking_fs=mock_viking_fs)
        await manager.initialize()

        loaded = await manager.get_task("bak-task-id")
        assert loaded is not None
        assert loaded.to_uri == "viking://resources/bak"


class TestWatchManagerConcurrency:
    """Tests for WatchManager concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_task_creation(self, watch_manager: WatchManager):
        """Test concurrent task creation."""

        async def create_task(index: int):
            return await watch_manager.create_task(
                path=f"/test/path{index}",
                to_uri=f"viking://resources/test{index}",
            )

        tasks = await asyncio.gather(*[create_task(i) for i in range(10)])

        assert len(tasks) == 10
        assert len({task.task_id for task in tasks}) == 10

        all_tasks = await watch_manager.get_all_tasks(
            account_id=TEST_ACCOUNT_ID,
            user_id=TEST_USER_ID,
            role=TEST_ROLE,
        )
        assert len(all_tasks) == 10

    @pytest.mark.asyncio
    async def test_concurrent_read_write(self, watch_manager: WatchManager):
        """Test concurrent read and write operations."""
        task = await watch_manager.create_task(path="/test/path")

        async def update_task(index: int):
            await watch_manager.update_task(
                task_id=task.task_id,
                account_id=TEST_ACCOUNT_ID,
                user_id=TEST_USER_ID,
                role=TEST_ROLE,
                reason=f"Update {index}",
            )

        async def read_task():
            return await watch_manager.get_task(task.task_id)

        operations = [update_task(i) for i in range(5)] + [read_task() for _ in range(5)]
        results = await asyncio.gather(*operations, return_exceptions=True)

        assert all(not isinstance(r, Exception) for r in results)

        final_task = await watch_manager.get_task(task.task_id)
        assert final_task is not None
