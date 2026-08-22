# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ``vlm.timeout`` propagation and derived async retry deadlines.

Before this was wired through, ``_build_openai_client_kwargs`` exposed a
``timeout`` parameter (#1208) but callers never passed it, so the default
timeout was always used and end users could not override it via ``ov.conf``.
These tests lock in that the config value flows through to the underlying
OpenAI and LiteLLM clients. They also cover the cancellation-cooperative retry
deadline derived by OpenAI-compatible async backends and the threaded Codex
exception to that policy.
"""

import asyncio
from unittest import mock

import httpx
import openai
import pytest
from pydantic import ValidationError

from openviking.models.vlm.backends.openai_vlm import (
    OpenAIVLM,
    _build_openai_client_kwargs,
)
from openviking_cli.utils.config.vlm_config import VLMConfig


def test_vlm_config_accepts_timeout():
    cfg = VLMConfig(model="gpt-4o-mini", api_key="sk-x", timeout=120.0)
    assert cfg.timeout == 120.0


def test_vlm_config_timeout_defaults_to_600():
    cfg = VLMConfig(model="gpt-4o-mini", api_key="sk-x")
    assert cfg.timeout == 600.0


def test_vlm_config_rejects_non_positive_timeout():
    with pytest.raises(ValidationError):
        VLMConfig(model="gpt-4o-mini", api_key="sk-x", timeout=0)


def test_build_openai_client_kwargs_default_timeout():
    kwargs = _build_openai_client_kwargs("openai", "sk-x", "https://example.invalid", None, None)
    assert kwargs["timeout"] == 600.0


def test_build_openai_client_kwargs_custom_timeout():
    kwargs = _build_openai_client_kwargs(
        "openai",
        "sk-x",
        "https://example.invalid",
        None,
        None,
        timeout=120.0,
    )
    assert kwargs["timeout"] == 120.0


def test_openai_vlm_propagates_config_timeout():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "timeout": 120.0,
        }
    )
    assert vlm.timeout == 120.0

    with mock.patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI") as fake:
        vlm.get_client()
    assert fake.call_args.kwargs.get("timeout") == 120.0


def test_openai_vlm_defaults_to_600_timeout_when_config_omits_it():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
        }
    )
    assert vlm.timeout == 600.0

    with mock.patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI") as fake:
        vlm.get_client()
    assert fake.call_args.kwargs.get("timeout") == 600.0


def test_litellm_build_kwargs_includes_timeout():
    from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider

    vlm = LiteLLMVLMProvider(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "timeout": 90.0,
        }
    )
    kwargs = vlm._build_text_kwargs(prompt="hi")
    assert kwargs["timeout"] == 90.0


def test_vlm_config_propagates_timeout_to_codex_backend():
    cfg = VLMConfig(
        provider="openai-codex",
        model="gpt-5.3-codex",
        api_key="oauth-token",
        api_base="https://example.invalid/codex",
        timeout=45.0,
    )

    vlm = cfg.get_vlm_instance()

    assert vlm.timeout == 45.0


def test_codex_vlm_propagates_config_timeout():
    from openviking.models.vlm.backends.codex_vlm import CodexVLM
    from tests.unit.test_codex_vlm import _build_final_response, _MockResponsesStream

    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": "gpt-5.3-codex",
            "api_key": "oauth-token",
            "api_base": "https://example.invalid/codex",
            "timeout": 45.0,
        }
    )

    with mock.patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI") as fake:
        fake.return_value.responses.create.return_value = _MockResponsesStream(
            _build_final_response("timeout ok")
        )
        assert vlm.get_completion("hello") == "timeout ok"

    assert fake.call_args.kwargs.get("timeout") == 45.0


def test_codex_vlm_skips_asyncio_deadline_for_threaded_requests():
    from openviking.models.vlm.backends.codex_vlm import CodexVLM

    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": "gpt-5.3-codex",
            "api_key": "oauth-token",
            "api_base": "https://example.invalid/codex",
            "timeout": 45.0,
        }
    )

    assert vlm._retry_timeout_seconds() is None


def test_openai_vlm_retry_deadline_scales_with_attempts():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "timeout": 10.0,
            "max_retries": 3,
        }
    )
    assert vlm._retry_timeout_seconds() == 40.0


@pytest.mark.asyncio
async def test_openai_vlm_retry_deadline_cancels_async_transport(monkeypatch):
    request_started = asyncio.Event()
    cancellation_seen = asyncio.Event()

    async def handle_request(_request: httpx.Request) -> httpx.Response:
        request_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            raise
        raise AssertionError("unreachable")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
    client = openai.AsyncOpenAI(
        api_key="sk-x",
        base_url="https://example.invalid/v1",
        http_client=http_client,
        max_retries=0,
    )
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid/v1",
            "timeout": 1.0,
            "max_retries": 0,
        }
    )
    monkeypatch.setattr(vlm, "get_async_client", lambda: client)

    try:
        with pytest.raises(TimeoutError, match="timed out after 1.0s"):
            await vlm.get_completion_async("hello")
    finally:
        await client.close()

    assert request_started.is_set()
    assert cancellation_seen.is_set()
