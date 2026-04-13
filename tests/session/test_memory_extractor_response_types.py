# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests that memory extraction normalizes common non-canonical LLM response shapes.

Covers:
- issue #605: Ollama models may return a bare JSON list instead of the
  expected {"memories": [...]} dict
- issue #1410: smaller local/OpenAI-compatible models may return a single
  memory object or wrap one under ``memories`` as an object
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

try:
    from openviking.session.memory_extractor import MemoryExtractor
except Exception:  # pragma: no cover - fallback for minimal local test env
    logger_stub = SimpleNamespace(
        debug=lambda *a, **k: None,
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )

    modules = {
        "openviking": ModuleType("openviking"),
        "openviking.core": ModuleType("openviking.core"),
        "openviking.core.context": ModuleType("openviking.core.context"),
        "openviking.prompts": ModuleType("openviking.prompts"),
        "openviking.server": ModuleType("openviking.server"),
        "openviking.server.identity": ModuleType("openviking.server.identity"),
        "openviking.storage": ModuleType("openviking.storage"),
        "openviking.storage.viking_fs": ModuleType("openviking.storage.viking_fs"),
        "openviking.telemetry": ModuleType("openviking.telemetry"),
        "openviking_cli": ModuleType("openviking_cli"),
        "openviking_cli.exceptions": ModuleType("openviking_cli.exceptions"),
        "openviking_cli.session": ModuleType("openviking_cli.session"),
        "openviking_cli.session.user_id": ModuleType("openviking_cli.session.user_id"),
        "openviking_cli.utils": ModuleType("openviking_cli.utils"),
        "openviking_cli.utils.config": ModuleType("openviking_cli.utils.config"),
    }

    modules["openviking.core.context"].Context = object
    modules["openviking.core.context"].ContextType = SimpleNamespace(
        MEMORY=SimpleNamespace(value="memory")
    )
    modules["openviking.core.context"].Vectorize = object
    modules["openviking.prompts"].render_prompt = lambda *a, **k: ""
    modules["openviking.server.identity"].RequestContext = object
    modules["openviking.storage.viking_fs"].get_viking_fs = lambda: None
    modules["openviking.telemetry"].get_current_telemetry = lambda: SimpleNamespace(
        measure=lambda *_a, **_k: SimpleNamespace(
            __enter__=lambda self: None,
            __exit__=lambda self, exc_type, exc, tb: False,
        )
    )

    class _NotFoundError(Exception):
        pass

    modules["openviking_cli.exceptions"].NotFoundError = _NotFoundError
    modules["openviking_cli.session.user_id"].UserIdentifier = object
    modules["openviking_cli.utils"].get_logger = lambda _name: logger_stub
    modules["openviking_cli.utils.config"].get_openviking_config = lambda: SimpleNamespace(
        language_fallback="en",
        vlm=None,
    )

    for name, module in modules.items():
        sys.modules.setdefault(name, module)

    module_path = (
        Path(__file__).resolve().parents[2] / "openviking" / "session" / "memory_extractor.py"
    )
    spec = importlib.util.spec_from_file_location(
        "openviking.session.memory_extractor", module_path
    )
    memory_extractor = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(memory_extractor)
    MemoryExtractor = memory_extractor.MemoryExtractor

def _normalize_parsed_data(data):
    return MemoryExtractor._normalize_extraction_payload(data)


def _make_memory(category="patterns", content="user prefers dark mode"):
    return {
        "category": category,
        "abstract": "sample abstract",
        "overview": "sample overview",
        "content": content,
        "event": "",
        "emoji": "",
    }


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

    def test_single_memory_object_wrapped_as_memories(self):
        """A bare memory object should be treated as one extracted memory."""
        payload = _make_memory(category="preferences", content="likes pour-over coffee")
        data = _normalize_parsed_data(payload)

        assert isinstance(data, dict)
        assert len(data["memories"]) == 1
        assert data["memories"][0]["category"] == "preferences"

    def test_memories_object_wrapped_into_single_item_list(self):
        """Some small models emit {"memories": {...}} instead of a list."""
        payload = {"memories": _make_memory(category="entities", content="dog named Wangcai")}
        data = _normalize_parsed_data(payload)

        assert isinstance(data, dict)
        assert len(data["memories"]) == 1
        assert data["memories"][0]["category"] == "entities"

    def test_items_wrapper_is_accepted(self):
        """Alternative wrapper keys like ``items`` should be normalized."""
        payload = {"items": [_make_memory(category="events", content="scheduled vet visit")]}
        data = _normalize_parsed_data(payload)

        assert isinstance(data, dict)
        assert len(data["memories"]) == 1
        assert data["memories"][0]["category"] == "events"

    def test_nested_data_memories_wrapper_is_accepted(self):
        """Nested ``data -> memories`` wrappers should be unwrapped."""
        payload = {
            "data": {
                "memories": _make_memory(category="preferences", content="prefers oat milk")
            }
        }
        data = _normalize_parsed_data(payload)

        assert isinstance(data, dict)
        assert len(data["memories"]) == 1
        assert data["memories"][0]["content"] == "prefers oat milk"

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
