# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Integration tests for ResourceService watch functionality."""

from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openviking.resource.watch_manager import WatchManager
from openviking.server.identity import RequestContext, Role
from openviking.service import resource_service as resource_service_module
from openviking.service.resource_service import ResourceService
from openviking.service.task_tracker import TaskStatus
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.exceptions import ConflictError, InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


async def get_task_by_uri(service: ResourceService, to_uri: str, ctx: RequestContext):
    return await service._watch_scheduler.watch_manager.get_task_by_uri(
        to_uri=to_uri,
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        role=str(ctx.role),
    )


class MockResourceProcessor:
    """Mock ResourceProcessor for testing."""

    def __init__(self):
        self.calls = []

    async def process_resource(self, **kwargs):
        self.calls.append(kwargs)
        root_uri = kwargs.get("to") or "viking://resources/test"
        return {
            "root_uri": root_uri,
            "_post_process": {"root_uri": root_uri},
            "_resource_lock": SimpleNamespace(
                active=False,
                to_handoff=MagicMock(return_value=None),
                close=AsyncMock(),
            ),
        }


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


class NoopTaskTracker:
    def __init__(self):
        self._count = 0

    async def create(self, *_args, **_kwargs):
        self._count += 1
        return SimpleNamespace(task_id="test-task")

    async def start(self, *_args, **_kwargs):
        pass

    async def complete(self, *_args, **_kwargs):
        pass

    async def wait(self, *_args, **_kwargs):
        return SimpleNamespace(status=TaskStatus.COMPLETED, result={})

    def count(self):
        return self._count


def disable_task_tracker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: NoopTaskTracker(),
    )


@pytest.fixture(autouse=True)
def isolate_service_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    task_tracker = NoopTaskTracker()
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: task_tracker,
    )
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: False)
    monkeypatch.setattr("openviking.connector.routing.is_git_repo_url", lambda _path: False)


@pytest_asyncio.fixture
async def watch_manager() -> AsyncGenerator[WatchManager, None]:
    manager = WatchManager(viking_fs=None)
    await manager.initialize()
    yield manager


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
    service._enqueue_add_resource_job = AsyncMock(return_value=SimpleNamespace(task_id="test-task"))
    yield service


@pytest_asyncio.fixture
def request_context() -> RequestContext:
    """Create request context for testing."""
    return RequestContext(
        user=UserIdentifier("test_account", "test_user"),
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
    async def test_watch_interval_rejected_for_uploaded_snapshot_source(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """A temp-upload source is a one-time snapshot: watching it would silently
        re-process stale content forever, so creation must fail loudly."""
        to_uri = "viking://resources/uploaded_resource"

        with pytest.raises(InvalidArgumentError, match="uploaded content"):
            await resource_service.add_resource(
                path="/app/.openviking/workspace/temp/upload/upload_abc123.zip",
                ctx=request_context,
                to=to_uri,
                watch_interval=30.0,
                args={"temp_file_id": "upload_abc123.zip"},
            )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is None

    @pytest.mark.asyncio
    async def test_watch_interval_auto_binds_root_uri_when_to_omitted(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        result = await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=None,
            watch_interval=30.0,
        )

        assert result["root_uri"] == "viking://resources/test"

        task = await get_task_by_uri(resource_service, "viking://resources/test", request_context)
        assert task is not None
        assert task.path == "/test/path"
        assert task.to_uri == "viking://resources/test"
        assert task.parent_uri is None
        assert task.watch_interval == 30.0

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
            processing_mode="vectors_only",
            custom_option="x",
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.build_index is False
        assert task.summarize is True
        assert task.processing_mode == "vectors_only"
        assert task.processor_kwargs.get("custom_option") == "x"
        assert "processing_mode" not in task.processor_kwargs

    @pytest.mark.asyncio
    async def test_add_resource_forwards_tags_to_ingest_without_calling_set_tags(
        self,
        resource_service: ResourceService,
        request_context: RequestContext,
        monkeypatch: pytest.MonkeyPatch,
    ):
        async def fake_set_tags(self, **kwargs):
            raise AssertionError("add_resource(tags=...) must write tags during ingest")

        class FakeQueueManager:
            async def wait_complete(self, timeout=None):
                return {}

        monkeypatch.setattr(ContentWriteCoordinator, "set_tags", fake_set_tags)
        monkeypatch.setattr(
            resource_service_module, "get_queue_manager", lambda: FakeQueueManager()
        )

        result = await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to="viking://resources/tagged_resource",
            wait=True,
            tags=["team=search"],
            tag_mode="append",
        )

        assert "tags_result" not in result
        assert resource_service._resource_processor.calls[-1]["ingest_options"] == IngestOptions(
            search_tags=["team=search"],
            search_tag_mode="append",
        )

    @pytest.mark.asyncio
    async def test_add_resource_rejects_invalid_tag_mode_before_processing(
        self,
        resource_service: ResourceService,
        request_context: RequestContext,
    ):
        with pytest.raises(InvalidArgumentError, match="unsupported tag mode"):
            await resource_service.add_resource(
                path="/test/path",
                ctx=request_context,
                tags=["team=search"],
                tag_mode="create",
            )

        assert resource_service._resource_processor.calls == []

    @pytest.mark.asyncio
    async def test_watch_task_persists_tag_policy_for_refresh(
        self,
        resource_service: ResourceService,
        request_context: RequestContext,
        monkeypatch: pytest.MonkeyPatch,
    ):
        to_uri = "viking://resources/watch_tag_policy"

        async def fake_set_tags(self, **kwargs):
            return {"tags_updated": True}

        class FakeQueueManager:
            async def wait_complete(self, timeout=None):
                return {}

        monkeypatch.setattr(ContentWriteCoordinator, "set_tags", fake_set_tags)
        monkeypatch.setattr(
            resource_service_module, "get_queue_manager", lambda: FakeQueueManager()
        )

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            wait=True,
            watch_interval=30.0,
            tags=["team=search"],
            tag_mode="replace",
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.processor_kwargs["tags"] == ["team=search"]
        assert task.processor_kwargs["tag_mode"] == "replace"

    @pytest.mark.asyncio
    async def test_execute_prepared_add_resource_job_forwards_tags_to_ingest(
        self,
        resource_service: ResourceService,
        request_context: RequestContext,
        monkeypatch: pytest.MonkeyPatch,
    ):
        finish_calls = []

        async def fake_set_tags(self, **kwargs):
            raise AssertionError("queued add_resource(tags=...) must write tags during ingest")

        async def fake_finish_prepared_resource(*args, **kwargs):
            finish_calls.append({"args": args, "kwargs": kwargs})
            return {"root_uri": "viking://resources/queued"}

        monkeypatch.setattr(ContentWriteCoordinator, "set_tags", fake_set_tags)
        resource_service._resource_processor.finish_prepared_resource = AsyncMock(
            side_effect=fake_finish_prepared_resource
        )

        msg = AddResourceMsg(
            task_id="task-1",
            root_uri="viking://resources/queued",
            account_id=request_context.account_id,
            user_id=request_context.user.user_id,
            role=str(request_context.role),
            prepared={"path": "/test/path"},
            tags=["team=search"],
            tag_mode="append",
        )

        result = await resource_service.execute_add_resource_job(
            msg,
            ctx=request_context,
            resource_lock=None,
            stage_callback=lambda _stage: None,
        )

        assert "tags_result" not in result
        assert finish_calls[-1]["kwargs"]["ingest_options"] == IngestOptions(
            search_tags=["team=search"],
            search_tag_mode="append",
        )

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


class TestAddResourceArgs:
    """Tests for parser-specific add_resource args."""

    @pytest.mark.asyncio
    async def test_forwards_args_to_resource_processor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        resource_service: ResourceService,
        request_context: RequestContext,
    ):
        disable_task_tracker(monkeypatch)

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            args={"feishu_access_token": " u-test "},
        )

        processor = resource_service._resource_processor
        assert processor.calls[-1]["feishu_access_token"] == "u-test"

    @pytest.mark.asyncio
    async def test_feishu_user_token_watch_stores_private_auth_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        resource_service: ResourceService,
        request_context: RequestContext,
    ):
        monkeypatch.setattr(
            resource_service_module,
            "load_feishu_app_credentials",
            lambda: object(),
        )
        disable_task_tracker(monkeypatch)
        to_uri = "viking://resources/feishu_user_watch"

        await resource_service.add_resource(
            path="https://example.feishu.cn/docx/doc_token",
            ctx=request_context,
            to=to_uri,
            watch_interval=30,
            args={
                "feishu_access_token": " u-test ",
                "feishu_refresh_token": " r-test ",
            },
        )

        processor = resource_service._resource_processor
        assert processor.calls[-1]["feishu_access_token"] == "u-test"
        assert "feishu_refresh_token" not in processor.calls[-1]

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.processor_kwargs == {}
        assert task.auth_state == {
            "provider": "feishu",
            "access_token": "u-test",
            "refresh_token": "r-test",
            "expires_at": None,
        }
        assert "auth_state" not in task.to_dict()

    @pytest.mark.asyncio
    async def test_git_token_watch_stores_private_auth_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        resource_service: ResourceService,
        request_context: RequestContext,
    ):
        monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
        disable_task_tracker(monkeypatch)
        repo_url = "https://git.example/org/private.git"
        to_uri = "viking://resources/git_private_watch"

        await resource_service.add_resource(
            path=repo_url,
            ctx=request_context,
            to=to_uri,
            watch_interval=30,
            args={
                "branch": "main",
                "auth_config": {
                    "username": "git-user",
                    "token": "git-secret",
                },
            },
        )

        processor = resource_service._resource_processor
        assert processor.calls[-1]["auth_config"] == {
            "username": "git-user",
            "token": "git-secret",
        }

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.processor_kwargs == {"branch": "main"}
        assert task.auth_state == {
            "provider": "git_http_basic",
            "username": "git-user",
            "token": "git-secret",
            "repo_url": repo_url,
        }
        public_task = task.to_dict()
        assert "auth_state" not in public_task
        assert "git-secret" not in str(public_task)


class TestWatchTaskConflict:
    """Tests for watch task conflict detection."""

    @pytest.mark.asyncio
    async def test_conflict_when_active_task_exists(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """A different source cannot replace an active watch."""
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
    async def test_same_source_updates_active_task(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Re-applying one source updates its active watch instead of conflicting."""
        to_uri = "viking://resources/idempotent_watch"
        source = "/test/same-path"

        await resource_service.add_resource(
            path=source,
            ctx=request_context,
            to=to_uri,
            reason="Original reason",
            watch_interval=30.0,
            args={"branch": "main"},
        )
        original = await get_task_by_uri(resource_service, to_uri, request_context)
        assert original is not None

        await resource_service.add_resource(
            path=source,
            ctx=request_context,
            to=to_uri,
            reason="Updated reason",
            watch_interval=45.0,
            args={"branch": "release"},
        )

        updated = await get_task_by_uri(resource_service, to_uri, request_context)
        assert updated is not None
        assert updated.task_id == original.task_id
        assert updated.path == source
        assert updated.reason == "Updated reason"
        assert updated.watch_interval == 45.0
        assert updated.processor_kwargs == {"branch": "release"}
        assert updated.is_active is True

    @pytest.mark.asyncio
    async def test_conflict_does_not_create_async_task(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """A rejected watch request must not leave behind a user-invisible task."""
        from openviking.service.task_tracker import get_task_tracker, set_task_tracker

        set_task_tracker(None)
        to_uri = "viking://resources/conflict_no_task"

        await resource_service.add_resource(
            path="/test/path1",
            ctx=request_context,
            to=to_uri,
            watch_interval=30.0,
        )
        task_count_before = get_task_tracker().count()

        with pytest.raises(ConflictError):
            await resource_service.add_resource(
                path="/test/path2",
                ctx=request_context,
                to=to_uri,
                watch_interval=45.0,
            )

        assert get_task_tracker().count() == task_count_before

    @pytest.mark.asyncio
    async def test_conflict_when_task_exists_but_hidden_by_permission(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        to_uri = "viking://resources/cross_user_conflict"
        other_user_ctx = RequestContext(
            user=UserIdentifier("test_account", "other_user"),
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
    async def test_same_user_context_sees_existing_task(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        to_uri = "viking://resources/same_user_conflict"
        same_user_ctx = RequestContext(
            user=UserIdentifier("test_account", "test_user"), role=Role.USER
        )

        await resource_service.add_resource(
            path="/test/path1",
            ctx=request_context,
            to=to_uri,
            watch_interval=30.0,
        )

        visible_task = await get_task_by_uri(resource_service, to_uri, same_user_ctx)
        assert visible_task is not None

        with pytest.raises(ConflictError) as exc_info:
            await resource_service.add_resource(
                path="/test/path2",
                ctx=same_user_ctx,
                to=to_uri,
                watch_interval=45.0,
            )

        assert "already being monitored" in str(exc_info.value)
        assert to_uri in str(exc_info.value)

        original_task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert original_task is not None

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
            role=str(request_context.role),
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

    @pytest.mark.asyncio
    async def test_same_user_can_cancel_existing_task(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        to_uri = "viking://resources/cancel_same_user"
        same_user_ctx = RequestContext(
            user=UserIdentifier("test_account", "test_user"), role=Role.USER
        )

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=30.0,
        )

        await resource_service.add_resource(
            path="/test/path",
            ctx=same_user_ctx,
            to=to_uri,
            watch_interval=0,
        )

        original_task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert original_task is not None
        assert original_task.is_active is False


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
            role=str(request_context.role),
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
        service._enqueue_add_resource_job = AsyncMock(
            return_value=SimpleNamespace(task_id="test-task")
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
        service._enqueue_add_resource_job = AsyncMock(
            return_value=SimpleNamespace(task_id="test-task")
        )

        result = await service.add_resource(
            path="/test/path",
            ctx=request_context,
            to="viking://resources/test",
            watch_interval=30.0,
        )

        assert result is not None
        assert "root_uri" in result
