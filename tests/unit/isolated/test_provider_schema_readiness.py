"""provider schema身份仅公开版本/checksum，不建立数据库或输出其他配置。"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from openviking.server.routers import system


@pytest.mark.asyncio
async def test_provider_schema_readiness_projects_only_public_identity(monkeypatch):
    provider = SimpleNamespace(
        shared_state_backend="mysql",
        shared_schema={"version": 1, "checksum": "d" * 64, "password": "never-public"},
        check_state=AsyncMock(),
    )
    service = SimpleNamespace(_initialized=True, _agfs_client=provider, embedding_provider=object())
    monkeypatch.setattr(system, "get_service", lambda: service)
    monkeypatch.setattr(system, "_probe_agfs_readiness", AsyncMock(return_value={"status": "ok", "checks": {"filesystem": "ok"}}))
    storage = SimpleNamespace(health_check=AsyncMock(return_value=True))
    monkeypatch.setattr(system, "get_viking_fs", lambda: SimpleNamespace(_get_vector_store=lambda: storage))
    monkeypatch.setattr("openviking_cli.utils.config.open_viking_config.OpenVikingConfigSingleton.get_instance", lambda: SimpleNamespace())
    monkeypatch.setattr("openviking_cli.utils.ollama.detect_ollama_in_config", lambda config: (False, None, None))
    response = await system.readiness_check(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(api_key_manager=None))))
    assert response.status_code == 200
    value = json.loads(response.body)
    assert value["shared_schema"] == {"version": 1, "checksum": "d" * 64}
    assert "never-public" not in response.body.decode()
