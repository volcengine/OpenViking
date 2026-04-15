# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openviking.models.vlm.backends import codex_auth
from openviking.models.vlm.backends.codex_auth import resolve_codex_runtime_credentials
from openviking.models.vlm.backends.codex_vlm import CodexVLM
from openviking_cli.utils.config.vlm_config import VLMConfig


class _MockResponsesStream:
    def __init__(self, final_response):
        self._final_response = final_response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(())

    def get_final_response(self):
        return self._final_response


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


@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_codex_text_completion_uses_responses_api(mock_resolve, mock_openai_class):
    mock_resolve.return_value = {
        "api_key": "oauth-token",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    mock_real_client = MagicMock()
    mock_real_client.responses.stream.return_value = _MockResponsesStream(_build_final_response("hello from codex"))
    mock_openai_class.return_value = mock_real_client

    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})
    result = vlm.get_completion("hello")

    assert result == "hello from codex"
    call_kwargs = mock_real_client.responses.stream.call_args.kwargs
    assert call_kwargs["model"] == "gpt-5.3-codex"
    assert call_kwargs["input"] == [{"role": "user", "content": "hello"}]
    assert "messages" not in call_kwargs


@patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI")
@patch("openviking.models.vlm.backends.codex_vlm.resolve_codex_runtime_credentials")
def test_codex_vision_completion_converts_images(mock_resolve, mock_openai_class):
    mock_resolve.return_value = {
        "api_key": "oauth-token",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    mock_real_client = MagicMock()
    mock_real_client.responses.stream.return_value = _MockResponsesStream(_build_final_response("image result"))
    mock_openai_class.return_value = mock_real_client

    vlm = CodexVLM({"provider": "openai-codex", "model": "gpt-5.3-codex"})
    result = vlm.get_vision_completion("describe", [b"\x89PNG\r\n\x1a\n0000"])

    assert result == "image result"
    call_kwargs = mock_real_client.responses.stream.call_args.kwargs
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
    mock_real_client.responses.stream.return_value = _MockResponsesStream(_build_final_response("async hello"))
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


@patch("openviking.models.vlm.backends.codex_auth.has_codex_auth_available", return_value=True)
def test_vlm_config_accepts_codex_without_api_key(_mock_auth_available):
    config = VLMConfig(provider="openai-codex", model="gpt-5.3-codex")

    assert config.is_available() is True
    assert config.get_vlm_instance().__class__.__name__ == "CodexVLM"


@patch("openviking.models.vlm.backends.codex_auth.has_codex_auth_available", return_value=True)
def test_vlm_config_default_provider_resolves_codex(_mock_auth_available):
    config = VLMConfig(
        model="gpt-5.3-codex",
        default_provider="codex",
        providers={"openai": {"api_key": "sk-test"}, "codex": {}},
    )

    provider_config, provider_name = config.get_provider_config()

    assert provider_name == "openai-codex"
    assert provider_config == {}


@patch("openviking.models.vlm.backends.codex_auth.has_codex_auth_available", return_value=True)
def test_vlm_config_mixed_providers_do_not_auto_pick_codex(_mock_auth_available):
    config = VLMConfig(
        model="gpt-5.3-codex",
        providers={"openai": {"api_key": "sk-test"}, "codex": {}},
    )

    provider_config, provider_name = config.get_provider_config()

    assert provider_name == "openai"
    assert provider_config["api_key"] == "sk-test"


def test_vlm_config_default_provider_without_model_fails_validation():
    with pytest.raises(ValueError, match="requires 'model' to be set"):
        VLMConfig(default_provider="codex", providers={"codex": {}})


def test_vlm_config_empty_provider_block_without_model_fails_validation():
    with pytest.raises(ValueError, match="requires 'model' to be set"):
        VLMConfig(providers={"codex": {}})


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
    monkeypatch.delenv("OPENVIKING_CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENVIKING_CODEX_REFRESH_TOKEN", raising=False)

    creds = resolve_codex_runtime_credentials(refresh_if_expiring=False)

    assert creds["path"] == str(ov_auth_path)
    assert ov_auth_path.exists()
    persisted = json.loads(ov_auth_path.read_text(encoding="utf-8"))
    assert persisted["provider"] == "openai-codex"
    assert persisted["auth_owner"] == "external"
    assert persisted["tokens"]["refresh_token"] == "refresh-token"
    assert persisted["imported_from"] == str(bootstrap_path)


def test_codex_auth_native_login_defaults_to_openviking_owner(tmp_path, monkeypatch):
    ov_auth_path = tmp_path / "codex_auth.json"
    monkeypatch.setenv("OPENVIKING_CODEX_AUTH_PATH", str(ov_auth_path))

    codex_auth.save_codex_tokens("header.payload.signature", "refresh-token")

    persisted = json.loads(ov_auth_path.read_text(encoding="utf-8"))
    assert persisted["auth_owner"] == "openviking"
    assert "imported_from" not in persisted


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
