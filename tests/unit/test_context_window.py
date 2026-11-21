# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.models.vlm.backends.litellm_vlm import OLLAMA_DEFAULT_NUM_CTX
from openviking.models.vlm.context_window import (
    MIN_PROMPT_TOKEN_LIMIT,
    resolve_context_window,
    resolve_prompt_token_limit,
)
from openviking.session.session import Session


def _vlm(**kwargs):
    return SimpleNamespace(
        model=kwargs.pop("model", None),
        provider=kwargs.pop("provider", None),
        extra_request_body=kwargs.pop("extra_request_body", None),
        max_tokens=kwargs.pop("max_tokens", None),
    )


@pytest.mark.parametrize(
    ("vlm", "expected"),
    [
        (_vlm(model="ollama/qwen3.5:9b"), OLLAMA_DEFAULT_NUM_CTX),
        (_vlm(model="qwen3.5:9b", provider="ollama"), OLLAMA_DEFAULT_NUM_CTX),
        (
            _vlm(model="qwen3.5:9b", provider="ollama", extra_request_body={"num_ctx": 32768}),
            32768,
        ),
        (_vlm(model="gpt-4o", provider="openai"), 128000),
        (_vlm(model="a-model-nobody-registered"), None),
    ],
)
def test_ollama_routes_report_the_window_that_is_really_requested(vlm, expected):
    assert resolve_context_window(vlm) == expected


def test_num_ctx_comes_from_the_same_default_litellm_sends():
    vlm = _vlm(model="qwen3.5:9b", provider="ollama")
    assert resolve_context_window(vlm) == OLLAMA_DEFAULT_NUM_CTX


def test_prompt_limit_reserves_output_protocol_and_a_safety_margin():
    vlm = _vlm(model="ollama/qwen3.5:9b")

    limit = resolve_prompt_token_limit(vlm)

    # 16384 window - 4096 output - 2048 protocol/pages - 819 safety margin
    assert limit == 9421
    assert limit < OLLAMA_DEFAULT_NUM_CTX


def test_configured_output_is_clamped_inside_the_window():
    vlm = _vlm(
        model="qwen3.5:9b",
        provider="ollama",
        extra_request_body={"num_ctx": 16384},
        max_tokens=32768,
    )

    limit = resolve_prompt_token_limit(vlm)

    # 16384 - min(32768, 16384 // 2) - 2048 - 819
    assert limit == 5325


@pytest.mark.parametrize(
    "vlm",
    [
        _vlm(model="qwen3.5:9b", provider="ollama", extra_request_body={"num_ctx": 1024}),
        _vlm(model="some-unregistered-model"),
        _vlm(),
        None,
    ],
)
def test_unusable_windows_leave_batching_unbounded(vlm):
    assert resolve_prompt_token_limit(vlm) is None


def test_prompt_limit_is_above_the_minimum_batch_size():
    vlm = _vlm(model="ollama/qwen3.5:9b")

    assert resolve_prompt_token_limit(vlm) >= MIN_PROMPT_TOKEN_LIMIT


@pytest.mark.asyncio
async def test_session_derives_the_limit_from_the_configured_model():
    session = Session(viking_fs=None)
    session._get_vlm_config = AsyncMock(
        return_value=_vlm(model="qwen3.5:9b", provider="ollama", extra_request_body=None)
    )

    assert await session._extraction_prompt_token_limit() == resolve_prompt_token_limit(
        _vlm(model="qwen3.5:9b", provider="ollama")
    )


@pytest.mark.asyncio
async def test_session_budget_lookup_never_breaks_a_commit():
    session = Session(viking_fs=None)
    session._get_vlm_config = AsyncMock(side_effect=RuntimeError("no resolver"))

    assert await session._extraction_prompt_token_limit() is None
