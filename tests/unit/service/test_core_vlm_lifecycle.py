# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Service lifecycle coverage for configured VLM clients."""

from types import SimpleNamespace

import pytest

from openviking.service.core import OpenVikingService


@pytest.mark.asyncio
async def test_service_closes_vlm_after_queue_workers_stop(monkeypatch) -> None:
    events = []

    class _ResourceService:
        async def close_background_tasks(self) -> None:
            events.append("background")

    class _QueueManager:
        def stop(self) -> None:
            events.append("queue")

    class _VLMConfig:
        def close(self) -> None:
            events.append("vlm")

    service = OpenVikingService.__new__(OpenVikingService)
    service._resource_service = _ResourceService()
    service._watch_scheduler = None
    service._session_auto_commit_scheduler = None
    service._queue_manager = _QueueManager()
    service._vikingdb_manager = None
    service._agfs_client = None
    service._config = SimpleNamespace(vlm=_VLMConfig())
    service._initialized = True
    monkeypatch.setattr(service, "_release_data_dir_lock", lambda: None)

    await service.close()

    assert events == ["background", "queue", "vlm"]
