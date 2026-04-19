# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the shared ``vlm.max_concurrent`` semaphore.

``max_concurrent`` used to cap only the semantic queue worker path. Callers
that invoked ``get_completion_async`` directly (for example the session
archive summary generator at ``openviking/session/session.py``) bypassed the
cap. These tests lock in that the cap now applies to every async VLM call.
"""

import asyncio

import pytest

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider


@pytest.mark.asyncio
async def test_get_completion_async_respects_max_concurrent(monkeypatch):
    vlm = LiteLLMVLMProvider(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-fake",
            "api_base": "https://example.invalid",
            "max_concurrent": 2,
            "max_retries": 0,
        }
    )

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def fake_acompletion(**kwargs):
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1

        class _Resp:
            choices = [
                type(
                    "c",
                    (),
                    {"message": type("m", (), {"content": "ok"})()},
                )()
            ]
            usage = type(
                "u",
                (),
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )()

        return _Resp()

    monkeypatch.setattr(
        "openviking.models.vlm.backends.litellm_vlm.acompletion",
        fake_acompletion,
    )

    await asyncio.gather(*(vlm.get_completion_async(prompt=f"q{i}") for i in range(10)))

    assert peak <= 2, f"expected peak <= max_concurrent (2), got {peak}"


@pytest.mark.asyncio
async def test_max_concurrent_disabled_when_nonpositive(monkeypatch):
    vlm = LiteLLMVLMProvider(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-fake",
            "api_base": "https://example.invalid",
            "max_concurrent": 0,
            "max_retries": 0,
        }
    )

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def fake_acompletion(**kwargs):
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        async with lock:
            in_flight -= 1

        class _Resp:
            choices = [
                type(
                    "c",
                    (),
                    {"message": type("m", (), {"content": "ok"})()},
                )()
            ]
            usage = type(
                "u",
                (),
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )()

        return _Resp()

    monkeypatch.setattr(
        "openviking.models.vlm.backends.litellm_vlm.acompletion",
        fake_acompletion,
    )

    await asyncio.gather(*(vlm.get_completion_async(prompt=f"q{i}") for i in range(5)))

    assert peak == 5, f"expected uncapped concurrency (5), got {peak}"
