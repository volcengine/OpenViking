"""Exercise the external provider through Hermes, with no bundled provider."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


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


def test_initialized_profile_owns_connection_and_recall_across_other_profile(external_provider, monkeypatch):
    from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    requests = []
    servers = []

    def start_server(label):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((label, self.path, body, self.headers.get("X-API-Key"),
                                 self.headers.get("X-OpenViking-Actor-Peer")))
                payload = b'{"result":{"memories":[]}}'
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
        servers.append((server, worker))
        return f"http://127.0.0.1:{server.server_port}"

    @contextmanager
    def profile_scope(home):
        home_token = set_hermes_home_override(home)
        secret_token = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
        try:
            yield
        finally:
            reset_secret_scope(secret_token)
            reset_hermes_home_override(home_token)

    try:
        endpoint_a, endpoint_b = start_server("a"), start_server("b")
        home_a, provider, module, _ = external_provider("owner-a")
        home_b, _, _, _ = external_provider("owner-b")
        for home, endpoint, label, budget in (
            (home_a, endpoint_a, "a", 1100), (home_b, endpoint_b, "b", 2200)
        ):
            (home / "config.yaml").write_text(
                "memory:\n  provider: openviking\n  openviking:\n"
                f"    endpoint: {endpoint}\n    recall_limit: {3 if label == 'a' else 9}\n"
                "    profile_token_budget: ${OV_TEST_BUDGET}\n", encoding="utf-8"
            )
            (home / ".env").write_text(
                f"OPENVIKING_API_KEY=key-{label}\n"
                f"OPENVIKING_ACCOUNT=account-{label}\nOPENVIKING_USER=user-{label}\n"
                f"OPENVIKING_AGENT=peer-{label}\nOV_TEST_BUDGET={budget}\n", encoding="utf-8"
            )

        monkeypatch.setattr(module, "_classify_runtime_openviking_health", lambda *_: ("healthy", ""))
        with profile_scope(home_a):
            provider.initialize("session-a", hermes_home=str(home_a))
            assert provider._client._api_key == "key-a"
            assert provider._profile_token_budget() == 1100

        with profile_scope(home_b):
            provider.handle_tool_call("viking_search", {"query": "preference", "mode": "deep"})
            assert provider._client._account == "account-a"
            assert provider._client._user == "user-a"
            assert provider._recall_config()["limit"] == 3
            assert provider._profile_token_budget() == 1100

            (home_a / ".env").write_text(
                "OPENVIKING_API_KEY=key-a-new\nOPENVIKING_ACCOUNT=account-a\n"
                "OPENVIKING_USER=user-a\nOPENVIKING_AGENT=peer-a-new\n"
                "OV_TEST_BUDGET=1100\n", encoding="utf-8"
            )
            provider.handle_tool_call("viking_search", {"query": "preference", "mode": "deep"})

            # Removing A's credentials must not borrow B's active or process values.
            (home_a / ".env").write_text(
                "OPENVIKING_AGENT=peer-a\nOV_TEST_BUDGET=1100\n", encoding="utf-8"
            )
            monkeypatch.setenv("OPENVIKING_API_KEY", "process-b-key")
            monkeypatch.setenv("OPENVIKING_ACCOUNT", "process-b-account")
            monkeypatch.setenv("OPENVIKING_USER", "process-b-user")
            provider.handle_tool_call("viking_search", {"query": "preference", "mode": "deep"})
            assert (provider._client._api_key, provider._client._account, provider._client._user) == (
                "", "default", "default"
            )

            # An absent A endpoint must not route to B's process-level endpoint.
            (home_a / "config.yaml").write_text("memory:\n  provider: openviking\n", encoding="utf-8")
            monkeypatch.setenv("OPENVIKING_ENDPOINT", endpoint_b)
            assert provider._resolve_bound_connection_settings()["endpoint"] == module._DEFAULT_ENDPOINT

        assert [(label, key, peer) for label, _, _, key, peer in requests] == [
            ("a", "key-a", "peer-a"), ("a", "key-a-new", "peer-a-new"),
            ("a", None, "peer-a")
        ]
        assert all(body["session_id"] == "session-a" for _, _, body, _, _ in requests)
    finally:
        for server, worker in servers:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


def test_routed_profile_does_not_borrow_launch_process_env(external_provider, monkeypatch):
    from agent.secret_scope import (
        build_profile_secret_scope,
        get_secret,
        reset_secret_scope,
        serves_routed_profile,
        set_secret_scope,
    )
    from hermes_constants import (
        get_routing_process_hermes_home,
        pin_process_hermes_home,
        process_hermes_home_is_pinned,
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    launch_home, launch_provider, launch_module, _ = external_provider("launch")
    routed_home, routed_provider, routed_module, _ = external_provider("routed")
    launch_provider._hermes_home, launch_provider._hermes_home_bound = str(launch_home), True
    routed_provider._hermes_home, routed_provider._hermes_home_bound = str(routed_home), True
    monkeypatch.setattr("agent.secret_scope._MULTIPLEX_ACTIVE", False)
    monkeypatch.setenv("HERMES_HOME", str(launch_home))
    monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:19521")
    monkeypatch.setenv("OPENVIKING_API_KEY", "launch-key")
    prior_pin = get_routing_process_hermes_home() if process_hermes_home_is_pinned() else None
    pin_process_hermes_home(launch_home)
    home_token = set_hermes_home_override(routed_home)
    scope_token = set_secret_scope(build_profile_secret_scope(routed_home), profile_home=str(routed_home))
    try:
        # Some hosts mirror the routed home into process env; the pinned launch
        # home, not the current override or env mirror, owns process credentials.
        monkeypatch.setenv("HERMES_HOME", str(routed_home))
        assert serves_routed_profile()
        assert get_secret("OPENVIKING_API_KEY") is None
        routed = routed_provider._resolve_bound_connection_settings()
        launch = launch_provider._resolve_bound_connection_settings()
        assert (routed["endpoint"], routed["api_key"]) == (routed_module._DEFAULT_ENDPOINT, "")
        assert (launch["endpoint"], launch["api_key"]) == ("http://127.0.0.1:19521", "launch-key")
    finally:
        reset_secret_scope(scope_token)
        reset_hermes_home_override(home_token)
        pin_process_hermes_home(prior_pin)


def test_multiplex_launch_profile_uses_frozen_process_secrets(external_provider, monkeypatch):
    from agent.secret_scope import get_secret
    from hermes_constants import (
        get_routing_process_hermes_home,
        pin_process_hermes_home,
        process_hermes_home_is_pinned,
    )
    from tui_gateway import launch_profile_policy

    launch_home, launch_provider, launch_module, _ = external_provider("multiplex-launch")
    routed_home, routed_provider, routed_module, _ = external_provider("multiplex-routed")
    launch_provider._hermes_home, launch_provider._hermes_home_bound = str(launch_home), True
    routed_provider._hermes_home, routed_provider._hermes_home_bound = str(routed_home), True
    monkeypatch.setenv("HERMES_HOME", str(launch_home))
    monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:19522")
    monkeypatch.setenv("OPENVIKING_API_KEY", "frozen-launch-key")
    monkeypatch.setattr(launch_profile_policy, "_snapshot", None)
    launch_profile_policy.capture_launch_env()
    monkeypatch.setattr("agent.secret_scope._MULTIPLEX_ACTIVE", True)
    prior_pin = get_routing_process_hermes_home() if process_hermes_home_is_pinned() else None
    pin_process_hermes_home(launch_home)
    try:
        # A later process mutation must not affect the launch snapshot or leak to B.
        monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:19523")
        monkeypatch.setenv("OPENVIKING_API_KEY", "poisoned-live-key")
        with launch_profile_policy.launch_profile_runtime_scope(launch_home):
            assert get_secret("OPENVIKING_API_KEY") == "frozen-launch-key"
            launch = launch_provider._resolve_bound_connection_settings()
            routed = routed_provider._resolve_bound_connection_settings()
            assert (launch["endpoint"], launch["api_key"]) == (
                "http://127.0.0.1:19522", "frozen-launch-key"
            )
            assert (routed["endpoint"], routed["api_key"]) == (routed_module._DEFAULT_ENDPOINT, "")

        (launch_home / ".env").write_text("OPENVIKING_API_KEY=file-key\n")
        with launch_profile_policy.launch_profile_runtime_scope(launch_home):
            assert launch_provider._resolve_bound_connection_settings()["api_key"] == "file-key"

        # The messaging gateway may activate multiplex without freezing a launch
        # snapshot. In that case, never capture its live process env on demand.
        monkeypatch.setattr(launch_profile_policy, "_snapshot", None)
        without_snapshot = launch_provider._resolve_bound_connection_settings()
        assert (without_snapshot["endpoint"], without_snapshot["api_key"]) == (
            launch_module._DEFAULT_ENDPOINT, "file-key"
        )
        assert launch_profile_policy._snapshot is None
    finally:
        pin_process_hermes_home(prior_pin)


def test_api_key_trusted_retry_keeps_default_identity(external_provider):
    _, _, module, _ = external_provider("trusted-retry")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            account = self.headers.get("X-OpenViking-Account")
            user = self.headers.get("X-OpenViking-User")
            requests.append((self.path, account, user))
            if self.path == "/health":
                status, payload = 200, {"status": "ok", "healthy": True, "version": "0.4.18", "auth_mode": "trusted"}
            elif account == user == "default":
                status, payload = 200, {"result": {}}
            else:
                status, payload = 400, {"error": {"code": "INVALID_ARGUMENT", "message":
                    "Trusted mode requests must include X-OpenViking-Account and X-OpenViking-User."}}
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        settings = module._resolve_connection_settings({}, env={"OPENVIKING_API_KEY": "test-key"})
        assert (settings["account"], settings["user"]) == ("default", "default")
        client = module._VikingClient(endpoint, settings["api_key"],
                                      account=settings["account"], user=settings["user"])
        assert client.validate_auth() == {"result": {}}
        assert requests[:2] == [
            ("/api/v1/system/status", None, None),
            ("/api/v1/system/status", "default", "default"),
        ]
        ok, message, role = module._validate_openviking_setup_values(
            {"endpoint": endpoint, "api_key": "test-key"})
        assert (ok, message, role) == (True, "", "root")
        assert all((account, user) in ((None, None), ("default", "default"))
                   for _, account, user in requests)
        assert requests[-1] == ("/api/v1/admin/accounts", "default", "default")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize("target", ["memory", "user"])
def test_external_native_memory_lifecycle_survives_restart(external_provider, target):
    from agent.memory_manager import MemoryManager

    files, requests = {}, []

    class Client:
        _endpoint, _api_key, _account, _user, _agent = "http://test", "", "test", "alice", ""

        def get(self, path, **kwargs):
            return {"result": {"user": self._user}}

        def post(self, path, payload):
            requests.append(("write", dict(payload)))
            files[payload["uri"]] = payload["content"]
            return {"result": {"uri": payload["uri"]}}

        def delete(self, path, *, params):
            requests.append(("delete", dict(params)))
            del files[params["uri"]]
            return {"result": {"uri": params["uri"]}}

    client = Client()
    operations = [
        {"action": "add", "new_text": "Preferred shell is zsh"},
        {"action": "replace", "old_text": "zsh", "new_text": "Preferred shell is fish"},
        {"action": "remove", "old_text": "fish"},
    ]
    uri = None
    for index, operation in enumerate(operations):
        home, provider, _, _ = external_provider("mirror-restart")
        provider._hermes_home = str(home)
        provider._ensure_client = provider._new_client = lambda: client
        manager = MemoryManager()
        manager.add_provider(provider)
        result = {"success": True}
        if index == 1:
            result["replaced_entries"] = {1: "Preferred shell is zsh"}
        elif index == 2:
            result["removed_entries"] = {1: "Preferred shell is fish"}
        manager.notify_memory_tool_write(result, {"target": target, "operations": [operation]})
        provider.shutdown()
        registry = json.loads((home / "openviking/memory_mirror_registry.json").read_text())
        if index == 0:
            uri = next(iter(files))
            assert files == {uri: "Preferred shell is zsh"}
        elif index == 1:
            assert files == {uri: "Preferred shell is fish"}
        else:
            assert files == {}
        assert [entry["uri"] for entry in registry["entries"]] == ([uri] if files else [])

    assert [payload["uri"] for _, payload in requests] == [uri, uri, uri]
    assert requests[1][1]["wait"] is True
    assert requests[2][1] == {"uri": uri, "recursive": False, "wait": True}


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
        if operation == "mirror":
            provider._native_memory_mirror.shutdown(timeout=10)
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


def _live_provider(external_provider, monkeypatch, name="live-commit"):
    from unittest.mock import Mock

    home, provider, module, _ = external_provider(name)
    monkeypatch.setenv("HERMES_HOME", str(home))
    provider._hermes_home = str(home)
    provider._session_id = "live-sid"
    provider._client = Mock()
    provider._client.get.return_value = {"pending_tokens": 20000}
    provider._ensure_client = lambda: True
    provider._new_client = lambda: provider._client
    provider._acquire_run_lock()
    return home, provider, module


def _finish_turn(provider, user="one", sid="live-sid"):
    provider.sync_turn(user, "reply", session_id=sid)
    assert provider._drain_writers(sid, timeout=5)
    assert provider._drain_finalizers(timeout=5)


def test_live_session_commits_at_token_threshold_and_rearms(external_provider, monkeypatch):
    """Small turns do not trigger a commit; crossing the token limit does, repeatedly."""
    home, provider, _ = _live_provider(external_provider, monkeypatch)
    client = provider._client
    client.get.return_value = {"pending_tokens": 19999}
    for _ in range(7):
        _finish_turn(provider)
    assert not [c for c in client.post.call_args_list if c.args[0].endswith("/commit")]
    marker = provider._state_path("pending", "live-sid")
    assert marker.exists()
    client.get.return_value = {"result": {"pending_tokens": 20000}}
    _finish_turn(provider)
    assert provider._turn_count == 0
    assert provider._has_committed_session("live-sid")
    assert not marker.exists()
    client.get.return_value = {"pending_tokens": 100}
    _finish_turn(provider, "new turn")
    assert not provider._has_committed_session("live-sid")
    assert marker.exists()
    client.get.return_value = {"pending_tokens": 25000}
    _finish_turn(provider)
    commits = [c for c in client.post.call_args_list if c.args[0].endswith("/commit")]
    assert len(commits) == 2
    assert commits[0].args == ("/api/v1/sessions/live-sid/commit", {"keep_recent_count": 0})
    provider.on_session_end([])
    assert len([c for c in client.post.call_args_list if c.args[0].endswith("/commit")]) == 2


def test_live_commit_waits_for_registered_writer_before_committing(external_provider, monkeypatch):
    """A live threshold commit must not cross an upload that is still running."""
    _, provider, _ = _live_provider(external_provider, monkeypatch)
    upload_started = threading.Event()
    release_upload = threading.Event()
    commit_paths = []

    def post(path, payload=None, **kwargs):
        if path.endswith("/messages/batch"):
            upload_started.set()
            assert release_upload.wait(timeout=5)
        elif path.endswith("/commit"):
            commit_paths.append(path)
        return {}

    provider._client.post.side_effect = post
    provider.sync_turn("one", "reply", session_id="live-sid")
    try:
        assert upload_started.wait(timeout=5)
        assert commit_paths == []
    finally:
        release_upload.set()
    assert provider._drain_writers("live-sid", timeout=5)
    assert provider._drain_finalizers(timeout=5)
    assert commit_paths == ["/api/v1/sessions/live-sid/commit"]


def test_failed_live_commit_stays_pending_and_retries_on_next_turn(external_provider, monkeypatch):
    _, provider, _ = _live_provider(external_provider, monkeypatch)
    commit_attempts = []

    def post(path, payload=None, **kwargs):
        if path.endswith("/commit"):
            commit_attempts.append(path)
            if len(commit_attempts) == 1:
                raise RuntimeError("temporary failure")
        return {}

    provider._client.post.side_effect = post
    _finish_turn(provider)
    assert provider._turn_count == 1
    assert not provider._has_committed_session("live-sid")
    assert provider._state_path("pending", "live-sid").exists()
    _finish_turn(provider, "two")
    assert len(commit_attempts) == 2
    assert provider._turn_count == 0
    assert not provider._state_path("pending", "live-sid").exists()


@pytest.mark.parametrize("metadata", [{}, {"pending_tokens": "invalid"}, RuntimeError("unavailable")])
def test_live_metadata_failure_does_not_replay_uploaded_turn(external_provider, monkeypatch, metadata):
    _, provider, _ = _live_provider(external_provider, monkeypatch)
    if isinstance(metadata, Exception):
        provider._client.get.side_effect = metadata
    else:
        provider._client.get.return_value = metadata
    _finish_turn(provider)
    assert provider._client.post.call_count == 1
    assert provider._state_path("pending", "live-sid").exists()
    provider._client.get.side_effect = None
    provider._client.get.return_value = {"pending_tokens": 20000}
    _finish_turn(provider)
    assert provider._has_committed_session("live-sid")


def test_live_failed_upload_does_not_commit_partial_turn(external_provider, monkeypatch):
    _, provider, _ = _live_provider(external_provider, monkeypatch)
    provider._client.post.side_effect = RuntimeError("upload unavailable")
    _finish_turn(provider)
    assert not provider._client.get.called
    assert provider._state_path("pending", "live-sid").exists()
    provider._client.post.side_effect = None
    _finish_turn(provider)
    assert provider._has_committed_session("live-sid")


def test_live_config_schema_save_and_profile_overrides(external_provider, monkeypatch):
    from agent.secret_scope import build_profile_secret_scope, reset_secret_scope, set_secret_scope
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home_a, provider, module, _ = external_provider("threshold-a")
    home_b, _, _, _ = external_provider("threshold-b")
    provider.save_config({"commit_token_threshold": 8000}, str(home_a))
    provider.save_config({"commit_token_threshold": 12000}, str(home_b))
    (home_b / ".env").write_text("OPENVIKING_COMMIT_TOKEN_THRESHOLD=4000\n")
    monkeypatch.setenv("OPENVIKING_COMMIT_TOKEN_THRESHOLD", "99000")
    schema = next(f for f in provider.get_config_schema() if f["key"] == "commit_token_threshold")
    assert schema["default"] == 20000
    assert schema["env_var"] == "OPENVIKING_COMMIT_TOKEN_THRESHOLD"
    monkeypatch.setattr("agent.secret_scope._MULTIPLEX_ACTIVE", True)
    for home, expected in [(home_a, 8000), (home_b, 4000), (home_a, 8000)]:
        token = set_hermes_home_override(home)
        scope = set_secret_scope(build_profile_secret_scope(home))
        try:
            cfg = module._load_hermes_openviking_config()
            assert provider._setting("commit_token_threshold", cfg) == expected
        finally:
            reset_secret_scope(scope)
            reset_hermes_home_override(token)


@pytest.mark.parametrize("value,expected", [("bad", 20000), (True, 20000), ("nan", 20000), (999, 1000), (1000001, 1000000)])
def test_live_threshold_validation(external_provider, value, expected):
    _, provider, _, _ = external_provider("threshold-validation")
    assert provider._setting("commit_token_threshold", {"commit_token_threshold": value}) == expected


def test_live_commit_uses_upload_client_after_connection_change(external_provider, monkeypatch):
    from unittest.mock import Mock

    _, provider, _ = _live_provider(external_provider, monkeypatch)
    original = provider._client
    replacement = Mock()

    def get(*_args, **_kwargs):
        provider._client = replacement
        return {"pending_tokens": 20000}

    original.get.side_effect = get
    _finish_turn(provider)
    assert original.post.call_args.args[0].endswith("/commit")
    replacement.post.assert_not_called()


def test_session_switch_commits_below_live_threshold(external_provider, monkeypatch):
    _, provider, _ = _live_provider(external_provider, monkeypatch)
    provider._client.get.return_value = {"pending_tokens": 1}
    _finish_turn(provider)
    provider.on_session_switch("new-sid")
    assert provider._drain_finalizers(timeout=5)
    assert provider._client.post.call_args.args[0] == "/api/v1/sessions/live-sid/commit"
    assert provider._session_id == "new-sid"


@pytest.mark.parametrize("agent_context", ["cron", "subagent", "flush"])
def test_non_primary_contexts_skip_writes(external_provider, monkeypatch, agent_context):
    """cron/subagent/flush contexts stay read-only: no turn uploads, commits, or mirroring."""
    from unittest.mock import Mock

    home, provider, module, _ = external_provider(f"non-primary-{agent_context}")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:19531")
    monkeypatch.setattr(module, "_classify_runtime_openviking_health", lambda *_: ("healthy", ""))
    provider.initialize("live-sid", hermes_home=str(home))
    assert (provider._agent_context, provider._writes_enabled) == ("primary", True)
    provider.initialize("live-sid", hermes_home=str(home), agent_context=agent_context)
    assert (provider._agent_context, provider._writes_enabled) == (agent_context, False)

    provider._client = Mock()
    provider._client.get.return_value = {"pending_tokens": 20000}
    provider._ensure_client = lambda: True
    provider._new_client = lambda: provider._client
    provider._acquire_run_lock()
    _finish_turn(provider)
    provider.on_session_switch("new-sid")
    provider.on_session_end([])
    provider.on_memory_write("add", "user", f"not mirrored ({agent_context})")
    assert provider._drain_finalizers(timeout=5)

    assert provider._client.method_calls == []
    assert provider._pending_sessions() == []


@pytest.mark.parametrize("agent_context", ["cron", "subagent", "flush"])
def test_non_primary_session_switch_keeps_search_on_current_session(external_provider, monkeypatch, agent_context):
    """A read-only provider must still follow session changes for recall."""
    from unittest.mock import Mock

    from agent.memory_manager import MemoryManager

    home, provider, module, _ = external_provider(f"read-only-switch-{agent_context}")
    monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:19531")
    monkeypatch.setattr(module, "_classify_runtime_openviking_health", lambda *_: ("healthy", ""))
    provider.initialize("old-sid", hermes_home=str(home), agent_context=agent_context)
    client = Mock()
    client.post.return_value = {"result": {"memories": []}}
    provider._client = client
    provider._ensure_client = lambda: client
    provider._profile_prefetched_sessions.update({"old-sid", "new-sid"})

    manager = MemoryManager()
    manager.add_provider(provider)
    manager.on_session_switch("new-sid", reason="compression")
    assert provider._session_id == "new-sid"
    assert provider._profile_prefetched_sessions == set()
    provider.handle_tool_call("viking_search", {"query": "preferences", "mode": "deep"})
    provider.on_session_end([])

    client.post.assert_called_once_with(
        "/api/v1/search/search", {"query": "preferences", "session_id": "new-sid"}
    )


def test_live_commit_does_not_block_next_turn_or_lose_its_pending_marker(external_provider, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    _, provider, _ = _live_provider(external_provider, monkeypatch)
    started = threading.Event()
    release = threading.Event()
    commits = []

    def post(path, payload=None, **kwargs):
        if path.endswith('/commit'):
            commits.append(path)
            started.set()
            assert release.wait(timeout=5)
        return {}

    provider._client.post.side_effect = post
    provider.sync_turn('first', 'reply', session_id='live-sid')
    assert started.wait(timeout=5)
    provider._client.get.return_value = {'pending_tokens': 100}
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            submitted = executor.submit(provider.sync_turn, 'second', 'reply', session_id='live-sid')
            submitted.result(timeout=2)
        finally:
            release.set()
    assert provider._drain_writers('live-sid', timeout=5)
    assert provider._drain_finalizers(timeout=5)
    assert not provider._has_committed_session('live-sid')
    assert provider._state_path('pending', 'live-sid').exists()
    provider.on_session_end([])
    assert len(commits) == 2


@pytest.fixture
def reload_provider(external_provider, monkeypatch):
    from unittest.mock import Mock

    home, provider, module, _ = external_provider("connection-reload")
    monkeypatch.setenv("HERMES_HOME", str(home))
    backends = {name: Mock() for name in ("alice", "bob")}
    for backend in backends.values():
        backend.get.return_value = {"result": {"pending_tokens": 20}}
        backend.post.return_value = {"result": {}}

    class Client:
        def __init__(self, endpoint, api_key="", *, account="", user="", agent=""):
            self._conn_snapshot = (endpoint, api_key, account, user, agent)
            self.get = backends[user].get
            self.post = backends[user].post

    monkeypatch.setattr(module, "_VikingClient", Client)
    monkeypatch.setattr(module, "_classify_runtime_openviking_health", lambda *_: ("healthy", ""))

    def reload(user, endpoint="http://127.0.0.1:19531"):
        monkeypatch.setenv("OPENVIKING_USER", user)
        monkeypatch.setenv("OPENVIKING_ENDPOINT", endpoint)
        monkeypatch.setenv("OPENVIKING_API_KEY", "private-test-key")
        if not provider._env_refresh_enabled:
            provider.initialize("same-sid", hermes_home=str(home))
        else:
            assert provider._ensure_client() is not None

    reload("alice")
    return home, provider, module, backends, reload


def test_upload_client_keeps_published_defaults_until_reload(external_provider, monkeypatch):
    _, provider, module, _ = external_provider("published-defaults")
    monkeypatch.setenv("OPENVIKING_USER", "alice")
    provider._endpoint = "http://127.0.0.1:19531"
    provider._client = module._VikingClient(provider._endpoint)
    provider._conn_snapshot = provider._settings_tuple()
    # /reload can update the environment before the provider sees the change.
    monkeypatch.setenv("OPENVIKING_USER", "bob")
    upload_client = provider._new_client()
    assert upload_client._headers()["X-OpenViking-User"] == "alice"


@pytest.mark.parametrize("bob_tokens", [20, 20000])
@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:19531", "http://127.0.0.1:19532"])
def test_reload_keeps_new_connection_pending_after_old_commit(reload_provider, monkeypatch, bob_tokens, endpoint):
    """A's finalizer must neither clear B's work nor suppress B's finalizer."""
    _, provider, _, backends, reload = reload_provider
    sid = "same-sid"
    alice_get = threading.Event()
    bob_get = threading.Event()
    release_alice = threading.Event()
    release_bob = threading.Event()
    alice_claimed = threading.Event()
    claim = provider._claim_deferred_sid

    def observe_claim(*args, **kwargs):
        claimed = claim(*args, **kwargs)
        if claimed and not kwargs.get("release"):
            alice_claimed.set()
        return claimed

    def metadata(started, release, tokens):
        started.set()
        assert release.wait(timeout=10)
        return {"result": {"pending_tokens": tokens}}

    monkeypatch.setattr(provider, "_claim_deferred_sid", observe_claim)
    backends["alice"].get.side_effect = lambda *_: metadata(alice_get, release_alice, 20000)
    backends["bob"].get.side_effect = lambda *_: metadata(bob_get, release_bob, bob_tokens)
    provider.sync_turn("Alice's pending turn", "reply", session_id=sid)
    try:
        assert alice_get.wait(timeout=5)
        alice_marker = provider._state_path("pending", sid)
        reload("bob", endpoint)
        provider.sync_turn("Bob's pending turn", "reply", session_id=sid)
        assert bob_get.wait(timeout=5)
        bob_marker = provider._state_path("pending", sid)
        assert bob_marker.exists()
        # A claims the finalizer while B's metadata request is still pending.
        release_alice.set()
        assert alice_claimed.wait(timeout=5)
    finally:
        release_alice.set()
        release_bob.set()
    assert provider._drain_writers(sid, timeout=5)
    assert provider._drain_finalizers(timeout=5)
    assert not alice_marker.exists()
    if bob_tokens < 20000:
        assert bob_marker.exists()
        assert not provider._has_committed_session(sid)
        assert provider._turn_count == 1
    else:
        # B must get its own threshold finalizer while A already owns one.
        assert provider._has_committed_session(sid)
        assert not bob_marker.exists()
    provider.on_session_end([])
    assert not bob_marker.exists()
    for backend in backends.values():
        commits = [c for c in backend.post.call_args_list if c.args[0].endswith("/commit")]
        assert len(commits) == 1
        assert commits[0].args == (f"/api/v1/sessions/{sid}/commit", {"keep_recent_count": 0})


def test_reload_recovery_preserves_markers_for_other_connections(reload_provider, monkeypatch):
    """Failed commits survive restart and recover only with matching credentials."""
    home, provider, module, backends, reload = reload_provider
    sid = "same-sid"
    markers = {}

    def fail_commit(path, *_args, **_kwargs):
        if path.endswith("/commit"):
            raise RuntimeError("temporary failure")
        return {}

    for user in ("alice", "bob"):
        reload(user)
        backends[user].post.side_effect = fail_commit
        _finish_turn(provider, user, sid=sid)
        markers[user] = provider._state_path("pending", sid)
        provider.on_session_end([])
        assert markers[user].exists()
        assert not provider._has_committed_session(sid)
        assert "private-test-key" not in markers[user].read_text()
    assert markers["alice"] != markers["bob"]
    provider.shutdown()

    for backend in backends.values():
        backend.post.side_effect = None
        backend.post.reset_mock()
    for user in ("bob", "alice"):
        monkeypatch.setenv("OPENVIKING_USER", user)
        recovered = module.OpenVikingMemoryProvider()
        try:
            recovered.initialize("new-sid", hermes_home=str(home))
            assert recovered._drain_finalizers(timeout=5)
            assert not markers[user].exists()
            backends[user].post.assert_called_once_with(f"/api/v1/sessions/{sid}/commit", {"keep_recent_count": 0})
            if user == "bob":
                assert markers["alice"].exists()
                backends["alice"].post.assert_not_called()
        finally:
            recovered.shutdown()


def test_reload_back_to_original_identity_keeps_new_generation_pending(reload_provider):
    _, provider, _, backends, reload = reload_provider
    sid = "same-sid"
    first_get = threading.Event()
    release_first = threading.Event()
    later_get = threading.Event()
    bob_get = threading.Event()

    def alice_metadata(*_args):
        if not first_get.is_set():
            first_get.set()
            assert release_first.wait(timeout=10)
            return {"pending_tokens": 20000}
        later_get.set()
        return {"pending_tokens": 20}

    def bob_metadata(*_args):
        bob_get.set()
        return {"pending_tokens": 20}

    backends["alice"].get.side_effect = alice_metadata
    backends["bob"].get.side_effect = bob_metadata
    provider.sync_turn("old Alice turn", "reply", session_id=sid)
    try:
        assert first_get.wait(timeout=5)
        old_marker = provider._state_path("pending", sid)
        reload("bob")
        provider.sync_turn("Bob turn", "reply", session_id=sid)
        assert bob_get.wait(timeout=5)
        bob_marker = provider._state_path("pending", sid)
        reload("alice")
        provider.sync_turn("new Alice turn", "reply", session_id=sid)
        assert later_get.wait(timeout=5)
        new_marker = provider._state_path("pending", sid)
    finally:
        release_first.set()
    assert provider._drain_writers(sid, timeout=5)
    assert provider._drain_finalizers(timeout=5)
    assert not old_marker.exists()
    assert new_marker.exists() and bob_marker.exists()
    assert not provider._has_committed_session(sid)
    assert provider._turn_count == 1
    provider.on_session_end([])
    assert not new_marker.exists()
    assert bob_marker.exists()
    commits = [c for c in backends["alice"].post.call_args_list if c.args[0].endswith("/commit")]
    assert len(commits) == 2
