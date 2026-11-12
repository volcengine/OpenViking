# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Empty Serverless startup must never access business models or vectors."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from openviking.server.routers.system import readiness_check
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import set_task_tracker
from openviking.storage import viking_fs as viking_fs_module
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.utils.mock_agfs import MockLocalAGFS


async def test_empty_serverless_initializes_files_providers_and_assets(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Empty Serverless startup accessed business models or vectors")

    previous_fs = viking_fs_module._instance
    agfs = MockLocalAGFS(root_path=tmp_path / "agfs")
    acquire = AsyncMock(wraps=agfs.pathlock_acquire_batch_async)
    release = Mock(wraps=agfs.pathlock_release)
    monkeypatch.setattr(agfs, "pathlock_acquire_batch_async", acquire)
    monkeypatch.setattr(agfs, "pathlock_release", release)
    monkeypatch.setattr("openviking.utils.agfs_utils.create_agfs_client", lambda *a, **kw: agfs)
    monkeypatch.setattr("openviking.service.core.init_context_collection", forbidden)
    monkeypatch.setattr(
        "openviking.storage.viking_vector_index_backend.create_collection_adapter", forbidden
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.embedding_config.EmbeddingConfig.get_embedder", forbidden
    )
    monkeypatch.setattr("openviking.utils.process_lock.acquire_data_dir_lock", forbidden)
    monkeypatch.setattr(OpenVikingConfigSingleton, "_instance", None)
    OpenVikingConfigSingleton.initialize(
        config_dict={"runtime_mode": "serverless", "storage": {"workspace": str(tmp_path)}}
    )
    service = OpenVikingService(path=str(tmp_path))
    try:
        await service.initialize(start_background=False)
        path = service.viking_fs._uri_to_path("viking://")
        acquire.assert_awaited_once()
        assert acquire.await_args.args[1] == [{"path": path, "kind": "tree"}]
        release.assert_called_once()
        await service.reconcile_default_system_assets()
        assert service._shared_initialized
        assert service._vikingdb_manager._shared_adapter is None
        assert service.embedding_provider is not None
        assert service.vlm_resolver is not None
        assert service.vector_config_resolver is not None
        assert not service._data_dir_lock_acquired
        assert await service.viking_fs.read_file("viking://resources/.abstract.md")
        assert await service._queue_manager.size(service._queue_manager.EMBEDDING) == 0
    finally:
        await service.close()
        set_task_tracker(None)
        viking_fs_module._instance = previous_fs


@pytest.mark.parametrize(
    "failed_dependency", [None, "account_store", "task_store", "queue", "pathlock", "providers"]
)
async def test_readiness_probes_shared_dependencies_only(monkeypatch, failed_dependency):
    def probe(name):
        return AsyncMock(
            side_effect=RuntimeError("unavailable") if failed_dependency == name else None
        )

    fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_observe=probe("pathlock")),
        ls=AsyncMock(return_value=[]),
        _get_vector_store=lambda: (_ for _ in ()).throw(AssertionError("Business vector probe")),
    )
    service = SimpleNamespace(
        _initialized=True,
        _config=SimpleNamespace(runtime_mode="serverless"),
        runtime_config_manager=object(),
        embedding_provider=None if failed_dependency == "providers" else object(),
        vlm_resolver=object(),
        vector_config_resolver=object(),
        vikingdb_manager=object(),
        viking_fs=fs,
        _queue_manager=SimpleNamespace(EMBEDDING="embedding", size=probe("queue")),
    )
    manager = SimpleNamespace(list_accounts=probe("account_store"))
    monkeypatch.setattr("openviking.server.routers.system.get_service", lambda: service)
    monkeypatch.setattr("openviking.server.routers.system.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: SimpleNamespace(list_tasks=probe("task_store")),
    )
    monkeypatch.setattr(
        OpenVikingConfigSingleton, "get_instance",
        lambda: (_ for _ in ()).throw(AssertionError("Business model probe")),
    )
    app = FastAPI()
    app.state.api_key_manager = manager
    response = await readiness_check(Request({"type": "http", "app": app}))
    body = json.loads(response.body)
    assert response.status_code == (503 if failed_dependency else 200)
    assert "vectordb" not in body["checks"]
    assert "ollama" not in body["checks"]
    assert {"providers", "task_store", "queue", "pathlock"}.issubset(body["checks"])
