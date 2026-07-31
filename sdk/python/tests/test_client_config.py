import pytest
from openviking_sdk import AsyncHTTPClient


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
