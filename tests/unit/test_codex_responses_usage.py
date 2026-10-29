# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.models.vlm.backends.codex_responses_adapter import (
    _build_chat_completion_like_response,
)


@pytest.mark.parametrize(
    "output",
    [
        [
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="answer")],
            )
        ],
        [
            SimpleNamespace(
                type="function_call",
                call_id="call-1",
                name="remember",
                arguments='{"fact":"stable"}',
            )
        ],
    ],
    ids=["text", "tool"],
)
def test_codex_response_preserves_detailed_token_usage(output):
    response = _build_chat_completion_like_response(
        SimpleNamespace(
            output=output,
            usage=SimpleNamespace(
                input_tokens=1200,
                input_tokens_details=SimpleNamespace(cached_tokens=1000),
                output_tokens=90,
                output_tokens_details=SimpleNamespace(reasoning_tokens=60),
                total_tokens=1290,
            ),
        ),
        "gpt-5.4-mini",
    )

    assert response.usage.prompt_tokens == 1200
    assert response.usage.prompt_tokens_details.cached_tokens == 1000
    assert response.usage.completion_tokens == 90
    assert response.usage.completion_tokens_details.reasoning_tokens == 60
    assert response.usage.total_tokens == 1290
