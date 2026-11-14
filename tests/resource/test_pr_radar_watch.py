# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.resource.watch_types import WatchRun, WatchTask
from openviking_cli.exceptions import InvalidArgumentError


@pytest.mark.parametrize("unchanged", [True, False])
@pytest.mark.parametrize("radar_fails", [True, False])
async def test_watch_runs_radar_before_head_gate(
    monkeypatch, watch_manager, unchanged, radar_fails
):
    from openviking.parse.accessors.git_accessor import GitAccessor

    sha = "a" * 40
    radar = AsyncMock(side_effect=OSError("Radar failed") if radar_fails else None)
    monkeypatch.setattr("openviking.resource.pr_radar_runner.run_radar", radar)
    probe = AsyncMock(return_value=("https://github.com/a/b", sha))
    monkeypatch.setattr(GitAccessor, "probe_git_remote_head", probe)
    source = SimpleNamespace(
        _resource_processor=SimpleNamespace(github_token_for=AsyncMock(return_value=None)),
        refresh_resource=AsyncMock(return_value={"status": "completed"}),
    )
    watch_manager._resource_service = source
    watch = WatchTask(
        path="https://github.com/a/b",
        to_uri="viking://resources/b",
        processor_kwargs={"pr_radar": {"enabled": True, "digest_interval_minutes": 30}},
        source_checkpoint={
            "kind": "git",
            "head_sha": sha if unchanged else "b" * 40,
            "instruction": "",
        },
    )
    await watch_manager._submit_resource(WatchRun(watch.task_id, None, watch), AsyncMock())
    radar.assert_awaited_once_with(watch, watch_manager._viking_fs, source._resource_processor)
    probe.assert_awaited_once()
    if unchanged:
        source.refresh_resource.assert_not_awaited()
    else:
        source.refresh_resource.assert_awaited_once()
        assert "pr_radar" not in source.refresh_resource.call_args.kwargs
    assert "pr_radar" in watch.processor_kwargs


async def test_watch_create_rejects_radar_before_persistence(watch_manager):
    with pytest.raises(InvalidArgumentError, match="digest_interval_minutes"):
        await watch_manager.create_task(
            path="https://github.com/a/b",
            to_uri="viking://resources/b",
            processor_kwargs={"pr_radar": {"enabled": True}},
        )


async def test_resource_creation_rejects_radar_without_watch():
    from openviking.server.identity import RequestContext, Role
    from openviking.service.resource_service import ResourceService
    from openviking_cli.session.user_id import UserIdentifier

    service = ResourceService.__new__(ResourceService)
    service._resource_processor = object()
    service._skill_processor = object()
    service._viking_fs = object()
    service._runtime_config_manager = None
    ctx = RequestContext(user=UserIdentifier("tenant", "alice"), role=Role.USER)
    with pytest.raises(InvalidArgumentError, match="native GitHub Watch"):
        await service.add_resource(
            path="https://github.com/a/b",
            ctx=ctx,
            watch_interval=0,
            args={"pr_radar": {"enabled": True, "digest_interval_minutes": 30}},
        )
