# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""`_call_llm` forces JSON output on the tool-less path (issue #1541).

When tools are disabled the model must emit final operations as a JSON object
in message content. Weak models otherwise return plain prose, parsing fails,
and extraction stores zero memories. The fix passes
``response_format={"type": "json_object"}`` to the VLM only on that path.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.models.vlm.base import VLMResponse
from openviking.session.memory.extract_loop import ExtractLoop


def _make_loop(captured: dict) -> ExtractLoop:
    """Build a minimally-wired ExtractLoop whose VLM records its call kwargs."""

    async def fake_completion(**kwargs):
        captured.clear()
        captured.update(kwargs)
        # Valid empty-operations JSON so the tool-less path parses cleanly.
        return VLMResponse(content="{}")

    vlm = Mock(model="test-model")
    vlm.get_completion_async = AsyncMock(side_effect=fake_completion)

    loop = ExtractLoop(vlm=vlm, viking_fs=Mock(), context_provider=Mock())
    loop._mark_cache_breakpoint = AsyncMock()
    loop._tool_schemas = [{"type": "function", "function": {"name": "read"}}]
    loop._expected_fields = ["delete_uris"]
    # parse path: any content maps to a truthy operations object
    loop._operations_model = SimpleNamespace(model_json_schema=lambda: {})
    return loop


class TestCallLlmResponseFormat:
    @pytest.mark.asyncio
    async def test_tool_less_path_forces_json_object(self, monkeypatch):
        captured: dict = {}
        loop = _make_loop(captured)
        # Tools disabled -> final-operations path.
        loop._disable_tools_for_iteration = True

        # Make parsing deterministic and independent of schema internals.
        monkeypatch.setattr(
            "openviking.session.memory.extract_loop.parse_json_with_stability",
            lambda content, model_class, expected_fields: ({}, None),
        )

        await loop._call_llm(messages=[{"role": "user", "content": "hi"}])

        assert captured["response_format"] == {"type": "json_object"}
        assert captured["tools"] is None

    @pytest.mark.asyncio
    async def test_tool_path_omits_response_format(self):
        captured: dict = {}
        loop = _make_loop(captured)
        # Tools enabled -> structured tool-call path, no response_format.
        loop._disable_tools_for_iteration = False

        await loop._call_llm(messages=[{"role": "user", "content": "hi"}])

        assert captured["response_format"] is None
        assert captured["tools"] == loop._tool_schemas
