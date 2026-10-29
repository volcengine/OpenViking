import asyncio
import threading
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

        # A manual execution must also settle before account cleanup proceeds.
        started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def blocked_refresh(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                raise

        resource_service.refresh_resource = blocked_refresh
        execution = asyncio.create_task(scheduler.schedule_task(task.task_id))
        await asyncio.wait_for(started.wait(), 1)
        other = await manager.create_task(
            path="https://example.com/other", to_uri="viking://resources/other",
            account_id="other", user_id="user", watch_interval=5,
        )
        deletion = asyncio.create_task(scheduler.delete_tasks(task.account_id))
        await asyncio.wait_for(cancelled.wait(), 1)
        assert not deletion.done()
        release.set()
        await asyncio.wait_for(deletion, 1)
        await asyncio.gather(execution, return_exceptions=True)
        assert await manager.get_task(task.task_id) is None
        assert await manager.get_task(other.task_id) is not None
        assert scheduler.executing_tasks == set()

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

        started = threading.Event()

        async def execute_from_worker():
            started.set()
            await scheduler._execute_task(task.model_copy(deep=True))

        async with coordinator.mutation(task.account_id, [old_uri, new_uri]):
            execution = asyncio.create_task(
                asyncio.to_thread(lambda: asyncio.run(execute_from_worker()))
            )
            assert await asyncio.to_thread(started.wait, 3)
            await asyncio.sleep(0.01)
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
            return_value=("api-key", "tos", {}, None)
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


class TestWatchSchedulerGitShaGate:
    REPO = "https://github.com/volcengine/OpenViking.git"

    def _build_scheduler(self, monkeypatch, probe):
        monkeypatch.setattr(
            "openviking.parse.accessors.git_accessor.GitAccessor.probe_git_remote_head",
            probe,
        )

        class FakeResourceService(ResourceService):
            def __init__(self):
                super().__init__()
                self.calls = []

            async def refresh_resource(self, **kwargs):
                self.calls.append(kwargs)
                return {"root_uri": kwargs.get("to"), "status": "completed"}

        resource_service = FakeResourceService()
        scheduler = WatchScheduler(resource_service=resource_service, check_interval=1)
        return scheduler, resource_service

    async def _setup_task(
        self,
        scheduler,
        *,
        source_checkpoint=None,
        path=None,
        processor_kwargs=None,
        instruction="",
    ):
        manager = WatchManager(viking_fs=None)
        await manager.initialize()
        scheduler._watch_manager = manager
        task = await manager.create_task(
            path=path or self.REPO,
            to_uri="viking://resources/openviking",
            watch_interval=30.0,
            processor_kwargs=processor_kwargs,
            instruction=instruction,
        )
        if source_checkpoint is not None:
            await manager.update_source_checkpoint(task.task_id, source_checkpoint)
        return manager, task

    @pytest.mark.asyncio
    async def test_unchanged_head_sha_skips_refresh(self, monkeypatch):
        state = {"kind": "git", "head_sha": "a" * 40, "instruction": ""}
        probe = AsyncMock(return_value=("refs/heads/main", "a" * 40))
        scheduler, resource_service = self._build_scheduler(monkeypatch, probe)
        manager, task = await self._setup_task(scheduler, source_checkpoint=state)

        await scheduler._execute_task(task)

        probe.assert_awaited_once()
        assert resource_service.calls == []
        updated = await manager.get_task(task.task_id)
        assert updated.last_status == "completed"
        assert updated.source_checkpoint == state
        assert updated.next_execution_time is not None
        assert updated.next_execution_time > datetime.now()

    @pytest.mark.asyncio
    async def test_unchanged_skip_preserves_last_task_id(self, monkeypatch):
        state = {"kind": "git", "head_sha": "a" * 40, "instruction": ""}
        probe = AsyncMock(return_value=("refs/heads/main", "a" * 40))
        scheduler, resource_service = self._build_scheduler(monkeypatch, probe)
        manager, task = await self._setup_task(scheduler, source_checkpoint=state)
        await manager.record_execution(
            task.task_id,
            status="completed",
            execution_task_id="ingest-task-1",
        )

        await scheduler._execute_task(task)

        assert resource_service.calls == []
        updated = await manager.get_task(task.task_id)
        assert updated.last_status == "completed"
        assert updated.last_task_id == "ingest-task-1"

    @pytest.mark.asyncio
    async def test_commit_pin_uses_commit_checkpoint_without_refresh(self, monkeypatch):
        commit = "a" * 40
        probe = AsyncMock(return_value=("", commit))
        scheduler, resource_service = self._build_scheduler(monkeypatch, probe)
        manager, task = await self._setup_task(
            scheduler,
            source_checkpoint={"kind": "git", "head_sha": commit, "instruction": ""},
            processor_kwargs={"commit": commit},
        )

        await scheduler._execute_task(task)

        probe.assert_awaited_once()
        assert resource_service.calls == []
        updated = await manager.get_task(task.task_id)
        assert updated.last_status == "completed"
        assert updated.source_checkpoint == {
            "kind": "git",
            "head_sha": commit,
            "instruction": "",
        }

    @pytest.mark.asyncio
    async def test_account_github_token_is_used_for_probe_without_persisting(self, monkeypatch):
        state = {"kind": "git", "head_sha": "a" * 40, "instruction": ""}
        probe = AsyncMock(return_value=("refs/heads/main", "a" * 40))
        scheduler, resource_service = self._build_scheduler(monkeypatch, probe)
        token_resolver = AsyncMock(return_value="account-token")
        resource_service._resource_processor = SimpleNamespace(
            github_token_for=token_resolver,
        )
        manager, task = await self._setup_task(scheduler, source_checkpoint=state)

        await scheduler._execute_task(task)

        token_resolver.assert_awaited_once()
        probe.assert_awaited_once_with(
            self.REPO,
            auth_config={"token": "account-token"},
        )
        assert resource_service.calls == []
        updated = await manager.get_task(task.task_id)
        assert updated.source_checkpoint == state
        assert "auth_config" not in updated.processor_kwargs

    @pytest.mark.asyncio
    async def test_changed_instruction_triggers_refresh(self, monkeypatch):
        sha = "a" * 40
        probe = AsyncMock(return_value=("refs/heads/main", sha))
        scheduler, resource_service = self._build_scheduler(monkeypatch, probe)
        manager, task = await self._setup_task(
            scheduler,
            source_checkpoint={
                "kind": "git",
                "head_sha": sha,
                "instruction": "old instruction",
            },
            instruction="new instruction",
        )

        await scheduler._execute_task(task)

        assert len(resource_service.calls) == 1
        assert resource_service.calls[0]["instruction"] == "new instruction"
        updated = await manager.get_task(task.task_id)
        assert updated.last_status == "completed"

    @pytest.mark.asyncio
    async def test_probe_timeout_fails_open_to_refresh(self, monkeypatch):
        async def blocked_probe(*_args, **_kwargs):
            await asyncio.Event().wait()

        scheduler, resource_service = self._build_scheduler(monkeypatch, blocked_probe)
        monkeypatch.setattr(scheduler, "DEFAULT_GIT_PROBE_TIMEOUT", 0.01)
        manager, task = await self._setup_task(scheduler)

        await asyncio.wait_for(scheduler._execute_task(task), timeout=1.0)

        assert len(resource_service.calls) == 1
        updated = await manager.get_task(task.task_id)
        assert updated.last_status == "completed"

    @pytest.mark.asyncio
    async def test_changed_head_sha_triggers_refresh(self, monkeypatch):
        probe = AsyncMock(return_value=("refs/heads/main", "b" * 40))
        scheduler, resource_service = self._build_scheduler(monkeypatch, probe)
        manager, task = await self._setup_task(scheduler)

        await scheduler._execute_task(task)

        assert len(resource_service.calls) == 1
        updated = await manager.get_task(task.task_id)
        assert updated.source_checkpoint is None
        assert updated.last_status == "completed"

    @pytest.mark.asyncio
    async def test_async_refresh_uses_completed_result_checkpoint(self, monkeypatch):
        probe = AsyncMock(return_value=("refs/heads/main", "b" * 40))
        monkeypatch.setattr(
            "openviking.parse.accessors.git_accessor.GitAccessor.probe_git_remote_head",
            probe,
        )

        class FakeResourceService(ResourceService):
            async def refresh_resource(self, **_kwargs):
                return {"status": "success", "task_id": "refresh-task-1"}

        class CompletedStatus:
            value = "completed"

        class CompletedTask:
            status = CompletedStatus()
            error = None
            result = {"meta": {"resolved_head_sha": "b" * 40}}

        tracker = AsyncMock()
        tracker.wait.return_value = CompletedTask()
        monkeypatch.setattr(
            "openviking.service.task_tracker.get_task_tracker",
            lambda: tracker,
        )
        scheduler = WatchScheduler(
            resource_service=FakeResourceService(),
            check_interval=1,
        )
        manager, task = await self._setup_task(
            scheduler,
            source_checkpoint={
                "kind": "git",
                "head_sha": "b" * 40,
                "instruction": "old instruction",
            },
            instruction="new instruction",
        )

        await scheduler._execute_task(task)

        updated = await manager.get_task(task.task_id)
        assert updated.source_checkpoint == {
            "kind": "git",
            "head_sha": "b" * 40,
            "instruction": "new instruction",
        }
        assert updated.last_status == "completed"

    @pytest.mark.asyncio
    async def test_probe_failure_fails_open_to_full_refresh(self, monkeypatch):
        async def failing_probe(*_args, **_kwargs):
            raise RuntimeError("ls-remote boom")

        scheduler, resource_service = self._build_scheduler(monkeypatch, failing_probe)
        manager, task = await self._setup_task(
            scheduler,
            source_checkpoint={"kind": "git", "head_sha": "a" * 40},
        )

        await scheduler._execute_task(task)

        assert len(resource_service.calls) == 1
        updated = await manager.get_task(task.task_id)
        assert updated.last_status == "completed"
        # No fresh checkpoint available: previous state is left untouched.
        assert updated.source_checkpoint["head_sha"] == "a" * 40

    @pytest.mark.asyncio
    async def test_sync_completed_refresh_persists_checkpoint(self, monkeypatch):
        sha = "c" * 40
        probe = AsyncMock(return_value=("refs/heads/main", sha))
        monkeypatch.setattr(
            "openviking.parse.accessors.git_accessor.GitAccessor.probe_git_remote_head",
            probe,
        )

        class SyncResourceService(ResourceService):
            def __init__(self):
                super().__init__()
                self.calls = []

            async def refresh_resource(self, **kwargs):
                self.calls.append(kwargs)
                # No queue manager: the terminal result is returned directly,
                # carrying the imported revision meta without a task_id.
                return {
                    "root_uri": kwargs.get("to"),
                    "status": "completed",
                    "meta": {"resolved_head_sha": sha},
                }

        resource_service = SyncResourceService()
        scheduler = WatchScheduler(resource_service=resource_service, check_interval=1)
        manager, task = await self._setup_task(scheduler)

        await scheduler._execute_task(task)

        assert len(resource_service.calls) == 1
        updated = await manager.get_task(task.task_id)
        assert updated.last_status == "completed"
        assert updated.source_checkpoint == {
            "kind": "git",
            "head_sha": sha,
            "instruction": "",
        }

    @pytest.mark.asyncio
    async def test_non_git_source_does_not_probe(self, monkeypatch):
        probe = AsyncMock(return_value=("refs/heads/main", "a" * 40))
        scheduler, resource_service = self._build_scheduler(monkeypatch, probe)
        manager, task = await self._setup_task(
            scheduler,
            path="https://example.com/doc.md",
        )

        await scheduler._execute_task(task)

        probe.assert_not_awaited()
        assert len(resource_service.calls) == 1
