# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from openviking_sdk import AsyncHTTPClient
from openviking_sdk.config import resolve_client_config


def test_explicit_arguments_win_over_env(monkeypatch):
    monkeypatch.setenv("OPENVIKING_URL", "http://env-host:1933")
    monkeypatch.setenv("OPENVIKING_API_KEY", "env-key")
    monkeypatch.setenv("OPENVIKING_ACCOUNT", "env-account")
    monkeypatch.setenv("OPENVIKING_USER", "env-user")
    monkeypatch.setenv("OPENVIKING_ACTOR_PEER_ID", "env-actor")
    monkeypatch.setenv("OPENVIKING_TIMEOUT", "12.5")

    client = AsyncHTTPClient(
        url="http://explicit-host:1933",
        api_key="explicit-key",
        account="explicit-account",
        user="explicit-user",
        actor_peer_id="explicit-actor",
        timeout=33.0,
    )

    assert client._url == "http://explicit-host:1933"
    assert client._api_key == "explicit-key"
    assert client._account == "explicit-account"
    assert client._user_id == "explicit-user"
    assert client._actor_peer_id == "explicit-actor"
    assert client._timeout == 33.0


def test_env_values_fill_missing_fields(monkeypatch):
    monkeypatch.setenv("OPENVIKING_URL", "http://env-host:1933")
    monkeypatch.setenv("OPENVIKING_API_KEY", "env-key")
    monkeypatch.setenv("OPENVIKING_ACCOUNT", "env-account")
    monkeypatch.setenv("OPENVIKING_USER", "env-user")
    monkeypatch.setenv("OPENVIKING_ACTOR_PEER_ID", "env-actor")
    monkeypatch.setenv("OPENVIKING_TIMEOUT", "12.5")

    client = AsyncHTTPClient()

    assert client._url == "http://env-host:1933"
    assert client._api_key == "env-key"
    assert client._account == "env-account"
    assert client._user_id == "env-user"
    assert client._actor_peer_id == "env-actor"
    assert client._timeout == 12.5


def test_url_is_required_when_missing_everywhere(monkeypatch):
    monkeypatch.delenv("OPENVIKING_URL", raising=False)

    with pytest.raises(ValueError, match="url is required"):
        AsyncHTTPClient()


@pytest.mark.parametrize("timeout", [0, -1, -0.5, float("nan"), float("inf"), True, 1e7])
def test_explicit_timeout_must_be_positive_and_representable(timeout):
    with pytest.raises(ValueError, match="Invalid timeout value for 'timeout'"):
        resolve_client_config(url="http://127.0.0.1:1933", timeout=timeout)


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "1e7", "not-a-number"])
def test_env_timeout_is_validated(monkeypatch, raw):
    monkeypatch.delenv("OPENVIKING_CLI_CONFIG_FILE", raising=False)
    monkeypatch.setenv("OPENVIKING_TIMEOUT", raw)

    with pytest.raises(ValueError, match="OPENVIKING_TIMEOUT"):
        resolve_client_config(url="http://127.0.0.1:1933")


def test_ovcli_timeout_is_validated(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text('{"url": "http://127.0.0.1:1933", "timeout": -1}')
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("OPENVIKING_TIMEOUT", raising=False)

    with pytest.raises(ValueError, match="ovcli.timeout"):
        resolve_client_config()


def test_valid_timeout_values_are_accepted(monkeypatch):
    monkeypatch.delenv("OPENVIKING_CLI_CONFIG_FILE", raising=False)
    monkeypatch.delenv("OPENVIKING_TIMEOUT", raising=False)

    assert resolve_client_config(url="http://127.0.0.1:1933", timeout=0.5).timeout == 0.5
    assert resolve_client_config(url="http://127.0.0.1:1933", timeout=1e6).timeout == 1e6

    monkeypatch.setenv("OPENVIKING_TIMEOUT", "12.5")
    assert resolve_client_config(url="http://127.0.0.1:1933").timeout == 12.5
