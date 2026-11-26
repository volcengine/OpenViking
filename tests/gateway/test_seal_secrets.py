"""seal_secrets: registered secrets reach the model only as references, and back."""

import asyncio
import gzip
import json

import httpx
import orjson
import pytest
import pytest_asyncio
from aiohttp import web
from conftest import MCP_TOOLS, mcp_tool, privacy_reference
from test_compaction import INSTRUCTION_START, summary_response

from openviking_gateway import sealing
from openviking_gateway.protocols import SSEDecoder, text_content
from openviking_gateway.proxy import ResponseCapture
from openviking_gateway.sealing import (
    REFERENCE,
    REPLACED_NOTE,
    SESSION_NOTE,
    json_literals,
    rewrite_json,
    secret_note,
    splice,
)

VALUE = "sk-live-abcdef123456"
TOKEN = "ghp_NEWTOKEN1234567890"
PATHS = {"chat": "/v1/chat/completions", "anthropic": "/v1/messages", "responses": "/v1/responses"}


# The fake OpenViking's version: one with the seal endpoint, as a dev build of that release.
SEAL_VERSION = "0.5.7.dev44"


@pytest_asyncio.fixture
async def gateway_backend(gateway_backend):
    gateway_backend[3]["version"] = SEAL_VERSION
    yield gateway_backend


def auth(key, **extra):
    return {"Authorization": "Bearer " + key["key"], **extra}


async def profile(client, admin, **settings):
    response = await client.put(
        "/admin/policies/default",
        headers=admin,
        json={"name": "Default", "gateway_tools": False, "seal_secrets": True, **settings},
    )
    assert response.status_code == 200, response.text


def privacy(app):
    return app.state.test_backend["privacy"]


def sent_texts(fake):
    return [item for call in fake.calls for item in call]


def request(protocol, messages, stream=False, tools=None):
    body = {"model": "model", "stream": stream}
    if protocol == "responses":
        body.update(input=messages, store=False)
    else:
        body["messages"] = messages
    if protocol == "anthropic":
        body["max_tokens"] = 100
    if tools:
        body["tools"] = tools
    return body


def user(protocol, text):
    kind = "input_text" if protocol == "responses" else "text"
    return {"role": "user", "content": [{"type": kind, "text": text}]}


def model_bodies(captured):
    return [orjson.loads(raw) for path, raw, _ in captured if path in PATHS.values()]


async def logs(client, admin):
    return (await client.get("/admin/logs", headers=admin)).json()


# Pure pieces.


def test_note_fills_list_and_upsert_example_only():
    first = privacy_reference("github-0001", category="auto")
    second = privacy_reference("github-0002", category="auto")
    assert secret_note([first, second]) == (
        "OpenViking stored a secret from the user's message and replaced it with a reference: "
        f"{first}, {second}. You will not see the value. Unless it is clearly for one-off use, "
        "save it under a proper name now with privacy_upsert (or `ov privacy upsert`), passing "
        "the reference as the value and a short description of what it is for, for example "
        'category="credential", target_key="github", values={"token": '
        f'"{first}"}}, description="GitHub PAT for the acme org". The temporary entry is removed '
        "once it is saved, so use the reference that privacy_upsert returns from then on. Write "
        "the reference as is wherever the secret is needed, for example `ov privacy run --env "
        "NAME='<reference>' -- sh -c '<command using \"$NAME\">'`; do not resolve it to plain "
        "text or print any part of it. Tell the user what was stored and under which name."
    )
    assert secret_note([]) == ""


@pytest.mark.parametrize(
    "text",
    [
        '{"a" : "x\\u00e9y secret-value z",  "b":[1, 2.50, "secret-value"]}',
        '{"k":"\\ud83d\\ude00 secret-value \\/ \\"q\\""}',
        '"secret-value"',
    ],
)
def test_json_literals_are_rewritten_in_place(text):
    def change(decoded):
        start = decoded.find("secret-value")
        return [(start, start + len("secret-value"), "{{ref/x}}")] if start >= 0 else []

    rewritten = rewrite_json(text, change)
    assert rewritten != text
    assert json.loads(rewritten) == json.loads(
        text.replace("secret-value", "{{ref/x}}" if "\\/" not in text else "{{ref\\/x}}")
    )
    # Bytes outside the replaced span stay as they were.
    head = text[: text.index("secret-value")]
    assert rewritten.startswith(head)
    assert rewrite_json("not json", change) is None
    assert splice(text, []) == text
    assert all(lit.text for lit in json_literals(text))


# Switch and management.


async def test_switch_off_makes_no_calls(running_gateway):
    app, client, _, key, captured, _ = running_gateway
    privacy(app).register(VALUE, "github")
    response = await client.post(
        PATHS["chat"], headers=auth(key), json=request("chat", [user("chat", "use " + VALUE)])
    )
    assert response.status_code == 200, response.text
    assert not privacy(app).calls
    assert VALUE.encode() in captured[-1][1]


async def test_profile_round_trip_keeps_the_switch(running_gateway):
    _, client, admin, _, _, _ = running_gateway
    await profile(client, admin)
    saved = (await client.get("/admin/policies/default", headers=admin)).json()
    assert saved["seal_secrets"] is True and saved["enroll_tool_secrets"] is False
    await profile(client, admin, enroll_tool_secrets=True)
    saved = (await client.get("/admin/policies/default", headers=admin)).json()
    assert saved["enroll_tool_secrets"] is True


async def test_context_header_cannot_set_the_seal_switches(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    override = {"seal_secrets": False, "enroll_tool_secrets": True}
    response = await client.post(
        PATHS["chat"],
        headers=auth(key, **{"X-OpenViking-Context": json.dumps(override)}),
        json=request("chat", [user("chat", "hi there")]),
    )
    assert response.status_code == 400
    assert all(name in response.text for name in override)
    assert not captured


async def test_current_profile_applies_to_an_earlier_session(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    privacy(app).register(VALUE, "github")
    headers = auth(key, **{"X-OpenViking-Session": "early"})
    first = [user("anthropic", "first message here")]
    assert (
        await client.post(PATHS["anthropic"], headers=headers, json=request("anthropic", first))
    ).status_code == 200
    await profile(client, admin)
    later = [*first, {"role": "assistant", "content": "ok"}, user("anthropic", "now " + VALUE)]
    response = await client.post(
        PATHS["anthropic"], headers=headers, json=request("anthropic", later)
    )
    assert response.status_code == 200, response.text
    assert VALUE.encode() not in captured[-1][1]
    # The model is told about references once, and later turns replay that text.
    told = json.dumps(SESSION_NOTE)[1:-1]
    assert json.dumps(model_bodies(captured)[-1]).count(told) == 1
    third = [*later, {"role": "assistant", "content": "done"}, user("anthropic", "next")]
    response = await client.post(
        PATHS["anthropic"], headers=headers, json=request("anthropic", third)
    )
    assert response.status_code == 200, response.text
    assert json.dumps(model_bodies(captured)[-1]).count(told) == 1


# Sealing.


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("stream", [False, True])
async def test_content_is_sealed(running_gateway, protocol, stream):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    body = request(protocol, [user(protocol, "deploy with " + VALUE)], stream)
    response = await client.post(PATHS[protocol], headers=auth(key), json=body)
    assert response.status_code == 200, response.text
    upstream = model_bodies(captured)[-1]
    assert VALUE not in json.dumps(upstream)
    assert reference in json.dumps(upstream)
    record = (await logs(client, admin))[0]
    assert record["secrets_sealed"] >= 1 and record["seal_calls"] >= 1
    assert VALUE not in json.dumps(record) and reference not in json.dumps(record)


async def test_structure_is_not_sealed(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    name = "deploy_service"
    reference = privacy(app).register(name, "tool")
    tools = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}]
    messages = [
        {"role": "user", "content": "please run it"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": '{"target":"deploy_service"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": name, "content": "deploy_service ok"},
        {"role": "user", "content": "did deploy_service work?"},
    ]
    response = await client.post(
        PATHS["chat"], headers=auth(key), json=request("chat", messages, tools=tools)
    )
    assert response.status_code == 200, response.text
    upstream = model_bodies(captured)[-1]
    assert upstream["tools"][0]["function"]["name"] == name
    call = upstream["messages"][1]["tool_calls"][0]
    assert call["function"]["name"] == name
    assert call["function"]["arguments"] == '{"target":"' + reference + '"}'
    assert upstream["messages"][2]["name"] == name
    assert upstream["messages"][2]["content"] == reference + " ok"
    assert reference in upstream["messages"][3]["content"]


async def test_json_arguments_are_rewritten_in_place(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    arguments = '{"cmd" :  "echo ' + VALUE + '",  "n": 1.50, "u": "caf\\u00e9"}'
    messages = [
        {"role": "user", "content": "run the command"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c", "type": "function", "function": {"name": "sh", "arguments": arguments}}
            ],
        },
        {"role": "tool", "tool_call_id": "c", "content": "done"},
    ]
    tools = [{"type": "function", "function": {"name": "sh", "parameters": {"type": "object"}}}]
    response = await client.post(
        PATHS["chat"], headers=auth(key), json=request("chat", messages, tools=tools)
    )
    assert response.status_code == 200, response.text
    sent = model_bodies(captured)[-1]["messages"][1]["tool_calls"][0]["function"]["arguments"]
    assert sent == arguments.replace(VALUE, reference)


@pytest.mark.parametrize("protocol", ["anthropic", "responses"])
async def test_encoded_files_in_tool_results_are_not_sent(running_gateway, protocol):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    fake = privacy(app)
    reference = fake.register(VALUE, "github")
    encoded, uri = "A" * 400, "data:image/png;base64," + "B" * 400
    if protocol == "anthropic":
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png"}}
        image["source"]["data"] = encoded
        output = [{"type": "text", "text": "key " + VALUE}, image, {"type": "text", "text": uri}]
        call = {"type": "tool_use", "id": "t", "name": "shot", "input": {}}
        result = {"type": "tool_result", "tool_use_id": "t", "content": output}
        messages = [
            user(protocol, "take a screenshot"),
            {"role": "assistant", "content": [call]},
            {"role": "user", "content": [result]},
        ]
    else:
        output = [
            {"type": "input_text", "text": "key " + VALUE},
            {"type": "input_image", "image_url": uri},
            {"type": "input_file", "file_data": encoded},
        ]
        messages = [
            user(protocol, "take a screenshot"),
            {"type": "function_call", "call_id": "c", "name": "shot", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c", "output": output},
        ]
    response = await client.post(
        PATHS[protocol], headers=auth(key), json=request(protocol, messages)
    )
    assert response.status_code == 200, response.text
    texts = [t["text"] for t in sent_texts(fake)]
    assert "key " + VALUE in texts
    assert not any(encoded in t or uri in t for t in texts)
    upstream = json.dumps(model_bodies(captured)[-1])
    assert VALUE not in upstream and reference in upstream
    assert encoded in upstream and uri in upstream


# Tokens and the history that holds them, for enroll_tool_secrets.
OLD, TOOL_OUT = "ghp_OLDTOKEN1234567890", "ghp_TOOLTOKEN123456789"
HARNESS, ASSISTANT = "ghp_HARNESS1234567890", "ghp_ASSISTANT12345678"
SYSTEM, DEFINITION = "ghp_SYSTEMPROMPT1234", "ghp_TOOLDEFINITION12"
REMINDER = "<system-reminder>env " + HARNESS + "</system-reminder>"


def full_history(protocol):
    """Every kind of content: user text, attached blocks and tool results in two turns,
    assistant text, the system prompt and a tool definition."""
    latest = [{"type": "text", "text": REMINDER}, {"type": "text", "text": "use " + TOKEN}]
    if protocol == "anthropic":
        messages = [
            {"role": "user", "content": "first " + OLD},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "assistant " + ASSISTANT},
                    {"type": "tool_use", "id": "t", "name": "sh", "input": {"c": "x"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t", "content": "out " + TOOL_OUT}
                ],
            },
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": latest},
        ]
        tools = [
            {"name": "sh", "description": "tool " + DEFINITION, "input_schema": {"type": "object"}}
        ]
        return {**request("anthropic", messages, tools=tools), "system": "system " + SYSTEM}
    if protocol == "chat":
        call = {"id": "c", "type": "function", "function": {"name": "sh", "arguments": "{}"}}
        messages = [
            {"role": "system", "content": "system " + SYSTEM},
            {"role": "user", "content": "first " + OLD},
            {"role": "assistant", "content": "assistant " + ASSISTANT, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "c", "content": "out " + TOOL_OUT},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": latest},
        ]
        definition = {"name": "sh", "description": "tool " + DEFINITION, "parameters": {}}
        return request("chat", messages, tools=[{"type": "function", "function": definition}])
    items = [
        user("responses", "first " + OLD),
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "assistant " + ASSISTANT}],
        },
        {"type": "function_call", "call_id": "c", "name": "sh", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c", "output": "out " + TOOL_OUT},
        {"role": "user", "content": [{**part, "type": "input_text"} for part in latest]},
    ]
    tools = [{"type": "function", "name": "sh", "description": "tool " + DEFINITION}]
    body = request("responses", items, tools=[{**tools[0], "parameters": {}}])
    return {**body, "instructions": "system " + SYSTEM}


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_enroll_tool_secrets_enrolls_tool_output_in_every_turn(running_gateway, protocol):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin, enroll_tool_secrets=True)
    fake = privacy(app)
    response = await client.post(PATHS[protocol], headers=auth(key), json=full_history(protocol))
    assert response.status_code == 200, response.text
    assert set(fake.values) == {OLD, TOOL_OUT, HARNESS, TOKEN}
    enrolled = " ".join(t["text"] for t in sent_texts(fake) if t.get("enroll"))
    for never in (ASSISTANT, SYSTEM, DEFINITION):
        assert never not in enrolled
    upstream = json.dumps(model_bodies(captured)[-1])
    for value in (OLD, TOOL_OUT, HARNESS, TOKEN):
        assert value not in upstream and fake.values[value] in upstream
    for value in (ASSISTANT, SYSTEM, DEFINITION):
        assert value in upstream
    # The note names the token the user typed in the latest turn only.
    assert upstream.count("OpenViking stored a secret") == 1
    assert json.dumps(secret_note([fake.values[TOKEN]]))[1:-1] in upstream


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_without_enroll_tool_secrets_tool_output_is_not_enrolled(running_gateway, protocol):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    fake = privacy(app)
    response = await client.post(PATHS[protocol], headers=auth(key), json=full_history(protocol))
    assert response.status_code == 200, response.text
    assert set(fake.values) == {TOKEN}
    assert {t["text"] for t in sent_texts(fake) if t.get("enroll")} == {"use " + TOKEN}
    upstream = json.dumps(model_bodies(captured)[-1])
    assert TOOL_OUT in upstream and HARNESS in upstream and TOKEN not in upstream


async def test_side_request_enrolls(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    body = request("anthropic", [user("anthropic", "title for " + TOKEN)])
    response = await client.post(
        PATHS["anthropic"],
        headers=auth(key, **{"X-Claude-Code-Request-Class": "auxiliary"}),
        json=body,
    )
    assert response.status_code == 200, response.text
    assert TOKEN in privacy(app).values


async def test_note_is_injected_once_and_replayed(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    headers = auth(key, **{"X-OpenViking-Session": "note"})
    first = [user("anthropic", "here is my token " + TOKEN)]
    response = await client.post(
        PATHS["anthropic"], headers=headers, json=request("anthropic", first)
    )
    assert response.status_code == 200, response.text
    reference = privacy(app).values[TOKEN]
    note = secret_note([reference])
    one = model_bodies(captured)[-1]
    assert json.dumps(one).count(json.dumps(note)[1:-1]) == 1
    assert "Secrets are sealed" in json.dumps(one)
    second = [
        *first,
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        user("anthropic", "thanks, next step"),
    ]
    response = await client.post(
        PATHS["anthropic"], headers=headers, json=request("anthropic", second)
    )
    assert response.status_code == 200, response.text
    two = model_bodies(captured)[-1]
    assert two["messages"][0] == one["messages"][0]
    assert json.dumps(two).count(json.dumps(note)[1:-1]) == 1
    assert (await logs(client, admin))[1]["secrets_enrolled"] == 1


async def test_cache_sends_only_new_text_and_resends_on_revision(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin, recall=False)
    fake = privacy(app)
    first = [{"role": "user", "content": "the first question text"}]
    assert (
        await client.post(PATHS["chat"], headers=auth(key), json=request("chat", first))
    ).status_code == 200
    seen = len(fake.calls)
    second = [
        *first,
        {"role": "assistant", "content": "an earlier answer text"},
        {"role": "user", "content": "the second question text"},
    ]
    assert (
        await client.post(PATHS["chat"], headers=auth(key), json=request("chat", second))
    ).status_code == 200
    texts = [t["text"] for call in fake.calls[seen:] for t in call]
    assert "the second question text" in texts
    assert "the first question text" not in texts
    seen = len(fake.calls)
    fake.register(VALUE, "rotated")
    assert (
        await client.post(PATHS["chat"], headers=auth(key), json=request("chat", second))
    ).status_code == 200
    texts = [t["text"] for call in fake.calls[seen:] for t in call]
    assert "the first question text" in texts
    record = (await logs(client, admin))[1]
    assert record["seal_cache_hits"] >= 1


async def test_egress_seals_recalled_text(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")

    async def handler(request):
        if request.path == "/api/v1/search/search":
            entry = {"uri": "viking://user/alice/memories/k", "text": "key " + VALUE}
            rendered = entry["uri"] + "\nkey " + VALUE
            return web.json_response(
                {"status": "ok", "result": {"entries": [entry], "rendered": rendered}}
            )
        return None

    app.state.test_backend["handler"] = handler
    response = await client.post(
        PATHS["anthropic"],
        headers=auth(key, **{"X-OpenViking-Session": "egress"}),
        json=request("anthropic", [user("anthropic", "what is my deploy key?")]),
    )
    assert response.status_code == 200, response.text
    upstream = json.dumps(model_bodies(captured)[-1])
    assert VALUE not in upstream and reference in upstream
    # The user's text holds no secret: what was sealed is what the gateway added.
    assert (await logs(client, admin))[0]["secrets_sealed"] >= 1


# Unsealing.


def tool_reply(protocol, name, arguments, text, stream):
    """The upstream's reply: assistant ``text`` and one client call with JSON ``arguments``."""
    if protocol == "chat":
        message = {"role": "assistant", "content": text}
        call = {"id": "call_1", "type": "function", "function": {"name": name}}
        if not stream:
            call["function"]["arguments"] = arguments
            return {
                "id": "c1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {**message, "tool_calls": [call]},
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        half = len(arguments) // 2
        return [
            {"id": "c1", "choices": [{"index": 0, "delta": message, "finish_reason": None}]},
            {
                "id": "c1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    **call,
                                    "function": {"name": name, "arguments": arguments[:half]},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "c1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": arguments[half:]}}
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            None,
        ]
    if protocol == "anthropic":
        blocks = [
            {"type": "text", "text": text},
            {"type": "tool_use", "id": "t1", "name": name, "input": json.loads(arguments)},
        ]
        value = {
            "id": "m1",
            "type": "message",
            "role": "assistant",
            "model": "model",
            "content": blocks,
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        if not stream:
            return value
        raw = orjson.dumps(blocks[1]["input"]).decode()
        half = len(raw) // 2
        return [
            {"type": "message_start", "message": {**value, "content": [], "stop_reason": None}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {**blocks[1], "input": {}},
            },
            *(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": part},
                }
                for part in (raw[:half], raw[half:])
            ),
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
    message = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    item = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }
    value = {
        "id": "r1",
        "object": "response",
        "model": "model",
        "status": "completed",
        "output": [message, item],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    if not stream:
        return value
    half = len(arguments) // 2
    return [
        {"type": "response.created", "response": {**value, "status": "in_progress", "output": []}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**message, "content": []},
        },
        {"type": "response.output_item.done", "output_index": 0, "item": message},
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {**item, "arguments": ""},
        },
        *(
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 1,
                "item_id": "fc_1",
                "delta": part,
            }
            for part in (arguments[:half], arguments[half:])
        ),
        {
            "type": "response.function_call_arguments.done",
            "output_index": 1,
            "item_id": "fc_1",
            "arguments": arguments,
        },
        {"type": "response.output_item.done", "output_index": 1, "item": item},
        {"type": "response.completed", "response": value},
    ]


def answer_with(app, protocol, name, arguments, text, stream, bodies=None):
    async def handler(request):
        if request.path != PATHS[protocol]:
            return None
        if bodies is not None:
            bodies.append(await request.read())
        reply = tool_reply(protocol, name, arguments, text, stream)
        if not stream:
            return web.json_response(reply)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for event in reply:
            data = b"[DONE]" if event is None else orjson.dumps(event)
            await response.write(b"data: " + data + b"\n\n")
        await response.write_eof()
        return response

    app.state.test_backend["handler"] = handler


def client_view(protocol, response, stream):
    """The assistant text and the call's arguments, as a JSON string, the client got."""
    if not stream:
        value = response.json()
        if protocol == "chat":
            message = value["choices"][0]["message"]
            return message["content"], message["tool_calls"][0]["function"]["arguments"]
        if protocol == "anthropic":
            text = next(b["text"] for b in value["content"] if b["type"] == "text")
            call = next(b for b in value["content"] if b["type"] == "tool_use")
            return text, json.dumps(call["input"])
        items = value["output"]
        text = next(i for i in items if i["type"] == "message")["content"][0]["text"]
        return text, next(i for i in items if i["type"] == "function_call")["arguments"]
    events = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
    events = [e for e in events if e]
    if protocol == "chat":
        text = "".join(
            c["delta"].get("content") or "" for e in events for c in e.get("choices") or []
        )
        arguments = "".join(
            t["function"].get("arguments", "")
            for e in events
            for c in e.get("choices") or []
            for t in c["delta"].get("tool_calls") or []
        )
        return text, arguments
    if protocol == "anthropic":
        text = "".join(
            e["delta"].get("text", "") for e in events if e["type"] == "content_block_delta"
        )
        arguments = "".join(
            e["delta"].get("partial_json", "") for e in events if e["type"] == "content_block_delta"
        )
        return text, arguments
    deltas = "".join(
        e["delta"] for e in events if e["type"] == "response.function_call_arguments.delta"
    )
    final = next(e for e in events if e["type"] == "response.completed")["response"]
    call = next(i for i in final["output"] if i["type"] == "function_call")
    assert deltas == call["arguments"]
    done = next(e for e in events if e["type"] == "response.function_call_arguments.done")
    assert done["arguments"] == call["arguments"]
    text = next(i for i in final["output"] if i["type"] == "message")["content"][0]["text"]
    return text, call["arguments"]


def shell_tools(protocol, name="shell"):
    if protocol == "chat":
        return [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}]
    if protocol == "anthropic":
        return [{"name": name, "input_schema": {"type": "object"}}]
    return [{"type": "function", "name": name, "parameters": {"type": "object"}}]


async def call_tool(client, key, protocol, stream, name="shell"):
    body = request(protocol, [user(protocol, "list my repos")], stream, shell_tools(protocol, name))
    return await client.post(PATHS[protocol], headers=auth(key), json=body)


# Two references in one string and one in another; ``secrets_unsealed`` counts the
# distinct references unsealed, the same on the relay and in the tool loop.
@pytest.mark.parametrize(
    ("path", "category"), [("relay", "credential"), ("tool_loop", "credential"), ("relay", "auto")]
)
@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
@pytest.mark.parametrize("stream", [False, True])
async def test_tool_arguments_are_unsealed(running_gateway, protocol, stream, path, category):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin, gateway_tools=path == "tool_loop")
    reference = privacy(app).register(VALUE, "github-k3j2m8", category=category)
    assert f"/privacy/{category}/" in reference
    arguments = json.dumps({"command": f"curl -u {reference}:{reference} x", "token": reference})
    answer_with(app, protocol, "shell", arguments, "I will use " + reference, stream)
    response = await call_tool(client, key, protocol, stream)
    assert response.status_code == 200, response.text
    text, sent = client_view(protocol, response, stream)
    assert json.loads(sent) == {"command": f"curl -u {VALUE}:{VALUE} x", "token": VALUE}
    # Assistant text is never unsealed.
    assert reference in text and VALUE not in text
    record = (await logs(client, admin))[0]
    assert ("hidden_rounds" in record) == (path == "tool_loop")
    assert record["secrets_unsealed"] == 1


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_openviking_tools_and_ov_privacy_commands_keep_references(running_gateway, protocol):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    arguments = json.dumps({"values": {"token": reference}})
    answer_with(app, protocol, "mcp__openviking__privacy_upsert", arguments, "ok", False)
    response = await call_tool(client, key, protocol, False, "mcp__openviking__privacy_upsert")
    assert response.status_code == 200, response.text
    assert json.loads(client_view(protocol, response, False)[1]) == {"values": {"token": reference}}
    command = {"command": f"ov privacy run --env T='{reference}' -- curl x", "other": reference}
    answer_with(app, protocol, "shell", json.dumps(command), "ok", False)
    response = await call_tool(client, key, protocol, False)
    assert json.loads(client_view(protocol, response, False)[1]) == {
        "command": command["command"],
        "other": VALUE,
    }


async def test_values_that_do_not_seal_back_and_missing_ones_stay_references(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    fake = privacy(app)
    short = fake.register("abc12", "short")
    gone = privacy_reference("renamed-away")
    other = privacy_reference("github", owner="bob")
    arguments = json.dumps({"a": short, "b": gone, "c": other})
    answer_with(app, "chat", "shell", arguments, "ok", False)
    response = await call_tool(client, key, "chat", False)
    assert response.status_code == 200, response.text
    assert client_view("chat", response, False)[1] == arguments
    assert other not in fake.resolves


async def test_resolve_failure_closes_the_billed_call(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    fake = privacy(app)
    reference = fake.register(VALUE, "github")
    fake.resolve_failure = 500
    answer_with(app, "chat", "shell", json.dumps({"c": reference}), "ok", False)
    response = await call_tool(client, key, "chat", False)
    assert response.status_code == 502
    assert response.json()["reason"] == "secret_unseal_failed"
    # The upstream answered in full, so its call is closed with the status it gave.
    [row] = (await client.get("/admin/logs", headers=admin)).json()
    [call] = row["upstream_calls"]
    assert call["status"] == 200 and call["error"] == "secret_unseal_failed"
    assert call["complete"] is False and "finished_at" in call


async def test_next_turn_seals_back_to_the_models_bytes(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    original = '{"command" :  "curl -H \'Bearer ' + reference + "' x\"}"
    bodies = []
    answer_with(app, "chat", "shell", original, "running", False, bodies)
    headers = auth(key, **{"X-OpenViking-Session": "roundtrip"})
    first = [{"role": "user", "content": "list my repos"}]
    tools = shell_tools("chat")
    response = await client.post(
        PATHS["chat"], headers=headers, json=request("chat", first, tools=tools)
    )
    assert response.status_code == 200, response.text
    message = response.json()["choices"][0]["message"]
    assert VALUE in message["tool_calls"][0]["function"]["arguments"]
    second = [*first, message, {"role": "tool", "tool_call_id": "call_1", "content": "[]"}]
    response = await client.post(
        PATHS["chat"], headers=headers, json=request("chat", second, tools=tools)
    )
    assert response.status_code == 200, response.text
    upstream = orjson.loads(bodies[-1])
    assistant = next(m for m in upstream["messages"] if m.get("tool_calls"))
    assert assistant["tool_calls"][0]["function"]["arguments"] == original
    assert VALUE.encode() not in bodies[-1]


# Revision 2 of the contract: the note follows `temporary`, sessions survive a rename,
# replies the kernel did not prepare keep the upstream's identity.


def test_gateway_text_holds_no_reference_to_copy():
    assert not REFERENCE.search(SESSION_NOTE) and not REFERENCE.search(REPLACED_NOTE)
    assert "/privacy/auto/" in SESSION_NOTE and "safe to share" in SESSION_NOTE
    assert "do not mask, cut or re-encode" in SESSION_NOTE
    assert "files on disk" in REPLACED_NOTE
    note = secret_note([privacy_reference("github-0001", category="auto")])
    assert REFERENCE.findall(note) == [("alice", "github-0001", "token")] * 2


async def test_note_follows_temporary_after_count_tokens_enrolled(running_gateway):
    """Another replica, or a count first, enrolled the token: the main turn still notes it."""
    app, client, admin, key, captured, _ = running_gateway
    app.state.sealer.cache.max_bytes = 0
    await profile(client, admin)
    headers = auth(key, **{"X-OpenViking-Session": "counted"})
    body = request("anthropic", [user("anthropic", "here is my token " + TOKEN)])
    response = await client.post("/v1/messages/count_tokens", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert "OpenViking stored a secret" not in captured[-1][1].decode()
    response = await client.post(PATHS["anthropic"], headers=headers, json=body)
    assert response.status_code == 200, response.text
    note = json.dumps(secret_note([privacy(app).values[TOKEN]]))[1:-1]
    assert json.dumps(model_bodies(captured)[-1]).count(note) == 1
    main, counted = (await logs(client, admin))[:2]
    assert counted["secrets_enrolled"] == 1 and not main.get("secrets_enrolled")


async def test_enrolled_counts_each_reference_once_and_never_from_cache(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    body = request("chat", [user("chat", f"token {TOKEN} and again {TOKEN}")])
    for _ in range(2):
        response = await client.post(PATHS["chat"], headers=auth(key), json=body)
        assert response.status_code == 200, response.text
        assert TOKEN not in json.dumps(model_bodies(captured)[-1])
    second, first = (await logs(client, admin))[:2]
    assert first["secrets_enrolled"] == 1 and not second.get("secrets_enrolled")


async def test_plugin_session_note_on_every_request_of_the_turn(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    fake = privacy(app)
    headers = auth(key, **{"X-OpenViking-Session": "plugin-turn", "X-OpenViking-Plugin": "claude"})
    tools = shell_tools("anthropic")
    first = [user("anthropic", "token " + TOKEN)]
    response = await client.post(
        PATHS["anthropic"], headers=headers, json=request("anthropic", first, tools=tools)
    )
    assert response.status_code == 200, response.text
    temporary = fake.values[TOKEN]
    note = json.dumps(secret_note([temporary]))[1:-1]
    one = model_bodies(captured)[-1]["messages"]
    assert json.dumps(one[-1]).count(note) == 1
    call = {"type": "tool_use", "id": "t1", "name": "shell", "input": {"c": "ls"}}
    result = {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
    second = [
        *first,
        {"role": "assistant", "content": [call]},
        {"role": "user", "content": [result]},
    ]
    response = await client.post(
        PATHS["anthropic"], headers=headers, json=request("anthropic", second, tools=tools)
    )
    assert response.status_code == 200, response.text
    # Stateless and identical on every request of the turn.
    assert model_bodies(captured)[-1]["messages"][0] == one[-1]
    fake.rename(temporary, "github")
    third = [
        *second,
        {"role": "assistant", "content": [{"type": "text", "text": "stored"}]},
        user("anthropic", "go on please"),
    ]
    response = await client.post(
        PATHS["anthropic"], headers=headers, json=request("anthropic", third, tools=tools)
    )
    assert response.status_code == 200, response.text
    upstream = json.dumps(model_bodies(captured)[-1])
    assert "OpenViking stored a secret" not in upstream and TOKEN not in upstream
    assert privacy_reference("github") in upstream


async def test_unseal_works_when_the_message_is_just_the_new_token(running_gateway):
    """The enroll result in the cache must not stop the round-trip check."""
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    fake = privacy(app)

    async def handler(request):
        if request.path != PATHS["chat"]:
            return None
        arguments = json.dumps({"command": "git clone https://" + fake.values[TOKEN] + "@x"})
        return web.json_response(tool_reply("chat", "shell", arguments, "cloning", False))

    app.state.test_backend["handler"] = handler
    body = request("chat", [user("chat", TOKEN)], tools=shell_tools("chat"))
    response = await client.post(PATHS["chat"], headers=auth(key), json=body)
    assert response.status_code == 200, response.text
    _, sent = client_view("chat", response, False)
    assert json.loads(sent) == {"command": f"git clone https://{TOKEN}@x"}


async def test_renaming_the_temporary_entry_keeps_the_session(running_gateway):
    """Turn 1 enrolls a token and a hidden privacy_upsert renames it; turn 2 continues."""
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin, gateway_tools=True)
    fake = privacy(app)
    schema = {"category": {"type": "string"}, "target_key": {"type": "string"}}
    schema["values"] = {"type": "object"}
    app.state.test_backend["catalog"] = [*MCP_TOOLS, mcp_tool("privacy_upsert", schema)]
    upstream = []

    async def handler(request):
        if request.path == "/mcp":
            arguments = (await request.json())["params"]["arguments"]
            saved = fake.rename(arguments["values"]["token"], arguments["target_key"])
            text = "saved " + saved
            return web.json_response(
                {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}
            )
        if request.path != PATHS["chat"]:
            return None
        payload = await request.json()
        upstream.append(payload)
        if len(upstream) == 1:
            arguments = {"target_key": "github", "values": {"token": fake.values[TOKEN]}}
            call = {
                "id": "gateway-1",
                "type": "function",
                "function": {
                    "name": "openviking_privacy_upsert",
                    "arguments": json.dumps(arguments),
                },
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
            finish = "tool_calls"
        else:
            message, finish = {"role": "assistant", "content": "Saved it as github."}, "stop"
        return web.json_response(
            {"id": "c1", "choices": [{"index": 0, "message": message, "finish_reason": finish}]}
        )

    app.state.test_backend["handler"] = handler
    first = [{"role": "user", "content": "here is my token " + TOKEN}]
    response = await client.post(PATHS["chat"], headers=auth(key), json=request("chat", first))
    assert response.status_code == 200, response.text
    assert len(upstream) == 2 and TOKEN in fake.values
    assert fake.values[TOKEN] == privacy_reference("github")
    reply = response.json()["choices"][0]["message"]
    second = [*first, reply, {"role": "user", "content": "thanks, what next?"}]
    response = await client.post(PATHS["chat"], headers=auth(key), json=request("chat", second))
    assert response.status_code == 200, response.text
    latest = (await logs(client, admin))[0]
    assert latest["session_source"] != "new"
    turn = json.dumps(upstream[-1])
    # The recorded injection and the hidden round replay; no second session start.
    assert turn.count("Secrets are sealed") == 1
    assert turn.count("OpenViking stored a secret") == 1
    assert "openviking_privacy_upsert" in turn
    assert TOKEN not in turn and privacy_reference("github") in turn


def stream_response(events):
    async def write(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for event in events:
            data = b"[DONE]" if event is None else orjson.dumps(event)
            name = (
                b""
                if event is None or "type" not in event
                else b"event: %s\n" % (event["type"].encode())
            )
            await response.write(name + b"data: " + data + b"\n\n")
        await response.write_eof()
        return response

    return write


@pytest.mark.parametrize("stream", [False, True])
async def test_responses_relay_keeps_the_upstream_identity(running_gateway, stream):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    arguments = json.dumps({"command": f"curl -H 'Bearer {reference}' x"})
    reply = tool_reply("responses", "shell", arguments, "ok", stream)

    async def handler(request):
        if request.path != PATHS["responses"]:
            return None
        return web.json_response(reply) if not stream else await stream_response(reply)(request)

    app.state.test_backend["handler"] = handler
    body = request("responses", [user("responses", "list my repos")], stream, shell_tools("x"))
    body.pop("store")
    response = await client.post(PATHS["responses"], headers=auth(key), json=body)
    assert response.status_code == 200, response.text
    _, sent = client_view("responses", response, stream)
    assert json.loads(sent) == {"command": f"curl -H 'Bearer {VALUE}' x"}
    if not stream:
        assert response.json()["id"] == "r1" and response.json()["status"] == "completed"
    else:
        events = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
        assert [e["type"] for e in events] == [e["type"] for e in reply]
        assert {e["response"]["id"] for e in events if "response" in e} == {"r1"}
    record = (await logs(client, admin))[0]
    assert record["secrets_unsealed"] == 1 and record["status"] == 200


async def test_responses_stream_cut_short_unseals_the_deltas(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    arguments = json.dumps({"command": f"curl -H 'Bearer {reference}' x"})
    events = tool_reply("responses", "shell", arguments, "ok", True)
    # The call never gets its own done events; the reply ends incomplete.
    reply = [
        e for e in events[:-1] if not (e.get("output_index") == 1 and e["type"].endswith(".done"))
    ]
    terminal = {**events[-1]["response"], "status": "incomplete"}
    reply.append({"type": "response.incomplete", "response": terminal})

    async def handler(request):
        if request.path != PATHS["responses"]:
            return None
        return await stream_response(reply)(request)

    app.state.test_backend["handler"] = handler
    body = request("responses", [user("responses", "list my repos")], True, shell_tools("x"))
    response = await client.post(PATHS["responses"], headers=auth(key), json=body)
    assert response.status_code == 200, response.text
    sent = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
    deltas = "".join(
        e["delta"] for e in sent if e["type"] == "response.function_call_arguments.delta"
    )
    final = next(e for e in sent if e["type"] == "response.incomplete")["response"]
    call = next(i for i in final["output"] if i["type"] == "function_call")
    unsealed = {"command": f"curl -H 'Bearer {VALUE}' x"}
    assert json.loads(deltas) == json.loads(call["arguments"]) == unsealed


async def test_responses_failed_event_reaches_the_client(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    failed = {
        "type": "response.failed",
        "response": {
            "id": "r1",
            "object": "response",
            "status": "failed",
            "error": {"code": "rate_limit_exceeded", "message": "slow down"},
            "output": [],
        },
    }
    created = {"type": "response.created", "response": {**failed["response"], "status": "queued"}}

    async def handler(request):
        if request.path != PATHS["responses"]:
            return None
        return await stream_response([created, failed])(request)

    app.state.test_backend["handler"] = handler
    body = request("responses", [user("responses", "list my repos")], True, shell_tools("x"))
    body.pop("store")
    response = await client.post(PATHS["responses"], headers=auth(key), json=body)
    assert response.status_code == 200, response.text
    events = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
    assert events == [created, failed]
    record = (await logs(client, admin))[0]
    assert record["upstream_error_code"] == "rate_limit_exceeded"


async def test_plugin_session_streams_text_before_a_call_arrives(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    arguments = json.dumps({"command": f"curl -H 'Bearer {reference}' x"})
    events = tool_reply("anthropic", "shell", arguments, "Let me check.", True)
    split = next(i for i, e in enumerate(events) if e.get("index") == 1)
    seen, waited = asyncio.Event(), []

    async def handler(request):
        if request.path != PATHS["anthropic"]:
            return None
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for number, event in enumerate(events):
            if number == split:
                try:
                    await asyncio.wait_for(seen.wait(), 5)
                    waited.append(True)
                except asyncio.TimeoutError:
                    waited.append(False)
            await response.write(b"data: " + orjson.dumps(event) + b"\n\n")
        await response.write_eof()
        return response

    async def spy(scope, receive, send):
        async def forward(message):
            if message["type"] == "http.response.body" and b"Let me check." in message.get(
                "body", b""
            ):
                seen.set()
            await send(message)

        await app(scope, receive, forward)

    app.state.test_backend["handler"] = handler
    headers = auth(key, **{"X-OpenViking-Plugin": "claude"})
    body = request(
        "anthropic", [user("anthropic", "list my repos")], True, shell_tools("anthropic")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=spy, root_path="/gateway"),
        base_url="http://gateway/gateway",
    ) as spying:
        response = await spying.post(PATHS["anthropic"], headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert waited == [True]
    text, sent = client_view("anthropic", response, True)
    assert text == "Let me check."
    assert json.loads(sent) == {"command": f"curl -H 'Bearer {VALUE}' x"}


async def test_egress_seals_the_compaction_summary_request(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    response = await client.put(
        "/admin/upstreams/chat",
        headers=admin,
        json={
            "name": "chat",
            "protocol": "chat",
            "base_url": app.state.openviking_url + "/v1",
            "api_key": "model-secret",
            "models": ["model"],
            "context_windows": {"model": 1024},
        },
    )
    assert response.status_code == 200, response.text
    summaries = []

    async def handler(request):
        if request.path == "/api/v1/search/search":
            entry = {"uri": "viking://user/alice/memories/k", "text": "key " + VALUE}
            return web.json_response(
                {"status": "ok", "result": {"entries": [entry], "rendered": "key " + VALUE}}
            )
        if request.path != PATHS["chat"]:
            return None
        payload = await request.json()
        if text_content(payload["messages"][-1]).startswith(INSTRUCTION_START):
            summaries.append(payload)
            return web.json_response(summary_response("chat", "Blue cluster chosen."))
        return web.json_response(
            {
                "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 5},
            }
        )

    app.state.test_backend["handler"] = handler
    headers = auth(key, **{"X-OpenViking-Session": "sealed-compaction"})
    messages = [{"role": "user", "content": "How do I deploy?"}]
    body = {"model": "model", "messages": messages}
    assert (await client.post(PATHS["chat"], headers=headers, json=body)).status_code == 200
    messages += [{"role": "assistant", "content": "hello"}, {"role": "user", "content": "Next?"}]
    assert (await client.post(PATHS["chat"], headers=headers, json=body)).status_code == 200
    [summary] = summaries
    assert VALUE not in json.dumps(summary) and reference in json.dumps(summary)


async def test_egress_seals_a_hidden_tool_continuation(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin, gateway_tools=True)
    reference = privacy(app).register(VALUE, "github")
    upstream = []

    async def handler(request):
        if request.path == "/mcp":
            content = [{"type": "text", "text": "found key " + VALUE}]
            return web.json_response({"jsonrpc": "2.0", "id": 1, "result": {"content": content}})
        if request.path != PATHS["chat"]:
            return None
        upstream.append(await request.read())
        if len(upstream) == 1:
            call = {
                "id": "gateway-1",
                "type": "function",
                "function": {"name": "openviking_find", "arguments": '{"query": "key"}'},
            }
            message, finish = (
                {"role": "assistant", "content": None, "tool_calls": [call]},
                "tool_calls",
            )
        else:
            message, finish = {"role": "assistant", "content": "done"}, "stop"
        return web.json_response(
            {"id": "c1", "choices": [{"index": 0, "message": message, "finish_reason": finish}]}
        )

    app.state.test_backend["handler"] = handler
    body = request("chat", [user("chat", "find my deploy key")])
    response = await client.post(PATHS["chat"], headers=auth(key), json=body)
    assert response.status_code == 200, response.text
    assert len(upstream) == 2
    assert VALUE.encode() not in upstream[1] and reference.encode() in upstream[1]


async def test_sealing_turned_on_later_drops_privacy_resolve(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin, gateway_tools=True, seal_secrets=False)
    resolve = mcp_tool("privacy_resolve", {"reference": {"type": "string"}}, ("reference",))
    app.state.test_backend["catalog"] = [*MCP_TOOLS, resolve]
    headers = auth(key, **{"X-OpenViking-Session": "later"})

    def offered():
        return {t["function"]["name"] for t in model_bodies(captured)[-1].get("tools", [])}

    first = [user("chat", "first message here")]
    response = await client.post(PATHS["chat"], headers=headers, json=request("chat", first))
    assert response.status_code == 200, response.text
    assert {"openviking_find", "openviking_privacy_resolve"} <= offered()
    await profile(client, admin, gateway_tools=True)
    later = [*first, {"role": "assistant", "content": "ok"}, user("chat", "and now?")]
    response = await client.post(PATHS["chat"], headers=headers, json=request("chat", later))
    assert response.status_code == 200, response.text
    assert "openviking_find" in offered() and "openviking_privacy_resolve" not in offered()


async def test_more_than_1024_texts_are_split_into_calls(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    fake = privacy(app)
    reference = fake.register(VALUE, "github")
    messages = []
    for number in range(1100):
        role = "user" if number % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"message number {number}"})
    messages[-1] = {"role": "user", "content": "last one " + VALUE}
    response = await client.post(PATHS["chat"], headers=auth(key), json=request("chat", messages))
    assert response.status_code == 200, response.text
    sizes = [len(call) for call in fake.calls]
    assert max(sizes) == 1024 and sum(sizes) >= 1100
    upstream = json.dumps(model_bodies(captured)[-1])
    assert VALUE not in upstream and reference in upstream


def test_size_limit_splits_calls(monkeypatch):
    monkeypatch.setattr(sealing, "MAX_BYTES", 64)
    units = [("a" * 40, False), ("b" * 40, True), ("c" * 20, False)]
    assert [len(batch) for batch in sealing.batches(units)] == [1, 2]


async def test_rejects_a_text_too_large(running_gateway, monkeypatch):
    _, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    monkeypatch.setattr(sealing, "MAX_BYTES", 64)
    response = await client.post(
        PATHS["chat"], headers=auth(key), json=request("chat", [user("chat", "d" * 65)])
    )
    assert response.status_code == 400
    assert response.json()["reason"] == "secret_seal_invalid_request"
    assert not model_bodies(captured)


# Revision 5 of the contract: the short text for sessions without the session text.

SHORT = json.dumps(REPLACED_NOTE)[1:-1]


def read_env_history(content):
    call = {"type": "tool_use", "id": "t1", "name": "shell", "input": {"c": "cat .env"}}
    result = {"type": "tool_result", "tool_use_id": "t1", "content": content}
    return [
        user("anthropic", "read my env file"),
        {"role": "assistant", "content": [call]},
        {"role": "user", "content": [result]},
    ]


async def test_plugin_session_gets_short_text_while_a_value_is_replaced(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    headers = auth(key, **{"X-OpenViking-Session": "short", "X-OpenViking-Plugin": "claude"})
    tools = shell_tools("anthropic")
    first = read_env_history("GH=" + VALUE)
    second = [
        *first,
        {"role": "assistant", "content": [{"type": "text", "text": "it holds GH"}]},
        user("anthropic", "thanks"),
    ]
    for messages in (first, second):
        body = request("anthropic", messages, tools=tools)
        response = await client.post(PATHS["anthropic"], headers=headers, json=body)
        assert response.status_code == 200, response.text
        upstream = json.dumps(model_bodies(captured)[-1])
        assert VALUE not in upstream and reference in upstream
        assert upstream.count(SHORT) == 1 and "Secrets are sealed" not in upstream
    # Next to the latest user text, like the note.
    assert SHORT in json.dumps(model_bodies(captured)[-1]["messages"][-1])


async def test_short_text_comes_before_the_note(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    headers = auth(key, **{"X-OpenViking-Plugin": "claude"})
    body = request("anthropic", [user("anthropic", "token " + TOKEN)])
    response = await client.post(PATHS["anthropic"], headers=headers, json=body)
    assert response.status_code == 200, response.text
    upstream = json.dumps(model_bodies(captured)[-1]["messages"][-1])
    note = json.dumps(secret_note([privacy(app).values[TOKEN]]))[1:-1]
    assert upstream.index(SHORT) < upstream.index(note)


async def test_plugin_session_without_replacement_gets_no_short_text(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    headers = auth(key, **{"X-OpenViking-Plugin": "claude"})
    for messages in (
        read_env_history("nothing secret"),
        read_env_history("GH=" + reference),
        [user("anthropic", "token " + reference)],
    ):
        body = request("anthropic", messages, tools=shell_tools("anthropic"))
        response = await client.post(PATHS["anthropic"], headers=headers, json=body)
        assert response.status_code == 200, response.text
        upstream = json.dumps(model_bodies(captured)[-1])
        assert "OpenViking replaced secret values" not in upstream
        assert "OpenViking stored a secret" not in upstream


async def test_count_tokens_and_side_requests_get_no_short_text(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    privacy(app).register(VALUE, "github")
    body = request("anthropic", read_env_history("GH=" + VALUE), tools=shell_tools("anthropic"))
    for path, extra in (
        ("/v1/messages/count_tokens", {}),
        (PATHS["anthropic"], {"X-Claude-Code-Request-Class": "auxiliary"}),
    ):
        headers = auth(key, **{"X-OpenViking-Plugin": "claude", **extra})
        response = await client.post(path, headers=headers, json=body)
        assert response.status_code == 200, response.text
        sent = captured[-1][1].decode()
        assert VALUE not in sent and "OpenViking replaced secret values" not in sent


async def test_kernel_session_gets_session_text_not_short_text(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    privacy(app).register(VALUE, "github")
    headers = auth(key, **{"X-OpenViking-Session": "kernel-short"})
    body = request("anthropic", [user("anthropic", "check GH=" + VALUE)])
    response = await client.post(PATHS["anthropic"], headers=headers, json=body)
    assert response.status_code == 200, response.text
    upstream = json.dumps(model_bodies(captured)[-1])
    assert VALUE not in upstream and json.dumps(SESSION_NOTE)[1:-1] in upstream
    assert "OpenViking replaced secret values" not in upstream


# CONTRACT §12: what cannot be sealed or unsealed fails; nothing goes on unsealed.


def set_version(app, version=None, status=200):
    """The fake OpenViking's ``/health`` from now on; the gateway forgets what it read."""
    app.state.test_backend["version"] = version
    app.state.test_backend["health_status"] = {"user-key": status}
    app.state.viking.identities.clear()


@pytest.mark.parametrize(
    ("version", "status", "detail"),
    [("0.5.6", 200, "OpenViking 0.5.6 cannot seal"), ("0.5.7", 500, "could not be read")],
    ids=["old-server", "health-failing"],
)
async def test_unsupported_server_is_not_asked_to_seal(running_gateway, version, status, detail):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    fake = privacy(app)
    fake.register(VALUE, "github")
    set_version(app, version, status)
    body = request("chat", [user("chat", "hello " + VALUE)])
    response = await client.post(PATHS["chat"], headers=auth(key), json=body)
    assert response.status_code == 503
    assert response.json()["reason"] == "secret_seal_failed"
    # The message names the case and what to do about it.
    assert detail in response.text and "seal_secrets" in response.text
    if version == "0.5.6":
        assert "0.5.7 or later" in response.text
    assert not model_bodies(captured) and not fake.calls
    assert not app.state.sealer.cache.entries


@pytest.mark.parametrize("failure", [500, 404, "malformed", "timeout"])
async def test_seal_failure_rejects_the_request(running_gateway, failure):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    app.state.sealer.timeout = 0.3
    fake = privacy(app)
    fake.register(VALUE, "github")
    fake.failure = failure
    body = request("chat", [user("chat", "hello " + VALUE)], tools=shell_tools("chat"))
    response = await client.post(PATHS["chat"], headers=auth(key), json=body)
    assert response.status_code == 503
    assert response.json()["reason"] == "secret_seal_failed"
    assert not model_bodies(captured)
    assert not app.state.sealer.cache.entries


async def test_one_failed_call_fails_the_whole_pass(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin, recall=False)
    fake = privacy(app)
    fake.register(VALUE, "github")
    handle = fake.handle

    async def second_batch_fails(request):
        texts = (await request.json())["texts"]
        if any("last one" in t["text"] for t in texts):
            return web.json_response({"status": "error"}, status=500)
        return await handle(request)

    fake.handle = second_batch_fails
    messages = [
        {"role": "user" if n % 2 == 0 else "assistant", "content": f"text {n} " + VALUE}
        for n in range(1100)
    ]
    messages[-1] = {"role": "user", "content": "last one " + VALUE}
    response = await client.post(PATHS["chat"], headers=auth(key), json=request("chat", messages))
    assert response.status_code == 503
    assert not model_bodies(captured)
    # The batch that did answer is not cached either.
    assert not app.state.sealer.cache.entries


async def test_unparseable_and_compressed_bodies_are_rejected(running_gateway):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    privacy(app).register(VALUE, "github")
    duplicate = (
        b'{"model":"model","model":"model","messages":[{"role":"user","content":"hi '
        + VALUE.encode()
        + b'"}]}'
    )
    compressed = gzip.compress(orjson.dumps(request("chat", [user("chat", "hi " + VALUE)])))
    for content, encoding in ((duplicate, {}), (compressed, {"Content-Encoding": "gzip"})):
        headers = auth(key, **{"Content-Type": "application/json", **encoding})
        response = await client.post(PATHS["chat"], headers=headers, content=content)
        assert response.status_code == 400
        assert response.json()["reason"] == "secret_seal_invalid_request"
    assert not captured
    assert not privacy(app).calls


@pytest.mark.parametrize("shape", ["unknown_endpoint", "background", "legacy_functions"])
async def test_requests_whose_calls_cannot_be_unsealed_are_rejected(running_gateway, shape):
    app, client, admin, key, captured, _ = running_gateway
    await profile(client, admin)
    privacy(app).register(VALUE, "github")
    if shape == "unknown_endpoint":
        path = "/v1/completions"
        body = {"model": "model", "prompt": "x " + VALUE, "tools": [{"type": "function"}]}
    elif shape == "legacy_functions":
        path = PATHS["chat"]
        body = request("chat", [user("chat", "hi " + VALUE)])
        body["functions"] = [{"name": "shell", "parameters": {}}]
    else:
        path = PATHS["responses"]
        body = request("responses", [user("responses", "hi " + VALUE)], tools=shell_tools("x"))
        body.pop("store")
        body["background"] = True
    response = await client.post(path, headers=auth(key), json=body)
    assert response.status_code == 400
    assert response.json()["reason"] == "secret_seal_invalid_request"
    assert not captured
    assert not privacy(app).calls


@pytest.mark.parametrize("path", ["relay", "tool_loop"])
@pytest.mark.parametrize("stream", [False, True])
async def test_resolve_failure_fails_the_reply(running_gateway, stream, path):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin, gateway_tools=path == "tool_loop")
    fake = privacy(app)
    reference = fake.register(VALUE, "github")
    fake.resolve_failure = 500
    answer_with(app, "chat", "shell", json.dumps({"c": reference}), "ok", stream)
    response = await call_tool(client, key, "chat", stream)
    assert response.status_code == (200 if stream else 502)
    assert "could not resolve a secret reference" in response.text
    assert reference not in response.text


async def test_unreadable_stream_fails_the_reply(running_gateway, monkeypatch):
    app, client, admin, key, _, _ = running_gateway
    await profile(client, admin)
    reference = privacy(app).register(VALUE, "github")
    arguments = json.dumps({"c": reference})

    async def handler(request):
        if request.path != PATHS["chat"]:
            return None
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for event in tool_reply("chat", "shell", arguments, "ok", True):
            data = b"[DONE]" if event is None else orjson.dumps(event)
            await response.write(b"data: " + data + b"\n\n")
            # One event per chunk, so frames are held before the reply turns unreadable.
            await asyncio.sleep(0.02)
        await response.write_eof()
        return response

    app.state.test_backend["handler"] = handler
    original, seen = ResponseCapture.events, []

    def events(self, values):
        # The reply becomes unreadable once a call has started and frames are held.
        seen.append(values)
        if any(sealing.opens_call("chat", v) for batch in seen[:-1] for v in batch):
            raise ValueError("unreadable")
        return original(self, values)

    monkeypatch.setattr(ResponseCapture, "events", events)
    response = await call_tool(client, key, "chat", True)
    assert response.status_code == 200, response.text
    assert "secret_unseal_failed" in response.text and reference not in response.text


def test_parse_item_builds_from_spans_and_takes_flags_only_when_true():
    reference = privacy_reference("github", "token")
    original = "token ghp_abcdefghijklmnop end"
    span = {"start": 6, "end": 26, "reference": reference, "temporary": 1, "created": "yes"}
    created = set()
    sealed = sealing.parse_item(original, {"text": 7, "replaced": [span]}, created)
    assert sealed.text == f"token {reference} end"
    assert sealed.spans == ((6, 26, reference, False),)
    assert not created
    # No spans is no change, as in the plugins' shared core.
    assert sealing.parse_item(original, {"text": original, "replaced": []}, created) is None
