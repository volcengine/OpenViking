"""Exercise profile routing and the actual MCP wire protocol over loopback HTTP."""

import asyncio
import copy
import importlib
import json
import threading
import time
from builtins import ExceptionGroup
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@contextmanager
def mcp_server(*, trusted=False, status_code=200, call_delay=0,
               health_status=200, health_payload=None, health_auth=False):
    requests = []
    entered, release = threading.Event(), threading.Event()
    release.set()
    schema = {
        "name": "forget",
        "description": "Server-owned description",
        "inputSchema": {
            "type": "object",
            "properties": {"uri": {"type": "string"}},
            "required": ["uri"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, value=None, status=200):
            body = json.dumps(value).encode() if value is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            requests.append((self.path, dict(self.headers), None))
            if self.path == "/health":
                if health_payload is not None:
                    entered.set()
                if health_auth and "Authorization" not in self.headers:
                    return self.respond({"error": {"message": "Authentication required"}}, 401)
                if health_status == 302:
                    self.send_response(302)
                    self.send_header("Location", "https://www.example.com/canonical-location")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                return self.respond(health_payload if health_payload is not None else
                                    {"status": "ok", "healthy": True, "version": "0.4.23"}, health_status)
            if self.path != "/api/v1/system/status":
                return self.respond(status=405)
            entered.set()
            assert release.wait(10)
            if status_code != 200:
                return self.respond({"error": {"message": "rejected"}}, status_code)
            if trusted and not self.headers.get("X-OpenViking-Account"):
                return self.respond(
                    {
                        "error": {
                            "message": "Trusted mode requests must include X-OpenViking-Account and X-OpenViking-User"
                        }
                    },
                    400,
                )
            self.respond({"status": "ok", "result": {"user": "alice"}})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, dict(self.headers), body))
            if "id" not in body:
                return self.respond(status=202)
            method = body["method"]
            if method == "initialize":
                result = {
                    "protocolVersion": body["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            elif method == "tools/list":
                result = {"tools": [schema]}
            elif method == "tools/call":
                time.sleep(call_delay)
                result = {
                    "content": [{"type": "text", "text": "server result"}],
                    "structuredContent": {"uri": body["params"]["arguments"]["uri"]},
                    "isError": False,
                }
            else:
                return self.respond(
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "error": {"code": -32601, "message": "unknown method"},
                    }
                )
            self.respond({"jsonrpc": "2.0", "id": body["id"], "result": result})

        def do_DELETE(self):
            self.respond(status=405)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, schema, entered, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def configure_home(home, endpoint, *, label="alice", linked=False):
    settings = {
        "use_ovcli_config": linked,
        "endpoint": endpoint,
        "account": label,
        "user": label,
        "agent": f"hermes.{label}",
    }
    if linked:
        settings["ovcli_config_path"] = str(home / "ovcli.conf")
        (home / "ovcli.conf").write_text(
            json.dumps(
                {
                    "url": endpoint,
                    "root_api_key": f"key-{label}",
                    "account": label,
                    "user": label,
                    "actor_peer_id": f"hermes.{label}",
                }
            )
        )
    else:
        (home / ".env").write_text(f"OPENVIKING_API_KEY=key-{label}\n")
    config = {
        "memory": {"provider": "openviking", "openviking": settings},
        "mcp_servers": {"openviking": {"headers": {"X-Custom": label}}},
    }
    (home / "config.yaml").write_text(json.dumps(config))
    return config


@pytest.mark.parametrize("enabled", [True, False, "false"])
@pytest.mark.parametrize("trust", ["full", "untrusted", None])
def test_setup_preserves_policy_and_never_copies_keys(external_provider, enabled, trust):
    home, _, module, _ = external_provider("mcp-setup")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    entry = {
        "url": "https://old.example/mcp",
        "enabled": enabled,
        "timeout": 91,
        "trust": trust,
        "env": {"SSL_CERT_FILE": "/explicit/ca.pem"},
        "tools": {"include": ["read"], "exclude": ["forget"]},
        "ssl_verify": "/custom/ca.pem",
        "headers": {"X-Custom": "${CUSTOM_SECRET}", "authorization": "old-secret"},
    }
    config = {"mcp_servers": {"openviking": entry, "other": {"url": "https://other.example/mcp"}}}
    original = copy.deepcopy(config)
    mcp.configure(config, str(home))
    new = config["mcp_servers"]["openviking"]
    assert new["command"] == "hermes" and new["args"] == ["openviking", "mcp"]
    assert new["env"]["HERMES_HOME"] == str(home.resolve())
    assert "old-secret" not in json.dumps(config) and "url" not in new
    assert new["headers"] == {"X-Custom": "${CUSTOM_SECRET}"}
    for key in ("enabled", "timeout", "tools", "ssl_verify", "trust"):
        assert new[key] == original["mcp_servers"]["openviking"][key]
    assert config["mcp_servers"]["other"] == original["mcp_servers"]["other"]
    before = copy.deepcopy(config)
    mcp.configure(config, str(home))
    assert config == before
    assert new["env"]["SSL_CERT_FILE"] == "/explicit/ca.pem"


def test_new_setup_exposes_all_tools_with_untrusted_policy(external_provider):
    home, _, module, _ = external_provider("mcp-default-policy")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    config = {}
    mcp.configure(config, str(home))
    entry = config["mcp_servers"]["openviking"]
    assert entry["trust"] == "untrusted"
    assert "tools" not in entry


def test_setup_preserves_existing_implicit_full_trust(external_provider):
    home, _, module, _ = external_provider("mcp-existing-policy")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    config = {"mcp_servers": {"openviking": {"url": "https://old.example/mcp"}}}
    mcp.configure(config, str(home))
    assert config["mcp_servers"]["openviking"]["trust"] == "full"


def test_transport_environment_survives_hermes_stdio_filter(external_provider, monkeypatch):
    from tools.mcp_tool_config import _build_safe_env, _interpolate_env_vars

    home, _, module, _ = external_provider("mcp-env")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    for key in mcp._TRANSPORT_ENV_KEYS:
        monkeypatch.setenv(key, "transport-setting")
    config = {}
    mcp.configure(config, str(home))
    env = _build_safe_env(_interpolate_env_vars(config["mcp_servers"]["openviking"]["env"]))
    for key in mcp._TRANSPORT_ENV_KEYS:
        assert env[key] == "transport-setting"


def test_unset_transport_references_are_removed_before_sdk_start(external_provider, monkeypatch):
    _, _, module, _ = external_provider("mcp-unset-env")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    for key in (*mcp._ENV_KEYS, *mcp._TRANSPORT_ENV_KEYS):
        monkeypatch.setenv(key, "${" + key + "}")

    async def serve(_home):
        assert all(key not in mcp.os.environ for key in (*mcp._ENV_KEYS, *mcp._TRANSPORT_ENV_KEYS))

    monkeypatch.setattr(mcp, "serve", serve)
    mcp.run(None)


@pytest.mark.parametrize("trusted", [False, True])
def test_server_owns_schemas_results_and_identity(external_provider, trusted):
    from mcp import types

    home, provider, module, _ = external_provider("mcp-http")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    assert provider.get_tool_schemas() == []
    with mcp_server(trusted=trusted) as (endpoint, requests, schema, *_):
        configure_home(home, endpoint)
        connection = mcp.Connection(home, module)
        tools = asyncio.run(connection.request("tools/list", None))
        assert tools.tools[0].model_dump(by_alias=True, exclude_none=True) == schema
        uri = "viking://user/alice/memories/exact.md"
        result = asyncio.run(
            connection.request(
                "tools/call", types.CallToolRequestParams(name="forget", arguments={"uri": uri})
            )
        )
        assert result.content[0].text == "server result"
        assert result.structured_content == {"uri": uri}
        calls = [
            (headers, body)
            for _, headers, body in requests
            if body and body.get("method") == "tools/call"
        ]
        assert len(calls) == 1
        headers, body = calls[0]
        assert body["params"]["arguments"] == {"uri": uri}
        assert headers["Authorization"] == "Bearer key-alice"
        assert headers["X-OpenViking-Actor-Peer"] == "hermes.alice"
        assert headers["X-Custom"] == "alice"
        assert headers.get("X-OpenViking-Account") == ("alice" if trusted else None)


def test_connection_snapshot_survives_reload_and_next_call_uses_new_connection(external_provider):
    from mcp import types

    home, _, module, _ = external_provider("mcp-reload")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server() as a, mcp_server() as b:
        configure_home(home, a[0], linked=True)
        connection = mcp.Connection(home, module)
        a[4].clear()
        results = []

        def request():
            results.append(
                asyncio.run(
                    connection.request(
                        "tools/call",
                        types.CallToolRequestParams(
                            name="forget", arguments={"uri": "viking://user/alice/memories/a.md"}
                        ),
                    )
                )
            )

        worker = threading.Thread(target=request)
        worker.start()
        try:
            assert a[3].wait(5)
            configure_home(home, b[0], label="bob", linked=True)
            a[4].set()
            worker.join(10)
            assert not worker.is_alive() and len(results) == 1
            assert not b[1]
            asyncio.run(connection.request("tools/list", None))
            a_calls = [h for _, h, body in a[1] if body and body.get("method") == "tools/call"]
            assert a_calls[0]["Authorization"] == "Bearer key-alice"
            assert all(h.get("Authorization") == "Bearer key-bob" for path, h, _ in b[1] if path != "/health")
        finally:
            a[4].set()
            worker.join(10)


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_failed_identity_probe_never_dispatches_or_retries_a_write(external_provider, status):
    from mcp import types

    home, _, module, _ = external_provider("mcp-failed-probe")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(status_code=status) as (endpoint, requests, *_):
        configure_home(home, endpoint)
        with pytest.raises(module._OpenVikingHTTPError) as caught:
            asyncio.run(
                mcp.Connection(home, module).request(
                    "tools/call",
                    types.CallToolRequestParams(name="forget", arguments={"uri": "exact.md"}),
                )
            )
        assert len(requests) == 2 and requests[-1][2] is None
        assert requests[0][0] == "/health" and "Authorization" not in requests[0][1]
        hint = mcp._error_hint(ExceptionGroup("private transport detail", [caught.value]))
        if status == 401:
            assert "authentication failed" in hint and "API key" in hint
        elif status == 403:
            assert "access was denied" in hint and "access permissions" in hint
        assert "rejected" not in hint and "key-alice" not in hint


@pytest.mark.parametrize("health_status, health_payload", [(302, None), (200, {}), (200, {"status": "ok", "healthy": False, "version": "0.4.23"})])
def test_unverified_endpoint_never_receives_mcp_credentials(external_provider, health_status, health_payload):
    home, _, module, _ = external_provider("mcp-unverified-endpoint")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(health_status=health_status, health_payload=health_payload) as (endpoint, requests, *_):
        configure_home(home, endpoint)
        with (home / ".env").open("a") as env:
            env.write('OPENVIKING_EXTRA_HEADERS={"CF-Access-Client-Secret":"synthetic-secret"}\n')
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(mcp.Connection(home, module).request("tools/list", None))
        assert "did not identify itself as OpenViking" in mcp._error_hint(caught.value)
        assert len(requests) == 1 and requests[0][0] == "/health"
        assert all(k not in requests[0][1] for k in ["X-API-Key", "Authorization", "CF-Access-Client-Secret", "X-Custom", "X-OpenViking-Actor-Peer"])


def test_authenticated_health_challenge_keeps_existing_hosted_server_support(external_provider):
    home, _, module, _ = external_provider("mcp-authenticated-health")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(health_auth=True) as (endpoint, requests, *_):
        configure_home(home, endpoint)
        assert asyncio.run(mcp.Connection(home, module).request("tools/list", None)).tools
        assert [r[0] for r in requests[:3]] == ["/health", "/health", "/api/v1/system/status"]
        assert "Authorization" not in requests[0][1]
        assert requests[1][1]["Authorization"] == "Bearer key-alice"
        assert "X-OpenViking-Account" not in requests[1][1]


@pytest.mark.parametrize(
    "name",
    [
        "mcp__openviking__find",
        "mcp__openviking__search",
        "mcp__openviking__read",
        "mcp__openviking__list",
        "mcp__openviking__ls",
        "mcp__openviking__tree",
        "mcp__openviking__grep",
        "mcp__openviking__glob",
        "viking_search",
        "mcp_openviking_search",
    ],
)
def test_retrieved_content_is_not_captured_again(external_provider, name):
    _, provider, _, _ = external_provider("mcp-filter")
    messages = [
        {"role": "user", "content": "My new preference"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "lookup", "type": "function", "function": {"name": name, "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "lookup", "name": name, "content": "OLD RETRIEVED MEMORY"},
        {"role": "assistant", "content": "Acknowledged"},
    ]
    batch = provider._messages_to_openviking_batch(messages)
    assert "OLD RETRIEVED MEMORY" not in json.dumps(batch)
    assert "My new preference" in json.dumps(batch)


@pytest.mark.parametrize(
    "name", ["mcp__openviking__remember", "mcp__openviking__write", "mcp_other_search"]
)
def test_write_results_and_other_servers_are_not_misclassified(external_provider, name):
    _, _, module, _ = external_provider("mcp-other")
    assert not module._is_openviking_recall_tool_name(name)


def test_timed_out_write_is_not_replayed(external_provider):
    import httpx2
    from mcp import types

    home, _, module, _ = external_provider("mcp-timeout")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(call_delay=0.5) as (endpoint, requests, *_):
        config = configure_home(home, endpoint)
        config["mcp_servers"]["openviking"]["timeout"] = 0.1
        (home / "config.yaml").write_text(json.dumps(config))
        try:
            asyncio.run(
                mcp.Connection(home, module).request(
                    "tools/call",
                    types.CallToolRequestParams(name="forget", arguments={"uri": "exact.md"}),
                )
            )
        except (ExceptionGroup, TimeoutError, httpx2.TimeoutException) as exc:
            assert "timed out" in mcp._error_hint(exc)
        else:
            pytest.fail("The slow write must time out")
        calls = [body for _, _, body in requests if body and body.get("method") == "tools/call"]
        assert len(calls) == 1


@pytest.mark.parametrize("enabled", [False, "false", 0, "off"])
def test_disabled_mcp_does_not_block_memory_setup(external_provider, monkeypatch, enabled):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home, _, module, _ = external_provider("mcp-disabled-setup")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    config = configure_home(home, "http://127.0.0.1:9")
    config["mcp_servers"]["openviking"]["enabled"] = enabled
    (home / "config.yaml").write_text(json.dumps(config))
    token = set_hermes_home_override(home)
    try:
        assert mcp.validate_connection(module, {}) == (True, "")
    finally:
        reset_hermes_home_override(token)


def test_setup_checks_mcp_without_changing_saved_connection(external_provider):
    home, _, module, _ = external_provider("mcp-preflight")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(status_code=404) as (endpoint, requests, *_):
        configure_home(home, endpoint)
        original = (home / "config.yaml").read_bytes()
        values = {
            "endpoint": endpoint,
            "api_key": "test-secret",
            "account": "alice",
            "user": "alice",
            "agent": "",
        }
        ok, message = mcp.validate_connection(module, values)
        assert not ok and "MCP is unavailable" in message
        assert "test-secret" not in message
        assert (home / "config.yaml").read_bytes() == original
        assert len(requests) == 2


def test_null_mcp_config_resolves_and_validates_before_setup(external_provider):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home, _, module, _ = external_provider("mcp-null-config")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server() as (endpoint, requests, *_):
        config = configure_home(home, endpoint)
        config["mcp_servers"] = None
        (home / "config.yaml").write_text(json.dumps(config))
        values, entry, _ = mcp.Connection(home, module).resolve()
        assert entry == {}
        token = set_hermes_home_override(home)
        try:
            assert mcp.validate_connection(module, values) == (True, "")
        finally:
            reset_hermes_home_override(token)
        assert any(body and body.get("method") == "tools/list" for _, _, body in requests)
        mcp.configure(config, str(home))
        assert config["mcp_servers"]["openviking"]["trust"] == "untrusted"


@pytest.mark.parametrize("linked", [False, True])
def test_gateway_headers_reach_discovery_and_setup_validation(external_provider, linked):
    home, _, module, _ = external_provider("mcp-gateway-headers")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server() as (endpoint, requests, *_):
        configure_home(home, endpoint, linked=linked)
        extra = {"CF-Access-Client-Id": "synthetic-id", "CF-Access-Client-Secret": "synthetic-secret"}
        if linked:
            path = home / "ovcli.conf"
            config = json.loads(path.read_text())
            config["extra_headers"] = extra
            path.write_text(json.dumps(config))
        else:
            with (home / ".env").open("a") as env:
                env.write("OPENVIKING_EXTRA_HEADERS=" + json.dumps(extra) + "\n")
        connection = mcp.Connection(home, module)
        assert asyncio.run(connection.request("tools/list", None)).tools
        values, _, _ = connection.resolve()
        assert mcp.validate_connection(module, values) == (True, "")
        assert all(all(headers.get(k) == v for k, v in extra.items()) for path, headers, _ in requests if path != "/health")
        assert all(k not in requests[0][1] for k in extra)


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("unverified", [False, True])
def test_discovery_waits_for_managed_port_recovery_only(external_provider, monkeypatch, managed, unverified):
    home, _, module, _ = external_provider("mcp-discovery-recovery")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(status_code=401, health_payload={} if unverified else None) as foreign, mcp_server() as owned:
        configure_home(home, foreign[0])
        connection = mcp.Connection(home, module)
        resolve = connection.resolve

        def settings():
            values, entry, _ = resolve()
            return values, entry, managed

        monkeypatch.setattr(connection, "resolve", settings)

        async def recover():
            task = asyncio.create_task(connection.discover(None))
            try:
                assert await asyncio.to_thread(foreign[3].wait, 5)
                configure_home(home, owned[0])
                return await task
            finally:
                if not task.done():
                    task.cancel()

        if managed:
            assert asyncio.run(recover()).tools[0].name == "forget"
        else:
            with pytest.raises(mcp._UnverifiedServerError if unverified else module._OpenVikingHTTPError):
                asyncio.run(recover())
        assert not any(body for _, _, body in foreign[1])
        if unverified:
            assert all("Authorization" not in h and "X-API-Key" not in h for _, h, _ in foreign[1])


@pytest.mark.parametrize("health_status", [401, 403])
def test_managed_auth_challenge_waits_without_sending_credentials(external_provider, health_status):
    home, _, module, _ = external_provider("mcp-managed-auth-recovery")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    foreign_health = {"status": "ok", "healthy": True, "version": "0.4.23",
                      "auth_mode": "trusted", "root_api_key_required": True}
    with mcp_server(health_status=health_status, health_payload=foreign_health) as foreign, mcp_server() as owned:
        paths = module.quick_local.managed_paths(home)
        paths.root.mkdir()
        key = "synthetic-managed-root-key"
        (home / "config.yaml").write_text(json.dumps({
            "memory": {"provider": "openviking", "openviking": {"deployment": module.quick_local.DEPLOYMENT}},
            "mcp_servers": {"openviking": {"connect_timeout": 5, "headers": {"X-Gateway-Secret": "gateway-key"}}},
        }))
        paths.server_config.write_text(json.dumps({"storage": {"workspace": str(paths.workspace)},
                                                  "server": {"root_api_key": key}}))
        profile = {"url": foreign[0], "root_api_key": key}
        paths.ovcli_config.write_text(json.dumps(profile))
        connection = mcp.Connection(home, module)

        async def recover():
            task = asyncio.create_task(connection.discover(None))
            try:
                assert await asyncio.to_thread(foreign[3].wait, 4)
                profile["url"] = owned[0]
                paths.ovcli_config.write_text(json.dumps(profile))
                return await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        assert asyncio.run(recover()).tools[0].name == "forget"
        assert foreign[1]
        for path, headers, body in foreign[1]:
            assert path == "/health" and body is None
            assert not {"Authorization", "X-API-Key", "X-Gateway-Secret"} & headers.keys()
        assert any(headers.get("X-API-Key") == key for _, headers, _ in owned[1])


@pytest.mark.parametrize("health_status", [401, 403])
def test_managed_provider_checks_ownership_before_auth_probe(external_provider, monkeypatch, health_status):
    home, provider, module, _ = external_provider("mcp-managed-provider-auth")
    foreign_health = {"status": "ok", "healthy": True, "version": "0.4.23",
                      "auth_mode": "trusted", "root_api_key_required": True}
    with mcp_server(health_status=health_status, health_payload=foreign_health) as (endpoint, requests, *_):
        paths = module.quick_local.managed_paths(home)
        paths.root.mkdir()
        key = "synthetic-managed-root-key"
        (home / "config.yaml").write_text(json.dumps({"memory": {"provider": "openviking", "openviking": {
            "deployment": module.quick_local.DEPLOYMENT,
        }}}))
        paths.server_config.write_text(json.dumps({"storage": {"workspace": str(paths.workspace)},
                                                  "server": {"root_api_key": key}}))
        paths.ovcli_config.write_text(json.dumps({"url": endpoint, "root_api_key": key}))
        recovery = []
        monkeypatch.setattr(provider, "_handle_runtime_openviking_unreachable", lambda **_kwargs: recovery.append(True))
        provider.initialize("auth-port-recovery", hermes_home=str(home), platform="cli", agent_context="cron")
        assert recovery and provider._client is None
        assert requests
        assert all("Authorization" not in h and "X-API-Key" not in h for _, h, _ in requests)


def test_discovery_timeout_is_bounded(external_provider, monkeypatch):
    home, _, module, _ = external_provider("mcp-discovery-deadline")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(status_code=503) as (endpoint, requests, *_):
        configure_home(home, endpoint)
        connection = mcp.Connection(home, module)
        values, entry, _ = connection.resolve()
        entry["connect_timeout"] = 0.1
        monkeypatch.setattr(connection, "resolve", lambda: (values, entry, True))
        with pytest.raises(TimeoutError):
            asyncio.run(connection.discover(None))
        assert len(requests) == 2


def test_discovery_deadline_preserves_connection_failure_hint(external_provider, monkeypatch):
    import socket

    home, _, module, _ = external_provider("mcp-unreachable-discovery")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{unavailable.getsockname()[1]}"
        unavailable.close()
        configure_home(home, endpoint)
        connection = mcp.Connection(home, module)
        values, entry, _ = connection.resolve()
        entry["connect_timeout"] = 1
        monkeypatch.setattr(connection, "resolve", lambda: (values, entry, False))
        with pytest.raises(TimeoutError) as caught:
            asyncio.run(connection.discover(None))
        assert "Cannot connect to OpenViking" in mcp._error_hint(caught.value)


def test_discovery_slow_request_is_still_reported_as_timeout(external_provider, monkeypatch):
    home, _, module, _ = external_provider("mcp-slow-discovery")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server() as (endpoint, requests, _, entered, release):
        configure_home(home, endpoint)
        release.clear()
        connection = mcp.Connection(home, module)
        values, entry, _ = connection.resolve()
        entry["connect_timeout"] = 0.1
        monkeypatch.setattr(connection, "resolve", lambda: (values, entry, False))
        try:
            with pytest.raises(TimeoutError) as caught:
                asyncio.run(connection.discover(None))
            assert entered.is_set() and len(requests) == 2
            assert "timed out" in mcp._error_hint(caught.value)
        finally:
            release.set()


@pytest.mark.parametrize("managed", [False, True])
def test_discovery_follows_server_start_during_budget(external_provider, monkeypatch, managed):
    import socket

    home, _, module, _ = external_provider("mcp-cold-server")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with socket.socket() as unavailable, mcp_server() as owned:
        unavailable.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{unavailable.getsockname()[1]}"
        unavailable.close()
        config = configure_home(home, endpoint)
        connection = mcp.Connection(home, module)
        resolve = connection.resolve

        def settings():
            values, entry, _ = resolve()
            entry["connect_timeout"] = 5
            return values, entry, managed

        monkeypatch.setattr(connection, "resolve", settings)

        async def recover():
            task = asyncio.create_task(connection.discover(None))
            await asyncio.sleep(0.1)
            config["memory"]["openviking"]["endpoint"] = owned[0]
            (home / "config.yaml").write_text(json.dumps(config))
            return await task

        assert asyncio.run(recover()).tools[0].name == "forget"


def test_tls_failure_is_actionable_and_not_retried(external_provider, monkeypatch):
    import ssl

    import httpx2

    home, _, module, _ = external_provider("mcp-tls-error")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    configure_home(home, "https://localhost:9")
    connection = mcp.Connection(home, module)
    calls = []

    async def request(*_):
        calls.append(1)
        try:
            raise ssl.SSLCertVerificationError("private certificate details")
        except ssl.SSLCertVerificationError as error:
            raise httpx2.ConnectError("private endpoint") from error

    monkeypatch.setattr(connection, "request", request)
    with pytest.raises(httpx2.ConnectError) as caught:
        asyncio.run(connection.discover(None))
    assert calls == [1]
    hint = mcp._error_hint(caught.value)
    assert "TLS verification failed" in hint and "SSL_CERT_FILE" in hint
    assert "private" not in hint
    assert "TLS verification failed" in mcp._error_hint(
        httpx2.ConnectError("('private certificate is not permitted for this usage',)")
    )
    assert "certificate paths" in mcp._error_hint(FileNotFoundError("secret path"))


@pytest.mark.parametrize("http_error", [False, True])
def test_root_key_rejection_has_a_safe_credential_hint(external_provider, http_error):
    _, _, module, _ = external_provider("mcp-root-key-error")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    message = "ROOT API keys cannot access tenant-scoped data APIs: private account details"
    rejected = module._OpenVikingHTTPError(message, 403) if http_error else RuntimeError(message)
    hint = mcp._error_hint(ExceptionGroup("transport failed", [rejected]))
    assert "user or admin OpenViking API key" in hint
    assert "private account details" not in hint


@pytest.mark.parametrize("servers", [None, {}])
def test_migration_notice_is_visible_once_without_writing_to_stdout(
    external_provider, caplog, capsys, servers
):
    home, provider, _, _ = external_provider("mcp-migration-notice")
    config = {"memory": {"provider": "openviking"}, "mcp_servers": servers}
    (home / "config.yaml").write_text(json.dumps(config))
    provider._hermes_home = str(home)
    callbacks = []
    provider._runtime_warning_callback = callbacks.append
    assert provider.get_tool_schemas() == []
    assert provider.get_tool_schemas() == []
    assert not callbacks
    assert "OpenViking tools now use MCP" in caplog.text
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.count("OpenViking tools now use MCP") == 1
    assert "hermes memory setup openviking" in output.err


def test_configured_but_disabled_mcp_needs_no_migration_notice(external_provider, capsys):
    import yaml

    home, provider, _, _ = external_provider("mcp-disabled-notice")
    provider._hermes_home = str(home)
    config_path = home / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["mcp_servers"] = {"openviking": {"enabled": False}}
    config_path.write_text(json.dumps(config))
    assert provider.get_tool_schemas() == []
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("relocation", ["clone", "import"])
@pytest.mark.parametrize("callback", [False, True])
def test_relocated_profile_repairs_only_adapter_home_and_warns_once(
    external_provider, monkeypatch, capsys, relocation, callback
):
    import shutil

    from hermes_cli.config import read_user_config_raw
    from hermes_constants import (
        get_hermes_home,
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    source, _, source_module, _ = external_provider("source")
    mcp = importlib.import_module(source_module.__name__ + ".mcp_tools")
    config = {
        "memory": {"provider": "openviking", "openviking": {"use_ovcli_config": False}},
        "mcp_servers": {"openviking": {
            "trust": "full", "enabled": False,
            "tools": {"include": ["read"], "exclude": ["forget"]},
            "headers": {"X-Gateway": "${GATEWAY_SECRET}"},
            "env": {"SSL_CERT_FILE": "${PRIVATE_CA}"},
        }, "other": {"command": "other-server"}},
        "model": {"default": "unchanged", "api_key": "${MODEL_SECRET}"},
    }
    mcp.configure(config, str(source))
    (source / "config.yaml").write_text(json.dumps(config))
    destination = source.parent / relocation
    shutil.copytree(source, destination)
    if relocation == "import":
        shutil.rmtree(source)
    home, provider, module, _ = external_provider(relocation)
    monkeypatch.setattr(module, "_classify_runtime_openviking_health", lambda *_: ("healthy", ""))
    notices = []
    kwargs = {"warning_callback": notices.append} if callback else {}
    # Simulate a routed destination while the caller remains bound to another home.
    token = set_hermes_home_override(source)
    try:
        provider.initialize("relocated", hermes_home=str(home), platform="cli", agent_context="cron", **kwargs)
        repaired = read_user_config_raw(home / "config.yaml")
        expected = copy.deepcopy(config)
        expected["mcp_servers"]["openviking"]["env"]["HERMES_HOME"] = str(home.resolve())
        assert repaired == expected
        before = (home / "config.yaml").read_bytes()
        provider.initialize("relocated-again", hermes_home=str(home), platform="cli", agent_context="cron", **kwargs)
        assert (home / "config.yaml").read_bytes() == before
        assert get_hermes_home() == source
    finally:
        reset_hermes_home_override(token)
    output = capsys.readouterr()
    messages = notices if callback else output.err.splitlines()
    assert len(messages) == 1
    assert str(source) in messages[0]
    assert "/reload-mcp" in messages[0] and "before using" in messages[0]
    assert output.out == ""
    if callback:
        assert output.err == ""


@pytest.mark.parametrize("entry", [
    None,
    {"url": "https://example.test/mcp", "env": {"HERMES_HOME": "/old/profile"}},
    {"command": "other", "args": ["serve"], "env": {"HERMES_HOME": "/old/profile"}},
    {"command": "hermes", "args": ["openviking", "mcp"], "env": {}},
    {"command": "hermes", "args": ["openviking", "mcp"], "env": {"HERMES_HOME": "${HERMES_HOME}"}},
])
def test_profile_repair_leaves_other_mcp_config_unchanged(external_provider, entry):
    home, _, module, _ = external_provider("mcp-repair-noop")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    config = {"mcp_servers": {"openviking": entry}}
    (home / "config.yaml").write_text(json.dumps(config))
    before = (home / "config.yaml").read_bytes()
    assert mcp.repair_profile_home(home) is None
    assert (home / "config.yaml").read_bytes() == before


def test_matching_profile_home_is_not_rewritten(external_provider):
    home, _, module, _ = external_provider("mcp-repair-matching")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    config = {}
    mcp.configure(config, str(home))
    (home / "config.yaml").write_text(json.dumps(config))
    before = (home / "config.yaml").read_bytes()
    assert mcp.repair_profile_home(home) is None
    assert (home / "config.yaml").read_bytes() == before


@pytest.mark.parametrize("silent", [False, True])
def test_failed_profile_repair_warns_once_without_stopping_memory(
    external_provider, monkeypatch, capsys, silent
):
    home, provider, module, _ = external_provider("mcp-repair-failure")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    config = {}
    mcp.configure(config, str(home.parent / "old"))
    (home / "config.yaml").write_text(json.dumps(config))
    before = (home / "config.yaml").read_bytes()
    from hermes_cli import config as host_config

    def refuse(*_args, **_kwargs):
        if not silent:
            raise PermissionError("private diagnostic")

    monkeypatch.setattr(host_config, "save_config", refuse)
    monkeypatch.setattr(module, "_classify_runtime_openviking_health", lambda *_: ("healthy", ""))
    for i in range(2):
        provider.initialize(str(i), hermes_home=str(home), platform="cli", agent_context="cron")
        assert provider._client is not None
    assert (home / "config.yaml").read_bytes() == before
    output = capsys.readouterr()
    assert output.err.count("could not repair") == 1
    assert "before using" in output.err and "hermes memory setup openviking" in output.err
    assert "private diagnostic" not in output.err


@pytest.mark.parametrize("previous", [
    r"C:\Users\source\.hermes\profiles\work",
    r"\\old-host\share\profiles\work",
    "/old-machine/.hermes/profiles/work",
])
def test_profile_repair_recognizes_foreign_absolute_paths(external_provider, previous):
    from hermes_cli.config import read_user_config_raw

    home, _, module, _ = external_provider("mcp-cross-platform-import")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    config = {}
    mcp.configure(config, str(home))
    config["mcp_servers"]["openviking"]["env"]["HERMES_HOME"] = previous
    (home / "config.yaml").write_text(json.dumps(config))
    assert mcp.repair_profile_home(home) == previous
    assert read_user_config_raw(home / "config.yaml")["mcp_servers"]["openviking"]["env"]["HERMES_HOME"] == str(home.resolve())
