# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for per-session x-opencode-session header injection (#4782)."""

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.request_session import (
    OPENCODE_SESSION_HEADER,
    bind_vlm_session_id,
    get_or_create_vlm_session_id,
)


def _openai_config(**overrides):
    config = {
        "provider": "openai",
        "api_key": "sk-test",
        "api_base": "https://opencode.ai/zen/go/v1",
        "model": "gpt-4o-mini",
        "max_retries": 0,
    }
    config.update(overrides)
    return config


@patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
def test_completion_injects_opencode_session_header(mock_openai_class):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    choice = MagicMock()
    choice.message.content = "ok"
    choice.finish_reason = "stop"
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[choice], usage=None
    )

    vlm = OpenAIVLM(_openai_config())
    assert vlm.get_completion(prompt="hi") == "ok"

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert OPENCODE_SESSION_HEADER in call_kwargs["extra_headers"]
    assert call_kwargs["extra_headers"][OPENCODE_SESSION_HEADER].startswith("ov-")


@patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
def test_completion_reuses_sticky_session_across_calls(mock_openai_class):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    choice = MagicMock()
    choice.message.content = "ok"
    choice.finish_reason = "stop"
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[choice], usage=None
    )

    vlm = OpenAIVLM(_openai_config())
    vlm.get_completion(prompt="one")
    vlm.get_completion(prompt="two")

    first = mock_client.chat.completions.create.call_args_list[0].kwargs["extra_headers"][
        OPENCODE_SESSION_HEADER
    ]
    second = mock_client.chat.completions.create.call_args_list[1].kwargs["extra_headers"][
        OPENCODE_SESSION_HEADER
    ]
    assert first == second


@patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
def test_bound_session_id_is_forwarded(mock_openai_class):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    choice = MagicMock()
    choice.message.content = "ok"
    choice.finish_reason = "stop"
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[choice], usage=None
    )

    vlm = OpenAIVLM(_openai_config())
    with bind_vlm_session_id("session-abc"):
        vlm.get_completion(prompt="hi")

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["extra_headers"][OPENCODE_SESSION_HEADER] == "session-abc"


@patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
def test_explicit_extra_headers_win(mock_openai_class):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    choice = MagicMock()
    choice.message.content = "ok"
    choice.finish_reason = "stop"
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[choice], usage=None
    )

    vlm = OpenAIVLM(
        _openai_config(extra_headers={OPENCODE_SESSION_HEADER: "fixed-global"})
    )
    vlm.get_completion(prompt="hi")

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert "extra_headers" not in call_kwargs
    # Still present on the client default_headers from config.
    assert mock_openai_class.call_args.kwargs["default_headers"][OPENCODE_SESSION_HEADER] == (
        "fixed-global"
    )


@patch("openviking.models.vlm.backends.openai_vlm.openai.AzureOpenAI")
def test_azure_does_not_auto_inject(mock_azure_class):
    mock_client = MagicMock()
    mock_azure_class.return_value = mock_client
    choice = MagicMock()
    choice.message.content = "ok"
    choice.finish_reason = "stop"
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[choice], usage=None
    )

    vlm = OpenAIVLM(
        {
            "provider": "azure",
            "api_key": "sk-test",
            "api_base": "https://example.openai.azure.com",
            "api_version": "2024-02-01",
            "model": "gpt-4o-mini",
            "max_retries": 0,
        }
    )
    assert vlm.get_completion(prompt="hi") == "ok"
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert "extra_headers" not in call_kwargs


def test_get_or_create_is_sticky_in_context():
    a = get_or_create_vlm_session_id()
    b = get_or_create_vlm_session_id()
    assert a == b
    assert a.startswith("ov-")


@pytest.mark.asyncio
@patch("openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI")
async def test_async_completion_injects_header(mock_async_openai_class):
    mock_client = MagicMock()
    mock_async_openai_class.return_value = mock_client
    choice = MagicMock()
    choice.message.content = "ok"
    choice.finish_reason = "stop"

    async def _create(**kwargs):
        return MagicMock(choices=[choice], usage=None)

    mock_client.chat.completions.create = _create
    # wrap to capture kwargs
    captured = {}

    async def _create_capture(**kwargs):
        captured.update(kwargs)
        return MagicMock(choices=[choice], usage=None)

    mock_client.chat.completions.create = _create_capture

    vlm = OpenAIVLM(_openai_config())
    assert await vlm.get_completion_async(prompt="hi") == "ok"
    assert OPENCODE_SESSION_HEADER in captured["extra_headers"]


@patch("openviking.models.vlm.backends.litellm_vlm.completion")
def test_litellm_completion_injects_opencode_session_header(mock_completion):
    from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider

    choice = MagicMock()
    choice.message.content = "ok"
    choice.message.tool_calls = None
    choice.finish_reason = "stop"
    mock_completion.return_value = MagicMock(choices=[choice], usage=None)

    vlm = LiteLLMVLMProvider(
        {
            "provider": "litellm",
            "api_key": "sk-test",
            "api_base": "https://opencode.ai/zen/go/v1",
            "model": "openai/gpt-4o-mini",
            "max_retries": 0,
        }
    )
    assert vlm.get_completion(prompt="hi") == "ok"
    call_kwargs = mock_completion.call_args.kwargs
    assert OPENCODE_SESSION_HEADER in call_kwargs["extra_headers"]
    assert call_kwargs["extra_headers"][OPENCODE_SESSION_HEADER].startswith("ov-")


@patch("openviking.models.vlm.backends.litellm_vlm.completion")
def test_litellm_explicit_header_wins(mock_completion):
    from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider

    choice = MagicMock()
    choice.message.content = "ok"
    choice.message.tool_calls = None
    choice.finish_reason = "stop"
    mock_completion.return_value = MagicMock(choices=[choice], usage=None)

    vlm = LiteLLMVLMProvider(
        {
            "provider": "litellm",
            "api_key": "sk-test",
            "api_base": "https://opencode.ai/zen/go/v1",
            "model": "openai/gpt-4o-mini",
            "max_retries": 0,
            "extra_headers": {OPENCODE_SESSION_HEADER: "fixed-litellm"},
        }
    )
    assert vlm.get_completion(prompt="hi") == "ok"
    call_kwargs = mock_completion.call_args.kwargs
    assert call_kwargs["extra_headers"][OPENCODE_SESSION_HEADER] == "fixed-litellm"
