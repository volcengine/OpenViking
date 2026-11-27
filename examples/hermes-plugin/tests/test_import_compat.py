"""Import the provider against both Hermes layouts that move flatten_message_text.

Hermes removed ``agent/message_content.py``; ``flatten_message_text`` now lives in
``session_rules/turns.py``. The plugin's top-level import runs at registration
time, so against a Hermes revision that dropped the old module, registration
failed with ``No module named 'agent.message_content'`` and the memory provider
was off for the session. The two locations export the same function with the
same signature, so the plugin imports whichever Hermes provides.

These tests stub the small set of Hermes modules the plugin imports at import
time, so they run without a Hermes checkout, and load the plugin fresh for each
layout so the chosen import is visible.
"""

import importlib
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _flatten(content, *, sep="\n"):
    # Same contract as Hermes' helper for the shapes the plugin passes in.
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text", "")))
        return sep.join(text for text in texts if text)
    return str(content)


def _reset_plugin_modules(monkeypatch):
    for name in list(sys.modules):
        if name == "hermes_plugin_under_test" or name.startswith("hermes_plugin_under_test."):
            monkeypatch.delitem(sys.modules, name, raising=False)


def _install_hermes(monkeypatch, *, layout):
    """Stub Hermes so the plugin imports. ``layout`` selects where the helper lives."""
    modules = {}

    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        monkeypatch.setitem(sys.modules, name, mod)
        modules[name] = mod
        return mod

    class MemoryProvider:  # the plugin only subclasses this
        pass

    def spawn_context_thread(*_args, **_kwargs):  # pragma: no cover - not called on import
        raise AssertionError("spawn_context_thread should not run during import")

    module("agent")
    module("agent.memory_provider", MemoryProvider=MemoryProvider, spawn_context_thread=spawn_context_thread)
    module("agent.secret_scope", get_secret=lambda name: None)
    module("agent.skill_commands", extract_user_instruction_from_skill_message=lambda content: None)
    module("hermes_cli", __version__="0.0.0-test")
    module("hermes_constants", get_hermes_home=lambda: Path("/tmp"), get_process_hermes_home=lambda: Path("/tmp"))
    module("tools")
    module("tools.registry", tool_error=lambda message: message)
    module("utils", atomic_json_write=lambda *_a, **_k: None, env_var_enabled=lambda *_a, **_k: False)

    if layout == "old":
        module("agent.message_content", flatten_message_text=_flatten)
        # A release old enough to still have the old module has no new one.
        monkeypatch.delitem(sys.modules, "session_rules", raising=False)
        monkeypatch.delitem(sys.modules, "session_rules.turns", raising=False)
    elif layout == "new":
        module("session_rules")
        module("session_rules.turns", flatten_message_text=_flatten)
        monkeypatch.delitem(sys.modules, "agent.message_content", raising=False)
    else:  # pragma: no cover - guard against a typo in a test
        raise AssertionError(layout)
    return modules


def _import_plugin(monkeypatch, *, layout):
    _reset_plugin_modules(monkeypatch)
    _install_hermes(monkeypatch, layout=layout)
    spec = importlib.util.spec_from_file_location(
        "hermes_plugin_under_test",
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "hermes_plugin_under_test", module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("layout", ["old", "new"])
def test_provider_imports_with_either_flatten_message_text_location(monkeypatch, layout):
    module = _import_plugin(monkeypatch, layout=layout)
    # The plugin routes every message body through this helper; it must be the
    # real one from the layout it found, not a leftover name error.
    assert module._message_text("hello") == "hello"
    assert module._message_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"


def test_new_layout_plugin_does_not_require_the_removed_module(monkeypatch):
    """The regression: a Hermes without agent.message_content must still import."""
    _reset_plugin_modules(monkeypatch)
    _install_hermes(monkeypatch, layout="new")
    assert "agent.message_content" not in sys.modules
    module = _import_plugin(monkeypatch, layout="new")
    from session_rules.turns import flatten_message_text as _from_new_location

    assert module._message_text is _from_new_location
