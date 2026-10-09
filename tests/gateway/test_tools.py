import asyncio
import base64
import copy
from types import SimpleNamespace

import orjson
import pytest
from conftest import MCP_TOOLS, mcp_tool

from context_gateway.capture import capture_messages
from context_gateway.models import Policy, Upstream
from context_gateway.notices import TOOL_NOTICE, tool_tail
from context_gateway.proxy import upstream_url
from context_gateway.storage import SQLiteKernelStore
from context_gateway.tool_catalog import notice_head, select_tools, tool_block_reason
from context_gateway.tool_executor import ToolExecutor, attachment_bytes, attachments
from context_gateway.tool_loop import HiddenToolLoop
from context_gateway.tool_protocols import ResponseCapture, hidden_chain, tool_protocol
from context_gateway.tool_protocols.common import sse
from context_gateway.vendors import ark_url


@pytest.mark.parametrize(
    "vendor,replay,extra,offered",
    [
        ("generic", None, {}, True),
        # DeepSeek needs every reply's reasoning once tools are present; the gateway restores it.
        ("deepseek", None, {}, True),
        ("deepseek", None, {"thinking": {"type": "enabled"}}, True),
        ("deepseek", False, {}, False),
        ("deepseek", False, {"thinking": {"type": "enabled"}}, False),
        ("deepseek", False, {"thinking": {"type": "disabled"}}, True),
        ("ark", None, {"tools": [{"type": "custom", "name": "x"}]}, False),
    ],
)
def test_vendor_tool_capabilities(vendor, replay, extra, offered):
    upstream = Upstream(
        name="x", base_url="http://model", protocol="chat", vendor=vendor, replay_reasoning=replay
    ).model_dump()
    reason = tool_block_reason(extra, "chat", upstream)
    assert (reason == "") == offered
    if vendor == "deepseek" and not offered:
        assert reason == "deepseek_reasoning_history_required"


def test_default_policy_offers_read_only_and_future_tools():
    policy = Policy().model_dump()
    assert policy["gateway_tools"]
    catalog = [*MCP_TOOLS, mcp_tool("future", {})]
    names = {
        tool["function"]["name"].removeprefix("openviking_")
        for tool in select_tools(catalog, policy)
    }
    assert names == {"find", "search", "read", "list", "grep", "glob", "health", "future"}


def test_tools_default_to_all_and_only_raw_disabled_names_are_excluded():
    policy = Policy(gateway_tools=True, disabled_tools=["write", "openviking_read"]).model_dump()
    selected = select_tools(MCP_TOOLS, policy)
    names = {tool["function"]["name"] for tool in selected}
    assert names == {"openviking_" + t["name"] for t in MCP_TOOLS if t["name"] != "write"}
    # Imports are offered without a shell or attachment; a local path fails at call time.
    assert {"openviking_add_resource", "openviking_add_skill", "openviking_read"} <= names
    catalog = [mcp_tool("future", {"new_argument": {"type": "string"}})]
    future = select_tools(catalog, policy)[0]["function"]
    assert future == {
        "name": "openviking_future",
        "description": catalog[0]["description"],
        "parameters": catalog[0]["inputSchema"],
    }
    for tool in selected:
        if tool["function"]["name"] in {"openviking_add_resource", "openviking_add_skill"}:
            assert (
                tool["function"]["parameters"]["properties"]["attachment_index"]["type"]
                == "integer"
            )
            assert "anyOf" not in str(tool)
    assert "attachment_index" not in str(MCP_TOOLS)


def test_file_bytes_and_extracted_text():
    name, data = attachment_bytes(
        {
            "filename": "../../notes.txt",
            "file_data": "data:text/plain;base64," + base64.b64encode(b"hello").decode(),
        },
        100,
    )
    assert name == "notes.txt" and data == b"hello"
    with pytest.raises(ValueError):
        attachment_bytes({"file_data": "a" * 1000}, 10)
    with pytest.raises(ValueError):
        attachment_bytes({"file_id": "remote-file"}, 100)
    parts = attachments(
        {
            "messages": [
                {
                    "role": "system",
                    "content": '<context><source id="1" name="report.pdf">hello &amp; world</source></context>',
                }
            ]
        },
        "chat",
    )
    assert parts == [{"filename": "report.pdf.txt", "text": "hello & world"}]


async def test_tool_claim_timeout_and_byte_bound(setup_kernel, credential, policy):
    kernel, store, viking, encryption = setup_kernel
    policy.update(gateway_tools=True, tool_result_bytes=1024)
    prepared = await kernel.prepare(
        {"messages": [{"role": "user", "content": "Save this"}]},
        "chat",
        {"x-openviking-session": "tools"},
        credential,
        {"id": "upstream"},
        policy,
    )
    executed = []

    async def mcp(name, key, args):
        executed.append((name, key, args))
        await asyncio.sleep(0.03)
        return {"content": [{"type": "text", "text": '"' * 10000}]}

    viking.mcp = mcp
    call = {
        "id": "write-1",
        "function": {
            "name": "openviking_write",
            "arguments": '{"uri":"viking://~/notes/a","content":"note"}',
        },
    }
    another = SQLiteKernelStore(store.path, encryption)
    executors = [
        ToolExecutor(viking, s, prepared, credential, "http://gateway", 1024)
        for s in [store, another]
    ]
    results = await asyncio.gather(*(e.execute(call) for e in executors))
    assert len(executed) == 1
    assert results[0] == results[1]
    assert len(results[0]["content"].encode()) <= 1024
    assert results[0]["content"].endswith("[Tool result truncated.]")
    # A timed out write is remembered, including across a new store instance.
    policy["tool_timeout_seconds"] = 0.001
    prepared.root["policy"]["tool_timeout_seconds"] = 0.001
    call["id"] = "timeout-write"
    result = await executors[0].execute(call)
    assert "timed out" in result["content"]
    again = await executors[1].execute(call)
    assert again == result and len(executed) == 2


async def test_hidden_branch_restart_and_archive_mapping(setup_kernel, credential, policy):
    policy.update(gateway_tools=True)
    kernel, store, _, encryption = setup_kernel
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "blue"},
    ]
    history = [
        {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {
                    "id": "g",
                    "type": "function",
                    "function": {"name": "openviking_search", "arguments": "{}"},
                }
            ],
            "reasoning_content": "opaque",
        },
        {"role": "tool", "tool_call_id": "g", "content": "blue"},
        {"role": "assistant", "content": "blue"},
    ]
    p = await kernel.prepare({"messages": messages}, "chat", {}, credential, {"id": "u"}, policy)
    for kind, value in (("hidden", {"messages": history, "visible_count": 1}), ("reply", {})):
        await store.replay.put(p.scope, p.session, kind, hidden_chain(messages, "chat")[-1], value)
    kernel.store = SQLiteKernelStore(store.path, encryption)
    p = await kernel.prepare(
        {"messages": [*messages, {"role": "user", "content": "follow up"}]},
        "chat",
        {"x-openviking-session": "tools"},
        credential,
        {"id": "u"},
        policy,
    )
    assert p.body["messages"][2:5] == history
    edited = copy.deepcopy(messages)
    edited[-1]["content"] = "red"
    p = await kernel.prepare({"messages": edited}, "chat", {}, credential, {"id": "u"}, policy)
    assert len(p.body["messages"]) == 3
    # Replacing an archived prefix must not use expanded transcript indices.
    raw = [
        *messages,
        {"role": "user", "content": "next"},
        {"role": "assistant", "content": "later"},
    ]
    p = await kernel.prepare({"messages": raw}, "chat", {}, credential, {"id": "u"}, policy)
    p.body["messages"] = copy.deepcopy(raw)
    p.body_chain = hidden_chain(raw, "chat")
    from context_gateway.compaction import apply_cut
    from context_gateway.tool_protocols import replay_hidden

    p.records["replacement", p.chain[2]] = {"source": "compaction", "text": "summary"}
    apply_cut(p)
    p.body["messages"] = replay_hidden(p.body["messages"], p.body_chain, p.records, "chat")
    assert p.body["messages"] == [raw[0], {"role": "user", "content": "summary"}, *raw[3:]]


async def test_stream_text_precedes_tool_completion_and_cancel_closes():
    waiting = asyncio.Event()
    closed = []

    class Content:
        async def iter_any(self):
            yield sse({"choices": [{"delta": {"content": "live text"}, "finish_reason": None}]})
            await waiting.wait()
            pytest.fail("The client cancelled before the tool round completed")

    response = SimpleNamespace(
        status=200,
        headers={"content-type": "text/event-stream"},
        content=Content(),
        close=lambda: closed.append(True),
    )
    prepared = SimpleNamespace(
        body={"messages": [], "stream": True},
        protocol="chat",
        root={"policy": {}},
        metrics={},
        tools_active=True,
        reply_lead="",
    )
    loop = HiddenToolLoop(
        prepared, SimpleNamespace(allowed={"openviking_search"}), None, ResponseCapture("chat")
    )
    stream = loop.run(response, None)
    event = await asyncio.wait_for(stream.__anext__(), 0.5)
    assert b"live text" in event and not waiting.is_set()
    await stream.aclose()
    assert closed and not loop.capture.complete


def gateway_call(name, arguments, identifier="g-1"):
    if not isinstance(arguments, str):
        arguments = orjson.dumps(arguments).decode()
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": "openviking_" + name, "arguments": arguments},
    }


@pytest.mark.parametrize(
    "name,arguments,expected",
    [
        ("search", {"query": " release\n  date "}, '> OpenViking search: "release date"'),
        ("search", {"query": "q" * 200}, '> OpenViking search: "' + "q" * 79 + '…"'),
        ("read", {"uris": ["viking://a"]}, "> OpenViking read: viking://a"),
        (
            "read",
            {"uris": ["viking://a", "viking://b", 3]},
            "> OpenViking read: viking://a (+1 more)",
        ),
        ("list", {"uri": "viking://resources"}, "> OpenViking list: viking://resources"),
        ("list", {}, "> OpenViking list"),
        ("write", {"uri": "viking://n.md", "content": "x"}, "> OpenViking write: viking://n.md"),
        ("add_resource", {"path": "https://x/a.pdf"}, "> OpenViking add_resource: https://x/a.pdf"),
        ("add_resource", {"attachment_index": 0}, "> OpenViking add_resource: attachment 0"),
        ("add_resource", {"attachment_index": True}, "> OpenViking add_resource"),
        ("add_skill", {"path": "./s", "target_uri": "viking://t"}, "> OpenViking add_skill: ./s"),
        ("add_skill", {"target_uri": "viking://t"}, "> OpenViking add_skill: viking://t"),
        ("add_skill", {"attachment_index": 1}, "> OpenViking add_skill: attachment 1"),
        ("add_skill", {"data": "---\nname: s"}, "> OpenViking add_skill: SKILL.md text"),
        ("add_skill", {}, "> OpenViking add_skill"),
        ("search", "not json", "> OpenViking search"),
        ("search", "[1]", "> OpenViking search"),
        ("search", {"query": "  "}, "> OpenViking search"),
        ("find", {"query": "blue"}, '> OpenViking find: "blue"'),
        ("grep", {"uri": "viking://a", "pattern": "x"}, "> OpenViking grep"),
    ],
)
def test_notice_names_each_call_target(name, arguments, expected):
    head = notice_head(gateway_call(name, arguments))
    assert head == expected
    # Capture must recognize every rendered line, whatever its outcome.
    for outcome in ("done", "failed", "skipped"):
        assert TOOL_NOTICE.fullmatch(head + " — " + outcome)


@pytest.mark.parametrize(
    "failed,skipped,expected",
    [
        (False, False, " — done"),
        (True, False, " — failed"),
        (False, True, " — skipped"),
        (True, True, " — skipped"),
    ],
)
def test_notice_outcome(failed, skipped, expected):
    assert tool_tail(failed, skipped) == expected


async def test_notices_stream_around_each_gateway_call():
    contents = iter([("ok", False), ("x" * 4000, True)])
    deltas, seen = [], []

    async def execute(call):
        # The call's head has already streamed when the call starts.
        seen.append(deltas[-1])
        content, failed = next(contents)
        return {"role": "tool", "tool_call_id": call["id"], "content": content, "failed": failed}

    prepared = SimpleNamespace(
        body={"messages": [], "stream": True},
        protocol="chat",
        root={"policy": {"tool_total_tokens": 1000}},
        metrics={},
        tools_active=True,
        tools_closed=False,
        reply_lead="",
    )
    executor = SimpleNamespace(allowed={"openviking_search"}, execute=execute)
    loop = HiddenToolLoop(prepared, executor, None, ResponseCapture("chat"))
    loop.adapter.begin()
    calls = [
        gateway_call("search", {"query": "blue"}, "g-1"),
        gateway_call("add_resource", {"attachment_index": 0}, "g-2"),
        gateway_call("read", {"uris": ["viking://a", "viking://b"]}, "g-3"),
    ]
    async for chunk in loop.execute(calls):
        deltas.append(orjson.loads(chunk.removeprefix(b"data: "))["choices"][0]["delta"]["content"])
    assert deltas == [
        '\n\n> OpenViking search: "blue"',
        " — done",
        "\n\n> OpenViking add_resource: attachment 0",
        " — failed",
        "\n\n> OpenViking read: viking://a (+1 more)",
        " — skipped",
        "\n\n",
    ]
    assert seen == [deltas[0], deltas[2]]
    assert loop.adapter.visible[0]["content"] == "".join(deltas)
    # The model receives the real results, never the notices.
    assert "> OpenViking" not in orjson.dumps(loop.body["messages"]).decode()


async def test_notices_off_leave_the_reply_unchanged():
    async def execute(call):
        return {"role": "tool", "tool_call_id": call["id"], "content": "{}"}

    prepared = SimpleNamespace(
        body={"messages": [], "stream": True},
        protocol="chat",
        root={"policy": {"show_tool_calls": False}},
        metrics={},
        tools_active=True,
        tools_closed=False,
        reply_lead="",
    )
    executor = SimpleNamespace(allowed={"openviking_search"}, execute=execute)
    loop = HiddenToolLoop(prepared, executor, None, ResponseCapture("chat"))
    loop.adapter.begin()
    events = [e async for e in loop.execute([gateway_call("search", {"query": "blue"})])]
    assert events == [] and loop.adapter.visible == [{"role": "assistant", "content": ""}]


def test_capture_drops_tool_notices_from_assistant_text():
    notice = "\n\n> OpenViking add_resource: https://x/a.pdf — done\n\n"
    two = '\n\n> OpenViking search: "a — b" — done\n\n> OpenViking read: viking://a — failed\n\n'
    messages = [
        {"role": "user", "content": '> OpenViking search: "quoted by the user" — done'},
        {"role": "assistant", "content": "Let me call it." + notice + "Added."},
        {"role": "user", "content": "Again"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Looking."},
                {"type": "text", "text": two},
                {"type": "text", "text": "Found it."},
            ],
        },
        {"role": "user", "content": "Import it"},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": notice}],
        },
    ]
    captured = capture_messages(messages, [str(i) for i in range(len(messages))])
    texts = [m["parts"][0]["text"] for m in captured]
    assert texts == [
        messages[0]["content"],
        "Let me call it.\n\nAdded.",
        "Again",
        "Looking.\n\n\nFound it.",
        "Import it",
    ]


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
def test_omitted_hidden_history_drops_tool_notices(protocol):
    notice = '\n\n> OpenViking search: "blue" — done\n\n'
    messages = [
        {"role": "user", "content": notice},
        {"role": "assistant", "content": "Looking." + notice + "Blue."},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Looking."},
                {"type": "text", "text": notice},
                {"type": "tool_use", "id": "c-1", "name": "shell", "input": {}},
            ],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": notice}],
        },
        {"type": "function_call", "call_id": "c-1", "name": "shell", "arguments": "{}"},
    ]
    cleaned = tool_protocol(protocol).omit_hidden_history(messages)
    # Only assistant text changes; a part or item left empty is dropped.
    assert cleaned == [
        messages[0],
        {"role": "assistant", "content": "Looking.\n\nBlue."},
        {"role": "assistant", "content": [messages[2]["content"][0], messages[2]["content"][2]]},
        messages[4],
    ]


@pytest.mark.parametrize(
    "base,path,expected",
    [
        ("https://ark/api/v3", "/v1/chat/completions", "https://ark/api/v3/chat/completions"),
        ("https://ark", "/v1/responses/resp_1", "https://ark/api/v3/responses/resp_1"),
        ("https://ark/api/compatible/v1", "/v1/messages", "https://ark/api/compatible/v1/messages"),
    ],
)
def test_ark_base_paths(base, path, expected):
    assert ark_url({"base_url": base}, path) == expected


@pytest.mark.parametrize("vendor", ["ark", "byteplus"])
@pytest.mark.parametrize(
    "base,path,expected",
    [
        ("https://ark", "/v1/chat/completions", "https://ark/api/v3/chat/completions"),
        ("https://ark", "/v1/responses", "https://ark/api/v3/responses"),
        ("https://ark", "/v1/messages", "https://ark/api/compatible/v1/messages"),
        (
            "https://ark",
            "/v1/messages/count_tokens",
            "https://ark/api/compatible/v1/messages/count_tokens",
        ),
        ("https://ark", "/v1/models", "https://ark/api/v3/models"),
        ("https://ark/api/v3", "/v1/responses", "https://ark/api/v3/responses"),
        ("https://ark/api/compatible/v1", "/v1/messages", "https://ark/api/compatible/v1/messages"),
    ],
)
def test_ark_vendors_share_upstream_paths(vendor, base, path, expected):
    assert upstream_url({"base_url": base, "vendor": vendor}, path) == expected


@pytest.mark.parametrize("json_response", [False, True])
async def test_real_stateless_fastmcp_transport(json_response):
    import socket

    import aiohttp
    import uvicorn
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    from context_gateway.client import VikingClient

    mcp = FastMCP(
        "gateway-test",
        stateless_http=True,
        json_response=json_response,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    async def find(query: str) -> str:
        return "found: " + query

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(mcp.streamable_http_app(), access_log=False, log_level="error")
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        async with aiohttp.ClientSession(auto_decompress=False) as http:
            adapter = VikingClient(http, f"http://127.0.0.1:{port}", "0.4.16")
            catalog = await adapter.tools("test-key")
            assert [t["name"] for t in catalog] == ["find"]
            result = await adapter.mcp(
                "tools/call", "test-key", {"name": "find", "arguments": {"query": "blue"}}
            )
            assert result["content"][0]["text"] == "found: blue"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()


async def test_explicit_session_keeps_initial_tool_snapshot(setup_kernel, credential, policy):
    kernel, _, _, _ = setup_kernel
    policy.update(gateway_tools=True, disabled_tools=["list"])
    body = {"messages": [{"role": "user", "content": "Find blue"}]}
    first = await kernel.prepare(
        body, "chat", {"x-openviking-session": "tools"}, credential, {"id": "upstream"}, policy
    )
    policy = {**policy, "disabled_tools": ["find"]}
    body["messages"].extend(
        [{"role": "assistant", "content": "blue"}, {"role": "user", "content": "Follow up"}]
    )
    second = await kernel.prepare(
        body, "chat", {"x-openviking-session": "tools"}, credential, {"id": "upstream"}, policy
    )
    assert first.session == second.session
    assert first.body["tools"] == second.body["tools"]


async def test_incompatible_tools_keep_visible_history(setup_kernel, credential, policy):
    kernel, store, _, _ = setup_kernel
    policy.update(gateway_tools=True, recall=False)
    messages = [{"role": "user", "content": "search"}, {"role": "assistant", "content": "answer"}]
    headers = {"x-openviking-session": "downgrade"}
    first = await kernel.prepare(
        {"messages": messages}, "chat", headers, credential, {"id": "u"}, policy
    )
    transcript = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "owned",
                    "type": "function",
                    "function": {"name": "openviking_search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "owned", "content": "result"},
        messages[1],
    ]
    await store.replay.put(
        first.scope,
        first.session,
        "hidden",
        hidden_chain(messages, "chat")[-1],
        {"messages": transcript, "visible_count": 1},
    )
    body = {
        "messages": [*messages, {"role": "user", "content": "continue"}],
        "response_format": {"type": "json_object"},
    }
    prepared = await kernel.prepare(body, "chat", headers, credential, {"id": "u"}, policy)
    assert not prepared.tools_active
    # Only the opening note joins the new user turn; the visible history is untouched.
    assert prepared.body["messages"][:-1] == messages
    assert prepared.body["messages"][-1]["content"].startswith(
        'continue\n\n<openviking-context source="gateway-session-start">'
    )
    assert prepared.body["response_format"] == body["response_format"]
    assert all(not message.get("tool_calls") for message in prepared.body["messages"])


async def test_catalog_cache_single_flight_refresh_and_last_success():
    from context_gateway.client import VikingClient, VikingError

    calls, fail = [], False
    catalog = [mcp_tool("future", {})]

    async def mcp(method, key, params=None, timeout=120):
        calls.append((method, key, timeout))
        await asyncio.sleep(0)
        if fail:
            raise VikingError("openviking_unavailable")
        return {"tools": copy.deepcopy(catalog)}

    client = VikingClient(None, "http://one", "0.4.16")
    client.mcp = mcp
    results = await asyncio.gather(*(client.tools(str(i)) for i in range(8)))
    assert all(result == catalog for result in results)
    assert calls == [("tools/list", "0", 5)]
    # The list does not depend on the caller, so every key shares the cached copy.
    catalog.append(mcp_tool("new", {}))
    assert len(await client.tools("another-account")) == 1
    client.tools_cache.clear()
    assert len(await client.tools("new-key")) == 2
    # A failed refresh keeps serving the last good list.
    fail = True
    client.tools_cache.clear()
    assert len(await client.tools("offline")) == 2
    # Without one, failures and malformed lists both surface.
    fresh = VikingClient(None, "http://one", "0.4.16")
    fresh.mcp = mcp
    with pytest.raises(VikingError):
        await fresh.tools("offline")
    fail = False
    catalog[:] = [{"name": "broken"}]
    with pytest.raises(VikingError):
        await fresh.tools("malformed")


async def test_catalog_unavailable_freezes_safe_empty_root(setup_kernel, credential, policy):
    from context_gateway.client import VikingError

    kernel, _, viking, _ = setup_kernel
    policy.update(gateway_tools=True)
    viking.tools_failure = VikingError("private details")
    headers = {"x-openviking-session": "offline"}
    body = {"messages": [{"role": "user", "content": "Look up my deployment"}]}
    prepared = await kernel.prepare(body, "chat", headers, credential, {"id": "u"}, policy)
    assert not prepared.tools_active and prepared.root["tools"] == []
    assert prepared.metrics["tool_skip_reason"] == "tools_unavailable"
    assert prepared.metrics["tools_tokens"] == 0
    assert "private details" not in str(prepared.metrics)
    # OpenViking recovering reaches new sessions only; this one keeps its empty list.
    viking.tools_failure = None
    body["messages"] += [
        {"role": "assistant", "content": "I cannot search right now."},
        {"role": "user", "content": "Try again"},
    ]
    later = await kernel.prepare(body, "chat", headers, credential, {"id": "u"}, policy)
    assert later.root == prepared.root and not later.tools_active
    assert later.metrics["tool_skip_reason"] == "tools_unavailable"
    assert len(viking.tool_keys) == 1


async def test_tool_list_loads_only_for_sessions_that_can_use_tools(
    setup_kernel, credential, policy
):
    kernel, _, viking, _ = setup_kernel
    body = {"messages": [{"role": "user", "content": "Hello there"}]}
    await kernel.prepare(
        body, "chat", {"x-openviking-session": "off"}, credential, {"id": "u"}, policy
    )
    policy.update(gateway_tools=True)
    structured = {**body, "response_format": {"type": "json_object"}}
    prepared = await kernel.prepare(
        structured, "chat", {"x-openviking-session": "blocked"}, credential, {"id": "u"}, policy
    )
    assert prepared.metrics["tool_skip_reason"] == "tools_structured_output"
    assert viking.tool_keys == []


@pytest.mark.parametrize("arguments", ["{broken", "[]", "null", "{}", '{"value":"x","extra":1}'])
async def test_generic_executor_checks_frozen_keys_and_required(
    setup_kernel, credential, policy, arguments
):
    from unittest.mock import AsyncMock

    kernel, store, viking, _ = setup_kernel
    policy.update(gateway_tools=True)
    viking.catalog = [mcp_tool("future", {"value": {"type": "string"}}, ("value",))]
    prepared = await kernel.prepare(
        {"messages": [{"role": "user", "content": "Run new tool"}]},
        "chat",
        {},
        credential,
        {"id": "u"},
        policy,
    )
    viking.mcp = AsyncMock()
    executor = ToolExecutor(viking, store, prepared, credential, "", 1024)
    result = await executor.execute(gateway_call("future", arguments))
    assert result["failed"] and "Invalid tool arguments" in result["content"]
    viking.mcp.assert_not_called()


async def test_new_tools_preserve_arguments_and_normalize_failed_receipt(
    setup_kernel, credential, policy
):
    from unittest.mock import AsyncMock

    kernel, store, viking, _ = setup_kernel
    policy.update(gateway_tools=True)
    viking.catalog = [
        mcp_tool("future", {"attachment_index": {"type": "string"}}, ("attachment_index",))
    ]
    prepared = await kernel.prepare(
        {"messages": [{"role": "user", "content": "Run new tool"}]},
        "chat",
        {},
        credential,
        {"id": "u"},
        policy,
    )
    assert prepared.metrics["tools_tokens"] > 0
    # Type validation belongs to MCP; attachment_index is only a hook for add_*.
    args = {"attachment_index": 7}
    viking.mcp = AsyncMock(
        return_value={
            "content": [
                {"type": "text", "text": "first"},
                {"type": "image", "data": "secret-base64-image"},
                {"type": "audio", "data": "secret-base64-audio"},
                {"type": "text", "text": "last"},
            ],
            "structuredContent": {"duplicated": "secret structured content"},
            "isError": True,
        }
    )
    executor = ToolExecutor(viking, store, prepared, credential, "", 1024)
    call = gateway_call("future", args)
    result = await executor.execute(call)
    assert result["content"] == (
        "first\n[OpenViking returned image content; omitted.]\n"
        "[OpenViking returned audio content; omitted.]\nlast"
    )
    assert result["failed"] is True
    viking.mcp.assert_awaited_once_with(
        "tools/call", credential["openviking_key"], {"name": "future", "arguments": args}
    )
    assert await ToolExecutor(viking, store, prepared, credential, "", 1024).execute(call) == result
    assert viking.mcp.await_count == 1
    assert tool_tail(result["failed"], False) == " — failed"
    assert "failed" not in tool_protocol("chat")({}).results([result])[0]
    anthropic = tool_protocol("anthropic")({})
    assert anthropic.results([result])[0]["content"][0]["is_error"] is True
    assert "is_error" not in anthropic.results([{**result, "failed": False}])[0]["content"][0]
    assert tool_protocol("responses")({}).results([result])[0]["output"] == result["content"]


async def test_saved_root_keeps_its_tools(setup_kernel, credential, policy):
    from unittest.mock import AsyncMock

    from context_gateway.storage import digest

    kernel, store, viking, _ = setup_kernel
    body = {"messages": [{"role": "user", "content": "Find my deployment"}]}
    initial = await kernel.prepare(body, "chat", {}, credential, {"id": "u"}, policy)
    # A saved session keeps the tool list it froze, whatever the catalog offers now.
    search = {
        "type": "function",
        "function": {
            "name": "openviking_search",
            "description": "Search the user's OpenViking memories and resources.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
        },
    }
    saved = {key: value for key, value in initial.root.items() if key != "tool_skip_reason"}
    saved.update(tools=[search], policy={**policy, "gateway_tools": True})
    await store.replay.put(initial.scope, digest("saved"), "root", "", saved)
    prepared = await kernel.prepare(
        body, "chat", {"x-openviking-session": "saved"}, credential, {"id": "u"}, policy
    )
    assert prepared.tools_active
    assert [tool["function"]["name"] for tool in prepared.body["tools"]] == ["openviking_search"]
    assert viking.tool_keys == []
    viking.mcp = AsyncMock(return_value={"content": [{"type": "text", "text": "found"}]})
    result = await ToolExecutor(viking, store, prepared, credential, "", 1024).execute(
        gateway_call("search", {"query": "deploy", "limit": 3})
    )
    assert result["content"] == "found" and not result["failed"]
    assert viking.mcp.call_args.args[2] == {
        "name": "search",
        "arguments": {"query": "deploy", "limit": 3},
    }


def test_policy_accepts_storage_metadata_only():
    stored = {**Policy(gateway_tools=True).model_dump(), "id": "default", "revision": 4}
    assert Policy.model_validate(stored) == Policy(gateway_tools=True)
    with pytest.raises(ValueError):
        Policy.model_validate({"unknown_setting": True})


@pytest.mark.parametrize(
    "value",
    [[], {"content": None}, {"content": [None]}, {"content": [{"type": "text", "text": 7}]}],
)
async def test_malformed_mcp_result_is_a_failed_tool(setup_kernel, credential, policy, value):
    from unittest.mock import AsyncMock

    kernel, store, viking, _ = setup_kernel
    policy.update(gateway_tools=True)
    prepared = await kernel.prepare(
        {"messages": [{"role": "user", "content": "Search"}]},
        "chat",
        {},
        credential,
        {"id": "u"},
        policy,
    )
    viking.mcp = AsyncMock(return_value=value)
    result = await ToolExecutor(viking, store, prepared, credential, "", 1024).execute(
        gateway_call("find", {"query": "x"})
    )
    assert result["failed"]
    assert result["content"] == "Invalid tool arguments or OpenViking operation failed"
