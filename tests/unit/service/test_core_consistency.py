# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for core service behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.storage.index_consistency import IndexConsistencyReport
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


def test_service_passes_queue_worker_concurrency_to_storage(monkeypatch) -> None:
    storage_calls = []
    embedder = SimpleNamespace(is_sparse=False)
    config = SimpleNamespace(
        default_account="default",
        default_user="default",
        storage=object(),
        embedding=SimpleNamespace(
            max_concurrent=10,
            dimension=1024,
            get_embedder=lambda: embedder,
        ),
        vlm=SimpleNamespace(max_concurrent=32),
        parser_api=SimpleNamespace(),
        queue_workers=SimpleNamespace(
            external_parse=SimpleNamespace(max_concurrent=9),
            add_resource=SimpleNamespace(max_concurrent=7),
            session_commit=SimpleNamespace(max_concurrent=5),
        ),
        git=object(),
    )

    monkeypatch.setattr(
        "openviking.service.core.initialize_openviking_config",
        lambda **_kwargs: config,
    )
    monkeypatch.setattr(OpenVikingService, "_ensure_data_dir_lock_acquired", lambda self: None)
    monkeypatch.setattr(
        OpenVikingService,
        "_build_ragfs_binding_config",
        lambda self: object(),
    )

    def _capture_storage_init(self, *args, **kwargs) -> None:
        storage_calls.append((args, kwargs))

    monkeypatch.setattr(OpenVikingService, "_init_storage", _capture_storage_init)

    OpenVikingService()

    assert storage_calls[0][1]["max_concurrent_external_parse"] == 9
    assert storage_calls[0][1]["max_concurrent_add_resource"] == 7
    assert storage_calls[0][1]["max_concurrent_session_commit"] == 5


def _service_with_fs(stat_result: dict) -> tuple[OpenVikingService, AsyncMock]:
    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = True
    service._user = UserIdentifier.the_default_user()
    service._vikingdb_manager = AsyncMock()
    service._viking_fs = AsyncMock()
    service._viking_fs.stat.return_value = stat_result
    return service, service._viking_fs


@pytest.mark.asyncio
async def test_check_consistency_rejects_file_uri() -> None:
    service, viking_fs = _service_with_fs({"isDir": False})
    ctx = RequestContext(user=service.user, role=Role.ROOT)

    with pytest.raises(
        InvalidArgumentError,
        match="Consistency check only supports directory URIs",
    ):
        await service.check_consistency(
            uri="viking://resources/consistency-file.md",
            ctx=ctx,
        )

    viking_fs.tree.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_consistency_preserves_directory_behavior() -> None:
    service, viking_fs = _service_with_fs({"isDir": True})
    viking_fs.tree.return_value = []
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    report = IndexConsistencyReport(expected=(), missing_records=())

    with patch(
        "openviking.service.core.check_index_consistency",
        new=AsyncMock(return_value=report),
    ) as check:
        result = await service.check_consistency(uri="viking://resources", ctx=ctx)

    assert result == report.to_dict()
    viking_fs.stat.assert_awaited_once_with(
        "viking://resources",
        ctx=ctx,
        skip_count=True,
    )
    viking_fs.tree.assert_awaited_once()
    check.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_stops_queue_manager_before_ragfs_binding(monkeypatch) -> None:
    events: list[str] = []

    class ResourceService:
        async def close_background_tasks(self) -> None:
            events.append("resource_tasks")

    class QueueManager:
        def stop(self) -> None:
            events.append("queue_manager")

    class RagfsClient:
        def close(self) -> None:
            events.append("ragfs_binding")

    service = OpenVikingService.__new__(OpenVikingService)
    service._resource_service = ResourceService()
    service._watch_scheduler = None
    service._session_auto_commit_scheduler = None
    service._queue_manager = QueueManager()
    service._vikingdb_manager = None
    service._agfs_client = RagfsClient()
    service._viking_fs = object()
    service._resource_processor = object()
    service._skill_processor = object()
    service._session_compressor = object()
    service._directory_initializer = object()
    service._privacy_config_service = object()
    service._initialized = True
    monkeypatch.setattr(
        OpenVikingService,
        "_release_data_dir_lock",
        lambda self: events.append("data_dir_lock"),
    )

    await service.close()

    assert events.index("queue_manager") < events.index("ragfs_binding")
    assert events[-1] == "data_dir_lock"
