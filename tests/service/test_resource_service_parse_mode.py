# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""ResourceService coverage for add_resource parse modes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service import resource_service as resource_service_module
from openviking.service.resource_service import ResourceService
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


class _ResourceProcessor:
    def __init__(self):
        self.calls = []

    async def process_resource(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "success",
            "root_uri": "viking://resources/test",
            "_post_process": {},
        }


class _DiscardedBackgroundTask:
    def add_done_callback(self, callback):
        callback(self)


@pytest.fixture
def ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("test_account", "test_user"),
        role=Role.USER,
    )


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> ResourceService:
    tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
        fail=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: False)

    def discard_background(coro):
        coro.close()
        return _DiscardedBackgroundTask()

    monkeypatch.setattr(resource_service_module.asyncio, "create_task", discard_background)

    instance = ResourceService(
        vikingdb=object(),
        viking_fs=object(),
        resource_processor=_ResourceProcessor(),
        skill_processor=object(),
    )
    monkeypatch.setattr(instance._connector, "should_delegate", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        instance,
        "_enqueue_add_resource_job",
        AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
    )
    return instance


@pytest.mark.asyncio
async def test_no_split_is_forwarded_and_persisted_for_watch_replay(
    service: ResourceService,
    ctx: RequestContext,
):
    watch_manager = SimpleNamespace(
        get_task_by_uri=AsyncMock(return_value=None),
        create_task=AsyncMock(return_value=SimpleNamespace(task_id="watch-1")),
    )
    scheduler = SimpleNamespace(watch_manager=watch_manager)
    service._watch_scheduler = scheduler

    await service.add_resource(
        path="/test/path",
        ctx=ctx,
        to="viking://resources/no_split_watch",
        watch_interval=30.0,
        parse_mode="no_split",
    )

    processor = service._resource_processor
    assert processor.calls[-1]["parse_mode"] == "no_split"
    assert watch_manager.create_task.await_args.kwargs["processor_kwargs"]["parse_mode"] == (
        "no_split"
    )


@pytest.mark.asyncio
async def test_rejects_parse_mode_inside_args(service: ResourceService, ctx: RequestContext):
    with pytest.raises(InvalidArgumentError, match="parse_mode"):
        await service.add_resource(
            path="/test/path",
            ctx=ctx,
            to="viking://resources/test",
            args={"parse_mode": "no_split"},
        )


@pytest.mark.asyncio
async def test_rejects_invalid_parse_mode(service: ResourceService, ctx: RequestContext):
    with pytest.raises(InvalidArgumentError, match="parse_mode"):
        await service.add_resource(
            path="/test/path",
            ctx=ctx,
            to="viking://resources/test",
            parse_mode="unsupported",
        )


@pytest.mark.asyncio
async def test_no_split_allows_directory_flattening(
    service: ResourceService,
    ctx: RequestContext,
):
    await service.add_resource(
        path="/test/path",
        ctx=ctx,
        to="viking://resources/test",
        parse_mode="no_split",
        preserve_structure=False,
    )

    processor = service._resource_processor
    assert processor.calls[-1]["parse_mode"] == "no_split"
    assert processor.calls[-1]["preserve_structure"] is False


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

    await service.add_resource(
        path="https://example.com/manual.pdf",
        ctx=ctx,
        to="viking://resources/manual",
        parse_mode="no_split",
        allow_local_path_resolution=False,
    )

    direct_probe.assert_not_called()
    api_probe.assert_not_called()
