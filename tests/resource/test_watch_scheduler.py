import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.resource.uri_mutation_coordinator import UriMutationCoordinator
from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_scheduler import WatchScheduler
from openviking.server.identity import Role
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

    def test_task_timeout_must_be_positive(self):
        rs = ResourceService()
        with pytest.raises(ValueError, match="task_timeout must be > 0"):
            WatchScheduler(resource_service=rs, task_timeout=0)


class TestWatchSchedulerExecutionHold:
    @pytest.mark.asyncio
    async def test_held_due_task_is_skipped_until_released(self):
        class FakeResourceService(ResourceService):
            def __init__(self):
                super().__init__()
                self.calls = []

            async def refresh_resource(self, **kwargs):
                self.calls.append(kwargs)
                return {"root_uri": kwargs.get("to")}

        resource_service = FakeResourceService()
        scheduler = WatchScheduler(resource_service=resource_service, check_interval=1)
        manager = WatchManager(viking_fs=None)
        await manager.initialize()
        scheduler._watch_manager = manager
        task = await manager.create_task(
            path="https://example.com/doc",
            to_uri="viking://resources/doc",
            watch_interval=5,
        )
        task.next_execution_time = datetime.now() - timedelta(minutes=1)

        assert await scheduler.hold_execution(task.task_id) is True
        await scheduler._check_and_execute_due_tasks()
        assert resource_service.calls == []

        await scheduler.release_execution(task.task_id)
        await scheduler._check_and_execute_due_tasks()
        await asyncio.gather(*list(scheduler._execution_tasks))
        assert len(resource_service.calls) == 1
        assert scheduler._executing_tasks == set()

    @pytest.mark.asyncio
    async def test_stuck_ingestion_does_not_block_later_scheduler_passes(self, monkeypatch):
        second_started = asyncio.Event()
        cancelled = asyncio.Event()

        class FakeResourceService(ResourceService):
            async def refresh_resource(self, **kwargs):
                if kwargs["path"].endswith("stuck"):
                    return {"status": "success", "task_id": "stuck-ingestion"}
                second_started.set()
                return {"status": "completed"}

        class FakeTracker:
            async def wait(self, *_args, timeout=None, **_kwargs):
                await asyncio.wait_for(asyncio.Event().wait(), timeout=timeout)

            async def cancel(self, *_args, **_kwargs):
                cancelled.set()

        monkeypatch.setattr(
            "openviking.service.task_tracker.get_task_tracker",
            lambda: FakeTracker(),
        )
        scheduler = WatchScheduler(
            resource_service=FakeResourceService(),
            check_interval=1,
            max_concurrency=1,
            task_timeout=0.01,
        )
        manager = WatchManager(viking_fs=None)
        await manager.initialize()
        scheduler._watch_manager = manager

        stuck = await manager.create_task(
            path="https://example.com/stuck",
            to_uri="viking://resources/stuck",
            watch_interval=5,
        )
        stuck.next_execution_time = datetime.now() - timedelta(minutes=1)
        await asyncio.wait_for(scheduler._check_and_execute_due_tasks(), timeout=1)
        await asyncio.sleep(0)

        later = await manager.create_task(
            path="https://example.com/later",
            to_uri="viking://resources/later",
            watch_interval=5,
        )
        later.next_execution_time = datetime.now() - timedelta(minutes=1)
        await asyncio.wait_for(scheduler._check_and_execute_due_tasks(), timeout=1)
        await asyncio.wait_for(second_started.wait(), timeout=1)
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        await asyncio.gather(*list(scheduler._execution_tasks))

        updated = await manager.get_task(stuck.task_id)
        assert updated is not None
        assert updated.last_status == "failed"
        assert updated.last_error == "ingestion task timed out after 0.01s"


class TestWatchSchedulerResourceExistence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "source,failed_token,code,http_status,recursive,deactivate",
        [
            ("https://team.feishu.cn/wiki/root", "root", 131005, 400, False, True),
            ("https://team.larkoffice.com/wiki/root", "root", 131005, 400, True, True),
            ("https://team.feishu.cn/wiki/root", "root", 131006, 400, True, False),
            ("https://team.feishu.cn/wiki/root", "root", 999, 500, True, False),
            ("https://team.feishu.cn/wiki/root", "root", 999, 404, True, False),
            ("https://team.feishu.cn/wiki/root", "child", 131005, 400, True, False),
            ("https://team.feishu.cn/docx/root", "root", 131005, 400, False, False),
            ("https://example.com/wiki/root", "root", 131005, 400, False, False),
        ],
        ids=[
            "deleted",
            "deleted-recursive",
            "permission",
            "transient",
            "http404",
            "child",
            "document",
            "other-host",
        ],
    )
    async def test_feishu_deleted_root_watch_stays_inactive_after_reload(
        self, monkeypatch, source, failed_token, code, http_status, recursive, deactivate
    ):
        from openviking.parse.accessors.feishu_accessor import FeishuAccessor
        from openviking_cli.exceptions import NotFoundError

        accessor = FeishuAccessor()

        def get_node(request):
            assert request.token == failed_token
            return SimpleNamespace(
                code=code,
                msg="provider error",
                raw=SimpleNamespace(status_code=http_status),
                success=lambda: False,
            )

        client = SimpleNamespace(
            wiki=SimpleNamespace(v2=SimpleNamespace(space=SimpleNamespace(get_node=get_node)))
        )
        monkeypatch.setattr(accessor, "_get_client", lambda **kwargs: client)

        class FakeResourceService(ResourceService):
            async def refresh_resource(self, *args, **kwargs):
                if failed_token == "root" and "/wiki/" in source:
                    return await accessor.preflight_source(
                        kwargs["path"], feishu_recursive=recursive
                    )
                # A child disappearing must not deactivate the root watch.
                return accessor._fetch_wiki_node(failed_token)

        class FakeVikingFS:
            def __init__(self):
                self.files: dict[str, str] = {}

            async def read_file(self, uri, ctx=None):
                if uri not in self.files:
                    raise NotFoundError(uri)
                return self.files[uri]

            async def write_file(self, uri, content, ctx=None):
                self.files[uri] = content

        storage = FakeVikingFS()
        manager = WatchManager(viking_fs=storage)
        await manager.initialize()
        scheduler = WatchScheduler(resource_service=FakeResourceService(), check_interval=1)
        scheduler._watch_manager = manager
        task = await manager.create_task(
            path=source, to_uri="viking://resources/wiki", watch_interval=60
        )

        await scheduler._execute_task(task)

        reloaded = WatchManager(viking_fs=storage)
        await reloaded.initialize()
        updated = await reloaded.get_task(task.task_id)
        assert updated is not None
        assert updated.is_active is not deactivate
        assert updated.last_status == "failed"
        assert updated.last_error is not None
        assert f"code={code}" in updated.last_error
        assert (updated.next_execution_time is None) is deactivate
        updated.next_execution_time = datetime.now() - timedelta(minutes=1)
        assert bool(await reloaded.get_due_tasks()) is not deactivate

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
            async def stat(self, uri, ctx=None, skip_count=False):
                assert skip_count is True
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
        assert updated.last_status == "failed"
        assert updated.last_error == f"Watched target URI does not exist: {task.to_uri}"
        assert resource_service.calls == []

    @pytest.mark.asyncio
    async def test_target_uri_check_error_does_not_deactivate_task(self, tmp_path):
        class FakeVikingFS:
            async def stat(self, uri, ctx=None, skip_count=False):
                assert skip_count is True
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
        assert updated.last_status == "completed"
        assert updated.last_execution_time is not None

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
        assert resource_service.calls[0]["ctx"].role == Role.USER
        assert resource_service.calls[0]["ctx"].bypass_acl is True
        assert "connector_states" not in resource_service.calls[0]

    @pytest.mark.asyncio
    async def test_connector_failure_is_recorded_on_watch(self):
        class FakeResourceService(ResourceService):
            async def refresh_resource(self, **kwargs):
                return {
                    "status": "failed",
                    "task_id": "connector-task-1",
                    "error": "connector task failed: TOS pull failed",
                }

        resource_service = FakeResourceService()
        scheduler = WatchScheduler(resource_service=resource_service, check_interval=1)
        manager = WatchManager(viking_fs=None)
        await manager.initialize()
        scheduler._watch_manager = manager
        task = await manager.create_task(
            path="tos://bucket/docs/",
            to_uri="viking://resources/imports",
            watch_interval=30.0,
            auth_state={"provider": "connector_encrypted", "ciphertext": "encrypted"},
        )
        resource_service._connector.restore_watch_request = AsyncMock(
            return_value=("api-key", "tos", {})
        )

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert updated is not None
        assert updated.last_task_id == "connector-task-1"
        assert updated.last_status == "failed"
        assert updated.last_error == "connector task failed: TOS pull failed"

    @pytest.mark.asyncio
    async def test_native_import_failure_is_recorded_on_watch(self, tmp_path, monkeypatch):
        class FakeResourceService(ResourceService):
            async def refresh_resource(self, **kwargs):
                return {"status": "success", "task_id": "add-resource-task-1"}

        class FailedStatus:
            value = "failed"

        class FailedTask:
            status = FailedStatus()
            error = "document import failed"

        tracker = AsyncMock()
        tracker.wait.return_value = FailedTask()
        monkeypatch.setattr(
            "openviking.service.task_tracker.get_task_tracker",
            lambda: tracker,
        )
        source = tmp_path / "source.txt"
        source.write_text("ok")
        scheduler = WatchScheduler(resource_service=FakeResourceService(), check_interval=1)
        manager = WatchManager(viking_fs=None)
        await manager.initialize()
        scheduler._watch_manager = manager
        task = await manager.create_task(
            path=str(source),
            to_uri="viking://resources/imports/source.txt",
            watch_interval=30.0,
        )

        await scheduler._execute_task(task)

        tracker.wait.assert_awaited_once_with(
            "add-resource-task-1",
            account_id=task.account_id,
            user_id=task.user_id,
            timeout=10800,
        )
        updated = await manager.get_task(task.task_id)
        assert updated is not None
        assert updated.last_task_id == "add-resource-task-1"
        assert updated.last_status == "failed"
        assert updated.last_error == "document import failed"
