"""Experimental agent-managed context windows: native tools, resets and signals."""

import json
import re
import time
from types import SimpleNamespace

import orjson
import pytest
from aiohttp import web
from conftest import MCP_TOOLS
from test_app import completion, enable_tools
from test_compaction import Summarizer, answered
from test_native_tools import native_response, request_body, visible_response, wire_response

from context_gateway.blocks import block, gateway_note, history_hint, token_estimate
from context_gateway.capture import CapturePipeline
from context_gateway.capture_store import Document
from context_gateway.compaction import active_cut
from context_gateway.models import Policy
from context_gateway.protocols import SSEDecoder, text_content
from context_gateway.records import RecordKind as K
from context_gateway.tool_catalog import select_tools
from context_gateway.tool_protocols import ResponseCapture, hidden_chain, tool_protocol
from context_gateway.tool_protocols.common import sse
from context_gateway.windows import (
    ALONE,
    NATIVE_TOOLS,
    NEW_CONTEXT,
    REMINDERS,
    SEARCHABLE,
    STAY,
    amount,
    context_remaining,
    due,
    new_context,
    window_number,
)

PATHS = {"chat": "/v1/chat/completions", "anthropic": "/v1/messages", "responses": "/v1/responses"}
FIELDS = {"chat": "messages", "anthropic": "messages", "responses": "input"}
NOTES = {"reason": "Phase one is done.", "notes": "Goal: ship X. Done: A.", "next_steps": "Test."}
NOTICE = "> OpenViking new_context — done"
OPENING = re.compile(
    r'<openviking-context source="gateway-session-start">[\s\S]*?</openviking-context>'
)


def model_reply(protocol, number, calls=()):
    """One upstream reply; each (name, arguments) in calls is a gateway-run call."""
    if protocol == "chat":
        message = {"role": "assistant", "content": f"answer-{number}"}
        if calls:
            message["tool_calls"] = [
                {
                    "id": f"gateway-{number}-{index}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
                for index, (name, arguments) in enumerate(calls)
            ]
        return completion(message, "tool_calls" if calls else "stop")
    value = native_response(protocol, number, owned=bool(calls))
    items = value["content"] if protocol == "anthropic" else value["output"]
    template = items.pop() if calls else None
    for index, (name, arguments) in enumerate(calls):
        if protocol == "anthropic":
            items.append({**template, "id": f"gateway-{number}-{index}", "name": name})
            items[-1]["input"] = arguments
        else:
            items.append(
                {
                    **template,
                    "id": f"fc_{number}_{index}",
                    "call_id": f"gateway-{number}-{index}",
                    "name": name,
                    "arguments": json.dumps(arguments),
                }
            )
    return value


async def reply(request, protocol, value, streaming):
    if protocol != "chat":
        return await wire_response(request, protocol, value, streaming)
    if not streaming:
        return web.json_response(value)
    message, choice = value["choices"][0]["message"], value["choices"][0]
    events = [
        {
            "id": "chat-1",
            "choices": [
                {
                    "index": 0,
                    "delta": {k: v for k, v in message.items() if k != "tool_calls"},
                    "finish_reason": None,
                }
            ],
        },
        *(
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, **call}]}}]}
            for i, call in enumerate(message.get("tool_calls", []))
        ),
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
            "usage": value["usage"],
        },
    ]
    body = b"".join(sse(event) for event in events) + b"data: [DONE]\n\n"
    return web.Response(body=body, content_type="text/event-stream")


def client_body(protocol, streaming):
    if protocol == "chat":
        body = {
            "model": "model",
            "messages": [{"role": "user", "content": "Find blue"}],
            "stream": streaming,
            "tools": [{"type": "function", "function": {"name": "shell", "parameters": {}}}],
        }
    else:
        body = request_body(protocol, streaming)
    if protocol == "anthropic":
        body["system"] = "Be brief."
    else:
        role = "system" if protocol == "chat" else "developer"
        body[FIELDS[protocol]].insert(0, {"role": role, "content": "Be brief."})
    return body


def visible_messages(protocol, response, streaming):
    """The reply as the client keeps it in its history."""
    if protocol != "chat":
        value = visible_response(protocol, response, streaming)
        if protocol == "responses":
            return value["output"]
        return [{"role": "assistant", "content": value["content"]}]
    if not streaming:
        return [response.json()["choices"][0]["message"]]
    events = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
    text = "".join(
        choice["delta"].get("content") or ""
        for event in events
        if event
        for choice in event.get("choices", [])
    )
    return [{"role": "assistant", "content": text}]


def visible_text(messages):
    return "".join(
        text_content(m) if m.get("role") else "" for m in messages if m.get("type") != "reasoning"
    )


def stored(app, kind):
    store = app.state.store
    with store.connect() as c:
        rows = c.execute("SELECT anchor, value FROM replay WHERE kind=?", (kind,)).fetchall()
    return {anchor: store.decode(value) for anchor, value in rows}


def round_output(protocol, value):
    if protocol == "chat":
        return [value["choices"][0]["message"]]
    if protocol == "anthropic":
        return [{"role": "assistant", "content": value["content"]}]
    return value["output"]


@pytest.fixture
def confirmed_cuts(monkeypatch):
    cuts = []

    async def confirm_cut(self, request, anchor):
        cuts.append(anchor)

    monkeypatch.setattr(CapturePipeline, "confirm_cut", confirm_cut)
    return cuts


async def send(running_gateway, protocol, streaming, rounds, fail=False, body=None):
    """One client request whose hidden rounds make the given gateway calls, then answer."""
    app, client, _, key, _, _ = running_gateway
    body = body or client_body(protocol, streaming)
    run = SimpleNamespace(requests=[], upstream=[], mcp=[], body=body)

    async def backend(request):
        if request.path == "/mcp":
            run.mcp.append(await request.json())
            return web.json_response({"id": 1, "result": {"content": []}})
        if request.path != PATHS[protocol]:
            return None
        run.requests.append(await request.json())
        number = len(run.requests)
        if fail and number > len(rounds):
            return web.json_response({"error": "unavailable"}, status=503)
        value = model_reply(protocol, number, rounds[number - 1] if number <= len(rounds) else ())
        run.upstream.append(value)
        return await reply(request, protocol, value, streaming)

    app.state.test_backend["handler"] = backend
    run.headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "windows"}
    run.response = await client.post(PATHS[protocol], headers=run.headers, json=run.body)
    return run


def test_native_tools_are_frozen_only_with_agent_windows():
    policy = Policy(gateway_tools=True, disabled_tools=["new_context", NEW_CONTEXT]).model_dump()
    assert not any(t["function"]["name"] in NATIVE_TOOLS for t in select_tools(MCP_TOOLS, policy))
    assert len(select_tools(MCP_TOOLS, policy)) == len(MCP_TOOLS)
    policy["agent_windows"] = True
    natives = [t["function"] for t in select_tools(MCP_TOOLS, policy)[len(MCP_TOOLS) :]]
    # disabled_tools lists MCP names only and never removes a native tool.
    assert [t["name"] for t in natives] == list(NATIVE_TOOLS)
    assert natives[0]["description"].endswith(SEARCHABLE)
    assert natives[0]["parameters"]["required"] == ["reason", "notes"]
    assert natives[1]["parameters"] == {"type": "object", "properties": {}}
    # Earlier windows are only searchable when saved and both history tools exist.
    for change in ({"capture": False}, {"disabled_tools": ["grep"]}):
        natives = select_tools(MCP_TOOLS, {**policy, **change})[-2:]
        assert not natives[0]["function"]["description"].endswith(SEARCHABLE)
    note = gateway_note(Policy(), select_tools(MCP_TOOLS, policy))
    assert "You manage your own context windows with openviking_new_context" in note
    assert "context windows" not in gateway_note(Policy(), select_tools(MCP_TOOLS, {}))


def test_policy_requires_soft_ratio_below_hard_ratio():
    assert Policy().window_soft_ratio < Policy().window_hard_ratio
    with pytest.raises(ValueError):
        Policy(window_soft_ratio=0.8, window_hard_ratio=0.8)


@pytest.mark.parametrize(
    "protocol, message, expected",
    [
        (
            "chat",
            {"role": "tool", "tool_call_id": "c", "content": "out"},
            {"role": "tool", "tool_call_id": "c", "content": "out\n\nX"},
        ),
        (
            "chat",
            {"role": "tool", "tool_call_id": "c", "content": [{"type": "text", "text": "out"}]},
            {
                "role": "tool",
                "tool_call_id": "c",
                "content": [{"type": "text", "text": "out"}, {"type": "text", "text": "X"}],
            },
        ),
        (
            "anthropic",
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c"}]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "c"},
                    {"type": "text", "text": "X"},
                ],
            },
        ),
        (
            "responses",
            {"type": "function_call_output", "call_id": "c", "output": "out"},
            {"type": "function_call_output", "call_id": "c", "output": "out\n\nX"},
        ),
        (
            "responses",
            {
                "type": "function_call_output",
                "call_id": "c",
                "output": [{"type": "input_text", "text": "out"}],
            },
            {
                "type": "function_call_output",
                "call_id": "c",
                "output": [
                    {"type": "input_text", "text": "out"},
                    {"type": "input_text", "text": "X"},
                ],
            },
        ),
        (
            "responses",
            {"type": "custom_tool_call_output", "call_id": "c", "output": "out"},
            {"type": "custom_tool_call_output", "call_id": "c", "output": "out\n\nX"},
        ),
    ],
)
def test_context_appends_to_tool_results(protocol, message, expected):
    tool_protocol(protocol).append_context(message, "X")
    assert message == expected


def test_soft_reminder_fires_once_per_window_hard_repeats_and_cuts_count():
    policy = Policy()
    records = {(K.INJECTION, "b"): {"text": "x", "reminder": "soft"}}
    request = SimpleNamespace(
        chain=["a", "b", "c", "d"],
        capture_chain=["a", "b", "c", "d"],
        records=records,
        context_tokens=75,
        context_window=100,
    )
    assert active_cut(request) == -1 and window_number(request) == 1
    assert due(request, policy, -1) == ""
    request.context_tokens = 90
    assert due(request, policy, -1) == "hard"
    records[K.INJECTION, "d"] = {"text": "x", "reminder": "hard"}
    assert due(request, policy, -1) == "hard"
    records[K.REPLACEMENT, "b"] = {"source": "compaction", "text": "", "tokens": 0}
    assert active_cut(request) == 1 and window_number(request) == 1
    records[K.REPLACEMENT, "c"] = {"source": "window", "text": "", "tokens": 0}
    assert (
        active_cut(request) == 2 and window_number(request) == 2 and window_number(request, 2) == 1
    )
    del records[K.INJECTION, "d"]
    request.context_tokens = 75
    assert due(request, policy, 2) == "soft"


async def windows_request(kernel, credential, policy, messages, upstream=None, protocol="chat"):
    policy.update(gateway_tools=True, agent_windows=True)
    body = {"model": "model", FIELDS[protocol]: messages}
    if protocol == "responses":
        body["store"] = False
    header = {"x-openviking-session": "windows"}
    return await kernel.prepare(body, protocol, header, credential, upstream or {"id": "u"}, policy)


async def test_window_header_carries_notes_message_hint_and_opening(
    setup_kernel, credential, policy
):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)
    messages = [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "First task"},
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": "<system-reminder>noise</system-reminder>Second task"},
    ]
    prepared = await windows_request(kernel, credential, policy, messages)
    prepared.capture = Document({"ov_session": "s-now", "delivered": "x", "previous": ["s-old"]})
    result = await new_context(prepared, credential, NOTES)
    anchor, value = result.pop("cut")
    assert result == {"content": "Context window 2 started.", "failed": False}
    assert anchor == prepared.capture_chain[-1]
    header = value["text"]
    assert value == {"source": "window", "text": header, "tokens": token_estimate(header)}
    hint = history_hint("alice", ["s-now", "s-old"], prepared.root["tools"], True)
    opening = OPENING.search(prepared.body["messages"][-1]["content"])[0]
    parts = [
        '<openviking-context source="gateway-window">\nThis is context window 2.',
        "the user did not write it",
        "Reason you gave: Phase one is done.",
        "Your notes:\nGoal: ship X. Done: A.",
        "Next steps:\nTest.",
        "The user's most recent message, verbatim:\nSecond task\n",
        hint,
        "</openviking-context>\n\n" + opening,
    ]
    positions = [header.index(part) for part in parts]
    assert positions == sorted(positions) and header.endswith(opening)
    assert "noise" not in header
    # Each earlier window counts; a retry or second reset at the anchor does not.
    store_value = {"source": "window", "text": "old", "tokens": 1}
    prepared.records[K.REPLACEMENT, prepared.capture_chain[1]] = store_value
    prepared.records[K.REPLACEMENT, anchor] = store_value
    assert "context window 3." in (await new_context(prepared, credential, NOTES))["cut"][1]["text"]
    for args in ({"reason": "r", "notes": " "}, {"reason": 1, "notes": "n"}):
        assert (await new_context(prepared, credential, args))["failed"]


async def test_new_context_needs_a_client_message_and_an_unreplaced_anchor(
    setup_kernel, credential, policy
):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)
    refused = {"content": STAY, "failed": True}
    # A cut at the empty anchor of system messages would apply to every session.
    system = [{"role": "system", "content": "Be brief."}]
    prepared = await windows_request(kernel, credential, policy, system)
    assert await new_context(prepared, credential, NOTES) == refused
    # A cut this request was built on keeps its stored text, so no window starts there.
    messages = [{"role": "user", "content": "First task"}]
    prepared = await windows_request(kernel, credential, policy, messages)
    anchor = prepared.capture_chain[-1]
    cut = {"source": "compaction", "text": "Summary", "tokens": 1}
    await store.replay.put(prepared.scope, prepared.session, K.REPLACEMENT, anchor, cut)
    prepared = await windows_request(kernel, credential, policy, messages)
    assert prepared.metrics["compaction_applied"] == anchor
    assert await new_context(prepared, credential, NOTES) == refused


async def test_context_remaining_reports_the_window(setup_kernel, credential, policy):
    kernel, _, _, _ = setup_kernel
    policy.update(recall=False)
    messages = [
        {"role": "user", "content": "One"},
        {"role": "assistant", "content": "A"},
        {"role": "user", "content": "Two"},
        {"role": "assistant", "content": "B"},
        {"role": "user", "content": "Three"},
    ]
    prepared = await windows_request(kernel, credential, policy, messages)
    prepared.records[K.REPLACEMENT, prepared.capture_chain[1]] = {"source": "window", "text": ""}
    prepared.observation = Document({"user_at": time.time() - 125})
    prepared.context_window = 100_000
    advice = {
        10_000: "no action needed; keep working.",
        80_000: "start a new window soon: once the current step is done, call "
        "openviking_new_context with notes that record your findings so far and what remains.",
        90_000: "call openviking_new_context now, on its own, with complete notes.",
    }
    for used, expected in advice.items():
        prepared.context_tokens = used
        result = await context_remaining(prepared, credential, {})
        assert result == {
            "content": f"Context window 2: ~{amount(used)} of 100k tokens used "
            f"({used // 1000}%), ~{amount(100_000 - used)} left.\n"
            "User messages in this window: 2.\n"
            "Time since the user's previous message: 2m.\n"
            "Advice: " + expected,
            "failed": False,
        }


async def test_status_line_ends_the_recall_block(setup_kernel, credential, policy):
    kernel, _, _, _ = setup_kernel
    upstream = {"id": "u", "context_windows": {"model": 200_000}}
    messages = [{"role": "user", "content": "Deploy to blue?"}]
    first = await windows_request(kernel, credential, policy, messages, upstream)
    status = (
        f"[context-status] window w1 · ~{amount(first.context_tokens)}/200k tokens "
        f"({first.context_tokens / 200_000:.0%})"
    )
    text = first.body["messages"][0]["content"]
    recalled = text[text.index('<openviking-context source="gateway-recall">') :]
    assert "Deploy using the blue cluster." in recalled
    assert recalled.endswith("\n\n" + status + "\n</openviking-context>")
    record = first.records[K.INJECTION, first.chain[0]]
    # The status line costs nothing against the recall budget.
    assert record["tokens"] == token_estimate(recalled.replace("\n\n" + status, ""))
    assert first.metrics["window"] == 1 and "window_reminder" not in first.metrics
    capture = ResponseCapture("chat")
    capture.nonstream(completion({"role": "assistant", "content": "Use blue."}))
    await kernel.completed(first, credential, capture)
    # With nothing new recalled the block holds only the status line, now with the gap
    # since the user's previous message.
    messages += [{"role": "assistant", "content": "Use blue."}, {"role": "user", "content": "Go"}]
    second = await windows_request(kernel, credential, policy, messages, upstream)
    status = (
        f"[context-status] window w1 · ~{amount(second.context_tokens)}/200k tokens "
        f"({second.context_tokens / 200_000:.0%}) · 0s since your previous message"
    )
    content = re.sub(r"· \d+s since", "· 0s since", second.body["messages"][-1]["content"])
    assert content == "Go\n\n" + block("gateway-recall", status)
    assert second.records[K.INJECTION, second.chain[-1]]["tokens"] == 0


@pytest.mark.parametrize("mode", ["off", "blocked_at_start", "blocked_later"])
async def test_windows_need_gateway_tools(setup_kernel, credential, policy, mode):
    kernel, _, _, _ = setup_kernel
    upstream = {"id": "u"}
    messages = [{"role": "user", "content": "Deploy to blue?"}]
    if mode == "blocked_at_start":
        upstream["allow_gateway_tools"] = False
    if mode == "off":
        policy.update(gateway_tools=True)
        prepared = await kernel.prepare(
            {"model": "model", "messages": messages},
            "chat",
            {"x-openviking-session": "off"},
            credential,
            upstream,
            policy,
        )
    else:
        prepared = await windows_request(kernel, credential, policy, messages, upstream)
    if mode == "blocked_later":
        assert prepared.tools_active
        messages += [{"role": "assistant", "content": "Yes."}, {"role": "user", "content": "Go"}]
        prepared = await kernel.prepare(
            {"model": "model", "messages": messages, "tool_choice": "required"},
            "chat",
            {"x-openviking-session": "windows"},
            credential,
            upstream,
            policy,
        )
    names = {t["function"]["name"] for t in prepared.root["tools"]}
    assert (NEW_CONTEXT in names) == (mode == "blocked_later")
    assert "[context-status] window" not in text_content(prepared.body["messages"][-1])
    assert "window" not in prepared.metrics


@pytest.mark.parametrize("words, expected", [(1, ""), (7200, "soft"), (12000, "hard")])
async def test_user_request_reminders(setup_kernel, credential, policy, words, expected):
    kernel, _, _, _ = setup_kernel
    policy.update(recall=False, window_soft_ratio=0.3, window_hard_ratio=0.6)
    upstream = {"id": "u", "context_windows": {"model": 20_000}}
    messages = [{"role": "user", "content": "word " * words}]
    prepared = await windows_request(kernel, credential, policy, messages, upstream)
    ratio = prepared.context_tokens / prepared.context_window
    assert {"": ratio < 0.3, "soft": 0.3 <= ratio < 0.6, "hard": 0.6 <= ratio < 0.9}[expected]
    record = prepared.records[K.INJECTION, prepared.chain[0]]
    assert record.get("reminder", "") == expected
    assert prepared.metrics.get("window_reminder", "") == expected
    assert record["tokens"] == 0
    text = text_content(prepared.body["messages"][0])
    assert ("[context-reminder] This" in text) == bool(expected)
    if expected:
        assert text.endswith(REMINDERS[expected].format(f"{ratio:.0%}") + "\n</openviking-context>")


def tool_round(protocol, number, output):
    call = f"c-{number}"
    if protocol == "chat":
        return [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call,
                        "type": "function",
                        "function": {"name": "shell", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": call, "content": output},
        ]
    if protocol == "anthropic":
        return [
            {"role": "assistant", "content": [{"type": "tool_use", "id": call, "name": "shell"}]},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": call, "content": output}],
            },
        ]
    return [
        {"type": "function_call", "call_id": call, "name": "shell", "arguments": "{}"},
        {"type": "function_call_output", "call_id": call, "output": output},
    ]


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_continuation_reminders_append_to_tool_results(
    setup_kernel, credential, policy, protocol
):
    kernel, _, _, _ = setup_kernel
    policy.update(recall=False, capture=False, window_soft_ratio=0.3, window_hard_ratio=0.6)
    upstream = {"id": "u", "context_windows": {"model": 20_000}}
    messages = [{"role": "user", "content": "Read the files"}]
    seen = []
    steps = [(7200, "soft"), (1, ""), (4800, "hard"), (1, "hard")]
    for number, (words, expected) in enumerate(steps):
        messages += tool_round(protocol, number, "data " * words)
        prepared = await windows_request(kernel, credential, policy, messages, upstream, protocol)
        assert prepared.kind == "continuation"
        assert prepared.metrics.get("window_reminder", "") == expected
        last = prepared.body[FIELDS[protocol]][-1]
        percent = f"{prepared.context_tokens / prepared.context_window:.0%}"
        reminder = block("gateway-recall", REMINDERS[expected].format(percent)) if expected else ""
        if protocol == "chat":
            assert last["content"].endswith("\n\n" + reminder if expected else "data ")
        elif protocol == "anthropic":
            assert last["content"][-1] == (
                {"type": "text", "text": reminder} if expected else messages[-1]["content"][-1]
            )
        else:
            assert last["output"].endswith("\n\n" + reminder if expected else "data ")
            assert "content" not in last
        if expected:
            seen.append(prepared.records[K.INJECTION, prepared.chain[-1]])
    assert [(r["reminder"], r["tokens"], r["uris"]) for r in seen] == [
        ("soft", 0, []),
        ("hard", 0, []),
        ("hard", 0, []),
    ]


@pytest.mark.parametrize(
    "protocol, last",
    [
        ("chat", {"role": "assistant", "content": "Sure"}),
        ("anthropic", {"role": "assistant", "content": [{"type": "text", "text": "Sure"}]}),
        ("responses", {"type": "local_shell_call_output", "id": "o", "output": "done"}),
    ],
)
async def test_continuation_reminders_skip_messages_that_are_not_tool_results(
    setup_kernel, credential, policy, protocol, last
):
    kernel, _, _, _ = setup_kernel
    policy.update(recall=False, capture=False, window_soft_ratio=0.3, window_hard_ratio=0.6)
    upstream = {"id": "u", "context_windows": {"model": 20_000}}
    messages = [{"role": "user", "content": "word " * 7200}, last]
    prepared = await windows_request(kernel, credential, policy, messages, upstream, protocol)
    frozen = Policy.model_validate(prepared.root["policy"])
    assert prepared.kind == "continuation" and due(prepared, frozen, -1) == "soft"
    assert prepared.body[FIELDS[protocol]][-1] == last
    assert (K.INJECTION, prepared.chain[-1]) not in prepared.records
    assert "window_reminder" not in prepared.metrics


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_reset_continues_in_the_new_window(
    running_gateway, confirmed_cuts, protocol, streaming
):
    app, client, admin, _, _, _ = running_gateway
    await enable_tools(client, admin, agent_windows=True)
    run = await send(running_gateway, protocol, streaming, [[(NEW_CONTEXT, NOTES)]])
    assert run.response.status_code == 200, run.response.text
    visible = visible_messages(protocol, run.response, streaming)
    text = visible_text(visible)
    assert NOTICE in text and text.endswith("answer-2")
    assert "Goal: ship X" not in run.response.text and "gateway-1" not in run.response.text
    field = FIELDS[protocol]
    first, second = run.requests
    assert NEW_CONTEXT in orjson.dumps(first["tools"]).decode()
    prefix = [m for m in first[field] if m.get("role") in {"system", "developer"}]
    header = second[field][-1]["content"]
    # The call and its result are not part of the new window.
    assert second[field] == [*prefix, {"role": "user", "content": header}]
    if protocol == "anthropic":
        assert second["system"] == "Be brief." and second["thinking"] == first["thinking"]
    assert header.startswith(
        '<openviking-context source="gateway-window">\nThis is context window 2.'
    )
    assert "Your notes:\nGoal: ship X. Done: A." in header
    assert "most recent message, verbatim:\nFind blue" in header
    opening = OPENING.search(text_content(first[field][-1]))[0]
    assert header.endswith("</openviking-context>\n\n" + opening)
    # The cut follows the last client message; the hidden record holds only the new window.
    anchor = hidden_chain(run.body[field], protocol)[-1]
    assert stored(app, "replacement") == {
        anchor: {"source": "window", "text": header, "tokens": token_estimate(header)}
    }
    assert confirmed_cuts == [anchor]
    assert stored(app, "hidden") == {
        hidden_chain([*run.body[field], *visible], protocol)[-1]: {
            "messages": round_output(protocol, run.upstream[1]),
            "visible_count": len(visible),
            "upstream_id": protocol,
        }
    }
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert logs[0]["window"] == 2 and logs[0]["window_reset"] is True


async def test_last_reset_in_a_request_wins(running_gateway, confirmed_cuts):
    app, client, admin, _, _, _ = running_gateway
    await enable_tools(client, admin, agent_windows=True)
    second_notes = {**NOTES, "notes": "Second notes."}
    run = await send(
        running_gateway, "chat", False, [[(NEW_CONTEXT, NOTES)], [(NEW_CONTEXT, second_notes)]]
    )
    assert run.response.status_code == 200, run.response.text
    header = run.requests[2]["messages"][-1]["content"]
    assert "This is context window 2." in header and "Second notes." in header
    assert [r["text"] for r in stored(app, "replacement").values()] == [header]
    (record,) = stored(app, "hidden").values()
    assert record["messages"] == round_output("chat", run.upstream[2])
    assert len(confirmed_cuts) == 1


@pytest.mark.parametrize("protocol", ["chat", "anthropic"])
async def test_new_context_must_be_the_only_call(running_gateway, confirmed_cuts, protocol):
    app, client, admin, _, _, _ = running_gateway
    await enable_tools(client, admin, agent_windows=True)
    calls = [(NEW_CONTEXT, NOTES), ("openviking_find", {"query": "blue"})]
    run = await send(running_gateway, protocol, False, [calls])
    assert run.response.status_code == 200, run.response.text
    field = FIELDS[protocol]
    first, second = run.requests
    output = round_output(protocol, run.upstream[0])
    assert second[field][: len(first[field]) + len(output)] == [*first[field], *output]
    results = second[field][len(first[field]) + len(output) :]
    if protocol == "anthropic":
        (message,) = results
        assert [(b["content"], b["is_error"]) for b in message["content"]] == [(ALONE, True)] * 2
    else:
        assert [r["content"] for r in results] == [ALONE, ALONE]
    assert not run.mcp and not stored(app, "replacement") and not confirmed_cuts
    text = visible_text(visible_messages(protocol, run.response, False))
    assert "> OpenViking new_context — failed" in text


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_failed_loop_after_a_reset_writes_nothing(running_gateway, confirmed_cuts, protocol):
    app, client, admin, _, _, _ = running_gateway
    await enable_tools(client, admin, agent_windows=True)
    run = await send(running_gateway, protocol, False, [[(NEW_CONTEXT, NOTES)]], fail=True)
    assert run.response.status_code == 503 and len(run.requests) == 2
    assert not stored(app, "replacement") and not stored(app, "hidden") and not confirmed_cuts


async def test_context_remaining_reads_the_latest_round(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    await enable_tools(client, admin, agent_windows=True, context_window=2000)
    run = await send(running_gateway, "chat", False, [[("openviking_context_remaining", {})]])
    assert run.response.status_code == 200, run.response.text
    # The first round reported 100 input and 20 output tokens.
    assert run.requests[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "gateway-1-0",
        "content": "Context window 1: ~120 of 2k tokens used (6%), ~1.9k left.\n"
        "User messages in this window: 1.\n"
        "Advice: no action needed; keep working.",
    }
    assert not stored(app, "replacement")


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_reset_replays_on_the_next_request(running_gateway, protocol):
    app, client, admin, _, _, _ = running_gateway
    await enable_tools(client, admin, agent_windows=True)
    run = await send(running_gateway, protocol, False, [[(NEW_CONTEXT, NOTES)]])
    field = FIELDS[protocol]
    header = run.requests[1][field][-1]["content"]
    # The history up to the cut is queued for delivery at once.
    anchor = hidden_chain(run.body[field], protocol)[-1]
    store = app.state.store
    with store.connect() as c:
        (value,) = c.execute("SELECT value FROM capture").fetchone()
    state = store.decode(value)
    confirmed = [turn["anchor"] for turn in state["pending"] if turn["confirmed"]]
    assert anchor in [state["delivered"], *confirmed]
    run.body[field] += [
        *visible_messages(protocol, run.response, False),
        {"role": "user", "content": "Go"},
    ]
    response = await client.post(PATHS[protocol], headers=run.headers, json=run.body)
    assert response.status_code == 200, response.text
    replay = run.requests[-1][field]
    prefix = [m for m in run.body[field] if m.get("role") in {"system", "developer"}]
    assert replay[: len(prefix) + 1] == [*prefix, {"role": "user", "content": header}]
    assert replay[len(prefix) + 1 : -1] == round_output(protocol, run.upstream[1])
    assert text_content(replay[-1]).startswith("Go\n")
    assert "[context-status] window w2 " in text_content(replay[-1])


async def test_reset_builds_the_window_the_next_request_replays(running_gateway, confirmed_cuts):
    app, client, admin, _, _, _ = running_gateway
    await enable_tools(client, admin, agent_windows=True)
    body = client_body("chat", False)
    late = {"role": "system", "content": "Answer in English."}
    body["messages"].append(late)
    run = await send(running_gateway, "chat", False, [[(NEW_CONTEXT, NOTES)]], body=body)
    assert run.response.status_code == 200, run.response.text
    window = run.requests[1]["messages"]
    # A system message after the cut stays after the header, where the replay puts it.
    assert window == [body["messages"][0], {"role": "user", "content": window[1]["content"]}, late]
    run.body["messages"] += [
        *visible_messages("chat", run.response, False),
        {"role": "user", "content": "Go"},
    ]
    response = await client.post(PATHS["chat"], headers=run.headers, json=run.body)
    assert response.status_code == 200, response.text
    assert run.requests[-1]["messages"][: len(window)] == window


async def test_compaction_cuts_when_the_model_never_starts_a_window(
    setup_kernel, credential, policy
):
    kernel, _, _, _ = setup_kernel
    policy.update(recall=False)
    upstream = {"id": "u", "context_windows": {"model": 20_000}}
    messages = [{"role": "user", "content": "Deploy to blue?"}]
    first = await windows_request(kernel, credential, policy, messages, upstream)
    reply = {"role": "assistant", "content": "Use blue."}
    await answered(kernel, first, credential, reply, tokens=19_000)
    messages += [reply, {"role": "user", "content": "Go"}]
    second = await kernel.prepare(
        {"model": "model", "messages": messages},
        "chat",
        {"x-openviking-session": "windows"},
        credential,
        upstream,
        policy,
        summarize=Summarizer("chat"),
    )
    assert second.metrics["compaction_tokens"] and second.metrics["context_tokens"] > 18_000
    summary, latest = second.body["messages"]
    # The opening block, and with it the window guidance, survives the cut.
    assert "You manage your own context windows" in summary["content"]
    # The status line describes the compacted context; a compaction does not number windows.
    assert second.context_tokens < 2_000
    status = (
        f"[context-status] window w1 · ~{amount(second.context_tokens)}/20k tokens "
        f"({second.context_tokens / 20_000:.0%})"
    )
    assert status in text_content(latest) and "[context-reminder]" not in text_content(latest)
    assert second.metrics["window"] == 1 and "window_reminder" not in second.metrics
