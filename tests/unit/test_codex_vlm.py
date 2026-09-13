# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from openviking.models.vlm.backends import codex_auth
from openviking.models.vlm.backends.codex_auth import resolve_codex_runtime_credentials
from openviking.models.vlm.backends.codex_responses_adapter import (
    _convert_message_for_responses,
)
from openviking.models.vlm.backends.codex_vlm import CodexVLM
from openviking_cli.utils.config.vlm_config import VLMConfig


class _MockResponsesStream:
    def __init__(self, final_response):
        self._final_response = final_response
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        events = [
            SimpleNamespace(type="response.output_item.done", item=item)
            for item in self._final_response.output or []
        ]
        events.append(SimpleNamespace(type="response.completed", response=self._final_response))
        return iter(events)

    def close(self):
        self.closed = True

    def get_final_response(self):
        return self._final_response


class _MockResponsesEventStream:
    def __init__(self, events):
        self._events = events
        self.closed = False

    def __iter__(self):
        return iter(self._events)

    def close(self):
        self.closed = True


def _build_final_response(text: str):
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=SimpleNamespace(input_tokens=11, output_tokens=7, total_tokens=18),
    )


def _build_fake_openai_client(text: str):
    client = MagicMock()
    client.responses.create.return_value = _MockResponsesStream(_build_final_response(text))
    return client


@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_codex_text_completion_uses_responses_api(mock_resolve, mock_openai_class):
    mock_resolve.return_value = {
        "api_key": "oauth-token",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    mock_real_client = MagicMock()
    mock_real_client.responses.create.return_value = _MockResponsesStream(
        _build_final_response("hello from codex")
    )
    mock_openai_class.return_value = mock_real_client

    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})
    result = vlm.get_completion("hello")

    assert result == "hello from codex"
    call_kwargs = mock_real_client.responses.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-5.3-codex"
    assert call_kwargs["input"] == [{"role": "user", "content": "hello"}]
    assert call_kwargs["stream"] is True
    assert "messages" not in call_kwargs
    mock_real_client.responses.stream.assert_not_called()


@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_codex_text_completion_passes_reasoning_effort_to_responses(
    mock_resolve, mock_openai_class
):
    mock_resolve.return_value = {
        "api_key": "oauth-token",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    mock_real_client = MagicMock()
    mock_real_client.responses.create.return_value = _MockResponsesStream(
        _build_final_response("reasoned answer")
    )
    mock_openai_class.return_value = mock_real_client

    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": "gpt-5.3-codex",
            "reasoning_effort": "xhigh",
        }
    )
    result = vlm.get_completion("hello")

    assert result == "reasoned answer"
    call_kwargs = mock_real_client.responses.create.call_args.kwargs
    assert call_kwargs["reasoning"] == {"effort": "xhigh"}


@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_codex_vision_completion_converts_images(mock_resolve, mock_openai_class):
    mock_resolve.return_value = {
        "api_key": "oauth-token",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    mock_real_client = MagicMock()
    mock_real_client.responses.create.return_value = _MockResponsesStream(
        _build_final_response("image result")
    )
    mock_openai_class.return_value = mock_real_client

    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})
    result = vlm.get_vision_completion("describe", [b"\x89PNG\r\n\x1a\n0000"])

    assert result == "image result"
    call_kwargs = mock_real_client.responses.create.call_args.kwargs
    content = call_kwargs["input"][0]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["image_url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "input_text", "text": "describe"}


@pytest.mark.asyncio
@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
async def test_codex_async_client_defers_runtime_credential_resolution(
    mock_resolve,
    mock_openai_class,
):
    mock_resolve.return_value = {
        "api_key": "oauth-token",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    mock_real_client = MagicMock()
    mock_real_client.responses.create.return_value = _MockResponsesStream(
        _build_final_response("async hello")
    )
    mock_openai_class.return_value = mock_real_client

    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})
    client = vlm.get_async_client()

    mock_resolve.assert_not_called()
    response = await client.chat.completions.create(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-5.3-codex",
    )

    assert response.choices[0].message.content == "async hello"
    mock_resolve.assert_called_once()


@pytest.mark.asyncio
@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
async def test_codex_async_stream_uses_text_deltas_when_completed_output_is_none(
    mock_resolve,
    mock_openai_class,
):
    mock_resolve.return_value = {
        "api_key": "oauth-token",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    final_response = SimpleNamespace(
        output=None,
        usage=SimpleNamespace(input_tokens=11, output_tokens=7, total_tokens=18),
    )
    mock_stream = _MockResponsesEventStream(
        [
            SimpleNamespace(type="response.output_text.delta", delta="overview "),
            SimpleNamespace(type="response.output_text.delta", delta="ready"),
            SimpleNamespace(type="response.completed", response=final_response),
        ]
    )
    mock_real_client = MagicMock()
    mock_real_client.responses.create.return_value = mock_stream
    mock_openai_class.return_value = mock_real_client

    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})
    response = await vlm.get_completion_async("summarize")

    assert response == "overview ready"
    assert mock_stream.closed is True
    call_kwargs = mock_real_client.responses.create.call_args.kwargs
    assert call_kwargs["stream"] is True
    mock_real_client.responses.stream.assert_not_called()


@patch("openviking.models.vlm.backends.codex_auth.has_codex_auth_available", return_value=True)
def test_vlm_config_accepts_codex_without_api_key(_mock_auth_available):
    config = VLMConfig(provider="openai-codex", model="gpt-5.3-codex")

    assert config.is_available() is True
    assert config.get_vlm_instance().__class__.__name__ == "CodexVLM"


@patch("openviking.models.vlm.backends.codex_auth.has_codex_auth_available", return_value=True)
def test_vlm_config_passes_codex_reasoning_effort(_mock_auth_available):
    config = VLMConfig(
        provider="openai-codex",
        model="gpt-5.3-codex",
        reasoning_effort="high",
    )

    assert config._build_vlm_config_dict()["reasoning_effort"] == "high"


@patch("openviking.models.vlm.backends.codex_auth.has_codex_auth_available", return_value=True)
def test_vlm_config_default_provider_resolves_codex(_mock_auth_available):
    config = VLMConfig(
        model="gpt-5.3-codex",
        default_provider="openai-codex",
        providers={"openai": {"api_key": "sk-test"}, "openai-codex": {}},
    )

    provider_config, provider_name = config.get_provider_config()

    assert provider_name == "openai-codex"
    assert provider_config == {}


@patch("openviking.models.vlm.backends.codex_auth.has_codex_auth_available", return_value=True)
def test_vlm_config_mixed_providers_do_not_auto_pick_codex(_mock_auth_available):
    config = VLMConfig(
        model="gpt-5.3-codex",
        providers={"openai": {"api_key": "sk-test"}, "openai-codex": {}},
    )

    provider_config, provider_name = config.get_provider_config()

    assert provider_name == "openai"
    assert provider_config["api_key"] == "sk-test"


def test_vlm_config_default_provider_without_model_fails_validation():
    with pytest.raises(ValueError, match="requires 'model' to be set"):
        VLMConfig(default_provider="openai-codex", providers={"openai-codex": {}})


def test_vlm_config_empty_provider_block_without_model_fails_validation():
    with pytest.raises(ValueError, match="requires 'model' to be set"):
        VLMConfig(providers={"openai-codex": {}})


def test_codex_auth_bootstraps_into_openviking_store(tmp_path, monkeypatch):
    ov_auth_path = tmp_path / "codex_auth.json"
    bootstrap_path = tmp_path / "codex_cli_auth.json"
    bootstrap_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "header.payload.signature",
                    "refresh_token": "refresh-token",
                },
                "last_refresh": "2026-04-13T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENVIKING_CODEX_AUTH_PATH", str(ov_auth_path))
    monkeypatch.setenv("OPENVIKING_CODEX_BOOTSTRAP_PATH", str(bootstrap_path))

    creds = resolve_codex_runtime_credentials(refresh_if_expiring=False)

    assert creds["path"] == str(ov_auth_path)
    assert ov_auth_path.exists()
    persisted = json.loads(ov_auth_path.read_text(encoding="utf-8"))
    assert persisted["provider"] == "openai-codex"
    assert persisted["auth_owner"] == "external"
    assert persisted["tokens"]["refresh_token"] == "refresh-token"
    assert persisted["imported_from"] == str(bootstrap_path)


def test_codex_auth_resyncs_external_tokens_before_cached_token_expires(tmp_path, monkeypatch):
    ov_auth_path = tmp_path / "codex_auth.json"
    bootstrap_path = tmp_path / "codex_cli_auth.json"
    old_access_token = _make_jwt_token({"exp": 9999999999, "version": "old"})
    new_access_token = _make_jwt_token({"exp": 9999999999, "version": "new"})
    ov_auth_path.write_text(
        json.dumps(
            {
                "auth_owner": "external",
                "imported_from": str(bootstrap_path),
                "tokens": {
                    "access_token": old_access_token,
                    "refresh_token": "old-refresh",
                },
            }
        ),
        encoding="utf-8",
    )
    bootstrap_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": new_access_token,
                    "refresh_token": "new-refresh",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENVIKING_CODEX_AUTH_PATH", str(ov_auth_path))

    creds = resolve_codex_runtime_credentials()

    assert creds["api_key"] == new_access_token
    persisted = json.loads(ov_auth_path.read_text(encoding="utf-8"))
    assert persisted["tokens"]["refresh_token"] == "new-refresh"


def test_codex_auth_force_refresh_resyncs_external_credentials_once(tmp_path, monkeypatch):
    ov_auth_path = tmp_path / "codex_auth.json"
    bootstrap_path = tmp_path / "codex_cli_auth.json"
    old_access_token = _make_jwt_token({"exp": 9999999999, "version": "old"})
    new_access_token = _make_jwt_token({"exp": 9999999999, "version": "new"})
    payload = {"tokens": {"access_token": new_access_token, "refresh_token": "new-refresh"}}
    bootstrap_path.write_text(json.dumps(payload), encoding="utf-8")
    ov_auth_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": old_access_token,
                    "refresh_token": "old-refresh",
                },
                "auth_owner": "external",
                "imported_from": str(bootstrap_path),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENVIKING_CODEX_AUTH_PATH", str(ov_auth_path))

    with patch.object(
        codex_auth,
        "_write_tokens_to_ov_store",
        wraps=codex_auth._write_tokens_to_ov_store,
    ) as mock_write:
        creds = resolve_codex_runtime_credentials(force_refresh=True)

    assert creds["api_key"] == new_access_token
    assert creds["auth_owner"] == "external"
    assert mock_write.call_count == 1


def test_codex_auth_parse_failure_preserves_external_owner(tmp_path, monkeypatch):
    ov_auth_path = tmp_path / "codex_auth.json"
    bootstrap_path = tmp_path / "codex_cli_auth.json"
    old_access_token = _make_jwt_token({"exp": 9999999999, "version": "old"})
    new_access_token = _make_jwt_token({"exp": 9999999999, "version": "new"})
    ov_payload = {
        "tokens": {
            "access_token": old_access_token,
            "refresh_token": "old-refresh",
        },
        "auth_owner": "external",
        "imported_from": str(bootstrap_path),
    }
    ov_auth_path.write_text(json.dumps(ov_payload), encoding="utf-8")
    bootstrap_path.write_text("{invalid", encoding="utf-8")
    monkeypatch.setenv("OPENVIKING_CODEX_AUTH_PATH", str(ov_auth_path))

    with patch.object(codex_auth, "refresh_codex_oauth") as mock_refresh:
        with pytest.raises(codex_auth.CodexAuthError, match="exists but could not be read"):
            resolve_codex_runtime_credentials(force_refresh=True)

    persisted = json.loads(ov_auth_path.read_text(encoding="utf-8"))
    assert persisted["auth_owner"] == "external"
    assert persisted["imported_from"] == str(bootstrap_path)
    assert persisted["tokens"]["refresh_token"] == "old-refresh"
    mock_refresh.assert_not_called()

    bootstrap_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": new_access_token,
                    "refresh_token": "new-refresh",
                }
            }
        ),
        encoding="utf-8",
    )

    recovered = resolve_codex_runtime_credentials()

    assert recovered["api_key"] == new_access_token
    assert recovered["auth_owner"] == "external"
    recovered_persisted = json.loads(ov_auth_path.read_text(encoding="utf-8"))
    assert recovered_persisted["auth_owner"] == "external"
    assert recovered_persisted["imported_from"] == str(bootstrap_path)


def test_codex_auth_native_login_defaults_to_openviking_owner(tmp_path, monkeypatch):
    ov_auth_path = tmp_path / "codex_auth.json"
    monkeypatch.setenv("OPENVIKING_CODEX_AUTH_PATH", str(ov_auth_path))

    codex_auth.save_codex_tokens("header.payload.signature", "refresh-token")

    persisted = json.loads(ov_auth_path.read_text(encoding="utf-8"))
    assert persisted["auth_owner"] == "openviking"
    assert "imported_from" not in persisted


def test_codex_auth_atomic_write_replaces_file_in_place(tmp_path):
    auth_path = tmp_path / "codex_auth.json"
    auth_path.write_text('{"old": true}\n', encoding="utf-8")

    codex_auth._atomic_write_json_file(auth_path, {"new": True})

    assert json.loads(auth_path.read_text(encoding="utf-8")) == {"new": True}
    assert not list(tmp_path.glob("codex_auth.json.*.tmp"))


def test_codex_auth_store_uses_windows_lock_when_fcntl_is_unavailable(tmp_path, monkeypatch):
    ov_auth_path = tmp_path / "codex_auth.json"
    lock_calls: list[tuple[int, int]] = []

    class _FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd: int, mode: int, size: int) -> None:
            lock_calls.append((mode, size))

    monkeypatch.setenv("OPENVIKING_CODEX_AUTH_PATH", str(ov_auth_path))
    monkeypatch.setattr(codex_auth, "fcntl", None)
    monkeypatch.setattr(codex_auth, "msvcrt", _FakeMsvcrt)

    codex_auth.save_codex_tokens("header.payload.signature", "refresh-token")

    assert ov_auth_path.exists()
    assert lock_calls == [(_FakeMsvcrt.LK_LOCK, 1), (_FakeMsvcrt.LK_UNLCK, 1)]


def _make_jwt_token(payload: dict) -> str:
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")
    )
    return f"header.{encoded}.signature"


def test_codex_auth_takeover_refreshes_when_external_store_missing(tmp_path, monkeypatch):
    ov_auth_path = tmp_path / "codex_auth.json"
    missing_external = tmp_path / "codex_cli_auth.json"
    expiring_access_token = _make_jwt_token({"exp": 0, "aud": "app_testclient"})
    ov_auth_path.write_text(
        json.dumps(
            {
                "provider": "openai-codex",
                "auth_mode": "chatgpt",
                "auth_owner": "external",
                "imported_from": str(missing_external),
                "client_id": "app_testclient",
                "tokens": {
                    "access_token": expiring_access_token,
                    "refresh_token": "refresh-token",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENVIKING_CODEX_AUTH_PATH", str(ov_auth_path))

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": _make_jwt_token({"exp": 9999999999}),
                "refresh_token": "new-refresh",
            }

    monkeypatch.setattr(codex_auth.httpx, "post", lambda *_args, **_kwargs: _Response())

    creds = resolve_codex_runtime_credentials(force_refresh=True)

    assert creds["source"] == "openviking"
    assert creds["auth_owner"] == "openviking"
    persisted = json.loads(ov_auth_path.read_text(encoding="utf-8"))
    assert persisted["auth_owner"] == "openviking"
    assert "imported_from" not in persisted
    assert persisted["tokens"]["refresh_token"] == "new-refresh"


def test_codex_auth_refresh_uses_persisted_client_id(tmp_path, monkeypatch):
    ov_auth_path = tmp_path / "codex_auth.json"
    access_token = _make_jwt_token({"exp": 0, "aud": "app_from_aud"})
    ov_auth_path.write_text(
        json.dumps(
            {
                "provider": "openai-codex",
                "auth_mode": "chatgpt",
                "auth_owner": "openviking",
                "client_id": "app_persisted_client",
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": "refresh-token",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENVIKING_CODEX_AUTH_PATH", str(ov_auth_path))
    recorded: dict[str, str] = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": _make_jwt_token({"exp": 9999999999}),
                "refresh_token": "new-refresh",
            }

    def _fake_post(*_args, **kwargs):
        recorded["client_id"] = kwargs["data"]["client_id"]
        return _Response()

    monkeypatch.setattr(codex_auth.httpx, "post", _fake_post)

    creds = resolve_codex_runtime_credentials(force_refresh=True)

    assert creds["source"] == "openviking"
    assert recorded["client_id"] == "app_persisted_client"


def test_codex_auth_refresh_requires_persisted_client_id(tmp_path, monkeypatch):
    ov_auth_path = tmp_path / "codex_auth.json"
    access_token = _make_jwt_token({"exp": 0, "aud": "app_from_aud"})
    ov_auth_path.write_text(
        json.dumps(
            {
                "provider": "openai-codex",
                "auth_mode": "chatgpt",
                "auth_owner": "openviking",
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": "refresh-token",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENVIKING_CODEX_AUTH_PATH", str(ov_auth_path))

    with pytest.raises(codex_auth.CodexAuthError, match="client_id"):
        resolve_codex_runtime_credentials(force_refresh=True)


@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_codex_sync_client_re_resolves_credentials_per_request(mock_resolve, mock_openai_class):
    mock_resolve.side_effect = [
        {
            "api_key": "oauth-token-a",
            "base_url": "https://chatgpt.com/backend-api/codex",
        },
        {
            "api_key": "oauth-token-b",
            "base_url": "https://chatgpt.com/backend-api/codex",
        },
    ]
    mock_openai_class.side_effect = [
        _build_fake_openai_client("first"),
        _build_fake_openai_client("second"),
    ]

    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})
    client = vlm.get_client()

    first = client.chat.completions.create(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-5.3-codex",
    )
    second = client.chat.completions.create(
        messages=[{"role": "user", "content": "hello again"}],
        model="gpt-5.3-codex",
    )

    assert first.choices[0].message.content == "first"
    assert second.choices[0].message.content == "second"
    assert mock_openai_class.call_args_list[0].kwargs["api_key"] == "oauth-token-a"
    assert mock_openai_class.call_args_list[1].kwargs["api_key"] == "oauth-token-b"


@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_codex_retries_unauthorized_once_with_refreshed_credentials(
    mock_resolve, mock_openai_class
):
    class _UnauthorizedError(Exception):
        status_code = 401

    mock_resolve.side_effect = [
        {"api_key": "stale-token", "base_url": "https://example.test"},
        {"api_key": "fresh-token", "base_url": "https://example.test"},
    ]
    stale_client = MagicMock()
    stale_client.responses.create.side_effect = _UnauthorizedError()
    fresh_client = _build_fake_openai_client("recovered")
    mock_openai_class.side_effect = [stale_client, fresh_client]

    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})

    assert vlm.get_completion("hello") == "recovered"
    assert mock_resolve.call_args_list == [call(), call(force_refresh=True)]
    stale_client.responses.create.assert_called_once()
    fresh_client.responses.create.assert_called_once()


@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_codex_does_not_retry_a_second_unauthorized_response(mock_resolve, mock_openai_class):
    class _UnauthorizedError(Exception):
        status_code = 401

    mock_resolve.side_effect = [
        {"api_key": "stale-token", "base_url": "https://example.test"},
        {"api_key": "still-invalid-token", "base_url": "https://example.test"},
    ]
    first_client = MagicMock()
    second_client = MagicMock()
    first_client.responses.create.side_effect = _UnauthorizedError()
    second_client.responses.create.side_effect = _UnauthorizedError()
    mock_openai_class.side_effect = [first_client, second_client]
    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})

    with pytest.raises(_UnauthorizedError):
        vlm.get_completion("hello")

    assert mock_resolve.call_args_list == [call(), call(force_refresh=True)]
    first_client.responses.create.assert_called_once()
    second_client.responses.create.assert_called_once()


@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_codex_translates_tool_history_into_responses_input(mock_resolve, mock_openai_class):
    mock_resolve.return_value = {
        "api_key": "oauth-token",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    mock_real_client = MagicMock()
    mock_real_client.responses.create.return_value = _MockResponsesStream(
        _build_final_response("final answer")
    )
    mock_openai_class.return_value = mock_real_client

    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})
    response = vlm.get_completion(
        messages=[
            {"role": "user", "content": "What is the weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"SF"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": '{"temperature":72}',
            },
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                },
            }
        ],
    )

    assert response.content == "final answer"
    input_items = mock_real_client.responses.create.call_args.kwargs["input"]
    assert input_items == [
        {"role": "user", "content": "What is the weather?"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_weather",
            "arguments": '{"city":"SF"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"temperature":72}',
        },
    ]


@pytest.mark.parametrize(
    ("content", "expected_output"),
    [
        (
            [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
            "firstsecond",
        ),
        ({"ok": True}, '{"ok": true}'),
    ],
)
def test_codex_preserves_legacy_text_tool_output_serialization(content, expected_output):
    converted = _convert_message_for_responses(
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": content,
        }
    )

    assert converted == [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": expected_output,
        }
    ]


@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_codex_preserves_images_in_function_call_output(mock_resolve, mock_openai_class):
    mock_resolve.return_value = {
        "api_key": "oauth-token",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    mock_real_client = MagicMock()
    mock_real_client.responses.create.return_value = _MockResponsesStream(
        _build_final_response("image inspected")
    )
    mock_openai_class.return_value = mock_real_client
    image_url = "data:image/png;base64,aW1hZ2U="

    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})
    result = vlm.get_completion(
        messages=[
            {"role": "user", "content": "Inspect the image"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_image", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [
                    {"type": "text", "text": "Source: image.png"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ]
    )

    assert result == "image inspected"
    input_items = mock_real_client.responses.create.call_args.kwargs["input"]
    assert input_items[-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": [
            {"type": "input_text", "text": "Source: image.png"},
            {"type": "input_image", "image_url": image_url},
        ],
    }


def test_codex_auth_invalid_exp_claim_is_treated_as_expiring():
    assert codex_auth._codex_access_token_is_expiring("not-a-jwt", skew_seconds=60) is True
    assert (
        codex_auth._codex_access_token_is_expiring(
            _make_jwt_token({"exp": "not-a-number"}),
            skew_seconds=60,
        )
        is True
    )
