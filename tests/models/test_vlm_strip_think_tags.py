# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reasoning blocks are removed from the text returned to VLM callers."""

import httpx
import pytest

from openviking_cli.utils.config.vlm_config import VLMConfig


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("Use <b>bold</b> text.", "Use <b>bold</b> text.", id="ordinary-markup"),
        pytest.param(
            '<think>first line\nsecond line</think>{"summary": "hello"}<think>more</think>',
            '{"summary": "hello"}',
            id="preserve-json-between-reasoning-blocks",
        ),
        pytest.param("<think>no final answer</think>", "", id="reasoning-only"),
    ],
)
def test_completion_returns_only_final_text(monkeypatch, content, expected):
    def send(client, request, **kwargs):
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"},
                ]
            },
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    config = VLMConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key="test-key",
        api_base="https://gateway.example.com/v1",
    )
    try:
        assert config.get_vlm_instance().get_completion("Summarize the note") == expected
    finally:
        config.close()
