# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Integration tests for ResourceService watch functionality."""

import asyncio
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
import pytest_asyncio

from openviking.parse.mode import ParseMode
from openviking.resource.feishu_watch_auth import FeishuAppCredentials
from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_scheduler import WatchScheduler
from openviking.resource.watch_types import WatchRunResult
from openviking.server.identity import RequestContext, Role
from openviking.service import resource_service as resource_service_module
from openviking.service.resource_service import ResourceService
from openviking.service.task_domain import WorkSummary
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking.service.task_work_index import TASK_WORK_ID_FIELD
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.exceptions import ConflictError, FailedPreconditionError, InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs
from tests.utils.watch_fixtures import shape_watch


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
            "_resource_lock": None,
        }

    def should_use_understanding_directly(self, *_args, **_kwargs):
        return False

    async def finish_prepared_resource(self, *_args, **_kwargs):
        return {"status": "success"}


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


class EnqueuedTasks:
    """Task records for jobs the test enqueued; each completes immediately."""

    def __init__(self):
        self.ids: set[str] = set()

    async def get(self, task_id, **_kwargs):
        if task_id not in self.ids:
            return None
        return SimpleNamespace(
            task_id=task_id,
            status=TaskStatus.COMPLETED,
            result={},
            error=None,
            is_terminal=lambda: True,
        )

    async def wait(self, task_id, **_kwargs):
        if task_id not in self.ids:
            raise KeyError(task_id)
        return await self.get(task_id)


async def run_watch_now(monkeypatch, resource_service, watch_vfs, task):
    """Release the first run, then refresh through the scheduler."""
    from openviking.resource.watch_scheduler import WatchScheduler

    tracker = EnqueuedTasks()
    monkeypatch.setattr("openviking.resource.watch_scheduler.get_task_tracker", lambda: tracker)

    def enqueued(msg, **_kwargs):
        tracker.ids.add(msg.task_id)
        return SimpleNamespace(task_id=msg.task_id)

    resource_service._enqueue_add_resource_job.side_effect = enqueued
    manager = resource_service._get_watch_manager()
    state = await manager.store.get_state(task.task_id)
    if state.current_run_id:
        await manager.finish_run(
            task.task_id, state.current_run_id, WatchRunResult(status="completed")
        )
    owner = RequestContext(user=UserIdentifier(task.account_id, task.user_id), role=Role.ROOT)
    await watch_vfs.mkdir(task.to_uri, ctx=owner, exist_ok=True)
    scheduler = WatchScheduler(
        resource_service,
        manager.store,
        viking_fs=watch_vfs,
        runtime_config_manager=resource_service._runtime_config_manager,
        leader_enabled=False,
    )
    await scheduler.start()
    try:
        assert await scheduler.schedule_task(task.task_id)
        await asyncio.gather(*list(scheduler._background))
    finally:
        await scheduler.stop()


def disable_task_tracker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: NoopTaskTracker(),
    )


@pytest.fixture
def runtime_config_manager():
    from openviking_cli.utils.config.parser_config import FeishuConfig

    async def resolve_account(account_id, resolver):
        assert account_id
        return resolver(
            SimpleNamespace(
                account=SimpleNamespace(feishu=None),
                cluster=SimpleNamespace(feishu=FeishuConfig()),
            )
        )

    return SimpleNamespace(resolve_account=resolve_account)


@pytest.fixture(autouse=True)
def isolate_service_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    task_tracker = NoopTaskTracker()
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: task_tracker,
    )
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: False)
    monkeypatch.setattr("openviking.connector.routing.is_git_repo_url", lambda _path: False)
    monkeypatch.setattr(
        "openviking.connector.delegate.ConnectorDelegate.supported_args",
        Mock(return_value=set()),
    )


@pytest_asyncio.fixture
async def resource_service(
    watch_manager: WatchManager, runtime_config_manager
) -> AsyncGenerator[ResourceService, None]:
    """Create ResourceService instance with watch support."""
    scheduler = MagicMock()
    scheduler.watch_manager = watch_manager
    service = ResourceService(
        vikingdb=MockVikingDB(),
        viking_fs=MockVikingFS(),
        resource_processor=MockResourceProcessor(),
        skill_processor=MockSkillProcessor(),
        watch_scheduler=scheduler,
        runtime_config_manager=runtime_config_manager,
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
            build_index=False,
            summarize=True,
            processing_mode="vectors_only",
            custom_option="x",
        )

        assert result is not None
        assert "root_uri" in result

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.path == "/test/path"
        assert task.source_type == "local"
        assert task.to_uri == to_uri
        assert task.reason == "Test monitoring"
        assert task.instruction == "Monitor for changes"
        assert task.watch_interval == 30.0
        assert task.is_active is True
        assert task.build_index is False
        assert task.summarize is True
        assert task.processing_mode == "vectors_only"
        assert task.processor_kwargs == {"custom_option": "x"}

    @pytest.mark.asyncio
    async def test_watch_interval_rejected_for_uploaded_snapshot_source(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """A temp-upload source is a static snapshot: watching it would silently
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
    async def test_add_resource_forwards_tags_to_ingest_without_calling_set_tags(
        self,
        resource_service: ResourceService,
        request_context: RequestContext,
        monkeypatch: pytest.MonkeyPatch,
    ):
        async def fake_set_tags(self, **kwargs):
            raise AssertionError("add_resource(tags=...) must write tags during ingest")

        monkeypatch.setattr(ContentWriteCoordinator, "set_tags", fake_set_tags)

        result = await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to="viking://resources/tagged_resource",
            wait=True,
            tags=["team=search"],
            tag_mode="append",
        )

        assert "tags_result" not in result
        assert resource_service._resource_processor.calls[-1]["tags"] == ["team=search"]
        assert resource_service._resource_processor.calls[-1]["tag_mode"] == "append"

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
    @pytest.mark.parametrize(("tags", "tag_mode"), [(["team=search"], "replace"), (None, "clear")])
    async def test_watch_task_persists_tag_policy_for_refresh(
        self,
        resource_service: ResourceService,
        request_context: RequestContext,
        tags,
        tag_mode,
    ):
        to_uri = "viking://resources/watch_tag_policy"
        await resource_service._execute_resource_ingestion(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            defer_post_processing=False,
            watch_interval=30.0,
            tags=tags,
            tag_mode=tag_mode,
            resource_lock={"owner_id": "owner", "lease_ref": "lease"},
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.processor_kwargs == {
            **({"tags": tags} if tags else {}),
            "tag_mode": tag_mode,
        }

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

    def test_add_resource_message_round_trip_preserves_clear_without_tags(self):
        message = AddResourceMsg(
            task_id="task-clear",
            path="https://example.com/demo.md",
            root_uri="viking://resources/demo",
            account_id="acct",
            user_id="user",
            role="user",
            tag_mode="clear",
        )

        restored = AddResourceMsg.from_dict(message.to_dict())

        assert restored.tags is None
        assert restored.tag_mode == "clear"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("watch_interval", [0, -10])
    async def test_non_positive_interval_creates_no_watch(
        self, resource_service: ResourceService, request_context: RequestContext, watch_interval
    ):
        to_uri = "viking://resources/no_watch"

        await resource_service.add_resource(
            path="/test/path",
            ctx=request_context,
            to=to_uri,
            watch_interval=watch_interval,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is None


class TestAddResourceArgs:
    """Tests for parser-specific add_resource args."""

    @pytest.mark.asyncio
    async def test_feishu_user_token_watch_stores_private_auth_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        resource_service: ResourceService,
        request_context: RequestContext,
        watch_vfs,
    ):
        from openviking.resource.feishu_watch_auth import FeishuRefreshedToken
        from openviking_cli.utils.config import open_viking_config as config_module

        config = config_module.ConnectorConfig(auth="https://connector.example/oauth/access_token")
        monkeypatch.setattr(
            config_module, "get_openviking_config", lambda: SimpleNamespace(connector=config)
        )
        monkeypatch.setattr(
            resource_service_module,
            "load_feishu_app_credentials",
            lambda config=None, app_id=None, app_secret=None: FeishuAppCredentials(
                app_id=app_id or "configured-app",
                app_secret=app_secret or "configured-secret",
                domain="https://open.feishu.cn",
                request_timeout=30,
            ),
        )
        monkeypatch.setattr(
            "openviking.resource.feishu_watch_auth.load_feishu_app_credentials",
            resource_service_module.load_feishu_app_credentials,
        )
        disable_task_tracker(monkeypatch)
        to_uri = "viking://resources/feishu_user_watch"
        resource_service._plan_source_job_target = AsyncMock(
            return_value=(to_uri, None, False, False)
        )

        expected_token = "u-test"

        async def preflight(_self, _source, *, feishu_access_token=None, **kwargs):
            assert feishu_access_token == expected_token
            assert kwargs["feishu_config"].domain == "https://open.feishu.cn"
            return SimpleNamespace(source_name=None, source_format="file")

        monkeypatch.setattr(
            "openviking.parse.accessors.feishu_accessor.FeishuAccessor.preflight_source",
            preflight,
        )

        await resource_service.add_resource(
            path="https://example.feishu.cn/docx/doc_token",
            ctx=request_context,
            to=to_uri,
            watch_interval=30,
            args={
                "feishu_access_token": " u-test ",
                "feishu_refresh_token": " r-test ",
                "feishu_app_id": " cli-test ",
                "feishu_app_secret": " secret-test ",
            },
        )

        enqueue_call = resource_service._enqueue_add_resource_job.await_args
        message = enqueue_call.args[0]
        task_auth = enqueue_call.kwargs["task_auth"]
        assert "u-test" not in str(message.to_dict())
        assert task_auth == {
            "provider": "feishu",
            "access_token": "u-test",
            "refresh_token": "r-test",
            "expires_at": None,
            "app_id": "cli-test",
            "app_secret": "secret-test",
        }
        assert "secret-test" not in str(message.to_dict())
        await resource_service.execute_add_resource_job(
            message,
            ctx=request_context,
            resource_lock=None,
            stage_callback=AsyncMock(),
            task_auth=task_auth,
        )

        processor = resource_service._resource_processor
        assert processor.calls[-1]["feishu_access_token"] == "u-test"
        assert "feishu_refresh_token" not in processor.calls[-1]
        assert "feishu_app_id" not in processor.calls[-1]
        assert "feishu_app_secret" not in processor.calls[-1]

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.source_type == "feishu"
        assert task.processor_kwargs == {}
        assert task.auth_state == {
            "provider": "feishu",
            "access_token": "u-test",
            "refresh_token": "r-test",
            "expires_at": None,
            "app_id": "cli-test",
            "app_secret": "secret-test",
        }
        assert "auth_state" not in task.to_dict()

        # A stored local watch still refreshes and queues work with connector.auth enabled.
        refresh = Mock(return_value=FeishuRefreshedToken("u-new", "r-new", 7200))
        monkeypatch.setattr(
            "openviking.resource.feishu_watch_auth.FeishuOAuthClient._refresh_user_access_token_sync",
            refresh,
        )
        expected_token = "u-new"
        await run_watch_now(monkeypatch, resource_service, watch_vfs, task)
        refresh.assert_called_once_with("r-test")
        updated = await get_task_by_uri(resource_service, to_uri, request_context)
        assert updated.task_id == task.task_id
        assert updated.is_active is True
        assert updated.auth_state["provider"] == "feishu"
        assert updated.auth_state["refresh_token"] == "r-new"
        queued_auth = resource_service._enqueue_add_resource_job.await_args.kwargs["task_auth"]
        assert queued_auth["access_token"] == "u-new"

    @pytest.mark.asyncio
    async def test_feishu_account_default_watch_does_not_store_app_secret(
        self,
        monkeypatch: pytest.MonkeyPatch,
        resource_service: ResourceService,
        request_context: RequestContext,
    ):
        monkeypatch.setattr(
            resource_service_module,
            "load_feishu_app_credentials",
            lambda config=None, app_id=None, app_secret=None: FeishuAppCredentials(
                app_id="account-app",
                app_secret="account-secret",
                domain="https://open.feishu.cn",
                request_timeout=30,
            ),
        )

        normalized = await resource_service._normalize_add_resource_args(
            {
                "feishu_access_token": "u-test",
                "feishu_refresh_token": "r-test",
            },
            ctx=request_context,
            watch_interval=30,
        )

        assert normalized.watch_auth_state == {
            "provider": "feishu",
            "access_token": "u-test",
            "refresh_token": "r-test",
            "expires_at": None,
            "app_id": "account-app",
        }

    def test_feishu_watch_refresh_restores_access_token_without_refresh_token(
        self,
        resource_service: ResourceService,
    ):
        msg = SimpleNamespace(
            path="https://example.feishu.cn/docx/doc_token",
            watch_interval=30.0,
            skip_watch_management=True,
            understanding_response_id=None,
            understanding_file_id=None,
        )

        auth_kwargs, watch_auth_state = resource_service._restore_source_task_auth(
            msg,
            {
                "provider": "feishu",
                "access_token": "u-refreshed",
                "domain": "https://open.feishu.cn",
            },
        )

        assert auth_kwargs == {"feishu_access_token": "u-refreshed"}
        assert watch_auth_state is None

    @pytest.mark.asyncio
    async def test_feishu_watch_refresh_preflight_uses_cluster_domain(
        self,
        monkeypatch: pytest.MonkeyPatch,
        resource_service: ResourceService,
        request_context: RequestContext,
    ):
        from openviking_cli.utils.config.parser_config import FeishuConfig

        async def resolve_account(account_id, resolver):
            assert account_id == request_context.account_id
            return resolver(
                SimpleNamespace(
                    account=SimpleNamespace(feishu=None),
                    cluster=SimpleNamespace(feishu=FeishuConfig(domain="https://open.feishu.cn")),
                )
            )

        seen = {}

        async def preflight(
            _self,
            _source,
            *,
            feishu_access_token=None,
            feishu_config=None,
            **_kwargs,
        ):
            seen["access_token"] = feishu_access_token
            seen["config_domain"] = feishu_config.domain
            return SimpleNamespace(source_name=None, source_format="file")

        resource_service._runtime_config_manager = SimpleNamespace(resolve_account=resolve_account)
        monkeypatch.setattr(
            "openviking.parse.accessors.feishu_accessor.FeishuAccessor.preflight_source",
            preflight,
        )

        plan = await resource_service._prepare_standard_source_plan(
            path="https://example.feishu.cn/docx/doc_token",
            ctx=request_context,
            mode=ParseMode.DEFAULT,
            allow_local_path_resolution=False,
            processor_kwargs={
                "feishu_access_token": "u-refreshed",
            },
            watch_auth_state={
                "provider": "feishu",
                "access_token": "u-refreshed",
                "domain": "https://open.larksuite.com",
            },
        )

        assert seen == {
            "access_token": "u-refreshed",
            "config_domain": "https://open.feishu.cn",
        }
        assert "domain" not in plan.task_auth

    @pytest.mark.asyncio
    async def test_feishu_watch_binds_account_app_identity_without_secret(
        self,
        monkeypatch: pytest.MonkeyPatch,
        resource_service: ResourceService,
        request_context: RequestContext,
    ):
        from openviking_cli.utils.config.parser_config import FeishuConfig

        async def resolve_account(account_id, resolver):
            assert account_id == request_context.account_id
            return resolver(
                SimpleNamespace(
                    account=SimpleNamespace(
                        feishu=SimpleNamespace(
                            app_id="account-app",
                            app_secret="account-secret",
                            max_rows_per_sheet=None,
                            max_records_per_table=None,
                            download_images=None,
                            request_timeout=None,
                        )
                    ),
                    cluster=SimpleNamespace(feishu=FeishuConfig()),
                )
            )

        resource_service._runtime_config_manager = SimpleNamespace(resolve_account=resolve_account)
        disable_task_tracker(monkeypatch)
        to_uri = "viking://resources/feishu-account-watch"
        resource_service._plan_source_job_target = AsyncMock(
            return_value=(to_uri, None, False, False)
        )
        monkeypatch.setattr(
            "openviking.parse.accessors.feishu_accessor.FeishuAccessor.preflight_source",
            AsyncMock(return_value=SimpleNamespace(source_name=None, source_format="file")),
        )

        await resource_service.add_resource(
            path="https://example.feishu.cn/docx/doc_token",
            ctx=request_context,
            to=to_uri,
            watch_interval=30,
            args={
                "feishu_access_token": "user-token",
                "feishu_refresh_token": "refresh-token",
            },
        )

        auth_state = resource_service._enqueue_add_resource_job.await_args.kwargs["task_auth"]
        assert auth_state["app_id"] == "account-app"
        assert "app_secret" not in auth_state

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("target", "is_active"),
        [
            ({"to": "viking://resources/feishu"}, False),
            ({"parent": "viking://resources"}, False),
            ({"to": "viking://resources/feishu"}, True),
        ],
    )
    async def test_native_feishu_watch_is_created_during_queue_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        resource_service: ResourceService,
        request_context: RequestContext,
        target,
        is_active,
    ):
        to_uri = "viking://resources/feishu"
        resource_service._plan_source_job_target = AsyncMock(
            return_value=(to_uri, None, False, False)
        )

        async def preflight(_self, _source, *, feishu_access_token=None, **_kwargs):
            return SimpleNamespace(source_name="Feishu", source_format="file")

        monkeypatch.setattr(
            "openviking.parse.accessors.feishu_accessor.FeishuAccessor.preflight_source",
            preflight,
        )

        result = await resource_service.add_resource(
            path="https://example.feishu.cn/docx/doc_token",
            ctx=request_context,
            watch_interval=30,
            is_active=is_active,
            **target,
        )

        assert await get_task_by_uri(resource_service, to_uri, request_context) is None
        assert result["task_id"] == "test-task"

        message = resource_service._enqueue_add_resource_job.await_args.args[0]
        assert message.is_active is is_active
        assert message.watch_task_id is None
        assert message.skip_watch_management is False
        assert AddResourceMsg.from_dict(message.to_dict()).is_active is is_active

        await resource_service.execute_add_resource_job(
            message,
            ctx=request_context,
            resource_lock=None,
            stage_callback=AsyncMock(),
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert message.watch_task_id == task.task_id
        assert task.is_active is (is_active is not False)
        assert (task.next_execution_time is not None) is (is_active is not False)
        assert len(resource_service._resource_processor.calls) == 1

    @pytest.mark.asyncio
    async def test_native_feishu_preflight_failure_does_not_create_watch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        resource_service: ResourceService,
        request_context: RequestContext,
    ):
        to_uri = "viking://resources/failed_feishu"

        async def fail_preflight(_self, _source, *, feishu_access_token=None, **_kwargs):
            raise RuntimeError("preflight unavailable")

        monkeypatch.setattr(
            "openviking.parse.accessors.feishu_accessor.FeishuAccessor.preflight_source",
            fail_preflight,
        )

        with pytest.raises(RuntimeError, match="preflight unavailable"):
            await resource_service.add_resource(
                path="https://example.feishu.cn/docx/doc_token",
                ctx=request_context,
                to=to_uri,
                watch_interval=30,
                is_active=False,
            )

        assert await get_task_by_uri(resource_service, to_uri, request_context) is None

    @pytest.mark.asyncio
    async def test_paused_watch_with_parent_rejects_non_feishu_source(
        self,
        resource_service: ResourceService,
        request_context: RequestContext,
    ):
        with pytest.raises(InvalidArgumentError, match="only supported"):
            await resource_service.add_resource(
                path="/test/path",
                ctx=request_context,
                parent="viking://resources",
                watch_interval=30,
                is_active=False,
            )

    @pytest.mark.asyncio
    async def test_feishu_user_token_watch_rejects_partial_app_credentials(
        self,
        resource_service: ResourceService,
        request_context: RequestContext,
    ):
        with pytest.raises(InvalidArgumentError, match="must be non-empty strings"):
            await resource_service.add_resource(
                path="https://example.feishu.cn/docx/doc_token",
                ctx=request_context,
                watch_interval=30,
                args={
                    "feishu_access_token": "u-test",
                    "feishu_refresh_token": "r-test",
                    "feishu_app_id": "cli-test",
                },
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("target", "is_active"),
        [
            ({"to": "viking://resources/git_private_watch"}, True),
            ({"to": "viking://resources/git_private_watch"}, False),
            ({"parent": "viking://resources"}, False),
        ],
    )
    async def test_git_token_watch_stores_private_auth_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        resource_service: ResourceService,
        request_context: RequestContext,
        target,
        is_active,
    ):
        monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
        disable_task_tracker(monkeypatch)
        repo_url = "https://git.example/org/private.git"
        to_uri = "viking://resources/git_private_watch"
        resource_service._preflight_git_source = AsyncMock(
            return_value=SimpleNamespace(
                source_name="private",
                source_path=repo_url,
                source_format="repository",
            )
        )
        resource_service._plan_source_job_target = AsyncMock(
            return_value=(to_uri, None, False, False)
        )

        await resource_service.add_resource(
            path=repo_url,
            ctx=request_context,
            **target,
            is_active=is_active,
            watch_interval=30,
            args={
                "branch": "main",
                "auth_config": {
                    "username": "git-user",
                    "token": "git-secret",
                },
            },
        )

        enqueue_call = resource_service._enqueue_add_resource_job.await_args
        message = enqueue_call.args[0]
        assert AddResourceMsg.from_dict(message.to_dict()).is_active is is_active
        task_auth = enqueue_call.kwargs["task_auth"]
        assert "git-secret" not in str(message.to_dict())
        assert task_auth == {
            "provider": "git_http_basic",
            "username": "git-user",
            "token": "git-secret",
            "repo_url": repo_url,
        }
        await resource_service.execute_add_resource_job(
            message,
            ctx=request_context,
            resource_lock=None,
            stage_callback=AsyncMock(),
            task_auth=task_auth,
        )

        processor = resource_service._resource_processor
        assert processor.calls[-1]["auth_config"] == {
            "username": "git-user",
            "token": "git-secret",
        }

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert len(processor.calls) == 1
        assert task.is_active is is_active
        assert (task.next_execution_time is not None) is is_active
        assert message.watch_task_id == task.task_id
        assert task.source_type == "git"
        assert task.processor_kwargs == {
            "branch": "main",
            "source_name": "private",
        }
        assert task.auth_state == {
            "provider": "git_http_basic",
            "username": "git-user",
            "token": "git-secret",
            "repo_url": repo_url,
        }
        public_task = task.to_dict()
        assert "auth_state" not in public_task
        assert "git-secret" not in str(public_task)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "error", "expected_status", "expected_error"),
    [
        (
            {"status": "error", "errors": ["parse failed", "write failed"]},
            None,
            "failed",
            "parse failed; write failed",
        ),
        (None, asyncio.CancelledError(), "cancelled", None),
    ],
)
async def test_add_resource_processor_records_paused_watch_result(
    monkeypatch: pytest.MonkeyPatch,
    result,
    error,
    expected_status: str,
    expected_error: str | None,
):
    task_tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.PENDING)),
        start=AsyncMock(),
        patch_task=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        get_task_auth=AsyncMock(return_value={}),
        wait_for_task_work=AsyncMock(),
        get_work_summary=AsyncMock(return_value=WorkSummary()),
        format_queue_status=Mock(return_value={}),
        format_queue_timing=Mock(return_value={}),
        get=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.CANCELLING)),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )

    async def execute_add_resource_job(message, **_kwargs):
        message.watch_task_id = "watch-1"
        if error:
            raise error
        return result

    execute = AsyncMock(side_effect=execute_add_resource_job)
    service = SimpleNamespace(
        execute_add_resource_job=execute,
        _link_resource_reason_memory=AsyncMock(),
        record_watch_execution=AsyncMock(),
    )
    processor = AddResourceProcessor(
        service,
        QueueManager.ADD_RESOURCE,
        SimpleNamespace(_async_agfs=SimpleNamespace(pathlock_release=AsyncMock())),
    )
    message = AddResourceMsg(
        task_id="add-resource-1",
        path="https://example.feishu.cn/docx/doc_token",
        root_uri="viking://resources/paused_feishu",
        account_id="account-1",
        user_id="user-1",
        role="user",
    )

    data = message.to_dict()
    data[TASK_WORK_ID_FIELD] = "work-1"
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await processor._process(message, data)
    else:
        await processor._process(message, data)

    service.record_watch_execution.assert_awaited_once_with(
        "watch-1",
        status=expected_status,
        execution_task_id="add-resource-1",
        error=expected_error,
    )


@pytest.mark.asyncio
async def test_record_watch_execution_redacts_error_in_log(caplog):
    scheduler = SimpleNamespace(settle=AsyncMock(return_value=True))
    service = ResourceService(watch_scheduler=scheduler)
    error = "connector failed: api_key=secret-watch-key, Authorization: Bearer secret-token"

    resource_service_module.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level("INFO", logger=resource_service_module.logger.name):
            await service.record_watch_execution(
                "watch-1",
                status="failed",
                execution_task_id="connector-1",
                error=error,
            )
    finally:
        resource_service_module.logger.removeHandler(caplog.handler)

    assert "secret-watch-key" not in caplog.text
    assert "secret-token" not in caplog.text
    assert "[REDACTED]" in caplog.text


@pytest_asyncio.fixture
async def native_watch_submission(monkeypatch, resource_service, watch_store, watch_vfs):
    """Real Task creation, queue consumer and Watch settlement with a stub parser."""
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    scheduler = WatchScheduler(
        resource_service, watch_store, viking_fs=watch_vfs, leader_enabled=False
    )
    await scheduler.start()
    resource_service._watch_scheduler = scheduler
    resource_service._viking_fs = watch_vfs
    resource_service._enqueue_add_resource_job = ResourceService._enqueue_add_resource_job.__get__(
        resource_service
    )
    queue = SimpleNamespace(enqueue=AsyncMock(return_value="message-1"))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr("openviking.resource.watch_scheduler.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker", lambda: tracker
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue)
    try:
        yield SimpleNamespace(service=resource_service, tracker=tracker, queue=queue)
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke_at", ["before-check", "during-insert"])
async def test_first_native_submission_rejects_a_released_run(
    monkeypatch, native_watch_submission, request_context, revoke_at
):
    service = native_watch_submission.service
    tracker = native_watch_submission.tracker
    scheduler = service._watch_scheduler
    manager = scheduler.watch_manager
    to_uri = "viking://resources/first-native"

    async def release_run():
        watch = await get_task_by_uri(service, to_uri, request_context)
        await shape_watch(manager.store, watch.task_id, run_started_at=1.0)
        await scheduler.sweep()
        assert (await manager.store.get_state(watch.task_id)).current_run_id is None

    if revoke_at == "before-check":

        async def handoff(_lease):
            await release_run()
            return None

        monkeypatch.setattr(service, "_lock_to_handoff_payload", handoff)
    else:
        create = tracker._store.create

        async def insert(task, **kwargs):
            await release_run()
            return await create(task, **kwargs)

        monkeypatch.setattr(tracker._store, "create", insert)

    with pytest.raises(FailedPreconditionError, match="no longer admitted"):
        await service._execute_resource_ingestion(
            path="https://example.org/doc",
            to=to_uri,
            ctx=request_context,
            defer_post_processing=True,
            watch_interval=1,
            task_id="first-native",
        )
    assert (
        await tracker.list_tasks(
            account_id=request_context.account_id, user_id=request_context.user.user_id
        )
        == []
    )
    native_watch_submission.queue.enqueue.assert_not_awaited()
    assert await manager.store.running() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("is_active", [False, True])
@pytest.mark.parametrize("work_failed", [False, True])
async def test_native_task_runs_after_submission_and_settles_after_work(
    native_watch_submission, request_context, is_active, work_failed
):
    service = native_watch_submission.service
    tracker = native_watch_submission.tracker
    scheduler = service._watch_scheduler
    to_uri = "viking://resources/accepted-native"
    owner = {"account_id": request_context.account_id, "user_id": request_context.user.user_id}
    result = await service._execute_resource_ingestion(
        path="https://example.org/doc",
        to=to_uri,
        ctx=request_context,
        defer_post_processing=True,
        watch_interval=1,
        is_active=is_active,
        task_id="accepted-native",
    )
    watch = await get_task_by_uri(service, to_uri, request_context)
    data = native_watch_submission.queue.enqueue.await_args.args[1]
    message = AddResourceMsg.from_dict(data)
    assert result["task_id"] == message.task_id
    await tracker.register_work(message.task_id, "queue-work", QueueManager.ADD_RESOURCE, **owner)
    await tracker.start_work(message.task_id, "queue-work", **owner)
    data[TASK_WORK_ID_FIELD] = "queue-work"
    service.execute_add_resource_job = AsyncMock(
        return_value={"status": "success", "root_uri": to_uri}
    )
    processor = AddResourceProcessor(service, QueueManager.ADD_RESOURCE, service._viking_fs)
    await asyncio.to_thread(lambda: asyncio.run(processor._process(message, data)))
    service.execute_add_resource_job.assert_awaited_once()
    assert not (await tracker.get(message.task_id, **owner)).is_terminal()
    assert (await scheduler.watch_manager.store.get_state(watch.task_id)).current_run_id == (
        message.task_id
    )

    if work_failed:
        await tracker.mark_work_failed(message.task_id, "queue-work", "Work failed", **owner)
    else:
        await tracker.mark_work_done(message.task_id, "queue-work", **owner)
    await asyncio.wait_for(asyncio.gather(*list(scheduler._background)), timeout=5)
    state = await scheduler.watch_manager.store.get_state(watch.task_id)
    assert state.current_run_id is None
    assert state.task.last_status == ("failed" if work_failed else "completed")
    assert state.task.last_error == ("Work failed" if work_failed else None)
    assert state.task.is_active is is_active
    assert (state.task.next_execution_time is not None) is is_active


@pytest.mark.asyncio
async def test_native_worker_callback_allows_shutdown_with_unfinished_work(
    native_watch_submission, request_context
):
    """A serial queue loop may pause between processing an item and stopping."""
    import threading

    service = native_watch_submission.service
    tracker = native_watch_submission.tracker
    scheduler = service._watch_scheduler
    owner = {"account_id": request_context.account_id, "user_id": request_context.user.user_id}
    await service._execute_resource_ingestion(
        path="https://example.org/doc",
        to="viking://resources/shutdown-native",
        ctx=request_context,
        defer_post_processing=True,
        watch_interval=1,
        task_id="shutdown-native",
    )
    data = native_watch_submission.queue.enqueue.await_args.args[1]
    message = AddResourceMsg.from_dict(data)
    await tracker.register_work(message.task_id, "queue-work", QueueManager.ADD_RESOURCE, **owner)
    await tracker.start_work(message.task_id, "queue-work", **owner)
    data[TASK_WORK_ID_FIELD] = "queue-work"
    service.execute_add_resource_job = AsyncMock(return_value={"status": "success"})
    processor = AddResourceProcessor(service, QueueManager.ADD_RESOURCE, service._viking_fs)
    worker_finished = asyncio.Event()
    release_worker = threading.Event()
    service_loop = asyncio.get_running_loop()

    def consume():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(processor._process(message, data))
            service_loop.call_soon_threadsafe(worker_finished.set)
            assert release_worker.wait(5)
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            asyncio.set_event_loop(None)

    worker = asyncio.create_task(asyncio.to_thread(consume))
    try:
        await asyncio.wait_for(worker_finished.wait(), timeout=5)
        assert not (await tracker.get(message.task_id, **owner)).is_terminal()
        await asyncio.wait_for(scheduler.stop(), timeout=2)
        assert not scheduler.is_running
    finally:
        release_worker.set()
        await worker


@pytest.mark.asyncio
async def test_first_native_watch_preserves_failed_task_completion(
    monkeypatch, native_watch_submission, request_context
):
    service = native_watch_submission.service
    tracker = native_watch_submission.tracker
    to_uri = "viking://resources/failed-completion"
    await service._execute_resource_ingestion(
        path="https://example.org/doc",
        to=to_uri,
        ctx=request_context,
        defer_post_processing=True,
        watch_interval=1,
        task_id="failed-completion",
    )
    watch = await get_task_by_uri(service, to_uri, request_context)
    data = native_watch_submission.queue.enqueue.await_args.args[1]
    message = AddResourceMsg.from_dict(data)
    service.execute_add_resource_job = AsyncMock(
        return_value={"status": "success", "root_uri": to_uri}
    )
    monkeypatch.setattr(
        tracker, "complete", AsyncMock(side_effect=ConnectionError("Task completion unavailable"))
    )
    processor = AddResourceProcessor(service, QueueManager.ADD_RESOURCE, service._viking_fs)
    await processor._process(message, data)
    task = await tracker.get(
        message.task_id,
        account_id=request_context.account_id,
        user_id=request_context.user.user_id,
    )
    state = await service._get_watch_manager().store.get_state(watch.task_id)
    assert task.status is TaskStatus.FAILED
    assert state.current_run_id is None
    assert state.task.last_status == "failed"
    assert state.task.last_error == task.error == "Task completion unavailable"


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
    async def test_same_source_to_different_targets_creates_separate_tasks(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """Only the target decides: one source may be watched into several targets."""
        source = "/test/shared-source"
        first_to = "viking://resources/shared_source_a"
        second_to = "viking://resources/shared_source_b"

        await resource_service.add_resource(
            path=source, ctx=request_context, to=first_to, watch_interval=30.0
        )
        await resource_service.add_resource(
            path=source, ctx=request_context, to=second_to, watch_interval=45.0
        )

        first = await get_task_by_uri(resource_service, first_to, request_context)
        second = await get_task_by_uri(resource_service, second_to, request_context)
        assert first is not None and second is not None
        assert first.task_id != second.task_id
        assert first.path == second.path == source
        assert (first.watch_interval, second.watch_interval) == (30.0, 45.0)
        assert first.is_active and second.is_active

    @pytest.mark.asyncio
    async def test_same_source_conflicts_with_active_task(
        self, resource_service: ResourceService, request_context: RequestContext
    ):
        """An active watch on the target rejects re-adding even the same source.

        The kernel cannot tell whether two imports are the same source (Connector
        types carry their identity in args), so only the target decides.
        """
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

        with pytest.raises(ConflictError, match="already being monitored"):
            await resource_service.add_resource(
                path=source,
                ctx=request_context,
                to=to_uri,
                reason="Updated reason",
                watch_interval=45.0,
                args={"branch": "release"},
            )

        unchanged = await get_task_by_uri(resource_service, to_uri, request_context)
        assert unchanged is not None
        assert unchanged.task_id == original.task_id
        assert unchanged.reason == "Original reason"
        assert unchanged.watch_interval == 30.0
        assert unchanged.processor_kwargs == {"branch": "main"}
        assert unchanged.is_active is True

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

        assert "already being monitored" in str(exc_info.value)
        assert to_uri in str(exc_info.value)

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None

        assert task.task_id not in str(exc_info.value)

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

        watch_manager = resource_service._watch_scheduler.watch_manager
        await watch_manager.update_task(
            task_id=task_id,
            account_id=request_context.account_id,
            user_id=request_context.user.user_id,
            role=str(request_context.role),
            is_active=False,
        )

        # A paused watch still owns its target: re-adding conflicts, and the watch
        # is reactivated through the watch itself.
        with pytest.raises(ConflictError, match="already being monitored"):
            await resource_service.add_resource(
                path="/test/path2",
                ctx=request_context,
                to=to_uri,
                reason="Updated reason",
                watch_interval=45.0,
            )

        await watch_manager.update_task(
            task_id=task_id,
            account_id=request_context.account_id,
            user_id=request_context.user.user_id,
            role=str(request_context.role),
            reason="Updated reason",
            watch_interval=45.0,
            is_active=True,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.task_id == task_id
        assert task.path == "/test/path1"
        assert task.reason == "Updated reason"
        assert task.watch_interval == 45.0
        assert task.is_active is True


class TestWatchTaskCancellation:
    """Tests for watch task cancellation."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("watch_interval", [0, -5])
    async def test_native_cancellation_reports_shared_target_conflict(
        self, resource_service: ResourceService, request_context: RequestContext, watch_interval
    ):
        manager = resource_service._watch_scheduler.watch_manager
        to_uri = "viking://resources/shared"
        tasks = [
            await manager.create_task(
                path=f"tos://bucket/{index}/",
                to_uri=to_uri,
                account_id=request_context.account_id,
                user_id=request_context.user.user_id,
                auth_state={"provider": "connector_encrypted", "ciphertext": "unused"},
            )
            for index in range(2)
        ]

        with pytest.raises(ConflictError, match="address one by task_id") as exc_info:
            await resource_service.add_resource(
                path="/test/path",
                ctx=request_context,
                to=to_uri,
                watch_interval=watch_interval,
            )

        assert all(task.is_active for task in tasks)
        assert all(task.task_id in str(exc_info.value) for task in tasks)
        resource_service._enqueue_add_resource_job.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("watch_interval", [0, -5])
    async def test_non_positive_interval_pauses_an_existing_watch(
        self, resource_service: ResourceService, request_context: RequestContext, watch_interval
    ):
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
            watch_interval=watch_interval,
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

        watch_manager = resource_service._watch_scheduler.watch_manager
        await watch_manager.update_task(
            task_id=task.task_id,
            account_id=request_context.account_id,
            user_id=request_context.user.user_id,
            role=str(request_context.role),
            is_active=False,
        )

        # Parameters change through the watch, never by re-adding to its target.
        with pytest.raises(ConflictError, match="already being monitored"):
            await resource_service.add_resource(
                path="/test/path2",
                ctx=request_context,
                to=to_uri,
                reason="Updated reason",
                instruction="Updated instruction",
                watch_interval=60.0,
            )

        await watch_manager.update_task(
            task_id=original_task_id,
            account_id=request_context.account_id,
            user_id=request_context.user.user_id,
            role=str(request_context.role),
            reason="Updated reason",
            instruction="Updated instruction",
            watch_interval=60.0,
            is_active=True,
        )

        task = await get_task_by_uri(resource_service, to_uri, request_context)
        assert task is not None
        assert task.task_id == original_task_id
        assert task.path == "/test/path1"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("encrypted", [False, True])
async def test_external_feishu_auth_survives_queue_and_multiple_watches(
    monkeypatch, resource_service, request_context, encrypted, watch_vfs
):
    from openviking.connector import auth
    from openviking.connector.client import ConnectorClient
    from openviking_cli.exceptions import InternalError
    from openviking_cli.utils.config import open_viking_config as config_module

    config = config_module.ConnectorConfig(auth="https://connector.example/oauth/access_token")
    monkeypatch.setattr(
        config_module, "get_openviking_config", lambda: SimpleNamespace(connector=config)
    )
    request_context.api_key = "external-api-key"
    if encrypted:
        resource_service._viking_fs._encryptor = SimpleNamespace(
            encrypt=AsyncMock(side_effect=lambda _account, data: b"OVE1" + data[::-1]),
            decrypt=AsyncMock(side_effect=lambda _account, data: data[4:][::-1]),
        )
    worker_context = RequestContext(user=request_context.user, role=request_context.role)
    reference = {
        "account_id": "cloud-account",
        "user_id": "cloud-user",
        "ov_user_id": request_context.user.user_id,
        "platform": "feishu_doc",
        "type": "oauth",
    }
    local_refresh = AsyncMock(
        side_effect=AssertionError("external watches must not refresh locally")
    )
    monkeypatch.setattr(
        "openviking.resource.feishu_watch_auth.FeishuOAuthClient.refresh_user_access_token",
        local_refresh,
    )
    fetched = []

    def fetch(_client, _url, api_key, ref):
        assert api_key == "external-api-key"
        assert ref == reference
        token = f"external-token-{len(fetched)}"
        fetched.append(token)
        return {"access_token": token, "expires_at": 4102444800}

    monkeypatch.setattr(ConnectorClient, "get_oauth_access_token", fetch)

    async def preflight(_self, _source, *, feishu_access_token=None, **kwargs):
        assert feishu_access_token == fetched[-1]
        assert kwargs["feishu_config"].domain == "https://open.feishu.cn"
        assert auth.current_feishu_token.get() is not None
        return SimpleNamespace(source_name=None, source_format="file")

    monkeypatch.setattr(
        "openviking.parse.accessors.feishu_accessor.FeishuAccessor.preflight_source", preflight
    )
    tasks = []
    for index in range(2):
        to = f"viking://resources/external-{index}"
        resource_service._plan_source_job_target = AsyncMock(return_value=(to, None, False, False))
        await resource_service.add_resource(
            path=f"https://example.feishu.cn/docx/doc-{index}",
            ctx=request_context,
            to=to,
            watch_interval=30,
            args={auth.OAUTH_REF_ARG: reference},
        )
        assert len(fetched) == 2 * index + 1
        call = resource_service._enqueue_add_resource_job.await_args
        message, state = call.args[0], call.kwargs["task_auth"]
        assert auth.is_external_feishu_auth(state)
        assert "external-api-key" not in str(message.to_dict())
        assert "external-token" not in str(message.to_dict())
        assert "external-token" not in str(state)
        assert "refresh_token" not in str(state)
        if encrypted:
            assert "external-api-key" not in str(state)
        await resource_service.execute_add_resource_job(
            message,
            ctx=worker_context,
            resource_lock=None,
            stage_callback=AsyncMock(),
            task_auth=state,
        )
        assert len(fetched) == 2 * index + 2
        assert resource_service._resource_processor.calls[-1]["feishu_access_token"] == fetched[-1]
        task = await get_task_by_uri(resource_service, to, request_context)
        assert task.auth_state == state
        assert task.processor_kwargs == {}
        assert "external-api-key" not in str(task.to_dict())
        tasks.append(task)
    assert auth.current_feishu_token.get() is None

    await run_watch_now(monkeypatch, resource_service, watch_vfs, tasks[0])
    assert len(fetched) == 5
    next_call = resource_service._enqueue_add_resource_job.await_args
    assert auth.is_external_feishu_auth(next_call.kwargs["task_auth"])
    assert next_call.args[0].skip_watch_management is True

    monkeypatch.setattr(
        ConnectorClient, "get_oauth_access_token", Mock(side_effect=InternalError("unavailable"))
    )
    await run_watch_now(monkeypatch, resource_service, watch_vfs, tasks[0])
    task = await get_task_by_uri(resource_service, tasks[0].to_uri, request_context)
    assert task.is_active is True
    assert task.last_status == "failed"
    local_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_feishu_requires_endpoint(monkeypatch, resource_service, request_context):
    from openviking.connector import auth

    reference = {
        "account_id": "account",
        "user_id": "user",
        "ov_user_id": request_context.user.user_id,
        "platform": "feishu_doc",
        "type": "oauth",
    }
    monkeypatch.setattr(auth, "external_auth_url", lambda: "")
    with pytest.raises(InvalidArgumentError, match="connector.auth is required"):
        await resource_service.add_resource(
            path="https://example.feishu.cn/docx/doc",
            ctx=request_context,
            to="viking://resources/test",
            args={auth.OAUTH_REF_ARG: reference},
        )


@pytest.mark.asyncio
async def test_processing_error_creates_no_watch(resource_service, request_context):
    manager = resource_service._get_watch_manager()
    resource_service._resource_processor.process_resource = AsyncMock(
        return_value={"status": "error", "error": "source failed"},
    )
    result = await resource_service._execute_resource_ingestion(
        path="https://example.org/test",
        to="viking://resources/test",
        ctx=request_context,
        defer_post_processing=True,
        watch_interval=1,
        task_id="first-run",
    )
    assert result["status"] == "error"
    assert (
        await manager.get_all_tasks(
            request_context.account_id, request_context.user.user_id, str(request_context.role)
        )
        == []
    )
    resource_service._enqueue_add_resource_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("committed_task", [False, True])
async def test_enqueue_failure_uses_task_as_authority(
    monkeypatch,
    resource_service,
    request_context,
    committed_task,
):
    manager = resource_service._get_watch_manager()
    resource_service._enqueue_add_resource_job = AsyncMock(side_effect=RuntimeError("queue down"))
    tracker = SimpleNamespace(get=AsyncMock(return_value=object() if committed_task else None))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    with pytest.raises(RuntimeError, match="queue down"):
        await resource_service._execute_resource_ingestion(
            path="https://example.org/test",
            to="viking://resources/test",
            ctx=request_context,
            defer_post_processing=True,
            watch_interval=1,
            task_id="first-run",
        )
    watch = await get_task_by_uri(resource_service, "viking://resources/test", request_context)
    state = await manager.store.get_state(watch.task_id)
    assert state.current_run_id == ("first-run" if committed_task else None)
    if not committed_task:
        assert watch.last_status == "failed"
        assert watch.last_error == "queue down"
