from __future__ import annotations

import json

import pytest
from openviking_sdk import AsyncHTTPClient


def test_async_http_client_loads_connection_fields_from_ovcli_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "api_key": "config-key",
                "account": "config-account",
                "user": "config-user",
                "timeout": 12.5,
            }
        )
    )
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("OPENVIKING_URL", raising=False)
    monkeypatch.delenv("OPENVIKING_API_KEY", raising=False)

    client = AsyncHTTPClient()

    assert client._url == "http://config-host:1933"
    assert client._api_key == "config-key"
    assert client._account == "config-account"
    assert client._user_id == "config-user"
    assert client._timeout == 12.5


def test_async_http_client_loads_upload_mode_and_extra_headers_from_ovcli_config(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "upload": {"mode": "shared"},
                "extra_headers": {
                    "X-Custom-Header": "custom-value",
                    "Authorization": "Bearer token",
                },
            }
        )
    )
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("OPENVIKING_URL", raising=False)

    client = AsyncHTTPClient()

    assert client._upload_mode == "shared"
    assert client._extra_headers == {
        "X-Custom-Header": "custom-value",
        "Authorization": "Bearer token",
    }


def test_async_http_client_explicit_arguments_override_ovcli_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "api_key": "config-key",
                "account": "config-account",
                "user": "config-user",
                "timeout": 12.5,
                "upload": {"mode": "shared"},
                "extra_headers": {"X-Custom-Header": "from-config"},
            }
        )
    )
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))

    client = AsyncHTTPClient(
        url="http://explicit-host:1933",
        api_key="explicit-key",
        account="explicit-account",
        user="explicit-user",
        timeout=33.0,
        extra_headers={"X-Custom-Header": "from-explicit"},
        upload_mode="local",
    )

    assert client._url == "http://explicit-host:1933"
    assert client._api_key == "explicit-key"
    assert client._account == "explicit-account"
    assert client._user_id == "explicit-user"
    assert client._timeout == 33.0
    assert client._extra_headers == {"X-Custom-Header": "from-explicit"}
    assert client._upload_mode == "local"


def test_async_http_client_reports_invalid_ovcli_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(json.dumps({"url": "http://localhost:1933", "timeout": "fast"}))
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("OPENVIKING_URL", raising=False)

    with pytest.raises(ValueError, match="Invalid CLI config"):
        AsyncHTTPClient()


def test_async_http_client_inherits_profile_enabled_from_ovcli_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "profile": True,
            }
        )
    )
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("OPENVIKING_URL", raising=False)

    client = AsyncHTTPClient()

    assert client._profile_enabled is True


def test_async_http_client_rejects_explicit_actor_peer_id_and_agent_id_together():
    with pytest.raises(ValueError, match="actor_peer_id cannot be used with agent_id"):
        AsyncHTTPClient(
            url="http://explicit-host:1933",
            actor_peer_id="actor-a",
            agent_id="legacy-agent",
        )
