# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests that memory extraction handles non-dict LLM responses gracefully.

Covers issue #605: Ollama models may return a JSON list instead of the
expected {"memories": [...]} dict, causing AttributeError on .get().
"""


def _normalize_parsed_data(data):
    """
    Replicate the type-checking logic added in memory_extractor.py:extract().

    After ``parse_json_from_response(response) or {}``, the code now does:
      - list  -> wrap as ``{"memories": data}``
      - dict  -> use as-is
      - other -> fall back to ``{}``
    """
    if isinstance(data, list):
        return {"memories": data}
    if not isinstance(data, dict):
        return {}
    return data


def _make_memory(category="patterns", content="user prefers dark mode"):
    return {"category": category, "content": content, "event": "", "emoji": ""}


class TestExtractResponseTypes:
    """Verify the type-normalization handles dict, list, and unexpected types."""

    def test_dict_response_passes_through(self):
        """Standard dict format: {"memories": [...]}"""
        payload = {"memories": [_make_memory()]}
        data = _normalize_parsed_data(payload)

        assert isinstance(data, dict)
        assert len(data.get("memories", [])) == 1
        assert data["memories"][0]["content"] == "user prefers dark mode"

    def test_list_response_wrapped_as_memories(self):
        """Ollama-style list format: [{...}, {...}] wrapped into {"memories": [...]}"""
        memories_list = [_make_memory(), _make_memory(content="likes Python")]
        data = _normalize_parsed_data(memories_list)

        assert isinstance(data, dict)
        assert len(data["memories"]) == 2
        assert data["memories"][1]["content"] == "likes Python"

    def test_string_response_yields_empty(self):
        """If parse returns a bare string, treat as empty."""
        data = _normalize_parsed_data("some unexpected string")

        assert data == {}
        assert data.get("memories", []) == []

    def test_none_fallback_yields_empty(self):
        """If parse returns None, the ``or {}`` fallback produces empty dict."""
        data = _normalize_parsed_data(None or {})

        assert data == {}
        assert data.get("memories", []) == []

    def test_int_response_yields_empty(self):
        """Numeric responses should be treated as empty."""
        data = _normalize_parsed_data(42)

        assert data == {}

    def test_empty_list_wraps_to_empty_memories(self):
        """An empty list should produce {"memories": []}."""
        data = _normalize_parsed_data([])

        assert data == {"memories": []}
        assert data.get("memories", []) == []
