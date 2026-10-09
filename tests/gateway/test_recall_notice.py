"""The recall notice: the user sees it at the start of a reply; the model and memory never do."""

import copy

import orjson
import pytest
from aiohttp import web
from conftest import replay_records
from test_app import enable_tools
from test_native_tools import native_events, native_response, request_body, visible_response

from context_gateway.blocks import gateway_note
from context_gateway.capture import capture_messages
from context_gateway.client import VikingError
from context_gateway.models import Policy
from context_gateway.notices import RECALL_NOTICE, recall_notice
from context_gateway.protocols import SSEDecoder
from context_gateway.tool_protocols import ResponseCapture, tool_protocol
from context_gateway.tool_protocols.common import merge_delta, sse

PATHS = {"chat": "/v1/chat/completions", "anthropic": "/v1/messages", "responses": "/v1/responses"}
# What the test gateway's search returns: one memory without a category.
NOTICE = "> OpenViking recall: 1 item (1 memory) — x\n\n"
FIELD = {"chat": "messages", "anthropic": "messages", "responses": "input"}


def entry(uri, category=None):
    return {"uri": uri, **({"category": category} if category else {})}


# Rendering


def test_recall_line_counts_categories_and_names():
    entries = [
        entry("viking://user/a/memories/experiences/booking_duplicate_handling.md", "experiences"),
        entry("viking://user/a/memories/preferences/user_lang_pref.md", "preferences"),
        entry("viking://resources/docs/guide/", "resources"),
        entry("viking://user/a/memories/events/launch.md", "events"),
    ]
    assert recall_notice([], "recalled", entries) == (
        "> OpenViking recall: 4 items (3 memories, 1 resource) — "
        "booking_duplicate_handling, user_lang_pref, guide, +1 more\n\n"
    )
    # One of each is singular; without a category the URI decides, else only the total counts.
    one = [entry("viking://user/a/memories/x.md")]
    assert recall_notice([], "recalled", one) == "> OpenViking recall: 1 item (1 memory) — x\n\n"
    mixed = [entry("viking://agent/skills/deploy/"), entry("viking://other/thing")]
    assert recall_notice([], "recalled", mixed) == (
        "> OpenViking recall: 2 items (1 skill) — deploy, thing\n\n"
    )
    assert recall_notice([], "recalled", []) == "> OpenViking recall: context added\n\n"


def test_recall_line_clips_names_and_stays_short():
    long = "n" * 100
    entries = [entry(f"viking://user/a/memories/{long}{i}\n.md", "memories") for i in range(10)]
    notice = recall_notice([], "recalled", entries)
    [line] = notice.splitlines()[:-1]
    assert len(line) < 200 and line.endswith(", +7 more") and "…" in line
    assert RECALL_NOTICE.fullmatch(notice)


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("openviking_unavailable", "OpenViking unavailable"),
        ("openviking_http_401", "OpenViking rejected the key (401)"),
        ("openviking_http_503", "OpenViking HTTP 503"),
        ("openviking_version_mismatch", "OpenViking version mismatch"),
        ("recall_timeout", "OpenViking timed out"),
        ("openviking_FORBIDDEN", "openviking_FORBIDDEN"),
    ],
)
def test_failed_recall_is_named(reason, text):
    assert recall_notice([], reason, []) == f"> OpenViking recall failed: {text}\n\n"


@pytest.mark.parametrize("reason", ["empty", "disabled", "budget", "reminder"])
def test_quiet_reasons_show_nothing(reason):
    assert recall_notice([], reason, []) == ""
    assert recall_notice(["history"], reason, []) == "> OpenViking context: earlier sessions\n\n"


def test_session_start_lists_injected_parts_in_order():
    notice = recall_notice(["skills", "history", "profile", "memories"], "recalled", [entry("a/x")])
    assert notice == (
        "> OpenViking context: user profile, memory index, skill list, earlier sessions\n"
        "> OpenViking recall: 1 item — x\n\n"
    )
    assert RECALL_NOTICE.fullmatch(notice)


# Stripping


SHOWN = "> OpenViking context: user profile\n" + NOTICE
TOOL_NOTICE = '\n\n> OpenViking search: "blue" — done\n\n'


@pytest.mark.parametrize("trimmed", [False, True])
def test_anthropic_strip_drops_the_lead_block(trimmed):
    reply = [
        {"type": "thinking", "thinking": "t", "signature": "s"},
        {"type": "text", "text": "Answer."},
    ]
    text = SHOWN.strip() if trimmed else SHOWN
    messages = [
        {"role": "user", "content": SHOWN + "quoted by the user"},
        {"role": "assistant", "content": [{"type": "text", "text": text}, *reply]},
    ]
    adapter = tool_protocol("anthropic")
    assert adapter.strip_lead(messages) == [
        messages[0],
        {"role": "assistant", "content": reply},
    ]
    # A client that moves thinking ahead of the lead block still loses the whole block.
    moved = [
        {"role": "assistant", "content": [reply[0], {"type": "text", "text": text}, *reply[1:]]}
    ]
    assert adapter.strip_lead(moved)[0]["content"] == reply
    # A client that merges text blocks, or sends a string, loses only the prefix.
    merged = [{"role": "assistant", "content": [reply[0], {"type": "text", "text": SHOWN + "A."}]}]
    assert adapter.strip_lead(merged)[0]["content"] == [
        reply[0],
        {"type": "text", "text": "A."},
    ]
    string = [{"role": "assistant", "content": SHOWN + "A."}]
    assert adapter.strip_lead(string) == [{"role": "assistant", "content": "A."}]


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
def test_strip_leaves_other_text_alone(protocol):
    messages = [
        {"role": "user", "content": SHOWN},
        {"role": "assistant", "content": "Before.\n\n" + SHOWN + "After."},
        {"role": "user", "content": "Again"},
        {"role": "assistant", "content": [{"type": "text", "text": TOOL_NOTICE}]},
        {"role": "user", "content": "Again"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "A."}, {"type": "text", "text": SHOWN}],
        },
        {"role": "user", "content": "Again"},
        # A line that only resembles a notice.
        {"role": "assistant", "content": "> OpenViking recall: x\nno blank line"},
    ]
    assert tool_protocol(protocol).strip_lead(messages) is messages


def test_chat_strip_restores_the_content_upstream_sent():
    adapter = tool_protocol("chat")
    calls = [{"id": "c-1", "type": "function", "function": {"name": "shell", "arguments": "{}"}}]
    messages = [
        {"role": "assistant", "content": SHOWN + "Answer."},
        {"role": "assistant", "content": SHOWN, "tool_calls": calls},
        {"role": "assistant", "content": SHOWN.strip()},
        {"role": "assistant", "content": [{"type": "text", "text": SHOWN + "A."}]},
    ]
    assert adapter.strip_lead(messages) == [
        {"role": "assistant", "content": "Answer."},
        {"role": "assistant", "content": None, "tool_calls": calls},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": [{"type": "text", "text": "A."}]},
    ]


def test_responses_strip_drops_the_first_item_of_each_reply():
    def message(text):
        return {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "annotations": [], "text": text}],
        }

    reasoning = {"type": "reasoning", "id": "rs_1", "summary": []}
    messages = [
        {"role": "user", "content": "Find blue"},
        message(SHOWN),
        reasoning,
        message("Answer."),
        # Later in the same reply, a notice is the model's.
        message(SHOWN),
        {"role": "user", "content": "Again"},
        {"role": "assistant", "content": SHOWN.strip()},
        {"role": "user", "content": "Again"},
        message(SHOWN + "Merged."),
    ]
    stripped = tool_protocol("responses").strip_lead(messages)
    assert stripped == [
        *messages[:1],
        *messages[2:6],
        messages[7],
        message("Merged."),
    ]


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
def test_recorded_reply_goes_without_the_notice(protocol):
    """Bookkeeping reads a reply the way the next request strips it."""
    body = {"model": "model", "stream": False}
    adapter = tool_protocol(protocol)(body)
    adapter.lead_with(NOTICE)
    adapter.begin()
    value = native_response(protocol, 1)
    adapter.load(value)
    adapter.end()
    final = adapter.final({})
    capture = ResponseCapture(protocol)
    capture.nonstream(final)
    plain = ResponseCapture(protocol)
    plain.nonstream(value)
    assert capture.output == plain.output
    if protocol == "anthropic":
        assert final["content"] == [{"type": "text", "text": NOTICE}, *value["content"]]
    elif protocol == "responses":
        assert final["output"][0]["content"][0]["text"] == NOTICE
        assert final["output"][1:] == value["output"]
    else:
        assert final["choices"][0]["message"]["content"] == NOTICE + "answer-1"


def test_capture_drops_recall_lines_from_assistant_text():
    messages = [
        {"role": "user", "content": SHOWN + "Quoted."},
        {"role": "assistant", "content": "Merged.\n" + SHOWN + "Answer."},
    ]
    captured = capture_messages(messages, ["a", "b"])
    assert [m["parts"][0]["text"] for m in captured] == [SHOWN + "Quoted.", "Merged.\nAnswer."]


def test_gateway_note_says_what_the_user_sees():
    off = gateway_note(Policy(capture=False), [])
    on = gateway_note(Policy(capture=False, show_recall=True), [])
    assert off.startswith("The OpenViking Context Gateway") and "summary" not in off
    assert on == off + (
        " The user sees a one-line summary of what was added (counts and names), "
        "not the added text."
    )
    assert "client does not show it" in on


# Kernel


async def prepare(kernel, body, credential, policy, **kwargs):
    return await kernel.prepare(
        body,
        "chat",
        {"x-openviking-session": "s"},
        credential,
        {"id": "upstream"},
        policy,
        **kwargs,
    )


async def test_notice_is_part_of_the_immutable_decision(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    viking.profile = "Alice maintains the gateway."
    policy.update(show_recall=True)
    body = {"messages": [{"role": "user", "content": "How do I deploy?"}]}
    one = await prepare(kernel, body, credential, policy)
    expected = (
        "> OpenViking context: user profile\n> OpenViking recall: 1 item (1 memory) — deploy\n\n"
    )
    decision = (await replay_records(store, one, "injection"))["injection", one.chain[0]]
    assert decision["notice"] == one.reply_lead == expected
    # A retry shows the same notice without recalling again.
    again = await prepare(kernel, body, credential, policy)
    assert again.reply_lead == expected and len(viking.recalls) == 1
    # Counting tokens, several choices or structured output get no notice.
    for request, extra in [
        ({**body, "n": 2}, {}),
        ({**body, "response_format": {"type": "json_object"}}, {}),
        (body, {"counting": True}),
    ]:
        assert (await prepare(kernel, request, credential, policy, **extra)).reply_lead == ""
    # The client resends the notice; the next turn reads exactly as if it had not.
    shown, plain = (
        {
            "messages": [
                *body["messages"],
                {"role": "assistant", "content": notice + "Use blue."},
                {"role": "user", "content": "What next?"},
            ]
        }
        for notice in (expected, "")
    )
    two = await prepare(kernel, shown, credential, policy)
    assert two.body == (await prepare(kernel, plain, credential, policy)).body
    assert two.messages[1] == {"role": "assistant", "content": "Use blue."}
    # The only memory is in context already, so this turn recalls nothing to show.
    assert two.reply_lead == ""
    # A continuation after a client tool shows none.
    calls = [{"id": "c-1", "type": "function", "function": {"name": "shell", "arguments": "{}"}}]
    tool = {
        "messages": [
            *body["messages"],
            {"role": "assistant", "content": expected, "tool_calls": calls},
            {"role": "tool", "tool_call_id": "c-1", "content": "cwd"},
        ]
    }
    three = await prepare(kernel, tool, credential, policy)
    assert three.kind == "continuation" and three.reply_lead == ""
    assert three.body["messages"][1]["content"] is None


async def test_failed_recall_and_flag_off(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    body = {"messages": [{"role": "user", "content": "How do I deploy?"}]}
    off = await prepare(kernel, body, credential, policy)
    decision = (await replay_records(store, off, "injection"))["injection", off.chain[0]]
    assert "notice" not in decision and off.reply_lead == ""
    viking.failure = VikingError("openviking_unavailable")
    policy.update(show_recall=True)
    failed = await kernel.prepare(
        body, "chat", {"x-openviking-session": "t"}, credential, {"id": "upstream"}, policy
    )
    assert failed.reply_lead == "> OpenViking recall failed: OpenViking unavailable\n\n"


# Through the gateway


def chat_events(value):
    message = dict(value["choices"][0]["message"])
    calls = message.pop("tool_calls", [])
    events = [
        {
            "id": value["id"],
            "model": "model",
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}
            ],
        },
        {"choices": [{"index": 0, "delta": {"content": message["content"]}}]},
    ]
    events += [
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": index, **call}]}}]}
        for index, call in enumerate(calls)
    ]
    finish = value["choices"][0]["finish_reason"]
    events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    return events


def wire_bytes(protocol, value):
    if protocol == "chat":
        return b"".join(sse(e) for e in chat_events(value)) + b"data: [DONE]\n\n"
    return b"".join(
        b"event: " + e["type"].encode() + b"\n" + sse(e) for e in native_events(protocol, value)
    )


async def reply(request, protocol, value, streaming):
    if not streaming:
        return web.json_response(value)
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    data = wire_bytes(protocol, value)
    for offset in range(0, len(data), 19):
        await response.write(data[offset : offset + 19])
    await response.write_eof()
    return response


def visible_output(protocol, response, streaming):
    """The reply as the client keeps it in its history."""
    if protocol == "responses":
        return visible_response(protocol, response, streaming)["output"]
    if protocol == "anthropic":
        content = visible_response(protocol, response, streaming)["content"]
        return [{"role": "assistant", "content": content}]
    if not streaming:
        message = response.json()["choices"][0]["message"]
    else:
        message = {}
        events = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
        deltas = [c["delta"] for e in events if e for c in e.get("choices", [])]
        # The notice is the first content, in a chunk that names the role.
        assert deltas[0] == {"role": "assistant", "content": NOTICE}
        for delta in deltas:
            delta = dict(delta)
            if delta.get("tool_calls"):
                calls = delta.pop("tool_calls")
                message["tool_calls"] = [
                    {k: v for k, v in c.items() if k != "index"} for c in calls
                ]
            merge_delta(message, delta)
    return [{k: v for k, v in message.items() if k in {"role", "content", "tool_calls"}}]


async def show_recall(client, admin, tools=False, **policy):
    await enable_tools(client, admin, recall=True, show_recall=True, gateway_tools=tools, **policy)


def model_backend(app, protocol, streaming, requests, upstream, respond):
    async def backend(request):
        if request.path == "/mcp":
            return web.json_response(
                {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "x"}]}}
            )
        if request.path != PATHS[protocol]:
            return None
        payload = await request.json()
        requests.append(copy.deepcopy(payload))
        value = respond(len(requests))
        if protocol == "responses":
            value["tools"] = payload.get("tools", [])
        upstream.append(value)
        return await reply(request, protocol, value, streaming)

    app.state.test_backend["handler"] = backend


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("tools", [False, True])
async def test_notice_leads_the_reply_and_never_reaches_the_model(
    running_gateway, protocol, streaming, tools
):
    app, client, admin, key, _, _ = running_gateway
    await show_recall(client, admin, tools)
    requests, upstream = [], []
    # With tools, the first turn runs one hidden round before it answers.
    model_backend(
        app,
        protocol,
        streaming,
        requests,
        upstream,
        lambda number: native_response(protocol, number, owned=tools and number == 1),
    )
    body = request_body(protocol, streaming)
    field = FIELD[protocol]
    # Anonymous: the next turn finds its session by the reply it resends.
    headers = {"Authorization": "Bearer " + key["key"]}
    for turn in range(2):
        response = await client.post(PATHS[protocol], headers=headers, json=body)
        assert response.status_code == 200, response.text
        output = visible_output(protocol, response, streaming)
        if protocol == "responses":
            assert output[0]["role"] == "assistant" and output[0]["content"][0]["text"] == NOTICE
            model = output[1:]
        elif protocol == "anthropic":
            assert output[0]["content"][0] == {"type": "text", "text": NOTICE}
            model = [{**output[0], "content": output[0]["content"][1:]}]
        else:
            assert output[0]["content"].startswith(NOTICE)
            model = [{**output[0], "content": output[0]["content"].removeprefix(NOTICE)}]
        if not tools:
            last = upstream[-1]
            expected = {
                "responses": last.get("output"),
                "anthropic": [{"role": "assistant", "content": last.get("content")}],
                "chat": [{"role": "assistant", "content": f"answer-{len(upstream)}"}],
            }
            assert model == expected[protocol]
        if turn == 0:
            body[field] += [*output, {"role": "user", "content": "Continue"}]
    replay = requests[-1][field]
    first = upstream[0]
    assert replay[0] == requests[0][field][0]
    if protocol == "responses":
        assert replay[1 : 1 + len(first["output"])] == first["output"]
    elif protocol == "anthropic":
        assert replay[1] == {"role": "assistant", "content": first["content"]}
    else:
        reply_message = first["choices"][0]["message"]
        assert {k: v for k, v in replay[1].items() if k != "reasoning_content"} == reply_message
    if tools:
        # The hidden transcript replaced the visible reply, tool results included.
        results = replay[2] if protocol != "responses" else replay[1 + len(first["output"])]
        assert "gateway-1" in orjson.dumps(results).decode()
    # No model request ever carries a notice, recall or tool.
    assert not any("> OpenViking" in orjson.dumps(r).decode() for r in requests)
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert len({log["session"] for log in logs}) == 1


async def test_anthropic_reasoning_restore_ignores_the_notice(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await show_recall(client, admin)
    configured = next(
        u
        for u in (await client.get("/admin/upstreams", headers=admin)).json()
        if u["id"] == "anthropic"
    )
    payload = {
        k: v
        for k, v in configured.items()
        if k not in {"id", "revision", "has_api_key", "header_names", "account"}
    }
    response = await client.put(
        "/admin/upstreams/anthropic", headers=admin, json={**payload, "replay_reasoning": True}
    )
    assert response.status_code == 200, response.text
    requests, upstream = [], []
    model_backend(
        app, "anthropic", False, requests, upstream, lambda n: native_response("anthropic", n)
    )
    body = request_body("anthropic", False)
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "restore"}
    response = await client.post(PATHS["anthropic"], headers=headers, json=body)
    content = response.json()["content"]
    assert content[0]["text"] == NOTICE
    # A client that drops thinking resends only the text blocks.
    texts = [b for b in content if b["type"] == "text"]
    body["messages"] += [
        {"role": "assistant", "content": texts},
        {"role": "user", "content": "Continue"},
    ]
    response = await client.post(PATHS["anthropic"], headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert requests[-1]["messages"][1] == {"role": "assistant", "content": upstream[0]["content"]}


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_flag_off_relays_upstream_bytes(running_gateway, protocol, streaming):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin, recall=True, gateway_tools=False)
    value = native_response(protocol, 1)
    raw = wire_bytes(protocol, value) if streaming else orjson.dumps(value)

    async def backend(request):
        if request.path != PATHS[protocol]:
            return None
        if not streaming:
            return web.Response(body=raw, content_type="application/json")
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(raw)
        await response.write_eof()
        return response

    app.state.test_backend["handler"] = backend
    headers = {"Authorization": "Bearer " + key["key"]}
    response = await client.post(
        PATHS[protocol], headers=headers, json=request_body(protocol, streaming)
    )
    assert response.status_code == 200 and response.content == raw
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert "hidden_rounds" not in logs[0] and logs[0]["recall_count"] == 1


async def test_retry_repeats_the_notice_and_a_continuation_shows_none(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await show_recall(client, admin)
    requests, upstream, searches = [], [], []

    async def count(request):
        if request.path == "/api/v1/search/search":
            searches.append(True)
        return await backend(request)

    model_backend(
        app,
        "chat",
        False,
        requests,
        upstream,
        lambda n: native_response("chat", n, mixed=n < 3),
    )
    backend = app.state.test_backend["handler"]
    app.state.test_backend["handler"] = count
    body = request_body("chat", False)
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "retry"}
    shown = []
    for _ in range(2):
        response = await client.post(PATHS["chat"], headers=headers, json=body)
        shown.append(response.json()["choices"][0]["message"])
    assert [m["content"] for m in shown] == [NOTICE + "answer-1", NOTICE + "answer-2"]
    assert len(searches) == 1
    body["messages"] += [
        {k: v for k, v in shown[0].items() if k in {"role", "content", "tool_calls"}},
        {"role": "tool", "tool_call_id": "client-1", "content": "cwd"},
    ]
    response = await client.post(PATHS["chat"], headers=headers, json=body)
    assert response.json()["choices"][0]["message"]["content"] == "answer-3"
    assert requests[-1]["messages"][1]["content"] == "answer-1"


@pytest.mark.parametrize(
    "extra", [{"n": 2}, {"response_format": {"type": "json_object"}}, {"stream": True}]
)
async def test_only_single_text_replies_get_a_notice(running_gateway, extra):
    _, client, admin, key, _, _ = running_gateway
    await show_recall(client, admin)
    body = {"model": "model", "messages": [{"role": "user", "content": "Find blue"}], **extra}
    response = await client.post(
        PATHS["chat"], headers={"Authorization": "Bearer " + key["key"]}, json=body
    )
    assert response.status_code == 200, response.text
    assert (orjson.dumps(NOTICE).decode()[1:-1] in response.text) == ("stream" in extra)


CUSTOM = {"type": "custom", "custom": {"name": "apply_patch"}}


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "call",
    [
        {"id": "c-1", "type": "function", "function": {"name": "openviking_x", "arguments": "{}"}},
        # A custom tool call has no `function`; such tools keep gateway tools out anyway.
        {"id": "c-1", "type": "custom", "custom": {"name": "apply_patch", "input": "x"}},
    ],
)
async def test_no_tool_session_hands_every_call_to_the_client(running_gateway, call, streaming):
    app, client, admin, key, _, _ = running_gateway
    await show_recall(client, admin)
    requests, upstream = [], []

    def respond(number):
        value = native_response("chat", number)
        if number == 1:
            value["choices"][0].update(
                message={"role": "assistant", "content": None, "tool_calls": [call]},
                finish_reason="tool_calls",
            )
        return value

    model_backend(app, "chat", streaming, requests, upstream, respond)
    body = request_body("chat", streaming)
    if call["type"] == "custom":
        body["tools"] = [CUSTOM]
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "client"}
    response = await client.post(PATHS["chat"], headers=headers, json=body)
    assert response.status_code == 200, response.text
    [message] = visible_output("chat", response, streaming)
    assert message["tool_calls"] == [call] and message["content"] == NOTICE
    body["messages"] += [message, {"role": "tool", "tool_call_id": "c-1", "content": "done"}]
    response = await client.post(PATHS["chat"], headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert requests[-1]["messages"][1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [call],
    }
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert not any(log.get("hidden_rounds") for log in logs)
