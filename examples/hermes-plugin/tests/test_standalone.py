"""Exercise the external provider through Hermes, with no bundled provider."""

import json
import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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


def test_external_discovery_preserves_profile_config_and_relative_setup(external_provider):
    home_a, provider_a, module_a, settings_a = external_provider("profile-a")
    before = (home_a / "config.yaml").read_bytes()
    _, provider_b, module_b, settings_b = external_provider("profile-b")
    _, provider_again, module_again, settings_again = external_provider("profile-a")

    assert provider_a.name == provider_b.name == "openviking"
    assert module_a is not module_b
    assert module_again is module_a
    assert settings_a["agent"] == settings_again["agent"] == "profile-a"
    assert settings_b["agent"] == "profile-b"
    assert module_a._setup._ov() is module_a
    assert module_b._setup._ov() is module_b
    assert provider_again.get_tool_schemas() == provider_a.get_tool_schemas()
    assert (home_a / "config.yaml").read_bytes() == before


def test_external_provider_dispatches_search_over_http(external_provider):
    _, provider, module, _ = external_provider("search")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body, self.headers.get("X-OpenViking-Actor-Peer")))
            payload = json.dumps(
                {
                    "result": {
                        "memories": [
                            {
                                "uri": "viking://user/alice/memories/preferences/test.md",
                                "score": 0.9,
                                "abstract": "Use concise replies.",
                            }
                        ]
                    }
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    client = module._VikingClient(f"http://127.0.0.1:{server.server_port}", agent="existing-peer")
    provider._client = client
    try:
        result = json.loads(
            provider.handle_tool_call(
                "viking_search", {"query": "reply preference", "mode": "fast"}
            )
        )
        assert requests == [("/api/v1/search/find", {"query": "reply preference"}, "existing-peer")]
        assert "Use concise replies." in json.dumps(result)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_cancelled_external_setup_keeps_existing_config(external_provider, monkeypatch):
    import hermes_cli.memory_setup as setup

    home, provider, _, _ = external_provider("setup")
    config_path = home / "config.yaml"
    before = config_path.read_bytes()
    env_path = home / ".env"
    env_path.write_text("UNRELATED_SETTING=keep\n", encoding="utf-8")
    monkeypatch.setattr(setup, "_curses_select", lambda *_args, **_kwargs: setup._CANCELLED)
    provider.post_setup(str(home), {"memory": {"provider": "openviking"}})
    assert config_path.read_bytes() == before
    assert env_path.read_text(encoding="utf-8") == "UNRELATED_SETTING=keep\n"
