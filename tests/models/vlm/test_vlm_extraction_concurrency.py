# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for bounding concurrent VLM calls during memory extraction (#3008).

Multiple sessions committing at once each spawn summary / long-term / execution
VLM calls. ``vlm.max_concurrent`` must bound the in-flight
``get_completion_async`` count process-wide (shared across sessions and VLM
instances), and 429 rate-limit responses must back off and retry rather than
failing the extraction.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM


def _fake_response(text: str = "ok"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )


def _make_vlm(max_concurrent: int, max_retries: int = 2) -> OpenAIVLM:
    return OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-test",
            "api_base": "http://localhost",
            "max_concurrent": max_concurrent,
            "max_retries": max_retries,
        }
    )


def _fake_client(create_fn):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn))
    )


class _FakeQuotaRateLimit(Exception):
    """A 429 the provider worded as quota-exceeded.

    ``classify_api_error`` matches the ``quotaexceeded`` pattern first, so this
    is classified as ``quota_exceeded`` and is NOT retryable via
    ``is_retryable_api_error`` (the historical behavior). It is however a
    rate-limit burst that ``is_retryable_rate_limit_error`` recognises, which is
    what the #3008 fix wires into VLM completion retries.
    """


_QUOTA_429_MESSAGE = "Error code: 429 - AccountQuotaExceeded, rate limit exceeded"


def test_max_concurrent_zero_is_unbounded():
    """max_concurrent <= 0 keeps the historical unbounded behavior (no semaphore)."""
    vlm = _make_vlm(max_concurrent=0)
    assert vlm.max_concurrent == 0
    assert vlm._get_completion_semaphore() is None


async def test_concurrent_completions_bounded_by_max_concurrent(monkeypatch):
    """A single VLM instance never exceeds max_concurrent in-flight calls."""
    max_concurrent = 4
    n_calls = 40
    vlm = _make_vlm(max_concurrent)

    state = {"in_flight": 0, "peak": 0, "count": 0}

    async def fake_create(**kwargs):
        state["in_flight"] += 1
        state["count"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            await asyncio.sleep(0.01)  # hold the slot so concurrency builds up
            return _fake_response("ok")
        finally:
            state["in_flight"] -= 1

    monkeypatch.setattr(vlm, "get_async_client", lambda: _fake_client(fake_create))

    results = await asyncio.gather(
        *[
            vlm.get_completion_async(messages=[{"role": "user", "content": "hi"}])
            for _ in range(n_calls)
        ]
    )

    assert len(results) == n_calls
    assert state["count"] == n_calls
    assert state["peak"] <= max_concurrent, (
        f"peak in-flight {state['peak']} exceeded max_concurrent={max_concurrent}"
    )
    assert state["peak"] > 1, "concurrency was not exercised"


async def test_semaphore_shared_across_vlm_instances(monkeypatch):
    """Two VLM instances with the same max_concurrent share one semaphore.

    This mirrors multiple sessions (each gets the shared singleton VLM, or
    independent instances built from the same config) committing concurrently:
    their combined in-flight VLM calls must stay within max_concurrent.
    """
    max_concurrent = 3
    vlm_a = _make_vlm(max_concurrent)
    vlm_b = _make_vlm(max_concurrent)  # same limit on the same loop => shared semaphore

    state = {"in_flight": 0, "peak": 0}

    async def fake_create(**kwargs):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            await asyncio.sleep(0.01)
            return _fake_response("ok")
        finally:
            state["in_flight"] -= 1

    client = _fake_client(fake_create)
    monkeypatch.setattr(vlm_a, "get_async_client", lambda: client)
    monkeypatch.setattr(vlm_b, "get_async_client", lambda: client)

    tasks = [
        vlm_a.get_completion_async(messages=[{"role": "user", "content": "hi"}])
        for _ in range(15)
    ]
    tasks += [
        vlm_b.get_completion_async(messages=[{"role": "user", "content": "hi"}])
        for _ in range(15)
    ]
    await asyncio.gather(*tasks)

    assert state["peak"] <= max_concurrent, (
        f"combined peak in-flight {state['peak']} exceeded max_concurrent={max_concurrent}"
    )
    assert state["peak"] > 1, "concurrency was not exercised"


async def test_429_quota_rate_limit_is_retried_with_backoff(monkeypatch):
    """A 429 rate-limit response is retried (with backoff) instead of failing fast.

    The fake 429 is classified as quota_exceeded (non-retryable historically), so
    this exercises the new rate-limit retry path. Backoff delay is stubbed to 0
    to keep the test fast.
    """
    monkeypatch.setattr(
        "openviking.models.vlm.backends.openai_vlm.completion_retry_delay",
        lambda attempt, error: 0,
    )

    vlm = _make_vlm(max_concurrent=0, max_retries=3)  # unbounded; focus on retry

    calls = {"n": 0}

    async def fake_create(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeQuotaRateLimit(_QUOTA_429_MESSAGE)
        return _fake_response("recovered")

    monkeypatch.setattr(vlm, "get_async_client", lambda: _fake_client(fake_create))

    result = await vlm.get_completion_async(messages=[{"role": "user", "content": "hi"}])

    assert result == "recovered"
    assert calls["n"] == 3, f"expected 2 retries + 1 success, got {calls['n']} calls"


async def test_429_raises_after_exhausting_retries(monkeypatch):
    """Persistent 429 still surfaces after retries are exhausted (no infinite loop)."""
    monkeypatch.setattr(
        "openviking.models.vlm.backends.openai_vlm.completion_retry_delay",
        lambda attempt, error: 0,
    )

    vlm = _make_vlm(max_concurrent=0, max_retries=2)
    calls = {"n": 0}

    async def fake_create(**kwargs):
        calls["n"] += 1
        raise _FakeQuotaRateLimit(_QUOTA_429_MESSAGE)

    monkeypatch.setattr(vlm, "get_async_client", lambda: _fake_client(fake_create))

    with pytest.raises(_FakeQuotaRateLimit):
        await vlm.get_completion_async(messages=[{"role": "user", "content": "hi"}])

    # initial attempt (0) + 2 retries => 3 total calls
    assert calls["n"] == 3
