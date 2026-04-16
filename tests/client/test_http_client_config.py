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
