from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.parsers.media.utils import MPEG_TS_PACKET_SIZE, MPEG_TS_PROBE_BYTES
from openviking.server.identity import RequestContext, Role
from openviking.service import resource_service as resource_service_module
from openviking.service.resource_service import ResourceService
from openviking.storage.queuefs import QueueManager
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking_cli.session.user_id import UserIdentifier


def mpeg_ts_probe() -> bytes:
    content = bytearray(MPEG_TS_PROBE_BYTES)
    for offset in range(0, MPEG_TS_PROBE_BYTES, MPEG_TS_PACKET_SIZE):
        content[offset] = 0x47
    return bytes(content)


@pytest.mark.asyncio
async def test_extensionless_remote_url_queues_frozen_understanding_route(
    monkeypatch,
    tmp_path,
):
    ctx = RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=Role.USER,
        api_key="secret",
    )
    downloaded = tmp_path / "download.pdf"
    downloaded.write_bytes(b"%PDF-1.7")
    prepared = LocalResource(
        path=downloaded,
        source_type=SourceType.HTTP,
        original_source="https://example.com/download?id=1",
        meta={
            "resolved_extension": ".pdf",
            "original_filename": "manual.pdf",
        },
        is_temporary=True,
    )
    lock = SimpleNamespace(
        to_handoff=lambda: SimpleNamespace(to_dict=lambda: {"handle_id": "lock-1"}),
        handoff=AsyncMock(),
        close=AsyncMock(),
    )
    processor = SimpleNamespace(
        understanding_api_enabled=lambda: True,
        should_use_understanding_directly=lambda _source, **_kwargs: False,
        prepare_resource=AsyncMock(return_value=prepared),
        should_use_understanding_api=lambda resource: resource is prepared,
        submit_understanding=AsyncMock(return_value="response-1"),
        tree_builder=SimpleNamespace(
            resolve_target_uri=AsyncMock(
                return_value=(
                    "viking://resources/manual",
                    "viking://resources/manual",
                )
            )
        ),
        reserve_unique_candidate=AsyncMock(return_value=("viking://resources/manual", lock)),
        process_resource=AsyncMock(),
    )
    service = ResourceService(
        vikingdb=object(),
        viking_fs=object(),
        resource_processor=processor,
        skill_processor=object(),
    )
    service._should_use_connector = lambda *_args, **_kwargs: False
    tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
        update_stage=AsyncMock(),
        fail=AsyncMock(),
    )
    queue_manager = SimpleNamespace(enqueue=AsyncMock())
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: False)
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: queue_manager,
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: object(),
    )

    result = await service.add_resource(
        path="https://example.com/download?id=1",
        ctx=ctx,
        to="viking://resources/manual",
        wait=False,
        allow_local_path_resolution=False,
    )

    assert result == {
        "status": "success",
        "root_uri": "viking://resources/manual",
        "task_id": "task-1",
    }
    _, message = queue_manager.enqueue.await_args.args
    assert queue_manager.enqueue.await_args.args[0] == QueueManager.EXTERNAL_PARSE
    queued = AddResourceMsg.from_dict(message)
    assert queued.args["parser_backend"] == "understanding"
    assert queued.args["resolved_extension"] == ".pdf"
    assert queued.understanding_response_id == "response-1"
    assert queued.source_name == "manual.pdf"
    assert not downloaded.exists()
    processor.submit_understanding.assert_awaited_once_with(prepared)
    processor.process_resource.assert_not_awaited()
    lock.handoff.assert_awaited_once()


@pytest.mark.asyncio
async def test_remote_mpeg_ts_url_queues_understanding_after_prepare(
    monkeypatch,
    tmp_path,
):
    ctx = RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=Role.USER,
        api_key="secret",
    )
    downloaded = tmp_path / "sample.ts"
    downloaded.write_bytes(mpeg_ts_probe())
    prepared = LocalResource(
        path=downloaded,
        source_type=SourceType.HTTP,
        original_source="https://example.com/video/sample.ts?sign=1",
        meta={
            "resolved_extension": "mpegts",
            "original_filename": "sample.ts",
        },
        is_temporary=True,
    )
    lock = SimpleNamespace(
        to_handoff=lambda: SimpleNamespace(to_dict=lambda: {"handle_id": "lock-1"}),
        handoff=AsyncMock(),
        close=AsyncMock(),
    )
    resolve_target_uri = AsyncMock(
        return_value=(
            "viking://resources/video/sample",
            "viking://resources/video/sample",
        )
    )
    processor = SimpleNamespace(
        understanding_api_enabled=lambda: True,
        should_use_understanding_directly=lambda _source, **_kwargs: False,
        prepare_resource=AsyncMock(return_value=prepared),
        should_use_understanding_api=lambda resource: resource is prepared,
        submit_understanding=AsyncMock(return_value="response-1"),
        tree_builder=SimpleNamespace(resolve_target_uri=resolve_target_uri),
        reserve_unique_candidate=AsyncMock(
            return_value=("viking://resources/video/sample", lock)
        ),
        process_resource=AsyncMock(),
    )
    service = ResourceService(
        vikingdb=object(),
        viking_fs=object(),
        resource_processor=processor,
        skill_processor=object(),
    )
    service._should_use_connector = lambda *_args, **_kwargs: False
    tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
        update_stage=AsyncMock(),
        fail=AsyncMock(),
    )
    queue_manager = SimpleNamespace(enqueue=AsyncMock())
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: False)
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: queue_manager,
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: object(),
    )

    result = await service.add_resource(
        path="https://example.com/video/sample.ts?sign=1",
        ctx=ctx,
        wait=False,
        allow_local_path_resolution=False,
    )

    assert result == {
        "status": "success",
        "root_uri": "viking://resources/video/sample",
        "task_id": "task-1",
    }
    processor.prepare_resource.assert_awaited_once()
    processor.process_resource.assert_not_awaited()
    processor.submit_understanding.assert_awaited_once_with(prepared)
    resolve_target_uri.assert_awaited_once()
    assert resolve_target_uri.await_args.kwargs["source_format"] == "video"
    _, message = queue_manager.enqueue.await_args.args
    assert queue_manager.enqueue.await_args.args[0] == QueueManager.EXTERNAL_PARSE
    queued = AddResourceMsg.from_dict(message)
    assert queued.args["parser_backend"] == "understanding"
    assert queued.args["resolved_extension"] == "mpegts"
    assert queued.understanding_response_id == "response-1"
    assert queued.source_name == "sample.ts"
    assert not downloaded.exists()
    lock.handoff.assert_awaited_once()
