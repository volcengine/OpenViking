# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Isolated model-free tests using the parent fixtures' real service and ASGI API."""

import json
import socket

import httpx
import pytest
import pytest_asyncio

from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture
def temp_dir(tmp_path):
    """Intentionally override the parent fixture used by service/upload_temp_dir.

    Keep the parent's service factory, but put its native workspace and config
    in pytest's per-test directory rather than test_data/tmp_server.
    """
    return tmp_path


@pytest.fixture
def offline_markdown_file(temp_dir):
    path = temp_dir / "oauth.md"
    path.write_text("# OAuth authentication\n\nUse OAuth authorization codes to authenticate.\n")
    return path


@pytest.fixture(autouse=True)
def offline_config(temp_dir, monkeypatch):
    config = {
        "enable_watch_scheduler": False,
        "storage": {
            "workspace": str(temp_dir / "data"),
            "agfs": {"backend": "local"},
            "vectordb": {"backend": "local"},
        },
        "embedding": {
            "dense": {
                "provider": "openai",
                "model": "offline-embedding",
                "api_key": "unused-offline-key",
                "dimension": 2048,
            }
        },
    }
    config_path = temp_dir / "ov.conf"
    config_path.write_text(json.dumps(config))
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(config_path=str(config_path))
    yield
    OpenVikingConfigSingleton.reset_instance()


@pytest.fixture(autouse=True)
def reject_external_network(monkeypatch):
    def reject_request(*args, **kwargs):
        pytest.fail("Offline API tests must not send an external network request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_request)
    # Catch other clients as well; ASGITransport needs no socket connections.
    monkeypatch.setattr(socket.socket, "connect", reject_request)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_request)


@pytest_asyncio.fixture
async def offline_resource_api(client, offline_markdown_file, upload_temp_dir):
    """Import a pre-staged file over ASGI; upload and API-key auth are not exercised."""
    response = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": offline_markdown_file.name, "wait": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok", body
    root_uri = body["result"]["root_uri"]
    assert root_uri, body
    return client, root_uri
