"""The standalone MCP bridge (core/mcp_bridge.py) with a fake session factory.

The module is imported as ``<plugin package>.core.mcp_bridge`` from the plugin
package that Hermes discovery loads, the same way the provider will reach it.
"""

import asyncio
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[1]
_LOADED = {}
# Read at import: the external_provider fixture clears OPENVIKING_* variables.
_LIVE_URL = os.environ.get("OPENVIKING_LIVE_MCP_URL", "")
_LIVE_KEY = os.environ.get("OPENVIKING_LIVE_MCP_API_KEY", "")


@pytest.fixture
def bridge(external_provider):
    module = _LOADED.get("module")
    if module is None:
        _, _, plugin, _ = external_provider("mcp-bridge")
        package = (
            plugin.__name__ if hasattr(plugin, "__path__") else plugin.__name__.rpartition(".")[0]
        )
        module = importlib.import_module(f"{package}.core.mcp_bridge")
        _LOADED["module"] = module
    return module


class FakeServer:
    """Scripted MCP server behind the bridge's session-factory seam."""

    def __init__(self):
        self.sessions = []
        self.calls = []
        self.on_initialize = None
        self.on_call = None
        self.pages = {
            None: (
                [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}],
                None,
            )
        }

    def factory(self, url, headers, on_http_status, timeout):
        server = self

        class Session:
            async def initialize(self):
                if server.on_initialize is not None:
                    await _maybe_await(server.on_initialize(on_http_status))

            async def list_tools(self, cursor):
                return server.pages[cursor]

            async def call_tool(self, name, arguments):
                server.calls.append((name, arguments))
                if server.on_call is not None:
                    return await _maybe_await(server.on_call(name, arguments, on_http_status))
                return {"content": [{"type": "text", "text": json.dumps(arguments)}]}

        @asynccontextmanager
        async def open_session():
            self.sessions.append({"url": url, "headers": dict(headers), "timeout": timeout})
            yield Session()

        return open_session()


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


class SpawnRecorder:
    """A spawn_context_thread stand-in that remembers every thread it made."""

    def __init__(self):
        self.threads = []

    def __call__(self, target, *, name, daemon=True, args=(), kwargs=None):
        thread = threading.Thread(target=target, name=name, daemon=daemon, args=args, kwargs=kwargs)
        self.threads.append(thread)
        return thread

    def assert_all_finished(self, timeout=2.0):
        for thread in self.threads:
            thread.join(timeout)
            assert not thread.is_alive(), thread.name


def _connection(bridge, server, **overrides):
    spawn = overrides.pop("spawn", SpawnRecorder())
    options = {
        "url": "http://ov.test/mcp",
        "headers": lambda: {"Authorization": "Bearer user-key"},
        "spawn": spawn,
        "session_factory": server.factory,
    }
    options.update(overrides)
    return bridge.McpConnection(**options), spawn


def _mcp_error(message="Server returned an error response", code=-32603):
    exceptions = pytest.importorskip("mcp.shared.exceptions")
    return exceptions.MCPError(code, message)


# ----------------------------------------------------------------------- conversion


def test_text_blocks_join_and_structured_echo_is_dropped(bridge):
    result = bridge.to_tool_result(
        "read",
        {
            "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}],
            "structuredContent": {"result": "hello\nworld"},
        },
    )
    assert result == bridge.ToolResult(text="hello\nworld")

    serialized_echo = {
        "content": [{"type": "text", "text": '{"count":3}'}],
        "structuredContent": {"count": 3},
    }
    assert bridge.to_tool_result("find", serialized_echo).text == '{"count":3}'


def test_non_text_blocks_become_readable_lines(bridge):
    text = bridge.to_tool_result(
        "read",
        {
            "content": [
                {"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"},
                {"type": "audio", "data": "AAAA", "mimeType": "audio/wav"},
                {
                    "type": "resource",
                    "resource": {
                        "uri": "viking://r",
                        "mimeType": "text/plain",
                        "text": "inline body",
                    },
                },
                {"type": "resource_link", "uri": "viking://linked", "name": "attachment"},
                {"type": "video"},
                "not a block",
            ],
            "structuredContent": {"count": 3},
        },
    ).text
    assert text.splitlines() == [
        "[image content omitted (image/png)]",
        "[audio content omitted (audio/wav)]",
        "[resource viking://r (text/plain)]",
        "inline body",
        "[resource link viking://linked — attachment]",
        "[unsupported MCP content block: video]",
        '{"count":3}',
    ]
    assert "aGVsbG8=" not in text


def test_structured_echo_is_compared_with_upstream_text_only(bridge):
    # The image note must not make the FastMCP echo look like new information.
    result = bridge.to_tool_result(
        "read",
        {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "image", "data": "x", "mimeType": "image/png"},
            ],
            "structuredContent": {"result": "hello"},
        },
    )
    assert result.text == "hello\n[image content omitted (image/png)]"


def test_empty_and_error_results(bridge):
    assert bridge.to_tool_result("health", {"content": []}) == bridge.ToolResult(text="")
    assert bridge.to_tool_result(
        "list", {"isError": True, "content": [{"type": "text", "text": "Directory not found"}]}
    ) == (bridge.ToolResult(text="Directory not found", is_error=True))
    assert (
        bridge.to_tool_result("write", {"isError": True, "content": []}).text
        == "OpenViking write failed"
    )


def test_sdk_models_convert_through_camel_case_aliases(bridge):
    types = pytest.importorskip("mcp.types")
    result = types.CallToolResult(
        content=[
            types.TextContent(text="ok"),
            types.ImageContent(data="eA==", mime_type="image/jpeg"),
        ],
        structured_content={"result": "ok"},
        is_error=True,
    )
    assert bridge.to_tool_result("read", result) == bridge.ToolResult(
        text="ok\n[image content omitted (image/jpeg)]", is_error=True
    )


@pytest.mark.parametrize(
    "text",
    ["字" * 12000, "line\n" * 1100, " " * 30000],
    ids=["multibyte", "many-lines", "wide"],
)
def test_truncation_bounds_all_blocks_together(bridge, text):
    result = bridge.to_tool_result(
        "read",
        {
            "content": [
                {"type": "text", "text": text},
                {"type": "image", "data": "x", "mimeType": "image/png"},
                {"type": "text", "text": text},
            ]
        },
    )
    assert result.truncated is True
    assert len(result.text.encode("utf-8")) <= bridge.MAX_RESULT_BYTES
    assert len(result.text.split("\n")) <= bridge.MAX_RESULT_LINES
    assert "�" not in result.text
    assert result.text.endswith(bridge.TRUNCATION_HINT)


def test_large_errors_are_bounded(bridge):
    result = bridge.to_tool_result(
        "read", {"isError": True, "content": [{"type": "text", "text": "error" * 20000}]}
    )
    assert result.is_error and result.truncated
    assert len(result.text.encode("utf-8")) <= bridge.MAX_RESULT_BYTES
    assert "Output truncated" in result.text


def test_output_at_the_limits_is_not_truncated(bridge):
    exact = "a" * bridge.MAX_RESULT_BYTES
    assert bridge.bound_text(exact) == (exact, False)
    lines = "\n".join(["x"] * bridge.MAX_RESULT_LINES)
    assert bridge.bound_text(lines) == (lines, False)


# ----------------------------------------------------------------------- calls


def test_call_uses_one_session_per_call_with_caller_headers(bridge):
    server = FakeServer()
    header_threads = []

    def headers():
        header_threads.append(threading.current_thread())
        return {"Authorization": "Bearer user-key", "X-OpenViking-Actor-Peer": "peer-1"}

    conn, spawn = _connection(bridge, server, headers=headers)
    first = bridge.call_tool(conn, "search", {"query": "a"})
    second = bridge.call_tool(conn, "remember", {"content": "b"})

    assert first == bridge.ToolResult(text='{"query": "a"}')
    assert second.text == '{"content": "b"}'
    assert server.calls == [("search", {"query": "a"}), ("remember", {"content": "b"})]
    assert len(server.sessions) == 2
    assert all(s["url"] == "http://ov.test/mcp" for s in server.sessions)
    assert server.sessions[0]["headers"]["X-OpenViking-Actor-Peer"] == "peer-1"
    assert header_threads == [threading.current_thread()] * 2
    assert len(spawn.threads) == 2
    assert all(t.name.startswith("openviking-mcp-") and t.daemon for t in spawn.threads)
    spawn.assert_all_finished()


def test_session_timeout_budget_includes_connection(bridge):
    server = FakeServer()
    conn, _ = _connection(bridge, server, call_timeout=4.0)
    bridge.call_tool(conn, "health", {})
    bridge.call_tool(conn, "health", {}, timeout=2.0)
    assert 3.5 < server.sessions[0]["timeout"] <= 4.0
    assert 1.5 < server.sessions[1]["timeout"] <= 2.0


def test_header_builder_failure_is_an_error_result(bridge):
    server = FakeServer()

    def headers():
        raise RuntimeError("no key configured")

    conn, spawn = _connection(bridge, server, headers=headers)
    result = bridge.call_tool(conn, "search", {"query": "a"})
    assert result.is_error and "no key configured" in result.text
    assert server.sessions == [] and spawn.threads == []


def test_deadline_expiry_returns_an_uncertain_write_and_the_thread_exits(bridge):
    server = FakeServer()

    async def hang(name, arguments, on_http_status):
        await asyncio.sleep(30)

    server.on_call = hang
    conn, spawn = _connection(bridge, server)
    started = time.monotonic()
    result = bridge.call_tool(conn, "write", {"uri": "viking://resources/a.md"}, timeout=0.2)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert result.is_error and result.uncertain
    assert "outcome unknown" in result.text and "timed out after 0.2s" in result.text
    assert len(server.calls) == 1
    spawn.assert_all_finished()


def test_deadline_expiry_for_read_only_tool_is_a_plain_failure(bridge):
    server = FakeServer()

    async def hang(name, arguments, on_http_status):
        await asyncio.sleep(30)

    server.on_call = hang
    conn, spawn = _connection(bridge, server)
    result = bridge.call_tool(conn, "search", {"query": "a"}, timeout=0.2)
    assert result.is_error and not result.uncertain
    assert result.text == "OpenViking search failed: timed out after 0.2s"
    assert len(server.calls) == 1  # the deadline is spent, so there is no retry
    spawn.assert_all_finished()


def test_worker_that_ignores_cancellation_is_abandoned_not_joined(bridge):
    server = FakeServer()
    release = threading.Event()

    def block(name, arguments, on_http_status):
        release.wait(10)  # blocks the worker's event loop, so cancellation cannot land
        return {"content": [{"type": "text", "text": "late"}]}

    server.on_call = block
    conn, spawn = _connection(bridge, server)
    started = time.monotonic()
    try:
        result = bridge.call_tool(conn, "edit", {"uri": "viking://resources/a.md"}, timeout=0.2)
        elapsed = time.monotonic() - started
        assert elapsed < 1.0
        assert result.is_error and result.uncertain
        assert spawn.threads[0].is_alive()
    finally:
        release.set()
    spawn.assert_all_finished()


def test_handshake_budget_is_capped_inside_a_call(bridge):
    server = FakeServer()

    async def slow_initialize(on_http_status):
        await asyncio.sleep(30)

    server.on_initialize = slow_initialize
    conn, spawn = _connection(bridge, server, handshake_timeout=0.1)
    started = time.monotonic()
    result = bridge.call_tool(conn, "remember", {"content": "x"}, timeout=5.0)
    assert time.monotonic() - started < 1.0
    assert result.is_error and not result.uncertain  # the call was never sent
    assert "initialize timed out" in result.text
    assert server.calls == [] and len(server.sessions) == 1
    spawn.assert_all_finished()


def test_write_tools_are_never_replayed_after_a_transport_failure(bridge):
    server = FakeServer()

    def reset(name, arguments, on_http_status):
        raise ConnectionResetError("connection reset by peer")

    server.on_call = reset
    conn, spawn = _connection(bridge, server)
    for tool in (
        "remember",
        "write",
        "edit",
        "forget",
        "add_resource",
        "cancel_watch",
        "unknown_tool",
    ):
        server.calls.clear()
        result = bridge.call_tool(conn, tool, {"x": 1})
        assert server.calls == [(tool, {"x": 1})], tool
        assert result.is_error and result.uncertain, tool
        assert f"OpenViking {tool} outcome unknown: ConnectionResetError" in result.text
    spawn.assert_all_finished()


def test_write_that_failed_before_sending_is_not_uncertain(bridge):
    server = FakeServer()

    def refuse(on_http_status):
        raise ConnectionRefusedError("refused")

    server.on_initialize = refuse
    conn, _ = _connection(bridge, server)
    result = bridge.call_tool(conn, "remember", {"content": "x"})
    assert result.is_error and not result.uncertain
    assert len(server.sessions) == 1 and server.calls == []


def test_read_only_tools_retry_once_after_a_transport_failure(bridge):
    server = FakeServer()
    failures = []

    def flaky(name, arguments, on_http_status):
        if not failures:
            failures.append(name)
            raise ConnectionResetError("connection reset by peer")
        return {"content": [{"type": "text", "text": "found"}]}

    server.on_call = flaky
    conn, spawn = _connection(bridge, server)
    result = bridge.call_tool(conn, "find", {"query": "a"})
    assert result == bridge.ToolResult(text="found")
    assert len(server.sessions) == 2 and len(server.calls) == 2
    assert len(spawn.threads) == 1  # the retry reuses the worker thread and its deadline
    spawn.assert_all_finished()


def test_read_only_tools_retry_at_most_once(bridge):
    server = FakeServer()

    def down(name, arguments, on_http_status):
        raise ConnectionResetError("connection reset by peer")

    server.on_call = down
    conn, _ = _connection(bridge, server)
    result = bridge.call_tool(conn, "read", {"uris": ["viking://a"]})
    assert result.is_error and not result.uncertain
    assert len(server.calls) == 2


def test_read_only_retry_is_skipped_without_budget(bridge):
    server = FakeServer()
    now = [100.0]

    def expire(name, arguments, on_http_status):
        now[0] += 14.9  # leaves less than the minimum retry budget of a 15 s call
        raise ConnectionResetError("connection reset by peer")

    server.on_call = expire
    conn, _ = _connection(bridge, server, clock=lambda: now[0])
    result = bridge.call_tool(conn, "search", {"query": "a"})
    assert result.is_error
    assert len(server.calls) == 1


@pytest.mark.parametrize("failure", ["protocol", "http-400"])
def test_read_only_tools_do_not_retry_rejections(bridge, failure):
    server = FakeServer()

    def reject(name, arguments, on_http_status):
        if failure == "http-400":
            on_http_status(400)
        raise _mcp_error("bad argument", code=-32602)

    server.on_call = reject
    conn, _ = _connection(bridge, server)
    result = bridge.call_tool(conn, "grep", {"pattern": "("})
    assert result.is_error and not result.uncertain
    assert "bad argument" in result.text
    assert len(server.calls) == 1
    assert result.http_status == (400 if failure == "http-400" else None)


def test_server_errors_retry_read_only_and_leave_writes_uncertain(bridge):
    server = FakeServer()

    def unavailable(name, arguments, on_http_status):
        on_http_status(503)
        raise _mcp_error()

    server.on_call = unavailable
    conn, _ = _connection(bridge, server)
    read = bridge.call_tool(conn, "tree", {"uri": "viking://"})
    assert read.is_error and read.http_status == 503 and len(server.calls) == 2
    server.calls.clear()
    write = bridge.call_tool(conn, "write", {"uri": "viking://resources/a.md"})
    assert write.is_error and write.uncertain and write.http_status == 503
    assert "HTTP 503" in write.text and len(server.calls) == 1


def test_forbidden_adds_the_root_key_hint(bridge):
    server = FakeServer()

    def forbidden(on_http_status):
        on_http_status(403)
        raise _mcp_error()

    server.on_initialize = forbidden
    conn, _ = _connection(bridge, server)
    result = bridge.call_tool(conn, "search", {"query": "a"})
    assert result.is_error and result.http_status == 403 and not result.uncertain
    assert "HTTP 403" in result.text
    assert result.text.endswith(bridge.ROOT_KEY_HINT)
    assert len(server.sessions) == 1  # a rejection is not a transport failure

    with pytest.raises(bridge.McpBridgeError) as raised:
        bridge.list_tools(conn)
    assert raised.value.http_status == 403
    assert "HTTP 403" in str(raised.value) and str(raised.value).endswith(bridge.ROOT_KEY_HINT)


def test_unauthorized_has_status_but_no_root_key_hint(bridge):
    server = FakeServer()

    def unauthorized(on_http_status):
        on_http_status(401)
        raise _mcp_error("Invalid API Key")

    server.on_initialize = unauthorized
    conn, _ = _connection(bridge, server)
    result = bridge.call_tool(conn, "health", {})
    assert result.text == "OpenViking health failed: HTTP 401: Invalid API Key"
    assert result.http_status == 401


def test_result_that_arrived_before_close_failed_is_kept(bridge):
    server = FakeServer()
    original = server.factory

    def factory(url, headers, on_http_status, timeout):
        @asynccontextmanager
        async def broken_close():
            async with original(url, headers, on_http_status, timeout) as session:
                yield session
            raise ConnectionResetError("reset while closing")

        return broken_close()

    conn, _ = _connection(bridge, server, session_factory=factory)
    result = bridge.call_tool(conn, "remember", {"content": "x"})
    assert result == bridge.ToolResult(text='{"content": "x"}')
    assert len(server.calls) == 1


def test_no_threads_remain_after_sequential_and_concurrent_calls(bridge):
    server = FakeServer()
    conn, spawn = _connection(bridge, server)
    before = {t.ident for t in threading.enumerate()}
    for i in range(10):
        assert bridge.call_tool(conn, "health", {"i": i}).text == json.dumps({"i": i})
    results = []
    callers = [
        threading.Thread(
            target=lambda i=i: results.append(bridge.call_tool(conn, "search", {"i": i}))
        )
        for i in range(10)
    ]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(5)
    bridge.list_tools(conn)

    assert sorted(json.loads(r.text)["i"] for r in results) == list(range(10))
    assert len(spawn.threads) == 21
    spawn.assert_all_finished()
    leftover = [
        t
        for t in threading.enumerate()
        if t.ident not in before and t.name.startswith("openviking-mcp")
    ]
    assert leftover == []


# ----------------------------------------------------------------------- tools/list


def test_list_tools_normalizes_and_follows_pages(bridge):
    server = FakeServer()
    server.pages = {
        None: (
            [
                {
                    "name": "find",
                    "description": "Find",
                    "inputSchema": {"type": "object", "properties": {"q": {}}},
                    "annotations": {"readOnlyHint": True},
                },
                {"description": "nameless"},
            ],
            "p2",
        ),
        "p2": ([{"name": "health"}], None),
    }
    conn, spawn = _connection(bridge, server)
    assert bridge.list_tools(conn) == [
        {
            "name": "find",
            "description": "Find",
            "inputSchema": {"type": "object", "properties": {"q": {}}},
        },
        {"name": "health", "description": "", "inputSchema": {"type": "object", "properties": {}}},
    ]
    assert len(server.sessions) == 1
    assert server.sessions[0]["timeout"] == bridge.HANDSHAKE_TIMEOUT_SECONDS
    spawn.assert_all_finished()


def test_list_tools_deadline_covers_the_handshake(bridge):
    server = FakeServer()

    async def slow_initialize(on_http_status):
        await asyncio.sleep(30)

    server.on_initialize = slow_initialize
    conn, spawn = _connection(bridge, server, handshake_timeout=0.2)
    started = time.monotonic()
    with pytest.raises(bridge.McpBridgeError, match=r"timed out after 0.2s during initialize"):
        bridge.list_tools(conn)
    assert time.monotonic() - started < 1.0
    spawn.assert_all_finished()


def test_list_tools_transport_failure(bridge):
    server = FakeServer()

    def refuse(on_http_status):
        raise ConnectionRefusedError("refused")

    server.on_initialize = refuse
    conn, _ = _connection(bridge, server)
    with pytest.raises(bridge.McpBridgeError, match="ConnectionRefusedError: refused") as raised:
        bridge.list_tools(conn)
    assert raised.value.http_status is None


# ----------------------------------------------------------------------- import hygiene


def test_cancellation_noise_filter_only_drops_bridge_stream_errors(bridge):
    import logging

    closed = type("ClosedResourceError", (Exception,), {})()
    other = ValueError("real problem")

    def record(thread_name, exc):
        rec = logging.LogRecord(
            "mcp.client.streamable_http",
            logging.ERROR,
            __file__,
            1,
            "Error parsing SSE message",
            (),
            (type(exc), exc, None),
        )
        rec.threadName = thread_name
        return rec

    noise = bridge._CancelledStreamFilter()
    assert noise.filter(record("openviking-mcp-search-1", closed)) is False
    assert noise.filter(record("openviking-mcp-search-1", other)) is True
    assert noise.filter(record("hermes-mcp-client", closed)) is True


def test_reimporting_the_module_has_no_side_effects(bridge):
    import atexit

    name = bridge.__name__
    parent = sys.modules[name.rpartition(".")[0]]
    threads_before = threading.active_count()
    callbacks_before = atexit._ncallbacks()
    sys.modules.pop(name)
    modules_before = set(sys.modules)
    try:
        fresh = importlib.import_module(name)
        assert fresh is not bridge
        assert threading.active_count() == threads_before
        assert atexit._ncallbacks() == callbacks_before
        added = set(sys.modules) - modules_before
        assert added == {name}, added
    finally:
        sys.modules[name] = bridge
        parent.mcp_bridge = bridge


def test_import_in_a_clean_interpreter_loads_no_sdk_and_no_threads():
    # Loaded by file path here so the interpreter holds nothing but this module.
    script = f"""
import atexit, importlib.util, socket, sys, threading
def refuse(*args, **kwargs):
    raise AssertionError("network touched during import")
socket.socket.connect = refuse
socket.create_connection = refuse
# Stdlib modules may register atexit hooks of their own (logging does); load them first.
import asyncio, contextlib, dataclasses, functools, itertools, json, logging, time, typing
callbacks = atexit._ncallbacks()
spec = importlib.util.spec_from_file_location("ov_core_mcp_bridge", {str(_PLUGIN_DIR / "core" / "mcp_bridge.py")!r})
module = sys.modules[spec.name] = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
leaked = sorted(m for m in sys.modules if m.split(".")[0] in ("mcp", "mcp_types", "httpx", "httpx2", "httpcore2", "anyio"))
assert not leaked, leaked
assert threading.active_count() == 1, threading.enumerate()
assert atexit._ncallbacks() == callbacks
print("clean")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "clean"


# ----------------------------------------------------------------------- live


@pytest.mark.skipif(not _LIVE_URL, reason="set OPENVIKING_LIVE_MCP_URL to run")
def test_live_server_lists_tools(bridge):
    """Opt-in: OPENVIKING_LIVE_MCP_URL=http://127.0.0.1:1933/mcp, OPENVIKING_LIVE_MCP_API_KEY=<user key>."""
    headers = {"Authorization": f"Bearer {_LIVE_KEY}"} if _LIVE_KEY else {}
    conn = bridge.McpConnection(url=_LIVE_URL, headers=lambda: headers)
    # The first call also pays for importing the SDK, so allow more than the 3 s default.
    tools = bridge.list_tools(conn, timeout=10.0)
    names = {tool["name"] for tool in tools}
    assert {"search", "read", "health"} <= names
    assert all(isinstance(tool["inputSchema"], dict) for tool in tools)
    health = bridge.call_tool(conn, "health", {})
    assert not health.is_error, health.text
