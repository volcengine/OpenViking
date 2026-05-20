# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.utils.config import OPENVIKING_CLI_CONFIG_ENV


def test_async_http_client_loads_missing_fields_from_ovcli_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "api_key": "config-key",
                "agent_id": "config-agent",
                "account": "config-account",
                "user": "config-user",
                "timeout": 12.5,
            }
        )
    )
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))

    client = AsyncHTTPClient(url="http://explicit-host:1933")

    assert client._url == "http://explicit-host:1933"
    assert client._api_key == "config-key"
    assert client._agent_id == "config-agent"
    assert client._account == "config-account"
    assert client._user_id == "config-user"
    assert client._timeout == 12.5


def test_async_http_client_explicit_values_override_ovcli_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "api_key": "config-key",
                "account": "config-account",
                "timeout": 12.5,
            }
        )
    )
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))

    client = AsyncHTTPClient(
        url="http://explicit-host:1933",
        api_key="explicit-key",
        account="explicit-account",
        timeout=33.0,
    )

    assert client._url == "http://explicit-host:1933"
    assert client._api_key == "explicit-key"
    assert client._account == "explicit-account"
    assert client._timeout == 33.0


@pytest.mark.asyncio
async def test_async_http_client_user_id_sets_openviking_user_header(monkeypatch):
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openviking_cli.client.http.httpx.AsyncClient", FakeAsyncClient)

    client = AsyncHTTPClient(
        url="http://explicit-host:1933",
        api_key="explicit-key",
        account="explicit-account",
        user_id="explicit-user",
        agent_id="explicit-agent",
        timeout=33.0,
        extra_headers={},
    )
    await client.initialize()

    assert client._user_id == "explicit-user"
    assert captured["headers"] == {
        "X-API-Key": "explicit-key",
        "X-OpenViking-Agent": "explicit-agent",
        "X-OpenViking-Account": "explicit-account",
        "X-OpenViking-User": "explicit-user",
    }


def test_async_http_client_rejects_unknown_ovcli_field(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(json.dumps({"ur": "http://localhost:1933"}))
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))

    with pytest.raises(ValueError, match=r"ovcli\.ur'.*ovcli\.url"):
        AsyncHTTPClient()


def test_async_http_client_reports_invalid_ovcli_value_path(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(json.dumps({"url": "http://localhost:1933", "timeout": "fast"}))
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))

    with pytest.raises(ValueError, match=r"Invalid value for 'ovcli\.timeout'"):
        AsyncHTTPClient()


def test_async_http_client_accepts_ovcli_upload_section(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "api_key": "config-key",
                "upload": {
                    "mode": "shared",
                    "ignore_dirs": "node_modules,.cache",
                    "include": "*.md,*.pdf",
                    "exclude": "*.tmp,*.log",
                },
            }
        )
    )
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))

    client = AsyncHTTPClient()

    assert client._url == "http://config-host:1933"
    assert client._api_key == "config-key"
    assert client._upload_mode == "shared"


def test_async_http_client_rejects_unknown_ovcli_upload_field(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://localhost:1933",
                "upload": {
                    "unknown": "value",
                },
            }
        )
    )
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))

    with pytest.raises(ValueError, match=r"ovcli\.upload\.unknown"):
        AsyncHTTPClient()


def test_async_http_client_loads_extra_headers_from_ovcli_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "api_key": "config-key",
                "extra_headers": {
                    "X-Custom-Header": "custom-value",
                    "Authorization": "Bearer token",
                },
            }
        )
    )
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))

    client = AsyncHTTPClient()

    assert client._url == "http://config-host:1933"
    assert client._extra_headers == {
        "X-Custom-Header": "custom-value",
        "Authorization": "Bearer token",
    }


def test_async_http_client_explicit_extra_headers_override_ovcli_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://localhost:1933",
                "api_key": "config-key",
                "extra_headers": {"X-Custom-Header": "from-config"},
            }
        )
    )
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))

    client = AsyncHTTPClient(
        extra_headers={"X-Custom-Header": "from-explicit", "Another-Header": "another-value"}
    )

    assert client._extra_headers == {
        "X-Custom-Header": "from-explicit",
        "Another-Header": "another-value",
    }


def test_async_http_client_loads_extra_header_alias_from_ovcli_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "api_key": "config-key",
                "extra_header": {
                    "X-Custom-Header": "custom-value",
                    "Authorization": "Bearer token",
                },
            }
        )
    )
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))

    client = AsyncHTTPClient()

    assert client._url == "http://config-host:1933"
    assert client._extra_headers == {
        "X-Custom-Header": "custom-value",
        "Authorization": "Bearer token",
    }


def test_async_http_client_prefers_extra_headers_over_alias(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "api_key": "config-key",
                "extra_headers": {"X-Custom-Header": "from-plural"},
                "extra_header": {"X-Custom-Header": "from-singular"},
            }
        )
    )
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(config_path))

    client = AsyncHTTPClient()

    # extra_headers 优先
    assert client._extra_headers == {"X-Custom-Header": "from-plural"}
