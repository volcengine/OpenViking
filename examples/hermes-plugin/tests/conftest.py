"""Load the plugin through Hermes discovery in isolated profile homes."""

import importlib
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest


@pytest.fixture
def external_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "0")
    for key in list(os.environ):
        if key.startswith("OPENVIKING_"):
            monkeypatch.delenv(key)

    import plugins.memory as memory
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setattr(memory, "_MEMORY_PLUGINS_DIR", tmp_path / "empty-bundled")
    providers = []

    def load(profile):
        home = tmp_path / profile
        target = home / "plugins" / "openviking"
        if not target.exists():
            shutil.copytree(
                Path(__file__).resolve().parents[1],
                target,
                ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache"),
            )
            (home / "config.yaml").write_text(
                "memory:\n  provider: openviking\n  openviking:\n"
                "    use_ovcli_config: false\n"
                f"    agent: {profile}\n",
                encoding="utf-8",
            )
        token = set_hermes_home_override(home)
        try:
            assert memory.find_provider_dir("openviking") == target
            provider = memory.load_memory_provider("openviking", register_skills=False)
            assert provider is not None
            module = sys.modules[type(provider).__module__]
            settings = module._resolve_connection_settings(module._load_hermes_openviking_config())
        finally:
            reset_hermes_home_override(token)
        providers.append(provider)
        return home, provider, module, settings

    yield load
    for provider in providers:
        provider.shutdown()


def core_submodule(plugin, name):
    """``<plugin package>.core.<name>`` for a plugin module Hermes loaded.

    Hermes imports the plugin under a namespace of its own (for an installed copy
    ``_hermes_user_memory.<name>``), not under its directory name, so a test finds
    a core module through the loaded package. ``plugin`` is the package or any of
    its modules.
    """
    package = plugin.__name__ if hasattr(plugin, "__path__") else plugin.__name__.rpartition(".")[0]
    return importlib.import_module(f"{package}.core.{name}")


@pytest.fixture
def core_module():
    """``core_module(module, "deps")``: see :func:`core_submodule`."""
    return core_submodule


@pytest.fixture
def inject_deps():
    """Swap plugin dependencies through ``Deps`` instead of patching module globals.

    ``inject(module, *providers, **fields)`` replaces ``fields`` in each given
    provider's Deps and in the default of the plugin's ``core.deps``, which later
    providers and helpers called without a provider read. Defaults are restored
    at teardown.
    """
    previous = []

    def inject(module, *providers, **fields):
        deps = core_submodule(module, "deps")
        previous.append((deps, deps.set_default_deps(replace(deps.default_deps(), **fields))))
        for provider in providers:
            provider._deps = replace(provider._deps, **fields)

    yield inject
    for deps, default in reversed(previous):
        deps.set_default_deps(default)


class FakeMcp:
    """Scripted OpenViking /mcp behind ``Deps.mcp_session``; records every session and call."""

    def __init__(self):
        self.sessions = []
        self.calls = []
        self.tools = []
        self.reply = lambda name, arguments: {"content": [{"type": "text", "text": f"{name} ok"}]}

    def factory(self, url, headers, on_http_status, timeout):
        from contextlib import asynccontextmanager

        fake = self

        class Session:
            async def initialize(self):
                return None

            async def list_tools(self, cursor):
                return list(fake.tools), None

            async def call_tool(self, name, arguments):
                fake.calls.append((name, dict(arguments)))
                return fake.reply(name, arguments)

        @asynccontextmanager
        async def open_session():
            self.sessions.append({"url": url, "headers": dict(headers)})
            yield Session()

        return open_session()


@pytest.fixture
def fake_mcp():
    return FakeMcp()
