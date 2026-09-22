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


@pytest.mark.parametrize("mode,rewrite", [("server", True), ("auto", "auto")])
@pytest.mark.parametrize(
    "response,expected",
    [
        ({"rendered": "raw", "digest": "compressed", "entries": []}, "compressed"),
        ({"rendered": "raw", "entries": []}, "raw"),
        ({"rendered": "raw", "stats": {"rewrite": "no_relevant"}}, ""),
    ],
)
def test_cloud_recall(external_provider, monkeypatch, mode, rewrite, response, expected):
    from unittest.mock import Mock

    _, provider, _, _ = external_provider("cloud-recall")
    monkeypatch.setenv("OPENVIKING_RECALL_COMPRESS", mode)
    client = Mock()
    client.post.return_value = {"result": response}
    assert (
        provider._search_prefetch_context(
            "remember deployment preferences", session_id="session", client=client
        )
        == expected
    )
    client.post.assert_called_once()
    path, body = client.post.call_args.args
    assert path == "/api/v1/search/search"
    assert body["mode"] == "context"
    assert body["rewrite"] == rewrite
    assert body["session_id"] == "session"
    assert 50 < client.post.call_args.kwargs["timeout"] <= 55


def test_cloud_recall_legacy_fallback(external_provider, monkeypatch):
    from unittest.mock import Mock

    _, provider, _, _ = external_provider("cloud-fallback")
    monkeypatch.setenv("OPENVIKING_RECALL_COMPRESS", "server")
    client = Mock()
    client.post.side_effect = [RuntimeError("unsupported mode"), {"result": {"memories": []}}]
    assert provider._search_prefetch_context("deployment preferences", client=client) == ""
    assert client.post.call_count == 2
    assert "rewrite" not in client.post.call_args.args[1]


@pytest.mark.parametrize(
    "uri",
    [
        "viking://user/zayn/memories/profile.md",
        "viking://user/zayn/memories/preferences/mem_abc123.md",
        "viking://user/zayn/peers/hermes/memories/preferences/mem_abc123.md",
        "viking://~/memories/profile.md",
        "viking://~/memories/preferences/mem_abc123.md",
        "viking://~/peers/hermes/memories/preferences/mem_abc123.md",
    ],
)
def test_external_provider_accepts_canonical_forget_uris(external_provider, uri):
    _, _, module, _ = external_provider("forget-canonical")
    user_space = "zayn" if uri.startswith("viking://user/") else None

    assert module._validate_forget_memory_uri(uri, user_space=user_space) == (uri, None)


@pytest.mark.parametrize(
    "uri",
    [
        "viking://user/memories/preferences/mem_abc123.md",
        "viking://user/peers/hermes/memories/preferences/mem_abc123.md",
    ],
)
def test_external_provider_rejects_uidless_forget_uris(external_provider, uri):
    _, _, module, _ = external_provider("forget-uidless")

    resolved, error = module._validate_forget_memory_uri(uri)

    assert resolved is None
    assert "user memory file URIs" in error


def test_external_provider_rejects_other_user_forget_uri(external_provider):
    _, _, module, _ = external_provider("forget-other-user")
    uri = "viking://user/someone-else/memories/preferences/mem_abc123.md"

    resolved, error = module._validate_forget_memory_uri(uri, user_space="zayn")

    assert resolved is None
    assert "your own memories" in error


def test_external_provider_forget_fails_closed_without_identity(external_provider):
    _, provider, _, _ = external_provider("forget-unverified")
    delete_calls = []

    class UnverifiedClient:
        def get(self, _path, **_kwargs):
            raise RuntimeError("identity probe unavailable")

        def delete(self, path, **kwargs):
            delete_calls.append((path, kwargs))
            return {"result": {}}

    provider._client = UnverifiedClient()
    result = json.loads(
        provider._tool_forget({"uri": "viking://user/alice/memories/preferences/mem_abc123.md"})
    )

    assert "identity" in result["error"].lower()
    assert delete_calls == []


@pytest.mark.parametrize(
    "uri",
    [
        "viking://user/alice/memories/./x.md",
        "viking://user/alice/memories/../../user/bob/memories/x.md",
        "viking://user/alice/memories/%2e/x.md",
        "viking://user/alice/memories/%2e%2e/x.md",
        "viking://~/memories/../x.md",
    ],
)
def test_external_provider_rejects_dot_segments_in_forget_uri(external_provider, uri):
    _, _, module, _ = external_provider("forget-dot-segments")

    resolved, error = module._validate_forget_memory_uri(uri, user_space="alice")

    assert resolved is None
    assert "dot path segments" in error


@pytest.mark.parametrize(
    "uri",
    [
        "viking://user/alice/memories/preferences/mem_abc123.md",
        "viking://~/memories/preferences/mem_abc123.md",
    ],
)
def test_external_provider_forget_keeps_verified_connection(external_provider, uri):
    _, provider, module, _ = external_provider("forget-connection-snapshot")
    identity_requested = threading.Event()
    continue_identity = threading.Event()
    requests = {"a": [], "b": []}

    def handler_for(server_name):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests[server_name].append(("GET", self.path))
                identity_requested.set()
                assert continue_identity.wait(timeout=5)
                self._respond({"status": "ok", "result": {"user": "alice"}})

            def do_DELETE(self):
                requests[server_name].append(("DELETE", self.path))
                self._respond({"status": "ok", "result": {"uri": uri}})

            def _respond(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        return Handler

    servers = [
        HTTPServer(("127.0.0.1", 0), handler_for(server_name))
        for server_name in ("a", "b")
    ]
    server_threads = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in servers
    ]
    for server_thread in server_threads:
        server_thread.start()

    provider._client = module._VikingClient(f"http://127.0.0.1:{servers[0].server_port}")
    result = []
    tool_thread = threading.Thread(
        target=lambda: result.append(provider.handle_tool_call("viking_forget", {"uri": uri}))
    )
    tool_thread.start()
    try:
        assert identity_requested.wait(timeout=5)
        provider._client = module._VikingClient(
            f"http://127.0.0.1:{servers[1].server_port}"
        )
        continue_identity.set()
        tool_thread.join(timeout=5)

        assert not tool_thread.is_alive()
        assert json.loads(result[0])["status"] == "deleted"
        assert [method for method, _ in requests["a"]] == ["GET", "DELETE"]
        assert requests["b"] == []
    finally:
        continue_identity.set()
        tool_thread.join(timeout=5)
        for server in servers:
            server.shutdown()
            server.server_close()
        for server_thread in server_threads:
            server_thread.join(timeout=5)


@pytest.mark.parametrize("operation", ["mirror", "recall"])
def test_external_provider_keeps_user_identity_across_reload(
    external_provider, monkeypatch, operation
):
    _, provider, module, _ = external_provider("identity-reload")
    captured = threading.Event()
    resume = threading.Event()
    requests = []

    def get(client, path, params=None, **_kwargs):
        requests.append((client._user, path, params))
        if path == "/api/v1/system/status":
            return {"result": {"user": client._user}}
        return {"result": {"content": f"Profile for {client._user}"}}

    monkeypatch.setattr(module._VikingClient, "get", get)
    monkeypatch.setattr(module._VikingClient, "post", lambda *_args, **_kwargs: {})
    resolve = provider._user_space

    def delayed_identity(client=None, **kwargs):
        if client is not None and client._user == "alice":
            captured.set()
            assert resume.wait(timeout=10)
        return resolve(client, **kwargs)

    monkeypatch.setattr(provider, "_user_space", delayed_identity)

    def publish(user):
        provider._endpoint, provider._api_key = "http://127.0.0.1:1933", ""
        provider._account, provider._user, provider._agent = "test", user, "hermes"
        provider._publish_client(provider._build_client(), provider._endpoint)

    publish("alice")
    worker = threading.Thread(
        target=lambda: (
            provider.on_memory_write("add", "memory", "Alice prefers tea")
            if operation == "mirror"
            else provider.prefetch("", session_id="alice-session")
        )
    )
    worker.start()
    try:
        assert captured.wait(timeout=10)
        publish("bob")
        resume.set()
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert provider._join_all(lambda: list(provider._memory_write_threads), 10)
        block = provider.prefetch("", session_id="bob-session")
        assert "viking://user/bob/memories/profile.md" in block
        assert "viking://user/alice/" not in block
        assert any(user == "bob" and path == "/api/v1/system/status" for user, path, _ in requests)
    finally:
        resume.set()
        worker.join(timeout=10)


def test_external_provider_does_not_cache_unbound_client_identity(external_provider):
    from types import SimpleNamespace

    _, provider, module, _ = external_provider("unbound-identity")
    provider._client = module._VikingClient("http://127.0.0.1:1933", user="bob")
    provider._conn_snapshot = ("http://127.0.0.1:1933", "", "default", "bob", "hermes")
    provider._client.get = lambda *_args, **_kwargs: {"result": {"user": "bob"}}
    unbound = SimpleNamespace(get=lambda *_args, **_kwargs: {"result": {"user": "alice"}})
    assert provider._user_space(unbound) == "alice"
    assert provider._user_space() == "bob"
    assert provider._user_space(unbound) == "alice"
    assert provider._user_space() == "bob"


def test_save_config_targets_explicit_home_and_restores_outer_scope(
    external_provider, tmp_path, monkeypatch
):
    import yaml

    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    _, provider, _, _ = external_provider("config-provider")
    active_home = tmp_path / "active"
    outer_home = tmp_path / "outer"
    target_home = tmp_path / "target"
    for home in (active_home, outer_home, target_home):
        home.mkdir()

    active_before = "model:\n  default: active-model\nmemory:\n  provider: openviking\n"
    outer_before = "model:\n  default: outer-model\nmemory:\n  provider: openviking\n"
    target_before = "model:\n  default: target-model\nmemory:\n  provider: openviking\n"
    (active_home / "config.yaml").write_text(active_before, encoding="utf-8")
    (outer_home / "config.yaml").write_text(outer_before, encoding="utf-8")
    (target_home / "config.yaml").write_text(target_before, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(active_home))

    outer_token = set_hermes_home_override(outer_home)
    try:
        provider.save_config(
            {"endpoint": "https://target.example/v1", "recall_policy": "always"},
            str(target_home),
        )

        # The explicit callback target is updated; neither ambient scope is touched.
        assert (active_home / "config.yaml").read_text(encoding="utf-8") == active_before
        assert (outer_home / "config.yaml").read_text(encoding="utf-8") == outer_before
        target = yaml.safe_load((target_home / "config.yaml").read_text(encoding="utf-8"))
        assert target["model"]["default"] == "target-model"
        assert target["memory"]["provider"] == "openviking"
        assert target["memory"]["openviking"] == {
            "endpoint": "https://target.example/v1",
            "recall_policy": "always",
        }

        # The nested override was restored, so a canonical write still targets outer_home.
        from hermes_cli.config import save_config

        save_config({"memory": {"provider": "outer-restored"}}, merge_existing=True)
        outer = yaml.safe_load((outer_home / "config.yaml").read_text(encoding="utf-8"))
        assert outer["memory"]["provider"] == "outer-restored"
        assert (active_home / "config.yaml").read_text(encoding="utf-8") == active_before
    finally:
        reset_hermes_home_override(outer_token)


def test_save_config_preserves_profiles_and_scope_when_target_is_invalid(external_provider):
    from hermes_constants import (
        get_hermes_home_override,
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    outer, provider, _, _ = external_provider("outer-config")
    target, _, _, _ = external_provider("invalid-target")
    outer_before = (outer / "config.yaml").read_bytes()
    invalid = "memory: [\n"
    (target / "config.yaml").write_text(invalid)
    token = set_hermes_home_override(outer)
    try:
        with pytest.raises(RuntimeError, match="formatting error"):
            provider.save_config({"recall_policy": "always"}, str(target))
        assert get_hermes_home_override() == str(outer)
        assert (outer / "config.yaml").read_bytes() == outer_before
        assert (target / "config.yaml").read_text() == invalid
    finally:
        reset_hermes_home_override(token)
