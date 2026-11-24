# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for stable readiness component states."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from openviking.server.app import create_app
from openviking.server.config import ServerConfig


async def test_readiness_tracks_startup_and_filesystem_health(monkeypatch):
    service = SimpleNamespace(_initialized=False, embedding_provider=object())
    filesystem = SimpleNamespace(
        ls=AsyncMock(return_value=[]),
        _get_vector_store=lambda: SimpleNamespace(health_check=AsyncMock(return_value=True)),
    )
    monkeypatch.setattr("openviking.server.dependencies._service", service)
    monkeypatch.setattr("openviking.server.routers.system.get_viking_fs", lambda: filesystem)
    monkeypatch.setattr(
        "openviking_cli.utils.ollama.detect_ollama_in_config",
        lambda config: (False, None, None),
    )

    app = create_app(config=ServerConfig(), service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        liveness = await client.get("/health")
        assert liveness.status_code == 200
        assert liveness.json()["healthy"] is True
        initializing = await client.get("/ready")
        assert initializing.status_code == 503
        assert initializing.json() == {"status": "not_ready", "reason": "initializing"}
        filesystem.ls.assert_not_awaited()

        service._initialized = True
        ready = await client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "checks": {
                "agfs": {"status": "ok", "checks": {"filesystem": "ok"}},
                "api_key_manager": "not_configured",
                "embedding": "ok",
                "ollama": "not_configured",
                "vectordb": "ok",
            },
        }

        filesystem.ls.side_effect = OSError("storage unavailable")
        unavailable = await client.get("/ready")
        assert unavailable.status_code == 503
        assert unavailable.json()["status"] == "not_ready"
        assert unavailable.json()["checks"]["agfs"] == {
            "status": "error",
            "checks": {"filesystem": "error"},
        }
        assert (await client.get("/health")).status_code == 200


def test_readiness_log_omits_exception_text(caplog):
    from openviking.server.routers.system import _log_readiness_error

    caplog.set_level("INFO", logger="openviking.server.routers.system")
    _log_readiness_error("vectordb", RuntimeError("postgresql://user:secret@10.0.0.7/db"))

    assert "component=vectordb" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "postgresql://" not in caplog.text
    assert "10.0.0.7" not in caplog.text


def test_pending_health_payload_omits_config_path():
    import runpy
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(str(root / "deploy/docker/pending_health_server.py"))
    payload = namespace["build_payload"]()

    assert payload["status"] == "pending_initialization"
    assert "config_file" not in payload
    assert "fix" in payload
    assert namespace["make_handler"]() is not None


async def test_readiness_ollama_unreachable_uses_stable_state(monkeypatch):
    service = SimpleNamespace(_initialized=True, embedding_provider=object())
    filesystem = SimpleNamespace(
        ls=AsyncMock(return_value=[]),
        _get_vector_store=lambda: SimpleNamespace(health_check=AsyncMock(return_value=True)),
    )
    monkeypatch.setattr("openviking.server.dependencies._service", service)
    monkeypatch.setattr("openviking.server.routers.system.get_viking_fs", lambda: filesystem)
    monkeypatch.setattr(
        "openviking_cli.utils.ollama.detect_ollama_in_config",
        lambda config: (True, "ollama.internal", 11434),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.ollama.check_ollama_running", lambda host, port: False
    )

    app = create_app(config=ServerConfig(), service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["ollama"] == "unreachable"
    assert "ollama.internal" not in response.text
    assert "11434" not in response.text
