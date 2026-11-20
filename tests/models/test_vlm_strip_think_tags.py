# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Only the visible model response reaches the VLM caller."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openviking_cli.utils.config.vlm_config import VLMConfig


@pytest.mark.parametrize(
    ("provider_text", "visible_text"),
    [
        ("Use <b>bold</b> formatting.", "Use <b>bold</b> formatting."),
        (
            '<think>Check the facts.\nBuild a summary.</think>\n{"abstract": "summary"}',
            '{"abstract": "summary"}',
        ),
        (
            "Hello <think>draft</think>world.<think>internal note</think>",
            "Hello world.",
        ),
        ("<think>Reasoning only.</think>", ""),
    ],
    ids=["literal-html", "multiline-reasoning-before-json", "interleaved-reasoning", "no-answer"],
)
def test_completion_returns_visible_content(monkeypatch, provider_text, visible_text):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=provider_text))], usage=None
    )
    client = Mock()
    client.chat.completions.create.return_value = response
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: client)
    config = VLMConfig(provider="openai", model="gpt-4o-mini", api_key="test-key")

    try:
        assert config.get_vlm_instance().get_completion("Summarize the document.") == visible_text
    finally:
        config.close()
