# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""ResourceService coverage for add_resource parse modes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_types import WatchRunResult
from openviking.server.identity import RequestContext, Role
from openviking.service import resource_service as resource_service_module
from openviking.service.resource_service import ResourceService
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


class _ResourceProcessor:
    def __init__(self):
        self.calls = []
        self.root_is_file = True

    async def process_resource(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "success",
            "root_uri": "viking://resources/test",
            "_post_process": {"root_is_file": self.root_is_file},
        }

    async def prepare_durable_source(self, *_args, **_kwargs):
        return None

    async def github_token_for(self, *_args, **_kwargs):
        return None

    async def finish_prepared_resource(self, *_args, **_kwargs):
        return {"status": "success"}


@pytest.fixture
def ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("test_account", "test_user"),
        role=Role.USER,
    )


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, watch_tracker) -> ResourceService:
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: False)

    instance = ResourceService(
        vikingdb=object(),
        viking_fs=object(),
        resource_processor=_ResourceProcessor(),
        skill_processor=object(),
    )
    monkeypatch.setattr(instance._connector, "should_delegate", lambda *_args, **_kwargs: False)

    async def enqueue(msg, **kwargs):
        task = await watch_tracker.create(
            "add_resource",
            task_id=msg.task_id,
            resource_id=msg.root_uri,
            account_id=msg.account_id,
            user_id=msg.user_id,
        )
        await kwargs["on_task_created"](task)
        return task

    monkeypatch.setattr(instance, "_enqueue_add_resource_job", AsyncMock(side_effect=enqueue))
    monkeypatch.setattr(
        instance,
        "_plan_source_job_target",
        AsyncMock(return_value=("viking://resources/test", None, False, False)),
    )
    return instance


@pytest.mark.asyncio
async def test_no_split_watch_replay_preserves_auto_bound_file_target(
    watch_store,
    watch_vfs,
    service: ResourceService,
    ctx: RequestContext,
):
    watch_manager = WatchManager(watch_store, viking_fs=watch_vfs, resource_service=service)
    service._watch_manager = watch_manager
    service._plan_source_job_target = AsyncMock(
        side_effect=lambda **kwargs: (
            "viking://resources/test",
            None,
            kwargs["defer_candidate_resolution"],
            False,
        )
    )

    result = await service.add_resource(
        path="https://example.com/guide.md",
        ctx=ctx,
        watch_interval=30.0,
        args={"parse_mode": "no_split"},
    )
    assert result["root_uri"] == "viking://resources/test"
    message = service._enqueue_add_resource_job.await_args.args[0]
    assert message.defer_target_resolution is False
    assert message.skip_watch_management and message.watch_task_id

    tasks = await watch_manager.get_all_tasks(
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        role=str(ctx.role),
    )
    assert len(tasks) == 1
    task = tasks[0]
    assert task.to_uri == "viking://resources/test"
    assert task.to_is_directory is False
    assert task.processor_kwargs["parse_mode"] == "no_split"
    assert service._resource_processor.calls[-1]["parse_mode"] == "no_split"

    first_run = await watch_store.get_state(task.task_id)
    await watch_manager.finish_run(
        task.task_id, first_run.current_run_id, WatchRunResult(status="completed")
    )
    await watch_vfs.write_file(task.to_uri, "content", ctx=ctx)
    assert await watch_manager.trigger(task.task_id)

    replay_message = service._enqueue_add_resource_job.await_args.args[0]
    assert replay_message.root_uri == "viking://resources/test"
    assert replay_message.to_is_directory is False


@pytest.mark.asyncio
async def test_no_split_defers_initial_root_when_parent_targeted(
    service: ResourceService,
    ctx: RequestContext,
):
    service._plan_source_job_target = AsyncMock(
        side_effect=lambda **kwargs: (
            "viking://resources/test",
            None,
            kwargs["defer_candidate_resolution"],
            False,
        )
    )

    result = await service.add_resource(
        path="https://example.com/guide.md",
        ctx=ctx,
        parent="viking://resources/docs",
        args={"parse_mode": "no_split"},
    )

    assert "root_uri" not in result
    assert service._plan_source_job_target.await_args.kwargs["defer_candidate_resolution"] is True
    message = service._enqueue_add_resource_job.await_args.args[0]
    assert message.defer_target_resolution is True


@pytest.mark.asyncio
async def test_no_split_watch_persists_auto_bound_multi_artifact_directory(
    watch_manager,
    service: ResourceService,
    ctx: RequestContext,
):
    service._resource_processor.root_is_file = False
    service._watch_manager = watch_manager

    await service.add_resource(
        path="https://example.com/guide.md",
        ctx=ctx,
        watch_interval=30.0,
        args={"parse_mode": "no_split"},
    )
    message = service._enqueue_add_resource_job.await_args.args[0]
    await service.execute_add_resource_job(
        message,
        ctx=ctx,
        resource_lock=None,
        stage_callback=AsyncMock(),
        task_auth={},
    )

    tasks = await watch_manager.get_all_tasks(
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        role=str(ctx.role),
    )
    assert len(tasks) == 1
    assert tasks[0].to_is_directory is True


@pytest.mark.asyncio
async def test_accepts_parse_mode_inside_args_without_mutating_input(
    service: ResourceService,
    ctx: RequestContext,
):
    args = {"parse_mode": "no_split", "site": False}

    await service.add_resource(
        path="/test/path",
        ctx=ctx,
        to="viking://resources/test",
        args=args,
    )

    assert args == {"parse_mode": "no_split", "site": False}
    processor = service._resource_processor
    assert processor.calls[-1]["parse_mode"] == "no_split"
    assert processor.calls[-1]["site"] is False


@pytest.mark.asyncio
async def test_rejects_invalid_parse_mode(service: ResourceService, ctx: RequestContext):
    with pytest.raises(InvalidArgumentError, match="parse_mode"):
        await service.add_resource(
            path="/test/path",
            ctx=ctx,
            to="viking://resources/test",
            args={"parse_mode": "unsupported"},
        )


@pytest.mark.asyncio
async def test_tos_auth_args_require_http_resource_url(
    service: ResourceService,
    ctx: RequestContext,
):
    with pytest.raises(
        InvalidArgumentError, match=r"tos_signature and tos_access are only supported"
    ):
        await service.add_resource(
            path="/test/path",
            ctx=ctx,
            to="viking://resources/test",
            args={"tos_signature": "signed-value"},
        )


@pytest.mark.asyncio
async def test_tos_auth_args_are_mutually_exclusive(
    service: ResourceService,
    ctx: RequestContext,
):
    with pytest.raises(InvalidArgumentError, match="cannot both be provided"):
        await service.add_resource(
            path="https://tos.example.com/object",
            ctx=ctx,
            to="viking://resources/test",
            args={
                "tos_signature": "signed-value",
                "tos_access": "access-key",
            },
        )


@pytest.mark.asyncio
async def test_tos_auth_args_are_snapshotted_and_omitted_from_queue(
    service: ResourceService,
    ctx: RequestContext,
):
    prepare_durable_source = AsyncMock(return_value=None)
    service._resource_processor.prepare_durable_source = prepare_durable_source

    await service.add_resource(
        path="https://tos.example.com/object",
        ctx=ctx,
        to="viking://resources/test",
        args={"tos_signature": "signed-value"},
        allow_local_path_resolution=False,
    )

    assert prepare_durable_source.await_args.kwargs["snapshot_required"] is True
    assert prepare_durable_source.await_args.kwargs["tos_signature"] == "signed-value"
    message = service._enqueue_add_resource_job.await_args.args[0]
    assert "tos_signature" not in message.args
    assert "tos_access" not in message.args


@pytest.mark.asyncio
async def test_no_split_allows_directory_flattening(
    service: ResourceService,
    ctx: RequestContext,
):
    await service.add_resource(
        path="/test/path",
        ctx=ctx,
        to="viking://resources/test",
        args={"parse_mode": "no_split"},
        preserve_structure=False,
    )

    processor = service._resource_processor
    assert processor.calls[-1]["parse_mode"] == "no_split"
    assert processor.calls[-1]["preserve_structure"] is False


@pytest.mark.asyncio
async def test_no_split_treats_explicit_to_as_directory_target(
    service: ResourceService,
    ctx: RequestContext,
):
    await service.add_resource(
        path="/test/神雕.md",
        ctx=ctx,
        to="viking://resources/0803_shendiao_01",
        args={"parse_mode": "no_split"},
    )

    processor = service._resource_processor
    assert processor.calls[-1]["to"] == "viking://resources/0803_shendiao_01"
    assert processor.calls[-1]["to_is_directory"] is True


@pytest.mark.asyncio
async def test_no_split_bypasses_understanding_shortcut(
    monkeypatch: pytest.MonkeyPatch,
    service: ResourceService,
    ctx: RequestContext,
):
    direct_probe = MagicMock(
        side_effect=AssertionError("Understanding shortcut must not run in no_split mode")
    )
    api_probe = MagicMock(
        side_effect=AssertionError("Understanding shortcut must not run in no_split mode")
    )
    monkeypatch.setattr(
        service._resource_processor,
        "should_use_understanding_directly",
        direct_probe,
        raising=False,
    )
    monkeypatch.setattr(
        service._resource_processor,
        "understanding_api_enabled",
        api_probe,
        raising=False,
    )

    result = await service.add_resource(
        path="https://example.com/manual.pdf",
        ctx=ctx,
        to="viking://resources/manual",
        args={"parse_mode": "no_split"},
        allow_local_path_resolution=False,
    )

    assert result["root_uri"] == "viking://resources/test"
    direct_probe.assert_not_called()
    api_probe.assert_not_called()


@pytest.mark.asyncio
async def test_native_git_enqueue_persists_internal_no_split_mode(
    monkeypatch: pytest.MonkeyPatch,
    service: ResourceService,
    ctx: RequestContext,
):
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._preflight_git_source = AsyncMock(
        return_value=SimpleNamespace(
            source_name="repo",
            source_path="https://github.com/volcengine/OpenViking.git",
            source_format="repository",
            head_sha=None,
        )
    )
    service._plan_source_job_target = AsyncMock(
        return_value=("viking://resources/repo", None, False, False)
    )
    service._enqueue_add_resource_job = AsyncMock(return_value=SimpleNamespace(task_id="task-git"))

    result = await service.add_resource(
        path="https://github.com/volcengine/OpenViking.git",
        ctx=ctx,
        to="viking://resources/repo",
        args={"parse_mode": "no_split"},
    )

    assert result["task_id"] == "task-git"
    message = service._enqueue_add_resource_job.await_args.args[0]
    assert isinstance(message, AddResourceMsg)
    assert message.parse_mode == "no_split"
    assert "parser_backend" not in message.args
