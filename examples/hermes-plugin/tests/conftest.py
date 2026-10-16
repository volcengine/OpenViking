"""Load the plugin through Hermes discovery in isolated profile homes."""

import os
import shutil
import sys
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
