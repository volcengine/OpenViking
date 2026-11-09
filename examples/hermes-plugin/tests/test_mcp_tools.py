"""Exercise profile routing and the actual MCP wire protocol over loopback HTTP."""

import argparse
import asyncio
import copy
import importlib
import json
import os
import sys
import threading
import time
from builtins import ExceptionGroup
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@contextmanager
def mcp_server(
    *,
    trusted=False,
    status_code=200,
    call_delay=0,
    health_status=200,
    health_payload=None,
    health_auth=False,
    mcp_status=200,
    call_statuses=(),
    tool_schema=None,
    tool_result=None,
):
    requests = []
    call_statuses = list(call_statuses)
    entered, release = threading.Event(), threading.Event()
    release.set()
    schema = (
        tool_schema
        if tool_schema is not None
        else {
            "name": "forget",
            "description": "Server-owned description",
            "inputSchema": {
                "type": "object",
                "properties": {"uri": {"type": "string"}},
                "required": ["uri"],
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        }
    )

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
                code = health_status() if callable(health_status) else health_status
                if health_payload is not None:
                    entered.set()
                if health_auth and "Authorization" not in self.headers:
                    return self.respond({"error": {"message": "Authentication required"}}, 401)
                if code == 302:
                    self.send_response(302)
                    self.send_header("Location", "https://www.example.com/canonical-location")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                return self.respond(
                    health_payload
                    if health_payload is not None
                    else {"status": "ok", "healthy": True, "version": "0.4.23"},
                    code,
                )
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
            if mcp_status != 200:
                return self.respond({"error": "synthetic private diagnostic"}, mcp_status)
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
                if call_statuses and (status := call_statuses.pop(0)) != 200:
                    return self.respond({"error": "synthetic failed operation"}, status)
                result = (
                    tool_result
                    if tool_result is not None
                    else {
                        "content": [{"type": "text", "text": "server result"}],
                        "structuredContent": {"uri": body["params"]["arguments"]["uri"]},
                        "isError": False,
                    }
                )
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
    assert Path(new["command"]).is_absolute()
    assert new["args"][-3:] == ["openviking", "mcp", "--profile-bound"]
    assert "HERMES_HOME" not in new["env"]
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
    home, _, module, _ = external_provider("mcp-unset-env")
    monkeypatch.setenv("HERMES_HOME", str(home))
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    for key in (*mcp._ENV_KEYS, *mcp._TRANSPORT_ENV_KEYS):
        monkeypatch.setenv(key, "${" + key + "}")

    async def serve(_home, **_kwargs):
        assert all(key not in mcp.os.environ for key in (*mcp._ENV_KEYS, *mcp._TRANSPORT_ENV_KEYS))

    monkeypatch.setattr(mcp, "serve", serve)
    mcp.run(argparse.Namespace(profile_bound=True))


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


@pytest.mark.parametrize("concurrent", [False, True])
def test_connection_reuses_session_and_closes_background_tasks(external_provider, concurrent):
    from mcp import types

    home, _, module, _ = external_provider("mcp-reuse")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(call_delay=0.05) as (endpoint, requests, *_):
        configure_home(home, endpoint)
        connection = adapter.Connection(home, module)

        async def exercise():
            original_tasks = asyncio.all_tasks()

            async def call(index):
                uri = f"viking://user/alice/memories/{index}.md"
                result = await connection.request(
                    "tools/call", types.CallToolRequestParams(name="forget", arguments={"uri": uri})
                )
                assert result.structured_content == {"uri": uri}

            if concurrent:
                await asyncio.gather(*(call(index) for index in range(3)))
            else:
                await connection.request("tools/list", None)
                for index in range(3):
                    await call(index)
            methods = [body["method"] for _, _, body in requests if body]
            assert methods.count("initialize") == 1
            assert methods.count("tools/call") == 3
            assert sum(path == "/health" for path, _, _ in requests) == 1
            await connection.aclose()
            assert asyncio.all_tasks() <= original_tasks

        asyncio.run(exercise())


def test_connection_replaces_credentials_without_interrupting_inflight_call(external_provider):
    from mcp import types

    home, _, module, _ = external_provider("mcp-rotate")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(call_delay=0.1) as (endpoint, requests, *_):
        configure_home(home, endpoint, linked=True)
        connection = adapter.Connection(home, module)

        async def exercise():
            await connection.request("tools/list", None)
            first = asyncio.create_task(
                connection.request(
                    "tools/call",
                    types.CallToolRequestParams(name="forget", arguments={"uri": "first"}),
                )
            )
            async with asyncio.timeout(5):
                while not any(
                    body and body.get("method") == "tools/call" for _, _, body in requests
                ):
                    await asyncio.sleep(0.01)
            configure_home(home, endpoint, label="bob", linked=True)
            second = await connection.request(
                "tools/call",
                types.CallToolRequestParams(name="forget", arguments={"uri": "second"}),
            )
            assert (await first).structured_content == {"uri": "first"}
            assert second.structured_content == {"uri": "second"}
            await connection.request("tools/list", None)
            methods = [body["method"] for _, _, body in requests if body]
            assert methods.count("initialize") == 2
            calls = [
                (headers, body)
                for _, headers, body in requests
                if body and body.get("method") == "tools/call"
            ]
            assert [headers["Authorization"] for headers, _ in calls] == [
                "Bearer key-alice",
                "Bearer key-bob",
            ]
            await connection.aclose()

        asyncio.run(exercise())


def test_connection_recovers_after_failed_call_without_replaying_it(external_provider):
    from mcp import types
    from mcp.shared.exceptions import MCPError

    home, _, module, _ = external_provider("mcp-reconnect")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(call_statuses=(500, 200)) as (endpoint, requests, *_):
        configure_home(home, endpoint)
        connection = adapter.Connection(home, module)

        async def exercise():
            await connection.request("tools/list", None)
            params = types.CallToolRequestParams(name="forget", arguments={"uri": "first"})
            with pytest.raises(MCPError):
                await connection.request("tools/call", params)
            calls = [body for _, _, body in requests if body and body.get("method") == "tools/call"]
            assert len(calls) == 1
            params = types.CallToolRequestParams(name="forget", arguments={"uri": "second"})
            assert (await connection.request("tools/call", params)).structured_content == {
                "uri": "second"
            }
            methods = [body["method"] for _, _, body in requests if body]
            assert methods.count("initialize") == 2
            assert methods.count("tools/call") == 2
            await connection.aclose()

        asyncio.run(exercise())


@pytest.mark.parametrize("health_status", [401, 403, 200])
def test_reused_managed_connection_checks_identity_without_sending_credentials(
    external_provider, monkeypatch, health_status
):
    from mcp import types

    home, _, module, _ = external_provider("mcp-managed-reuse")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    health = {"status": "ok", "healthy": True, "version": "0.4.23"}
    health_code = [200]
    with mcp_server(health_payload=health, health_status=lambda: health_code[0]) as (
        endpoint,
        requests,
        *_,
    ):
        configure_home(home, endpoint)
        connection = adapter.Connection(home, module)
        resolve = connection.resolve
        monkeypatch.setattr(connection, "resolve", lambda: (*resolve()[:2], True))

        async def exercise():
            await connection.request("tools/list", None)
            before = len(requests)
            if health_status == 200:
                health["healthy"] = False
            else:
                # Make the same listener challenge an otherwise valid identity.
                health_code[0] = health_status
            params = types.CallToolRequestParams(name="forget", arguments={"uri": "private"})
            with pytest.raises(adapter._UnverifiedServerError):
                await connection.request("tools/call", params)
            added = requests[before:]
            assert len(added) == 1 and added[0][0] == "/health"
            assert not {"Authorization", "X-API-Key", "X-Custom"} & added[0][1].keys()
            await connection.aclose()

        asyncio.run(exercise())


@pytest.mark.parametrize(
    "retry_setting", [3, 5, None], ids=["released-host", "host-override", "missing"]
)
def test_stdio_adapter_reuses_upstream_session(external_provider, retry_setting):
    import hermes_cli
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client

    home, _, module, _ = external_provider("mcp-stdio-reuse")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    env.update(
        HERMES_HOME=str(home),
        HOME=str(home),
        USERPROFILE=str(home),
        HERMES_ENABLE_PROJECT_PLUGINS="0",
        PYTHONPATH=str(Path(hermes_cli.__file__).resolve().parents[1]),
    )
    receipt = home / "discovery-budget.json"
    code = f"""
import argparse
import importlib.util
import json
from pathlib import Path
from tools import mcp_tool

retry_setting = {retry_setting!r}
if retry_setting is None:
    del mcp_tool._MAX_INITIAL_CONNECT_RETRIES
else:
    mcp_tool._MAX_INITIAL_CONNECT_RETRIES = retry_setting
spec = importlib.util.spec_from_file_location("adapter_test", {adapter.__file__!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original_discover = module.Connection.discover

async def discover(self, params, **kwargs):
    result = await original_discover(self, params, **kwargs)
    Path({str(receipt)!r}).write_text(json.dumps(kwargs), encoding="utf-8")
    return result

module.Connection.discover = discover
module.run(argparse.Namespace(profile_bound=True))
"""
    with mcp_server() as (endpoint, requests, *_):
        configure_home(home, endpoint)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", code],
            env=env,
        )

        async def exercise():
            async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
                await client.initialize()
                assert (await client.list_tools()).tools[0].name == "forget"
                for uri in ("first", "second"):
                    result = await client.send_request(
                        types.CallToolRequest(
                            params=types.CallToolRequestParams(
                                name="forget",
                                arguments={"uri": uri},
                            )
                        ),
                        types.CallToolResult,
                    )
                    assert not result.is_error
                    assert result.structured_content == {"uri": uri}

        asyncio.run(exercise())
        methods = [body["method"] for _, _, body in requests if body]
        assert methods.count("initialize") == 1
        assert methods.count("tools/call") == 2
        expected_retries = 3 if retry_setting is None else retry_setting
        assert json.loads(receipt.read_text(encoding="utf-8"))["attempts"] == 1 + expected_retries


@pytest.mark.parametrize("is_error", [False, True], ids=["complete-rules", "rule-error"])
def test_rule_search_preserves_server_schema_and_complete_result(external_provider, is_error):
    from mcp import types

    home, _, module, _ = external_provider("mcp-rules")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    schema = {
        "name": "rule_search",
        "description": "Check applies_when and exception before applying each Rule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_context": {"type": "string", "minLength": 1, "maxLength": 64000},
                "rule_probability_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.65,
                },
            },
            "required": ["task_context"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    }
    rendered = "judge unavailable" if is_error else "完整规则\n" * 17000 + "LAST RULE"
    response = {"content": [{"type": "text", "text": rendered}], "isError": is_error}
    with mcp_server(tool_schema=schema, tool_result=response) as (endpoint, requests, *_):
        configure_home(home, endpoint)
        connection = adapter.Connection(home, module)

        async def exercise():
            try:
                tools = await connection.request("tools/list", None)
                assert tools.model_dump(by_alias=True, exclude_none=True)["tools"] == [schema]
                for arguments in (
                    {"task_context": "deploy"},
                    {"task_context": "deploy", "rule_probability_threshold": 0},
                ):
                    result = await connection.request(
                        "tools/call",
                        types.CallToolRequestParams(name="rule_search", arguments=arguments),
                    )
                    assert result.is_error is is_error
                    assert result.content[0].text == rendered
            finally:
                await connection.aclose()

        asyncio.run(exercise())
        calls = [
            body["params"] for _, _, body in requests if body and body["method"] == "tools/call"
        ]
        assert calls == [
            {"name": "rule_search", "arguments": {"task_context": "deploy"}},
            {
                "name": "rule_search",
                "arguments": {"task_context": "deploy", "rule_probability_threshold": 0},
            },
        ]
        assert sum(1 for _, _, body in requests if body and body.get("method") == "initialize") == 1


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
            assert all(
                h.get("Authorization") == "Bearer key-bob"
                for path, h, _ in b[1]
                if path != "/health"
            )
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


@pytest.mark.parametrize(
    "health_status, health_payload",
    [(302, None), (200, {}), (200, {"status": "ok", "healthy": False, "version": "0.4.23"})],
)
def test_unverified_endpoint_never_receives_mcp_credentials(
    external_provider, health_status, health_payload
):
    home, _, module, _ = external_provider("mcp-unverified-endpoint")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(health_status=health_status, health_payload=health_payload) as (
        endpoint,
        requests,
        *_,
    ):
        configure_home(home, endpoint)
        with (home / ".env").open("a") as env:
            env.write('OPENVIKING_EXTRA_HEADERS={"CF-Access-Client-Secret":"synthetic-secret"}\n')
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(mcp.Connection(home, module).request("tools/list", None))
        assert "did not identify itself as OpenViking" in mcp._error_hint(caught.value)
        assert len(requests) == 1 and requests[0][0] == "/health"
        assert all(
            k not in requests[0][1]
            for k in [
                "X-API-Key",
                "Authorization",
                "CF-Access-Client-Secret",
                "X-Custom",
                "X-OpenViking-Actor-Peer",
            ]
        )


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
        "mcp__openviking__rule_search",
        "mcp_openviking_rule_search",
        "viking_rule_search",
        "mcp__openviking__read",
        "mcp__openviking__list",
        "mcp__openviking__ls",
        "mcp__openviking__tree",
        "mcp__openviking__grep",
        "mcp__openviking__glob",
        "viking_search",
        "mcp_openviking_search",
        "mcp__openviking__privacy_resolve",
        "mcp__openviking__privacy_tree",
        "mcp__openviking__privacy_upsert",
        "mcp__openviking__download",
        "mcp__openviking__future_tool",
        "mcp_openviking_privacy_resolve",
        "mcp_openviking_download",
        "mcp_openviking_future_tool",
        "mcp__other__privacy_resolve",
        "mcp_other_search",
    ],
)
@pytest.mark.parametrize("named_result", [True, False])
def test_retrieved_content_is_not_captured_again(external_provider, name, named_result):
    _, provider, _, _ = external_provider("mcp-filter")
    messages = [
        {"role": "user", "content": "My new preference"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "lookup",
                    "type": "function",
                    "function": {"name": name, "arguments": '{"reference":"PRIVATE_TOOL_INPUT"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "lookup",
            "content": "OLD RETRIEVED MEMORY",
            **({"name": name} if named_result else {}),
        },
        {"role": "assistant", "content": "Acknowledged"},
    ]
    batch = provider._messages_to_openviking_batch(messages)
    assert "OLD RETRIEVED MEMORY" not in json.dumps(batch)
    assert "PRIVATE_TOOL_INPUT" not in json.dumps(batch)
    assert "My new preference" in json.dumps(batch)


@pytest.mark.parametrize("tool", ["remember", "write", "edit", "forget"])
@pytest.mark.parametrize("prefix", ["mcp__openviking__", "mcp_openviking_"])
def test_reviewed_mcp_mutation_acknowledgements_are_captured(external_provider, tool, prefix):
    _, provider, _, _ = external_provider("mcp-mutation-capture")
    name = prefix + tool
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "mutation", "function": {"name": name, "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "mutation", "content": "Mutation acknowledged"},
    ]
    parts = provider._messages_to_openviking_batch(messages)[0]["parts"]
    assert parts[0]["tool_name"] == name
    assert parts[0]["tool_output"] == "Mutation acknowledged"


@pytest.mark.parametrize("named_result", [True, False])
def test_deferred_mcp_wrapper_does_not_capture_private_result(external_provider, named_result):
    _, provider, _, _ = external_provider("mcp-deferred-capture")
    messages = [
        {"role": "user", "content": "Use the saved credential"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "private",
                    "function": {
                        "name": "tool_call",
                        "arguments": json.dumps(
                            {
                                "calls": [
                                    {
                                        "name": "mcp__openviking__privacy_resolve",
                                        "arguments": {"reference": "PRIVATE_REFERENCE"},
                                    }
                                ]
                            }
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "private",
            "content": "SYNTHETIC_SECRET",
            **({"name": "tool_call"} if named_result else {}),
        },
    ]
    batch = json.dumps(provider._messages_to_openviking_batch(messages))
    assert "SYNTHETIC_SECRET" not in batch
    assert "PRIVATE_REFERENCE" not in batch
    assert "Use the saved credential" in batch


@pytest.mark.parametrize(
    "version,supported", [("0.4.22", False), ("0.4.23", True), ("0.5.0", True)]
)
def test_setup_enforces_mcp_server_minimum(external_provider, version, supported):
    home, _, module, _ = external_provider("mcp-server-version")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(health_payload={"status": "ok", "healthy": True, "version": version}) as (
        endpoint,
        requests,
        *_,
    ):
        configure_home(home, endpoint)
        before = (home / "config.yaml").read_bytes()
        ok, message = adapter.validate_connection(module, {"endpoint": endpoint})
        assert ok is supported
        assert (home / "config.yaml").read_bytes() == before
        if not supported:
            assert "OpenViking >=0.4.23" in message and "/mcp" in message
            assert [path for path, _, _ in requests] == ["/health"]


@pytest.mark.parametrize("status", [404, 405])
def test_missing_mcp_endpoint_reports_required_server(external_provider, status):
    home, _, module, _ = external_provider("mcp-missing-endpoint")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    with mcp_server(mcp_status=status) as (endpoint, *_):
        configure_home(home, endpoint)
        ok, message = adapter.validate_connection(module, {"endpoint": endpoint})
        assert not ok
        assert "OpenViking >=0.4.23" in message and "/mcp" in message
        assert "synthetic private diagnostic" not in message


@pytest.mark.parametrize(
    "name", ["mcp__openviking__remember", "mcp__openviking__write", "ordinary_tool"]
)
def test_write_results_and_other_servers_are_not_misclassified(external_provider, name):
    _, _, module, _ = external_provider("mcp-other")
    assert not module._skip_tool_capture(name)


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
        extra = {
            "CF-Access-Client-Id": "synthetic-id",
            "CF-Access-Client-Secret": "synthetic-secret",
        }
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
        assert all(
            all(headers.get(k) == v for k, v in extra.items())
            for path, headers, _ in requests
            if path != "/health"
        )
        assert all(k not in requests[0][1] for k in extra)


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("unverified", [False, True])
def test_discovery_waits_for_managed_port_recovery_only(
    external_provider, monkeypatch, managed, unverified
):
    home, _, module, _ = external_provider("mcp-discovery-recovery")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    with (
        mcp_server(status_code=401, health_payload={} if unverified else None) as foreign,
        mcp_server() as owned,
    ):
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
            with pytest.raises(
                mcp._UnverifiedServerError if unverified else module._OpenVikingHTTPError
            ):
                asyncio.run(recover())
        assert not any(body for _, _, body in foreign[1])
        if unverified:
            assert all("Authorization" not in h and "X-API-Key" not in h for _, h, _ in foreign[1])


@pytest.mark.parametrize("health_status", [401, 403])
def test_managed_auth_challenge_waits_without_sending_credentials(external_provider, health_status):
    home, _, module, _ = external_provider("mcp-managed-auth-recovery")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    foreign_health = {
        "status": "ok",
        "healthy": True,
        "version": "0.4.23",
        "auth_mode": "trusted",
        "root_api_key_required": True,
    }
    with (
        mcp_server(health_status=health_status, health_payload=foreign_health) as foreign,
        mcp_server() as owned,
    ):
        paths = module.quick_local.managed_paths(home)
        paths.root.mkdir()
        key = "synthetic-managed-root-key"
        (home / "config.yaml").write_text(
            json.dumps(
                {
                    "memory": {
                        "provider": "openviking",
                        "openviking": {"deployment": module.quick_local.DEPLOYMENT},
                    },
                    "mcp_servers": {
                        "openviking": {
                            "connect_timeout": 5,
                            "headers": {"X-Gateway-Secret": "gateway-key"},
                        }
                    },
                }
            )
        )
        paths.server_config.write_text(
            json.dumps(
                {"storage": {"workspace": str(paths.workspace)}, "server": {"root_api_key": key}}
            )
        )
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
def test_managed_provider_checks_ownership_before_auth_probe(
    external_provider, monkeypatch, health_status
):
    home, provider, module, _ = external_provider("mcp-managed-provider-auth")
    foreign_health = {
        "status": "ok",
        "healthy": True,
        "version": "0.4.23",
        "auth_mode": "trusted",
        "root_api_key_required": True,
    }
    with mcp_server(health_status=health_status, health_payload=foreign_health) as (
        endpoint,
        requests,
        *_,
    ):
        paths = module.quick_local.managed_paths(home)
        paths.root.mkdir()
        key = "synthetic-managed-root-key"
        (home / "config.yaml").write_text(
            json.dumps(
                {
                    "memory": {
                        "provider": "openviking",
                        "openviking": {
                            "deployment": module.quick_local.DEPLOYMENT,
                        },
                    }
                }
            )
        )
        paths.server_config.write_text(
            json.dumps(
                {"storage": {"workspace": str(paths.workspace)}, "server": {"root_api_key": key}}
            )
        )
        paths.ovcli_config.write_text(json.dumps({"url": endpoint, "root_api_key": key}))
        recovery = []
        monkeypatch.setattr(
            provider,
            "_handle_runtime_openviking_unreachable",
            lambda **_kwargs: recovery.append(True),
        )
        provider.initialize(
            "auth-port-recovery", hermes_home=str(home), platform="cli", agent_context="cron"
        )
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
    import httpx2

    home, _, module, _ = external_provider("mcp-unreachable-discovery")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    configure_home(home, "http://127.0.0.1:1")
    connection = mcp.Connection(home, module)
    values, entry, _ = connection.resolve()
    entry["connect_timeout"] = 1
    monkeypatch.setattr(connection, "resolve", lambda: (values, entry, False))
    failure = httpx2.ConnectError("Connection refused")
    calls = []

    async def refuse(method, params):
        calls.append((method, params))
        raise failure

    # Windows can take longer than this budget to reject a real socket.
    # Supply a known failure so this test checks preservation of its cause.
    monkeypatch.setattr(connection, "request", refuse)
    with pytest.raises(TimeoutError) as caught:
        asyncio.run(connection.discover(None))
    assert calls and all(call == ("tools/list", None) for call in calls)
    assert caught.value.__cause__ is failure
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
    from hermes_cli.config import yaml

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
        "mcp_servers": {
            "openviking": {
                "trust": "full",
                "enabled": False,
                "tools": {"include": ["read"], "exclude": ["forget"]},
                "headers": {"X-Gateway": "${GATEWAY_SECRET}"},
                "env": {"SSL_CERT_FILE": "${PRIVATE_CA}"},
            },
            "other": {"command": "other-server"},
        },
        "model": {"default": "unchanged", "api_key": "${MODEL_SECRET}"},
    }
    mcp.configure(config, str(source))
    expected = copy.deepcopy(config)
    config["mcp_servers"]["openviking"]["command"] = "hermes"
    config["mcp_servers"]["openviking"]["args"] = ["openviking", "mcp"]
    config["mcp_servers"]["openviking"]["env"]["HERMES_HOME"] = str(source)
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
        provider.initialize(
            "relocated", hermes_home=str(home), platform="cli", agent_context="cron", **kwargs
        )
        repaired = read_user_config_raw(home / "config.yaml")
        assert repaired == expected
        before = (home / "config.yaml").read_bytes()
        provider.initialize(
            "relocated-again", hermes_home=str(home), platform="cli", agent_context="cron", **kwargs
        )
        assert (home / "config.yaml").read_bytes() == before
        assert get_hermes_home() == source
    finally:
        reset_hermes_home_override(token)
    output = capsys.readouterr()
    messages = notices if callback else output.err.splitlines()
    assert len(messages) == 1
    assert messages[0].startswith(
        f"OpenViking MCP launch was migrated from {source} to launch-time profile binding. "
    )
    assert "/reload-mcp" in messages[0] and "before using" in messages[0]
    assert output.out == ""
    if callback:
        assert output.err == ""


@pytest.mark.parametrize(
    "entry",
    [
        None,
        {"url": "https://example.test/mcp", "env": {"HERMES_HOME": "/old/profile"}},
        {"command": "other", "args": ["serve"], "env": {"HERMES_HOME": "/old/profile"}},
        {"command": "custom-server", "args": ["openviking", "mcp", "--profile-bound"], "env": {}},
        {"command": "hermes", "args": ["custom", "mcp"], "env": {"HERMES_HOME": "${HERMES_HOME}"}},
    ],
)
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
    mcp.configure(config, str(home))
    config["mcp_servers"]["openviking"]["command"] = "hermes"
    config["mcp_servers"]["openviking"]["args"] = ["openviking", "mcp"]
    config["mcp_servers"]["openviking"]["env"]["HERMES_HOME"] = str(home.parent / "old")
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


@pytest.mark.parametrize(
    "previous",
    [
        r"C:\Users\source\.hermes\profiles\work",
        r"\\old-host\share\profiles\work",
        "/old-machine/.hermes/profiles/work",
    ],
)
def test_profile_repair_recognizes_foreign_absolute_paths(external_provider, previous):
    from hermes_cli.config import read_user_config_raw

    home, _, module, _ = external_provider("mcp-cross-platform-import")
    mcp = importlib.import_module(module.__name__ + ".mcp_tools")
    config = {}
    mcp.configure(config, str(home))
    config["mcp_servers"]["openviking"]["env"]["HERMES_HOME"] = previous
    (home / "config.yaml").write_text(json.dumps(config))
    assert mcp.repair_profile_home(home) == previous
    assert (
        "HERMES_HOME"
        not in read_user_config_raw(home / "config.yaml")["mcp_servers"]["openviking"]["env"]
    )
