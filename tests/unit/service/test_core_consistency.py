# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for core service behavior."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.config import AccountStoreConfig, ServerConfig
from openviking.service.core import OpenVikingService
from openviking.storage.queuefs import get_queue_manager
from openviking_cli.utils.config.open_viking_config import (
    OpenVikingConfig,
    OpenVikingConfigSingleton,
)
from openviking_cli.utils.config.vlm_config import VLMConfig


async def test_service_applies_configured_queue_worker_limits(tmp_path, monkeypatch) -> None:
    from openviking.service.task_tracker import set_task_tracker

    config_file = tmp_path / "ov.conf"
    config_file.write_text(
        json.dumps(
            {
                "storage": {"workspace": str(tmp_path / "workspace")},
                "vlm": {"provider": "openai", "model": "test-vlm", "api_key": "unused"},
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "model": "offline",
                        "api_key": "unused",
                        "dimension": 4,
                    }
                },
                "queue_workers": {
                    "external_parse": {"max_concurrent": 9},
                    "add_resource": {"max_concurrent": 7},
                    "reindex": {"max_concurrent": 4},
                    "session_commit": {"max_concurrent": 5},
                    "external_task": {"max_concurrent": 6},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_file))
    monkeypatch.setattr("openviking.server.dependencies._server_config", None)
    OpenVikingConfigSingleton.reset_instance()
    service = OpenVikingService()
    try:
        manager = get_queue_manager()
        assert manager._max_concurrent_for_queue(manager.EXTERNAL_PARSE) == 9
        assert manager._max_concurrent_for_queue(manager.ADD_RESOURCE) == 7
        assert manager._max_concurrent_for_queue(manager.REINDEX) == 4
        assert manager._max_concurrent_for_queue(manager.SESSION_COMMIT) == 5
        assert manager._max_concurrent_for_queue(manager.EXTERNAL_TASK) == 6
    finally:
        await service.close()
        set_task_tracker(None)
        OpenVikingConfigSingleton.reset_instance()


@pytest.mark.parametrize("source", ["file", "registered", "registered-over-file"])
def test_service_loads_account_store_runtime_from_config_file(tmp_path, monkeypatch, source):
    registered = None
    config_file = None
    if source != "registered":
        config_file = tmp_path / "ov.conf"
        account_store = {
            "provider": "redis_mysql" if source == "file" else "file",
            "params": {"cache": {"instance": "accountstore"}} if source == "file" else {},
        }
        config_file.write_text(json.dumps({"server": {"account_store": account_store}}))
    if source != "file":
        registered = ServerConfig(
            account_store=AccountStoreConfig(
                provider=" REDIS_MYSQL ",
                params={"cache": {"instance": "accountstore"}},
            )
        )

    monkeypatch.setattr(OpenVikingConfigSingleton, "get_config_file", lambda: config_file)
    monkeypatch.setattr("openviking.server.dependencies._server_config", registered)
    service = OpenVikingService.__new__(OpenVikingService)
    service._config = OpenVikingConfig.model_validate(
        {
            "storage": {"workspace": str(tmp_path)},
            "cache": {
                "instances": {
                    "default": {"provider": "dynamic"},
                    "accountstore": {
                        "provider": "redis",
                        "params": {"endpoints": ["redis://127.0.0.1:6380"]},
                    },
                },
            },
        }
    )

    binding = service._build_ragfs_binding_config()
    assert binding.account_store_cache_instance == "accountstore"
    cache = binding.to_binding_dict()["cache"]
    assert cache["runtime_enabled"] is True
    assert cache["cachefs"]["enabled"] is False
    assert cache["instances"]["accountstore"]["provider"] == "redis"
    assert cache["instances"]["accountstore"]["redis"]["endpoints"] == ["redis://127.0.0.1:6380"]


@pytest.mark.asyncio
async def test_close_stops_queue_manager_before_ragfs_binding(monkeypatch) -> None:
    from openviking.ttl import wiring

    events: list[str] = []

    async def close_ttl():
        events.append("ttl")

    adapter = SimpleNamespace(aclose=close_ttl)
    monkeypatch.setattr(wiring, "_adapter", adapter)
    monkeypatch.setattr(
        "openviking.service.core.get_task_tracker", lambda: SimpleNamespace(close=AsyncMock())
    )

    class ResourceService:
        async def close_background_tasks(self) -> None:
            events.append("resource_tasks")

    class QueueManager:
        def stop_workers(self) -> None:
            assert wiring.get_ttl_adapter() is adapter
            events.append("queue_manager")

        def stop(self) -> None:
            pass

    class RagfsClient:
        def close(self) -> None:
            events.append("ragfs_binding")

    service = OpenVikingService.__new__(OpenVikingService)
    service._resource_service = ResourceService()
    service._config = SimpleNamespace(vlm=VLMConfig())
    service._compile_scheduler = None
    service._ttl_adapter = adapter
    service._expiry_runtime = SimpleNamespace(
        stop_roles=AsyncMock(side_effect=lambda **_: events.append("expiry_roles")),
        close=AsyncMock(side_effect=lambda: events.append("expiry_storage")),
    )
    service._session_service = SimpleNamespace(
        set_session_expiry_adapter=lambda _: None, set_usage_reporter=Mock()
    )
    service._runtime_config_manager = None
    service._watch_runtime = None
    service._watch_manager = None
    service._session_auto_commit_scheduler = None
    service._queue_manager = QueueManager()
    service._vikingdb_manager = None
    service._agfs_client = RagfsClient()
    service._viking_fs = SimpleNamespace(set_post_write_hook=Mock())
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
    assert wiring.get_ttl_adapter() is None
    assert events.index("expiry_roles") < events.index("queue_manager")
    assert events.index("queue_manager") < events.index("ttl") < events.index("expiry_storage")
    assert events.index("expiry_storage") < events.index("ragfs_binding")
    assert events[-1] == "data_dir_lock"
