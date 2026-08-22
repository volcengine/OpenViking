# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Contract tests for the VolcEngine Chat Completions backend."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM
from openviking.models.vlm.base import VLMFactory


class _ForbiddenResponsesAPI:
    def create(self, **_kwargs):
        raise AssertionError("VolcEngine Chat Completions must not call responses.create")


def _chat_response(content: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )


def test_factory_builds_volcengine_from_config_mapping():
    config = {
        "provider": "volcengine",
        "model": "doubao-test",
        "api_key": "test-key",
        "api_base": "https://example.invalid/api/v3",
    }
    original_config = deepcopy(config)

    vlm = VLMFactory.create(config)

    assert config == original_config
    assert isinstance(vlm, VolcEngineVLM)
    assert vlm.provider == "volcengine"
    assert vlm.model == "doubao-test"
    assert vlm.api_base == "https://example.invalid/api/v3"


def test_sync_completion_uses_chat_completions_instead_of_responses(monkeypatch):
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return _chat_response("sync-chat-result")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        responses=_ForbiddenResponsesAPI(),
    )
    vlm = VolcEngineVLM(
        {
            "provider": "volcengine",
            "model": "doubao-test",
            "api_key": "test-key",
            "max_retries": 0,
        }
    )
    monkeypatch.setattr(vlm, "get_client", lambda: client)

    result = vlm.get_completion("hello")

    assert result == "sync-chat-result"
    assert len(captured) == 1
    assert captured[0]["model"] == "doubao-test"
    assert captured[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert "previous_response_id" not in captured[0]
    assert not hasattr(vlm, "_response_cache")


@pytest.mark.asyncio
async def test_async_completion_uses_chat_completions_instead_of_responses(monkeypatch):
    captured = []

    async def create(**kwargs):
        captured.append(kwargs)
        return _chat_response("async-chat-result")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        responses=_ForbiddenResponsesAPI(),
    )
    vlm = VolcEngineVLM(
        {
            "provider": "volcengine",
            "model": "doubao-test",
            "api_key": "test-key",
            "max_retries": 0,
        }
    )
    monkeypatch.setattr(vlm, "get_async_client", lambda: client)

    result = await vlm.get_completion_async("hello")

    assert result == "async-chat-result"
    assert len(captured) == 1
    assert captured[0]["model"] == "doubao-test"
    assert captured[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert "previous_response_id" not in captured[0]
    assert not hasattr(vlm, "_response_cache")
