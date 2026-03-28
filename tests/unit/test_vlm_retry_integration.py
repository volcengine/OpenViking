# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for VLM backends with unified retry logic.

Tests cover (using OpenAI backend as representative):
- completion retries on 429 (transient)
- completion does NOT retry on 401 (permanent)
- vision completion now retries (was zero before)
- uses config max_retries
- max_retries parameter removed from get_completion_async signature
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _HttpError(Exception):
    """Fake HTTP error carrying a numeric status code."""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


def _make_fake_response(content: str = "ok") -> SimpleNamespace:
    """Build a minimal fake OpenAI ChatCompletion response."""
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[choice], usage=usage)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def openai_vlm():
    """Create an OpenAIVLM instance with mocked clients."""
    from openviking.models.vlm.backends.openai_vlm import OpenAIVLM

    vlm = OpenAIVLM(
        {
            "api_key": "sk-test",
            "model": "gpt-4o-mini",
            "provider": "openai",
            "max_retries": 2,
        }
    )

    # Mock sync client
    mock_sync = MagicMock()
    vlm._sync_client = mock_sync

    # Mock async client
    mock_async = MagicMock()
    vlm._async_client = mock_async

    return vlm


# ---------------------------------------------------------------------------
# Tests: get_completion_async retries on 429
# ---------------------------------------------------------------------------


class TestCompletionAsyncRetries:
    async def test_retries_on_429(self, openai_vlm):
        """get_completion_async should retry on 429 (transient) and succeed."""
        errors = [_HttpError(429), _HttpError(429)]
        call_count = 0

        async def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if errors:
                raise errors.pop(0)
            return _make_fake_response("success")

        openai_vlm._async_client.chat.completions.create = fake_create

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await openai_vlm.get_completion_async(prompt="test")

        assert result == "success"
        assert call_count == 3  # 2 failures + 1 success

    async def test_no_retry_on_401(self, openai_vlm):
        """get_completion_async should NOT retry on 401 (permanent)."""
        call_count = 0

        async def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            raise _HttpError(401, "Unauthorized")

        openai_vlm._async_client.chat.completions.create = fake_create

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(_HttpError):
                await openai_vlm.get_completion_async(prompt="test")

        assert call_count == 1  # no retries

    async def test_uses_config_max_retries(self):
        """Backend should use self.max_retries from config, not a param."""
        from openviking.models.vlm.backends.openai_vlm import OpenAIVLM

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-4o-mini",
                "provider": "openai",
                "max_retries": 5,
            }
        )
        assert vlm.max_retries == 5

        # Config default is now 3
        vlm2 = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-4o-mini",
                "provider": "openai",
            }
        )
        assert vlm2.max_retries == 3


# ---------------------------------------------------------------------------
# Tests: get_vision_completion_async now retries
# ---------------------------------------------------------------------------


class TestVisionCompletionAsyncRetries:
    async def test_vision_retries_on_429(self, openai_vlm):
        """get_vision_completion_async should retry on 429 (was zero retry before)."""
        errors = [_HttpError(429)]
        call_count = 0

        async def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if errors:
                raise errors.pop(0)
            return _make_fake_response("vision ok")

        openai_vlm._async_client.chat.completions.create = fake_create

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await openai_vlm.get_vision_completion_async(
                prompt="describe",
                images=["http://example.com/img.png"],
            )

        assert result == "vision ok"
        assert call_count == 2  # 1 failure + 1 success


# ---------------------------------------------------------------------------
# Tests: sync completion retries
# ---------------------------------------------------------------------------


class TestCompletionSyncRetries:
    def test_sync_retries_on_429(self, openai_vlm):
        """get_completion should retry on 429."""
        errors = [_HttpError(429)]
        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if errors:
                raise errors.pop(0)
            return _make_fake_response("sync ok")

        openai_vlm._sync_client.chat.completions.create = fake_create

        with patch("time.sleep"):
            result = openai_vlm.get_completion(prompt="test")

        assert result == "sync ok"
        assert call_count == 2

    def test_sync_vision_retries_on_503(self, openai_vlm):
        """get_vision_completion should retry on 503."""
        errors = [_HttpError(503)]
        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if errors:
                raise errors.pop(0)
            return _make_fake_response("vision sync ok")

        openai_vlm._sync_client.chat.completions.create = fake_create

        with patch("time.sleep"):
            result = openai_vlm.get_vision_completion(
                prompt="describe",
                images=["http://example.com/img.png"],
            )

        assert result == "vision sync ok"
        assert call_count == 2


# ---------------------------------------------------------------------------
# Tests: signature change verification
# ---------------------------------------------------------------------------


class TestSignatureChange:
    def test_no_max_retries_in_get_completion_async(self):
        """get_completion_async should no longer accept max_retries parameter."""
        from openviking.models.vlm.backends.openai_vlm import OpenAIVLM

        sig = inspect.signature(OpenAIVLM.get_completion_async)
        param_names = list(sig.parameters.keys())

        assert "max_retries" not in param_names, (
            f"max_retries should be removed from get_completion_async, got params: {param_names}"
        )

    def test_no_max_retries_in_base_get_completion_async(self):
        """VLMBase.get_completion_async should no longer accept max_retries parameter."""
        from openviking.models.vlm.base import VLMBase

        sig = inspect.signature(VLMBase.get_completion_async)
        param_names = list(sig.parameters.keys())

        assert "max_retries" not in param_names, (
            f"max_retries should be removed from VLMBase.get_completion_async, got params: {param_names}"
        )

    def test_no_max_retries_in_litellm_get_completion_async(self):
        """LiteLLMVLMProvider.get_completion_async should no longer accept max_retries."""
        from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider

        sig = inspect.signature(LiteLLMVLMProvider.get_completion_async)
        param_names = list(sig.parameters.keys())

        assert "max_retries" not in param_names

    def test_no_max_retries_in_volcengine_get_completion_async(self):
        """VolcEngineVLM.get_completion_async should no longer accept max_retries."""
        from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM

        sig = inspect.signature(VolcEngineVLM.get_completion_async)
        param_names = list(sig.parameters.keys())

        assert "max_retries" not in param_names


# ---------------------------------------------------------------------------
# Tests: OpenAI SDK retry disabled
# ---------------------------------------------------------------------------


class TestOpenAISDKRetryDisabled:
    def test_sync_client_max_retries_zero(self):
        """OpenAI sync client should be created with max_retries=0."""
        from openviking.models.vlm.backends.openai_vlm import OpenAIVLM

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-4o-mini",
                "provider": "openai",
            }
        )

        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value = MagicMock()
            vlm._sync_client = None  # force re-creation
            vlm.get_client()
            call_kwargs = mock_openai.call_args
            assert call_kwargs[1].get("max_retries") == 0 or (
                len(call_kwargs[0]) == 0 and call_kwargs.kwargs.get("max_retries") == 0
            )

    def test_async_client_max_retries_zero(self):
        """OpenAI async client should be created with max_retries=0."""
        from openviking.models.vlm.backends.openai_vlm import OpenAIVLM

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-4o-mini",
                "provider": "openai",
            }
        )

        with patch("openai.AsyncOpenAI") as mock_async_openai:
            mock_async_openai.return_value = MagicMock()
            vlm._async_client = None  # force re-creation
            vlm.get_async_client()
            call_kwargs = mock_async_openai.call_args
            assert call_kwargs[1].get("max_retries") == 0 or (
                len(call_kwargs[0]) == 0 and call_kwargs.kwargs.get("max_retries") == 0
            )
