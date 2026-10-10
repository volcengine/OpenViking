"""Reasoning a client drops goes back to upstreams that need it, unchanged every turn."""

import orjson
import pytest
from aiohttp import web
from test_app import completion, enable_tools

from openviking_gateway.models import Upstream
from openviking_gateway.protocols import replays_reasoning
from openviking_gateway.records import RecordKind as K
from openviking_gateway.tool_protocols import ResponseCapture, hidden_chain

QUESTION = {"role": "user", "content": "How do I deploy?"}
FOLLOW_UP = {"role": "user", "content": "And then?"}


@pytest.mark.parametrize(
    "vendor,value,expected",
    [
        ("deepseek", None, True),
        ("ark", None, True),
        ("byteplus", None, True),
        ("generic", None, False),
        ("openai", None, False),
        ("anthropic", None, False),
        ("deepseek", False, False),
        ("generic", True, True),
    ],
)
def test_vendor_defaults(vendor, value, expected):
    upstream = Upstream(
        name="x", base_url="http://model", protocol="chat", vendor=vendor, replay_reasoning=value
    ).model_dump()
    assert replays_reasoning(upstream) is expected


async def configure_chat(client, admin, **fields):
    response = await client.put(
        "/admin/upstreams/chat",
        headers=admin,
        json={
            "name": "chat",
            "protocol": "chat",
            "base_url": "http://127.0.0.1:1",
            "api_key": "model-secret",
            "models": ["model"],
            "vendor": "deepseek",
            **fields,
        },
    )
    assert response.status_code == 200, response.text


def reasoning_backend(app, requests):
    """A DeepSeek-like upstream: every reply reasons about the latest question."""

    async def backend(request):
        if request.path != "/v1/chat/completions":
            return None
        payload = await request.json()
        requests.append(payload)
        question = payload["messages"][-1]["content"].split("\n\n")[0]
        message = {
            "role": "assistant",
            "content": "Answer: " + question,
            "reasoning_content": "Thinking about " + question,
        }
        if not payload.get("stream"):
            return web.json_response(completion(message))
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        events = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": ""}}]},
            {"choices": [{"index": 0, "delta": {"reasoning_content": "Thinking about "}}]},
            {"choices": [{"index": 0, "delta": {"reasoning_content": question}}]},
            {"choices": [{"index": 0, "delta": {"content": message["content"]}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        for event in events:
            await response.write(b"data: " + orjson.dumps(event) + b"\n\n")
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    app.state.test_backend["handler"] = backend


async def chat_turns(client, key, session, turns, streaming, keep_reasoning=False):
    """Send each user turn, resending replies the way a chat UI does: without reasoning."""
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": session}
    messages = []
    for text in turns:
        messages.append({"role": "user", "content": text})
        body = {"model": "model", "messages": messages, "stream": streaming}
        response = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert response.status_code == 200, response.text
        reply = {"role": "assistant", "content": "Answer: " + text}
        if keep_reasoning:
            reply["reasoning_content"] = "Client copy."
        messages.append(reply)


@pytest.mark.parametrize("streaming", [False, True])
async def test_chat_restores_dropped_reasoning_byte_for_byte(running_gateway, streaming):
    app, client, admin, key, captured, _ = running_gateway
    await configure_chat(client, admin, base_url=str(app.state.config.openviking_url))
    requests = []
    reasoning_backend(app, requests)
    await chat_turns(client, key, "deepseek", ["One?", "Two?", "Three?"], streaming)
    second, third = requests[1]["messages"], requests[2]["messages"]
    assert "reasoning_content" not in requests[0]["messages"][0]
    assert second[1] == {
        "role": "assistant",
        "content": "Answer: One?",
        "reasoning_content": "Thinking about One?",
    }
    assert third[3]["reasoning_content"] == "Thinking about Two?"
    # The restored prefix is identical across turns, so provider caches keep hitting.
    assert orjson.dumps(third[:3]) == orjson.dumps(second[:3])


async def test_chat_keeps_reasoning_the_client_sent(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await configure_chat(client, admin, base_url=str(app.state.config.openviking_url))
    requests = []
    reasoning_backend(app, requests)
    await chat_turns(client, key, "kept", ["One?", "Two?"], False, keep_reasoning=True)
    assert requests[1]["messages"][1]["reasoning_content"] == "Client copy."


async def test_option_off_restores_nothing_and_keeps_the_deepseek_gate(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    url = str(app.state.config.openviking_url)
    await enable_tools(client, admin)
    requests = []
    reasoning_backend(app, requests)
    await configure_chat(client, admin, base_url=url, vendor="deepseek", replay_reasoning=False)
    await chat_turns(client, key, "off", ["One?", "Two?"], False)
    assert all("tools" not in r for r in requests)
    assert "reasoning_content" not in requests[1]["messages"][1]
    # The vendor default restores reasoning, so DeepSeek sessions get gateway tools.
    await configure_chat(client, admin, base_url=url, vendor="deepseek")
    await chat_turns(client, key, "on", ["One?", "Two?"], False)
    assert any(t["function"]["name"] == "openviking_find" for t in requests[2]["tools"])
    assert requests[3]["messages"][1]["reasoning_content"] == "Thinking about One?"


async def test_generic_upstream_restores_nothing(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    url = str(app.state.config.openviking_url)
    await configure_chat(client, admin, base_url=url, vendor="generic")
    requests = []
    reasoning_backend(app, requests)
    await chat_turns(client, key, "generic", ["One?", "Two?"], False)
    assert "reasoning_content" not in requests[1]["messages"][1]


async def prepare(kernel, credential, policy, protocol, messages, session=None, headers=None):
    field = "input" if protocol == "responses" else "messages"
    body = {"model": "model", field: messages}
    if protocol == "responses":
        body["store"] = False
    return await kernel.prepare(
        body,
        protocol,
        {**({"x-openviking-session": session} if session else {}), **(headers or {})},
        credential,
        {"id": "upstream", "vendor": "ark"},
        policy,
    )


async def relay(kernel, credential, request, capture):
    await kernel.completed(request, credential, capture)


async def test_chat_reasoning_follows_a_fork(setup_kernel, credential, policy):
    kernel, _, _, _ = setup_kernel
    policy.update(recall=False, profile=False)
    first = await prepare(kernel, credential, policy, "chat", [QUESTION], "a")
    reply = {"role": "assistant", "content": "Use blue.", "reasoning_content": "Blue is live."}
    await relay(kernel, credential, first, ResponseCapture("chat", reply, complete=True))
    visible = {"role": "assistant", "content": "Use blue."}
    # Client-side compaction or a subagent continues the reply under another session id.
    fork = await prepare(kernel, credential, policy, "chat", [QUESTION, visible, FOLLOW_UP], "b")
    assert fork.session != first.session
    assert fork.body["messages"][1] == reply
    assert fork.metrics["reasoning_restored"] == 1
    # A plugin-managed session is forwarded as the client sent it.
    plugin = await prepare(
        kernel,
        credential,
        policy,
        "chat",
        [QUESTION, visible, FOLLOW_UP],
        "c",
        {"x-openviking-plugin": "1"},
    )
    assert plugin.body["messages"][1] == visible


async def test_anthropic_restores_thinking_blocks_in_place(setup_kernel, credential, policy):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False, profile=False)
    first = await prepare(kernel, credential, policy, "anthropic", [QUESTION], "a")
    thinking = {"type": "thinking", "thinking": "Blue is live.", "signature": "sig"}
    content = [thinking, {"type": "text", "text": "Use blue."}]
    await relay(
        kernel,
        credential,
        first,
        ResponseCapture("anthropic", {"role": "assistant", "content": content}, complete=True),
    )
    dropped = {"role": "assistant", "content": [{"type": "text", "text": "Use blue."}]}
    second = await prepare(
        kernel, credential, policy, "anthropic", [QUESTION, dropped, FOLLOW_UP], "a"
    )
    assert second.body["messages"][1] == {"role": "assistant", "content": content}
    # A client that resends the thinking keeps its own copy.
    kept = {"role": "assistant", "content": content}
    third = await prepare(kernel, credential, policy, "anthropic", [QUESTION, kept, FOLLOW_UP], "a")
    assert third.body["messages"][1] == kept
    assert "reasoning_restored" not in third.metrics


async def test_anthropic_thinking_resent_as_text_is_not_duplicated(
    setup_kernel, credential, policy
):
    kernel, _, _, _ = setup_kernel
    policy.update(recall=False, profile=False)
    first = await prepare(kernel, credential, policy, "anthropic", [QUESTION], "a")
    # DeepSeek through Ark returns unsigned thinking; pi resends it as text.
    content = [{"type": "thinking", "thinking": "Blue is live."}, {"type": "text", "text": "Blue."}]
    await relay(
        kernel,
        credential,
        first,
        ResponseCapture("anthropic", {"role": "assistant", "content": content}, complete=True),
    )
    as_text = {
        "role": "assistant",
        "content": [{"type": "text", "text": "Blue is live."}, {"type": "text", "text": "Blue."}],
    }
    second = await prepare(
        kernel, credential, policy, "anthropic", [QUESTION, as_text, FOLLOW_UP], "a"
    )
    assert second.body["messages"][1] == as_text


async def test_responses_restores_reasoning_items_before_their_reply(
    setup_kernel, credential, policy
):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False, profile=False)
    question = {"role": "user", "content": [{"type": "input_text", "text": "How do I deploy?"}]}
    first = await prepare(kernel, credential, policy, "responses", [question], "a")
    reasoning = {
        "id": "rs_1",
        "type": "reasoning",
        "summary": [],
        "content": [{"type": "reasoning_text", "text": "Blue is live."}],
    }
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "Use blue.", "annotations": []}],
    }
    capture = ResponseCapture("responses")
    capture.nonstream({"status": "completed", "output": [reasoning, message]})
    await relay(kernel, credential, first, capture)
    # The client resends the reply without its reasoning item.
    follow = {"role": "user", "content": [{"type": "input_text", "text": "And then?"}]}
    second = await prepare(
        kernel, credential, policy, "responses", [question, message, follow], "a"
    )
    assert second.body["input"] == [question, reasoning, message, follow]
    # A fork finds the reply by the anchor it has without the reasoning item.
    fork = await prepare(kernel, credential, policy, "responses", [question, message, follow], "b")
    assert fork.session != first.session
    assert fork.body["input"] == second.body["input"]
    # A client that keeps the item is forwarded unchanged.
    kept = await prepare(
        kernel, credential, policy, "responses", [question, reasoning, message, follow], "a"
    )
    assert kept.body["input"] == [question, reasoning, message, follow]
    records = await store.replay.read(first.scope, first.session, second.body_chain)
    assert [key for key in records if key[0] == K.REASONING]


async def relay_anthropic(kernel, credential, request, content):
    message = {"role": "assistant", "content": content}
    await relay(kernel, credential, request, ResponseCapture("anthropic", message, complete=True))


@pytest.mark.parametrize("lost", ["as_text", "upstream_changed"])
async def test_deepseek_tools_close_when_reasoning_cannot_be_restored(
    setup_kernel, credential, policy, lost
):
    kernel, _, _, _ = setup_kernel
    policy.update(gateway_tools=True, recall=False, profile=False)
    upstream = {"id": "upstream", "vendor": "deepseek"}
    body = {"model": "model", "messages": [QUESTION]}
    header = {"x-openviking-session": "a"}
    first = await kernel.prepare(body, "anthropic", header, credential, upstream, policy)
    assert first.tools_active
    content = [{"type": "thinking", "thinking": "Blue is live."}, {"type": "text", "text": "Blue."}]
    await relay_anthropic(kernel, credential, first, content)
    dropped = {"role": "assistant", "content": [{"type": "text", "text": "Blue."}]}
    body = {"model": "model", "messages": [QUESTION, dropped, FOLLOW_UP]}
    restored = await kernel.prepare(body, "anthropic", header, credential, upstream, policy)
    assert restored.tools_active and restored.metrics["reasoning_restored"] == 1
    if lost == "as_text":
        # pi resends unsigned thinking as text, so that reply reaches DeepSeek without it.
        text = {"type": "text", "text": "Blue is live."}
        reply = {"role": "assistant", "content": [text, {"type": "text", "text": "Blue."}]}
        body = {"model": "model", "messages": [QUESTION, reply, FOLLOW_UP]}
    else:
        # Moving upstream strips thinking and restores none.
        upstream = {"id": "other", "vendor": "deepseek"}
    request = await kernel.prepare(body, "anthropic", header, credential, upstream, policy)
    assert not request.tools_active and "tools" not in request.body
    assert request.metrics["tool_skip_reason"] == "deepseek_reasoning_history_required"
    # Other vendors accept replies without reasoning and keep their tools.
    other = {**upstream, "vendor": "ark"}
    kept = await kernel.prepare(body, "anthropic", header, credential, other, policy)
    assert kept.tools_active


async def test_responses_hidden_rounds_replay_when_the_client_drops_reasoning(
    setup_kernel, credential, policy
):
    from types import SimpleNamespace

    from openviking_gateway.tool_loop import HiddenToolLoop

    kernel, store, _, _ = setup_kernel
    policy.update(gateway_tools=True, recall=False, profile=False, capture=False)
    question = {"role": "user", "content": [{"type": "input_text", "text": "How do I deploy?"}]}
    first = await prepare(kernel, credential, policy, "responses", [question], "a")
    assert first.tools_active
    reasoning = {
        "id": "rs_1",
        "type": "reasoning",
        "summary": [],
        "content": [{"type": "reasoning_text", "text": "Look it up."}],
    }
    call = {
        "type": "function_call",
        "call_id": "g",
        "name": "openviking_find",
        "arguments": "{}",
    }
    result = {"type": "function_call_output", "call_id": "g", "output": "blue"}
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "Use blue.", "annotations": []}],
    }
    capture = ResponseCapture("responses")
    loop = HiddenToolLoop(first, SimpleNamespace(allowed={"openviking_find"}), store, capture)
    loop.adapter.begin()
    loop.adapter.load({"status": "completed", "output": [reasoning, message], "usage": {}})
    loop.adapter.end()
    loop.hidden, loop.transcript = True, [reasoning, call, result, message]
    await loop.persist()
    await relay(kernel, credential, first, capture)
    follow = {"role": "user", "content": [{"type": "input_text", "text": "And then?"}]}
    # The opening carries the gateway's session note, replayed byte for byte.
    expected = [first.body["input"][0], reasoning, call, result, message, follow]
    for history in ([question, reasoning, message, follow], [question, message, follow]):
        request = await prepare(kernel, credential, policy, "responses", history, "a")
        assert request.tools_active
        assert request.body["input"] == expected
    # A fork finds the hidden rounds by the reply it continues, without the reasoning item.
    fork = await prepare(kernel, credential, policy, "responses", [question, message, follow], "b")
    assert fork.session != first.session
    assert fork.body["input"] == expected


async def test_anthropic_omitted_hidden_history_reports_no_restored_thinking(
    setup_kernel, credential, policy
):
    kernel, store, _, _ = setup_kernel
    policy.update(gateway_tools=True, recall=False, profile=False)
    header, upstream = {"x-openviking-session": "a"}, {"id": "upstream", "vendor": "ark"}
    body = {"model": "model", "messages": [QUESTION]}
    first = await kernel.prepare(body, "anthropic", header, credential, upstream, policy)
    thinking = {"type": "thinking", "thinking": "Blue is live.", "signature": "sig"}
    await relay_anthropic(kernel, credential, first, [thinking, {"type": "text", "text": "Blue."}])
    dropped = {"role": "assistant", "content": [{"type": "text", "text": "Blue."}]}
    anchor = hidden_chain([QUESTION, dropped], "anthropic")[-1]
    await store.replay.put(
        first.scope, first.session, K.HIDDEN, anchor, {"messages": [], "visible_count": 1}
    )
    # A forced tool choice refuses gateway tools, so the hidden rounds and all thinking go.
    forced = {
        "model": "model",
        "messages": [QUESTION, dropped, FOLLOW_UP],
        "tool_choice": {"type": "tool", "name": "weather"},
        "tools": [{"name": "weather", "input_schema": {"type": "object"}}],
    }
    request = await kernel.prepare(forced, "anthropic", header, credential, upstream, policy)
    assert request.hidden_history_unavailable and not request.tools_active
    assert "thinking" not in str(request.body["messages"])
    assert "reasoning_restored" not in request.metrics
