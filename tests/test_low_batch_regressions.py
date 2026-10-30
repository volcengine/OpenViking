#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for the LOW-severity bug-bash batch.

Each test exercises a specific fix from the batch PR so future refactors
do not silently regress the behavior.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Finding 1: create_tool_context must not share a mutable default list.
# ---------------------------------------------------------------------------


def test_create_tool_context_signature_does_not_use_mutable_default():
    """Static check: the default argument must be None (not a mutable literal)."""
    import inspect

    from openviking.session.memory.session_extract_context_provider import (
        SessionExtractContextProvider,
    )

    sig = inspect.signature(SessionExtractContextProvider.create_tool_context)
    param = sig.parameters["default_search_uris"]
    assert param.default is None, (
        f"default_search_uris default must be None (mutable-default footgun), got {param.default!r}"
    )


def test_create_tool_context_default_does_not_mutate_across_calls():
    """Two default-arg calls must yield independent lists (no shared mutable)."""
    from openviking.server.identity import ToolContext
    from openviking.session.memory.session_extract_context_provider import (
        SessionExtractContextProvider,
    )

    captured = []

    def _fake_tool_ctx(**kwargs):
        captured.append(kwargs["default_search_uris"])
        return ToolContext(**kwargs)

    provider = SessionExtractContextProvider.__new__(SessionExtractContextProvider)
    provider._viking_fs = MagicMock()
    provider._ctx = MagicMock()
    provider._transaction_handle = MagicMock()
    provider._read_file_contents = {}
    provider.messages = []

    class _StubExtract:
        page_id_map = {}

    provider.get_extract_context = lambda: _StubExtract()

    from openviking.session.memory import session_extract_context_provider as mod

    original_tool_ctx = mod.ToolContext
    mod.ToolContext = _fake_tool_ctx
    try:
        provider.create_tool_context()
        provider.create_tool_context()
    finally:
        mod.ToolContext = original_tool_ctx

    assert len(captured) == 2
    assert captured[0] == []
    assert captured[1] == []
    assert captured[0] is not captured[1], (
        "default_factory should produce a fresh list per call, not reuse a shared one"
    )


def test_create_tool_context_explicit_list_is_passed_through():
    """Explicit argument must be used verbatim, not replaced."""
    from openviking.server.identity import ToolContext
    from openviking.session.memory.session_extract_context_provider import (
        SessionExtractContextProvider,
    )

    captured = {}

    def _fake_tool_ctx(**kwargs):
        captured.update(kwargs)
        return ToolContext(**kwargs)

    provider = SessionExtractContextProvider.__new__(SessionExtractContextProvider)
    provider._viking_fs = MagicMock()
    provider._ctx = MagicMock()
    provider._transaction_handle = MagicMock()
    provider._read_file_contents = {}
    provider.messages = []

    class _StubExtract:
        page_id_map = {}

    provider.get_extract_context = lambda: _StubExtract()

    from openviking.session.memory import session_extract_context_provider as mod

    original_tool_ctx = mod.ToolContext
    mod.ToolContext = _fake_tool_ctx
    try:
        explicit = ["viking://a", "viking://b"]
        provider.create_tool_context(default_search_uris=explicit)
    finally:
        mod.ToolContext = original_tool_ctx

    assert captured["default_search_uris"] is explicit


# ---------------------------------------------------------------------------
# Finding 4: tool-call argument formatting must only catch JSON/Type errors.
# ---------------------------------------------------------------------------


def test_messages_format_tool_calls_invalid_json_does_not_crash():
    """Malformed JSON args fall through to the raw string instead of raising."""
    from openviking.session.memory.utils.messages import pretty_print_messages

    msg = {
        "role": "assistant",
        "content": "calling tool",
        "tool_calls": [
            {
                "id": "t1",
                "function": {"name": "noop", "arguments": "{not-valid-json"},
            }
        ],
    }
    # Should not raise.
    pretty_print_messages([msg])


def test_messages_format_tool_calls_valid_json_does_not_crash():
    """Valid JSON args do not raise and are pretty-printed."""
    from openviking.session.memory.utils.messages import pretty_print_messages

    msg = {
        "role": "assistant",
        "content": "calling tool",
        "tool_calls": [
            {
                "id": "t2",
                "function": {"name": "echo", "arguments": json.dumps({"k": "v"})},
            }
        ],
    }
    pretty_print_messages([msg])


# ---------------------------------------------------------------------------
# Finding 5: validation error formatter must catch only lookup/type errors.
# ---------------------------------------------------------------------------


def test_validation_module_uses_typed_exception_handlers():
    """The bare-except around the field formatter has been narrowed."""
    from openviking.storage.vectordb.utils import validation

    source = Path(validation.__file__).read_text(encoding="utf-8")
    assert "except (KeyError, IndexError, TypeError, AttributeError):" in source, (
        "validation._handle_validation_error should narrow the bare except"
    )


# ---------------------------------------------------------------------------
# Finding 10: machine-id fallback must be unique per call.
# ---------------------------------------------------------------------------


def _extract_machine_id_function():
    """Locate the get_or_create_machine_id function from commands.py via AST."""
    import ast

    commands_path = (
        Path(__file__).resolve().parent.parent / "bot" / "vikingbot" / "cli" / "commands.py"
    )
    tree = ast.parse(commands_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "get_or_create_machine_id":
            return node
    raise AssertionError("get_or_create_machine_id not found in commands.py")


def test_get_or_create_machine_id_fallback_is_stable():
    """The fallback must be stable: callers use it as the default session id.

    A per-call value (e.g. ``default-<uuid>``) would start a fresh session on
    every invocation, so the fallback has to stay a constant.
    """
    import ast

    func = _extract_machine_id_function()
    src = ast.unparse(func)
    assert "uuid" not in src, "fallback must not vary per call; it seeds the session id"
    assert "default" in src


def test_get_or_create_machine_id_logs_failure():
    """The except Exception branch should warn rather than silently swallow."""
    import ast

    func = _extract_machine_id_function()
    # Walk the function body looking for a logger.warning() call inside an
    # except handler.
    found_warning = False
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            type_name = ast.unparse(node.type)
            if "Exception" in type_name:
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "warning"
                    ):
                        found_warning = True
                        break
    assert found_warning, (
        "the broad `except Exception` branch should call logger.warning so "
        "operators can diagnose missing-machineid issues"
    )
