# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
from pathlib import Path

import httpx
import openai
import pytest

from openviking.models.model_error import ModelError, ModelErrorKind
from openviking.models.vlm.base import VLMBase
from openviking.models.vlm.retry_vlm import RetryVLM
from openviking.utils.model_retry import model_retry_scope
from openviking_cli.utils.config.vlm_config import VLMConfig


class ProviderError(ModelError):
    def __init__(self, status_code: int):
        messages = {
            400: "400 invalid parameter",
            401: "401 unauthorized",
            503: "503 service unavailable",
        }
        kinds = {
            400: ModelErrorKind.INVALID_REQUEST,
            401: ModelErrorKind.AUTHENTICATION_ERROR,
            503: ModelErrorKind.TRANSIENT_SERVICE_ERROR,
        }
        super().__init__(kinds[status_code], messages[status_code], status_code=status_code)


class FakeVLM(VLMBase):
    def __init__(self, outcomes, *, media_outcomes=None):
        super().__init__({"provider": "fake", "model": "fake-model", "max_retries": 2})
        self.outcomes = list(outcomes)
        self.media_outcomes = list(media_outcomes or ["media"])
        self.calls = []
        self.closed = False

    def _next(self, method, kwargs):
        self.calls.append((method, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get_completion(self, **kwargs):
        return self._next("text", kwargs)

    async def get_completion_async(self, **kwargs):
        return self._next("async_text", kwargs)

    def get_vision_completion(self, **kwargs):
        return self._next("vision", kwargs)

    async def get_vision_completion_async(self, **kwargs):
        return self._next("async_vision", kwargs)

    def supports_media(self, **kwargs):
        return True

    async def get_media_completion_async(self, **kwargs):
        self.calls.append(("media", kwargs))
        outcome = self.media_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr("openviking.utils.model_retry.time.sleep", lambda _delay: None)
    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", _no_sleep)


@pytest.mark.parametrize(
    ("method", "expected_method"),
    [
        ("get_completion", "text"),
        ("get_vision_completion", "vision"),
    ],
)
def test_retry_vlm_retries_sync_transient_failures(method, expected_method):
    inner = FakeVLM([ProviderError(503), "ok"])
    vlm = RetryVLM(inner)

    result = getattr(vlm, method)(prompt="hello")

    assert result == "ok"
    assert [name for name, _kwargs in inner.calls] == [expected_method, expected_method]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "expected_method"),
    [
        ("get_completion_async", "async_text"),
        ("get_vision_completion_async", "async_vision"),
    ],
)
async def test_retry_vlm_scoped_failure_restores_async_retries(method, expected_method):
    error = ProviderError(503)
    inner = FakeVLM([error, ProviderError(503), "ok"])
    vlm = RetryVLM(inner)

    async with model_retry_scope(0):
        with pytest.raises(ProviderError) as raised:
            await getattr(vlm, method)(prompt="hello")
    assert raised.value is error
    assert len(inner.calls) == 1

    result = await getattr(vlm, method)(prompt="hello")

    assert result == "ok"
    assert [name for name, _kwargs in inner.calls] == [expected_method] * 3


@pytest.mark.parametrize("status_code", [400, 401])
def test_retry_vlm_does_not_retry_permanent_or_auth_errors(status_code):
    error = ProviderError(status_code)
    inner = FakeVLM([error, "unexpected"])

    with pytest.raises(ProviderError) as raised:
        RetryVLM(inner).get_completion(prompt="hello")

    assert raised.value is error
    assert len(inner.calls) == 1


def test_retry_vlm_honors_zero_retries():
    inner = FakeVLM([ProviderError(503), "unexpected"])
    inner.max_retries = 0
    vlm = RetryVLM(inner)
    vlm.max_retries = 0

    with pytest.raises(ProviderError):
        vlm.get_completion(prompt="hello")

    assert len(inner.calls) == 1


def test_retry_vlm_uses_max_retries_as_additional_attempts():
    errors = [ProviderError(503) for _ in range(3)]
    inner = FakeVLM(errors)

    with pytest.raises(ProviderError) as raised:
        RetryVLM(inner).get_completion(prompt="hello")

    assert raised.value is errors[-1]
    assert len(inner.calls) == 3


@pytest.mark.asyncio
async def test_retry_vlm_does_not_retry_cancelled_request():
    started = asyncio.Event()

    class BlockingVLM(FakeVLM):
        async def get_completion_async(self, **kwargs):
            self.calls.append(("async_text", kwargs))
            started.set()
            await asyncio.Event().wait()

    inner = BlockingVLM([])
    task = asyncio.create_task(RetryVLM(inner).get_completion_async(prompt="hello"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(inner.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get_completion_async", "get_vision_completion_async"])
async def test_retry_vlm_bounds_each_async_attempt_by_timeout(method):
    # SDK/httpx timeouts bound each socket read; a response that keeps trickling
    # bytes never trips them. ``timeout`` must bound the whole attempt.
    class StallOnceVLM(FakeVLM):
        async def _stall_once(self, name, kwargs):
            if not self.calls:
                self.calls.append((name, kwargs))
                await asyncio.Event().wait()
            return self._next(name, kwargs)

        async def get_completion_async(self, **kwargs):
            return await self._stall_once("async_text", kwargs)

        async def get_vision_completion_async(self, **kwargs):
            return await self._stall_once("async_vision", kwargs)

    inner = StallOnceVLM(["ok"])
    inner.timeout = 0.05

    result = await asyncio.wait_for(getattr(RetryVLM(inner), method)(prompt="hello"), 5)

    assert result == "ok"
    assert len(inner.calls) == 2


@pytest.mark.asyncio
async def test_retry_vlm_forwards_extension_kwargs_and_does_not_retry_media(tmp_path):
    media_error = ProviderError(503)
    inner = FakeVLM(["ok"], media_outcomes=[media_error, "unexpected"])
    vlm = RetryVLM(inner)

    assert await vlm.get_completion_async(prompt="hello", reasoning_effort="high") == "ok"
    assert inner.calls[0][1]["reasoning_effort"] == "high"

    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"media")
    with pytest.raises(ProviderError) as raised:
        await vlm.get_media_completion_async(
            prompt="describe",
            media_path=Path(media_path),
            filename="clip.mp4",
            media_type="video/mp4",
        )

    assert raised.value is media_error
    assert [name for name, _kwargs in inner.calls].count("media") == 1


def test_retry_vlm_delegates_token_tracker_and_close():
    inner = FakeVLM(["ok"])
    vlm = RetryVLM(inner)

    assert vlm.token_tracker is inner.token_tracker
    vlm.close()
    assert inner.closed is True


def test_vlm_config_switches_model_after_primary_not_found():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "primary.example.test":
            return httpx.Response(
                404,
                json={"error": {"code": "model_not_found", "message": "Model does not exist"}},
            )
        assert request.url.host == "backup.example.test"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "backup response"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    config = VLMConfig(
        model="missing-model",
        provider="openai",
        max_retries=2,
        credentials=[
            {
                "id": "primary",
                "api_key": "primary-key",
                "api_base": "https://primary.example.test/v1",
            },
            {
                "id": "backup",
                "api_key": "backup-key",
                "api_base": "https://backup.example.test/v1",
                "model": "backup-model",
            },
        ],
    )
    instance = config.get_vlm_instance()
    for credential in instance._vlm_instances:
        credential.inner._sync_client = openai.OpenAI(
            api_key="test-key",
            base_url=credential.inner.api_base,
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    try:
        assert instance.get_completion(prompt="hello") == "backup response"
        assert calls == ["primary.example.test", "backup.example.test"]
        assert instance.active_credential_id == "backup"
    finally:
        instance.close()
