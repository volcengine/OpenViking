from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.resource.watch_manager import WatchManager
from openviking.service.resource_service import ResourceService, _ResourceSourceInfo
from openviking_cli.exceptions import InvalidArgumentError


@pytest.mark.parametrize(
    "field",
    [
        "dingtalk_previous_state",
        "dingtalk_processing_key",
        "dingtalk_previous_digest",
    ],
)
@pytest.mark.asyncio
async def test_dingtalk_reuse_state_cannot_be_supplied_as_public_args(field):
    service = ResourceService()
    with pytest.raises(InvalidArgumentError):
        await service._normalize_add_resource_args(
            {field: {}}, watch_interval=0, ctx=SimpleNamespace()
        )


def test_watch_never_persists_an_import_lock():
    service = ResourceService()
    assert service._sanitize_watch_processor_kwargs(
        {
            "dingtalk_identity": "main",
            "resource_lock": {"lease": "temporary"},
        }
    ) == {"dingtalk_identity": "main"}


@pytest.mark.asyncio
async def test_dingtalk_parent_target_is_stable_instead_of_getting_a_suffix():
    lock = {"lease": "same-target"}
    tree_builder = SimpleNamespace(
        resolve_target_uri=AsyncMock(
            return_value=(
                "viking://resources/team/dingtalk_Source123",
                "viking://resources/team/dingtalk_Source123",
            )
        )
    )
    processor = SimpleNamespace(
        tree_builder=tree_builder,
        reserve_unique_candidate=AsyncMock(),
        ensure_candidate_parent_write_access=AsyncMock(),
    )
    viking_fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        _uri_to_path=lambda uri, ctx=None: "/resources/team/dingtalk_Source123",
        _async_agfs=SimpleNamespace(pathlock_acquire_tree=AsyncMock(return_value=lock)),
    )
    service = ResourceService(viking_fs=viking_fs, resource_processor=processor)

    root_uri, actual_lock, deferred, cleanup = await service._plan_source_job_target(
        path="https://alidocs.dingtalk.com/i/nodes/Source123",
        ctx=SimpleNamespace(),
        to="",
        parent="viking://resources/team",
        create_parent=False,
        source_info=_ResourceSourceInfo(
            source_name="dingtalk_Source123",
            source_path="https://alidocs.dingtalk.com/i/nodes/Source123",
            source_format="directory",
            stable_target=True,
        ),
        defer_candidate_resolution=False,
        to_is_directory=False,
    )

    assert root_uri == "viking://resources/team/dingtalk_Source123"
    assert actual_lock is lock
    assert deferred is False
    assert cleanup is False
    processor.reserve_unique_candidate.assert_not_awaited()


def test_dingtalk_watch_source_type_is_native():
    assert (
        ResourceService._infer_watch_source_type("https://alidocs.dingtalk.com/i/nodes/Source123")
        == "dingtalk"
    )


@pytest.mark.asyncio
async def test_paused_dingtalk_watch_keeps_identity_and_limits():
    manager = WatchManager(viking_fs=None)
    await manager.initialize()
    task = await manager.create_task(
        path="https://alidocs.dingtalk.com/i/nodes/Source123",
        source_type="dingtalk",
        to_uri="viking://resources/dingtalk_Source123",
        watch_interval=60,
        processor_kwargs={
            "dingtalk_identity": "team-docs",
            "dingtalk_max_nodes": 250,
            "dingtalk_max_depth": 12,
            "dingtalk_max_bytes": 1048576,
        },
        is_active=False,
    )

    public = task.to_dict()
    assert public["is_active"] is False
    assert public["next_execution_time"] is None
    assert public["source_type"] == "dingtalk"
    assert public["processor_kwargs"] == {
        "dingtalk_identity": "team-docs",
        "dingtalk_max_nodes": 250,
        "dingtalk_max_depth": 12,
        "dingtalk_max_bytes": 1048576,
    }
