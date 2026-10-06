import asyncio
import json
from pathlib import Path

import orjson
import pytest
from aiohttp import web
from conftest import MCP_TOOLS

from context_gateway.protocols import normalize
from context_gateway.tool_loop import REFUSED


@pytest.mark.parametrize(
    ("path", "protocol"),
    [
        ("/v1/chat/completions", "chat"),
        ("/v1/messages", "anthropic"),
        ("/v1/responses", "responses"),
    ],
)
async def test_http_replay_and_client_visibility(running_gateway, path, protocol):
    _, client, _, key, seen, _ = running_gateway
    headers = {
        "Authorization": "Bearer " + key["key"],
        "X-OpenViking-Session": "chat-1",
        "anthropic-version": "2023-06-01",
    }
    field = "input" if protocol == "responses" else "messages"
    body = {
        "model": "model",
        "store": False,
        field: [{"role": "user", "content": "Remember blue?"}],
        "unknown": {"preserve": [1, 2]},
    }
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert "openviking-context" not in response.text
    original = orjson.loads(seen[-1][1])
    assert "openviking-context" in str(original[field])
    body[field] += [
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "Continue please"},
    ]
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert orjson.loads(seen[-1][1])[field][0] == original[field][0]
    assert seen[-1][2].get(
        "Authorization", seen[-1][2].get("x-api-key", seen[-1][2].get("X-Api-Key"))
    ) in {"Bearer model-secret", "model-secret"}


@pytest.mark.parametrize("client_name", ["claude-code", "codex-cli", "openai-python"])
@pytest.mark.parametrize("session_header", [True, False])
async def test_recorded_client_prefixes(running_gateway, client_name, session_header):
    app, client, admin, key, seen, writes = running_gateway
    fixture = json.loads((Path(__file__).parent / "fixtures" / f"{client_name}.json").read_text())
    field = "input" if client_name == "codex-cli" else "messages"
    first, second = (r["body"][field] for r in fixture["requests"])
    # The upstream returns the recorded reply, so the second request continues it.
    reply = second[len(first) : -1]
    recorded_reply = {
        "claude-code": {
            "role": "assistant",
            "content": reply[0]["content"],
            "stop_reason": "end_turn",
        },
        "codex-cli": {"id": "resp-1", "object": "response", "status": "completed", "output": reply},
        "openai-python": {"choices": [{"message": reply[0], "finish_reason": "stop"}]},
    }[client_name]

    async def handler(request):
        if request.path != fixture["requests"][0]["path"] or upstream:
            return None
        seen.append((request.path, await request.read(), dict(request.headers)))
        return web.json_response(recorded_reply)

    app.state.test_backend["handler"] = handler
    upstream = []
    for recorded in fixture["requests"]:
        headers = {"Authorization": "Bearer " + key["key"]}
        headers.update(
            {k: v for k, v in recorded["headers"].items() if session_header or "session" not in k}
        )
        # Keep the captured message representation, but use the local JSON mock
        # response. SSE framing is exercised separately and in live acceptance.
        body = {**recorded["body"], "stream": False}
        response = await client.post(recorded["path"], headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert "openviking-context" not in response.text
        forwarded = [raw for path, raw, _ in seen if path == recorded["path"]][-1]
        upstream.append(orjson.loads(forwarded))
    old, new = (body[field] for body in upstream)
    assert "openviking-context" in str(old)
    assert [normalize(m) for m in old] == [normalize(m) for m in new[: len(old)]]
    assert "openviking-context" not in str(fixture)
    for field_name in ("tools", "system", "instructions"):
        if field_name in fixture["requests"][1]["body"]:
            assert upstream[1][field_name] == fixture["requests"][1]["body"][field_name]

    # A client retry and simultaneous duplicate requests must reuse the same
    # injection and must not capture the confirmed first turn more than once.
    repeated = await asyncio.gather(
        *(client.post(recorded["path"], headers=headers, json=body) for _ in range(3))
    )
    assert all(r.status_code == 200 for r in repeated)
    forwarded = [raw for path, raw, _ in seen if path == recorded["path"]][-3:]
    assert all(orjson.loads(raw) == upstream[1] for raw in forwarded)
    for _ in range(20):
        await app.state.worker.once()
        if writes:
            break
        await asyncio.sleep(0.01)
    source_ids = [
        source_id
        for batch in writes
        for message in batch["messages"]
        for source_id in message["source_message_ids"]
    ]
    assert source_ids and len(source_ids) == len(set(source_ids))
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert logs[0]["replay_hits"] >= 1


@pytest.mark.parametrize("client_name", ["claude-code", "codex-cli", "openai-python"])
async def test_recorded_client_disabled_passthrough(running_gateway, client_name):
    _, client, admin, _, seen, _ = running_gateway
    fixture = json.loads((Path(__file__).parent / "fixtures" / f"{client_name}.json").read_text())
    await client.put(
        "/admin/policies/off",
        headers=admin,
        json={
            "name": "Off",
            "recall": False,
            "capture": False,
            "compaction": False,
            "gateway_tools": False,
        },
    )
    minted = await client.post(
        "/admin/keys",
        headers=admin,
        json={
            "name": "off",
            "openviking_key": "user-key",
            "policy_id": "off",
            "upstream_ids": ["chat", "anthropic", "responses"],
        },
    )
    assert minted.status_code == 200
    for recorded in fixture["requests"]:
        raw = json.dumps({**recorded["body"], "stream": False}, indent=2).encode()
        response = await client.post(
            recorded["path"],
            headers={"Authorization": "Bearer " + minted.json()["key"]},
            content=raw,
        )
        assert response.status_code == 200
        assert [data for path, data, _ in seen if path == recorded["path"]][-1] == raw


async def test_raw_passthrough_errors_sse_and_large_numbers(running_gateway):
    _, client, admin, key, seen, _ = running_gateway
    headers = {"Authorization": "Bearer " + key["key"]}
    await client.put(
        "/admin/policies/off",
        headers=admin,
        json={"name": "Off", "recall": False, "capture": False, "compaction": False},
    )
    raw = b'{ "model":"model", "messages":[{"role":"user","content":"hi"}], "arg":922337203685477580922 }'
    response = await client.post("/v1/chat/completions", headers=headers, content=raw)
    assert response.status_code == 200
    assert seen[-1][1] == raw
    body = {"model": "error", "messages": [{"role": "user", "content": "hello"}]}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 429 and response.headers["retry-after"] == "4"
    assert response.content == b'{"error":{"raw":"provider error"}}'
    response = await client.post(
        "/v1/chat/completions", headers=headers, json={**body, "model": "model", "stream": True}
    )
    assert response.headers["request-id"] == "stream-id"
    assert response.content.endswith(b"data: [DONE]\n\n")
    assert b"openviking-context" not in response.content
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert logs[0]["cached_tokens"] == 100
    assert "model-secret" not in str(logs) and "user-key" not in str(logs)


async def test_key_scope_revocation_and_root_rejection(running_gateway):
    _, client, admin, key, _, _ = running_gateway
    for secret, expected in [("root", 403), ("other", 403)]:
        response = await client.post(
            "/admin/keys",
            headers=admin,
            json={
                "name": "bad",
                "openviking_key": secret,
                "policy_id": "default",
                "upstream_ids": ["chat"],
            },
        )
        assert response.status_code == expected
    other_admin = {**admin, "X-OpenViking-Account": "other"}
    assert (await client.get("/admin/keys", headers=other_admin)).json() == []
    listed = (await client.get("/admin/keys", headers=admin)).json()
    assert key["key"] not in str(listed) and "user-key" not in str(listed)
    await client.delete("/admin/keys/" + key["id"], headers=admin)
    response = await client.get("/v1/models", headers={"Authorization": "Bearer " + key["key"]})
    assert response.status_code == 401


async def test_stateful_responses_and_counting(running_gateway):
    _, client, _, key, seen, _ = running_gateway
    headers = {"Authorization": "Bearer " + key["key"]}
    raw = b'{"model":"model", "previous_response_id":"old", "input":"new"}'
    response = await client.post("/v1/responses", headers=headers, content=raw)
    assert response.status_code == 200 and seen[-1][1] == raw
    assert (await client.get("/v1/responses/resp-1", headers=headers)).status_code == 200
    assert (await client.get("/v1/responses/unknown", headers=headers)).status_code == 404
    body = {"model": "model", "messages": [{"role": "user", "content": "Remember this request"}]}
    # A token count joins the conversation by its session header.
    headers["X-OpenViking-Session"] = "counted"
    await client.post("/v1/messages", headers=headers, json=body)
    first = orjson.loads(seen[-1][1])
    await client.post("/v1/messages/count_tokens", headers=headers, json=body)
    assert orjson.loads(seen[-1][1]) == first


async def enable_tools(client, admin, **policy):
    response = await client.put(
        "/admin/policies/default",
        headers=admin,
        json={
            "name": "Tools",
            "gateway_tools": True,
            "disabled_tools": [],
            "recall": False,
            "compaction": False,
            **policy,
        },
    )
    assert response.status_code == 200, response.text


def tool_call(name="openviking_find", identifier="g-1", arguments=None):
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {"query": "blue"})},
    }


def completion(message, finish="stop", **extra):
    return {
        "id": "upstream-id",
        "object": "chat.completion",
        "model": "model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
        **extra,
    }


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("show", [True, False])
async def test_hidden_tools_mixed_replay_and_usage(running_gateway, streaming, mixed, show):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin, show_tool_calls=show)
    calls, model_requests = [], []

    async def backend(request):
        if request.path == "/mcp":
            payload = await request.json()
            calls.append(payload)
            assert request.headers["X-API-Key"] == "user-key"
            assert payload["params"]["name"] == "find"
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "the answer is blue"}]},
                }
            )
        if request.path != "/v1/chat/completions":
            return None
        payload = await request.json()
        model_requests.append(payload)
        first = len(model_requests) == 1
        message = {
            "role": "assistant",
            "content": "Looking. " if first else "Blue.",
            "reasoning_content": "private-1" if first else "private-2",
            "vendor_extension": {"signature": "opaque"},
        }
        if first:
            message["tool_calls"] = [tool_call()]
            if mixed:
                message["tool_calls"].append(tool_call("client_weather", "c-1", {"city": "Paris"}))
        value = completion(message, "tool_calls" if first else "stop", provider_field="keep")
        if not streaming:
            return web.json_response(value)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        content = dict(message)
        tools = content.pop("tool_calls", [])
        events = [
            {
                "id": "u-id",
                "provider_field": "keep",
                "choices": [{"index": 0, "delta": content, "finish_reason": None}],
            }
        ]
        # Split names and arguments across SSE fragments, including both kinds.
        for index, call in enumerate(tools):
            function = call["function"]
            events += [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": call["id"],
                                        "type": "function",
                                        "function": {"name": function["name"][:5], "arguments": ""},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "function": {
                                            "name": function["name"][5:],
                                            "arguments": function["arguments"],
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
            ]
        events.append(
            {
                "choices": [{"delta": {}, "finish_reason": value["choices"][0]["finish_reason"]}],
                "usage": value["usage"],
            }
        )
        raw = (
            b"".join(b"data: " + orjson.dumps(event) + b"\n\n" for event in events)
            + b"data: [DONE]\n\n"
        )
        for i in range(0, len(raw), 13):
            await response.write(raw[i : i + 13])
        await response.write_eof()
        return response

    app.state.test_backend["handler"] = backend
    body = {
        "model": "model",
        "messages": [{"role": "user", "content": "Find blue"}],
        "stream": streaming,
        # Some clients send a null stream_options on nonstreaming requests.
        "stream_options": {"include_usage": True} if streaming else None,
        "tools": [
            {
                "type": "function",
                "function": {"name": "client_weather", "parameters": {"type": "object"}},
            }
        ],
    }
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "tool-session"}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert "openviking_find" not in response.text
    from context_gateway.storage import digest

    sid = digest("tool-session")
    stored = await app.state.store.state.read(digest("tenant\0alice\0chat"), [sid])
    assert stored[sid].value["usage"]["input_tokens"] == 100
    assert len(calls) == 1 and len(model_requests) == (1 if mixed else 2)
    if streaming:
        from context_gateway.protocols import SSEDecoder
        from context_gateway.tool_protocols.common import merge_delta

        visible = {"role": "assistant", "content": ""}
        events = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
        events = [event for event in events if event]
        for event in events:
            for choice in event.get("choices", []):
                delta = dict(choice.get("delta", {}))
                if delta.get("tool_calls"):
                    visible["tool_calls"] = [
                        {k: v for k, v in c.items() if k != "index"}
                        for c in delta.pop("tool_calls")
                    ]
                merge_delta(visible, delta)
        assert response.content.count(b"data: [DONE]") == 1
        assert sum(bool(c.get("finish_reason")) for e in events for c in e.get("choices", [])) == 1
        assert events[-1]["usage"]["prompt_tokens"] == (100 if mixed else 200)
    else:
        visible = response.json()["choices"][0]["message"]
        assert response.json()["provider_field"] == "keep"
    notice = '\n\n> OpenViking find: "blue" — done\n\n' if show else ""
    assert visible["content"] == "Looking. " + notice + ("" if mixed else "Blue.")
    if streaming and show:
        # The call's head streams before it runs; its outcome follows.
        deltas = [c["delta"].get("content") for e in events for c in e.get("choices", [])]
        assert deltas[1:4] == ['\n\n> OpenViking find: "blue"', " — done", "\n\n"]
    # A typical chat UI drops reasoning/unknown fields from its returned history.
    visible = {k: v for k, v in visible.items() if k in {"role", "content", "tool_calls"}}
    body["messages"].append(visible)
    if mixed:
        body["messages"].append({"role": "tool", "tool_call_id": "c-1", "content": "Sunny"})
    else:
        body["messages"].append({"role": "user", "content": "Continue"})
    # Change the live policy; the existing root must retain its frozen tools.
    await enable_tools(client, admin, disabled_tools=["find"])
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200, response.text
    replay = model_requests[-1]
    assert replay["tools"] == model_requests[0]["tools"]
    hidden = replay["messages"][1]
    assert hidden["reasoning_content"] == "private-1"
    assert hidden["vendor_extension"] == {"signature": "opaque"}
    assert hidden["tool_calls"][0]["function"]["name"] == "openviking_find"
    assert replay["messages"][2]["tool_call_id"] == "g-1"
    if mixed:
        assert replay["messages"][3]["tool_call_id"] == "c-1"
    # The notices stay in the replaced visible span; the model never sees them.
    assert "> OpenViking" not in orjson.dumps(replay).decode()
    assert len(calls) == 1
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert any(log.get("hidden_rounds") == 1 for log in logs)
    assert not any("private-1" in str(log) for log in logs)


@pytest.mark.parametrize(
    "extra",
    [
        {"n": 2},
        {"response_format": {"type": "json_object"}},
        {"tool_choice": "required"},
        {"tool_choice": {"type": "function", "function": {"name": "foo"}}},
    ],
)
async def test_tool_capability_gate_and_frozen_conflict(running_gateway, extra):
    _, client, admin, key, seen, _ = running_gateway
    await enable_tools(client, admin)
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "blocked"}
    body = {"model": "model", "messages": [{"role": "user", "content": "hello"}]}
    response = await client.post("/v1/chat/completions", headers=headers, json={**body, **extra})
    assert response.status_code == 200
    assert not orjson.loads(seen[-1][1]).get("tools")
    headers["X-OpenViking-Session"] = "enabled"
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert len(orjson.loads(seen[-1][1])["tools"]) == len(MCP_TOOLS)
    response = await client.post("/v1/chat/completions", headers=headers, json={**body, **extra})
    assert response.status_code == 200
    assert not orjson.loads(seen[-1][1]).get("tools")


CLIENT_TOOLS = [
    {"type": "function", "function": {"name": "client_weather", "parameters": {"type": "object"}}}
]


@pytest.mark.parametrize("stubborn", [False, True])
async def test_tool_round_limit_refuses_gateway_calls_but_keeps_client_tools(
    running_gateway, stubborn
):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin, tool_max_rounds=1)
    requests, mcp_calls = [], []
    weather = tool_call("client_weather", "c-1", {"city": "Paris"})

    async def backend(request):
        if request.path == "/mcp":
            mcp_calls.append(await request.json())
            return web.json_response({"id": 1, "result": {"content": []}})
        if request.path == "/v1/chat/completions":
            requests.append(await request.json())
            number = len(requests)
            call = weather if number == 3 and not stubborn else tool_call(identifier=f"g-{number}")
            return web.json_response(
                completion(
                    {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"
                )
            )
        return None

    app.state.test_backend["handler"] = backend
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "tools"},
        json={
            "model": "model",
            "messages": [{"role": "user", "content": "find blue"}],
            "tools": CLIENT_TOOLS,
            "tool_choice": "auto",
        },
    )
    # Past the limit a gateway call is refused, and the client's tools stay callable.
    assert len(requests) == 3 and len(mcp_calls) == 1
    assert requests[2]["messages"][-1]["content"] == REFUSED
    assert all(r["tool_choice"] == "auto" and r["tools"] == requests[0]["tools"] for r in requests)
    if stubborn:
        # A model that calls gateway tools again after a refusal cannot loop.
        assert response.status_code == 502
    else:
        assert response.status_code == 200, response.text
        message = response.json()["choices"][0]["message"]
        assert message["tool_calls"] == [weather] and "— skipped" in message["content"]


@pytest.mark.parametrize(
    "header", [{"X-Claude-Code-Agent-Id": "child"}, {"X-OpenViking-Plugin": "1"}]
)
async def test_requests_without_gateway_tools_pass_client_tools_through(running_gateway, header):
    _, client, admin, key, seen, _ = running_gateway
    await enable_tools(client, admin)
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "parent"}
    body = {
        "model": "model",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": CLIENT_TOOLS,
        "tool_choice": "auto",
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert len(orjson.loads(seen[-1][1])["tools"]) == len(CLIENT_TOOLS) + len(MCP_TOOLS)
    # A sub-agent, or a conversation that now has an OpenViking plugin, never ran
    # gateway tools: it gets none, and its own tools and tool choice stay as sent.
    body["messages"] = [{"role": "user", "content": "explore the repository"}]
    response = await client.post("/v1/chat/completions", headers={**headers, **header}, json=body)
    assert response.status_code == 200, response.text
    forwarded = orjson.loads(seen[-1][1])
    assert forwarded["tools"] == CLIENT_TOOLS and forwarded["tool_choice"] == "auto"


@pytest.mark.parametrize("vendor", ["ark", "byteplus"])
@pytest.mark.parametrize(
    "path,protocol,target",
    [
        ("/api/v3/chat/completions", "chat", "/api/v3/chat/completions"),
        ("/api/v3/responses", "responses", "/api/v3/responses"),
        ("/api/compatible/v1/messages", "anthropic", "/api/compatible/v1/messages"),
    ],
)
async def test_ark_paths_unknown_fields_and_cache_key(
    running_gateway, vendor, path, protocol, target
):
    _, client, admin, key, seen, _ = running_gateway
    upstream = (await client.get("/admin/upstreams", headers=admin)).json()
    configured = next(u for u in upstream if u["id"] == protocol)
    payload = {
        k: v
        for k, v in configured.items()
        if k not in {"id", "revision", "has_api_key", "header_names", "account"}
    }
    payload["vendor"] = vendor
    response = await client.put("/admin/upstreams/" + protocol, headers=admin, json=payload)
    assert response.status_code == 200, response.text
    field = "input" if protocol == "responses" else "messages"
    body = {
        "model": "model",
        "store": False,
        field: [{"role": "user", "content": "Remember blue"}],
        "thinking": {"type": "enabled"},
        "caching": {"type": "enabled"},
        "expire_at": 1893456000,
        "encrypted_content": "opaque",
    }
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "ark"}
    for _ in range(2):
        response = await client.post(path, headers=headers, json=body)
        assert response.status_code == 200, response.text
        forwarded = orjson.loads(seen[-1][1])
        assert seen[-1][0] == target
        assert all(
            forwarded[k] == body[k]
            for k in ["thinking", "caching", "expire_at", "encrypted_content"]
        )
    if protocol != "anthropic":
        assert orjson.loads(seen[-2][1])["prompt_cache_key"] == forwarded["prompt_cache_key"]
    payload["coding_plan"] = True
    assert (
        await client.put("/admin/upstreams/" + protocol, headers=admin, json=payload)
    ).status_code == 200
    assert (await client.post(path, headers=headers, json=body)).status_code == 403


@pytest.mark.parametrize("name", ["add_resource", "add_skill"])
async def test_attachment_import_and_signed_upload_proxy(running_gateway, name):
    import base64

    app, client, admin, key, _, _ = running_gateway
    app.state.config.public_url = "https://gateway.example"
    await enable_tools(client, admin)
    model_requests, uploads, mcp_requests = [], [], []

    async def backend(request):
        if request.path == "/mcp":
            payload = await request.json()
            mcp_requests.append(payload)
            assert payload["params"]["name"] == name
            assert payload["params"]["arguments"]["path"].startswith("/client-upload/")
            return web.Response(
                content_type="text/event-stream",
                text="data: "
                + json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "POST multipart field file to http://private-ov/api/v1/resources/temp_upload?token=signed-once",
                                }
                            ]
                        },
                    }
                )
                + "\n\n",
            )
        if request.path == "/api/v1/resources/temp_upload":
            assert request.query["token"] == "signed-once"
            assert "Authorization" not in request.headers and "X-API-Key" not in request.headers
            multipart = await request.multipart()
            part = await multipart.next()
            uploads.append((part.name, part.filename, bytes(await part.read())))
            return web.json_response(
                {"status": "ok", "result": {"uri": "viking://resources/imported"}}
            )
        if request.path == "/v1/chat/completions":
            payload = await request.json()
            model_requests.append(payload)
            if len(model_requests) == 1:
                return web.json_response(
                    completion(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                tool_call("openviking_" + name, arguments={"attachment_index": 0})
                            ],
                        },
                        "tool_calls",
                    )
                )
            return web.json_response(completion({"role": "assistant", "content": "Imported."}))
        return None

    app.state.test_backend["handler"] = backend
    body = {
        "model": "model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Import the attached file"},
                    {
                        "type": "file",
                        "file": {
                            "filename": "SKILL.md",
                            "file_data": "data:text/plain;base64,"
                            + base64.b64encode(b"# Test skill\ncontent").decode(),
                        },
                    },
                ],
            }
        ],
    }
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "tools"},
        json=body,
    )
    assert response.status_code == 200, response.text
    assert len(mcp_requests) == 1 and uploads == [("file", "SKILL.md", b"# Test skill\ncontent")]
    assert "signed-once" not in response.text
    assert "viking://resources/imported" in model_requests[1]["messages"][-1]["content"]
    # The token is the sole upload authorization, and the destination is fixed.
    response = await client.post(
        "/context-gateway/uploads?token=signed-once&url=http://attacker",
        files={"file": ("notes.txt", b"upload")},
        headers={"Authorization": "Bearer not-forwarded"},
    )
    assert response.status_code == 200, response.text
    assert uploads[-1] == ("file", "notes.txt", b"upload")
    assert (
        await client.post("/context-gateway/uploads", files={"file": ("x.txt", b"x")})
    ).status_code == 400


async def test_shell_upload_instructions_use_public_proxy(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    app.state.config.public_url = "https://gateway.example"
    await enable_tools(client, admin)
    requests = []

    async def backend(request):
        if request.path == "/mcp":
            return web.json_response(
                {
                    "id": 1,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": "POST to http://private-ov/api/v1/resources/temp_upload?token=opaque%2Btoken",
                            }
                        ]
                    },
                }
            )
        if request.path == "/v1/chat/completions":
            payload = await request.json()
            requests.append(payload)
            if len(requests) == 1:
                return web.json_response(
                    completion(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                tool_call(
                                    "openviking_add_resource",
                                    arguments={"path": "/home/client/file.md"},
                                )
                            ],
                        },
                        "tool_calls",
                    )
                )
            result = payload["messages"][-1]["content"]
            assert "https://gateway.example/context-gateway/uploads?token=opaque%2Btoken" in result
            assert "private-ov" not in result and "user-key" not in result
            return web.json_response(
                completion(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tool_call("bash", "shell-call", {"command": "upload"})],
                    },
                    "tool_calls",
                )
            )
        return None

    app.state.test_backend["handler"] = backend
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "tools"},
        json={
            "model": "model",
            "messages": [{"role": "user", "content": "import file"}],
            "tools": [{"type": "function", "function": {"name": "bash"}}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "bash"


@pytest.mark.parametrize("vendor", ["ark", "byteplus"])
async def test_ark_responses_stream_keeps_native_fields_and_done(running_gateway, vendor):
    app, client, admin, key, _, _ = running_gateway
    upstreams = (await client.get("/admin/upstreams", headers=admin)).json()
    upstream = next(u for u in upstreams if u["id"] == "responses")
    allowed = {"name", "protocol", "base_url", "models", "aliases", "vendor"}
    payload = {k: v for k, v in upstream.items() if k in allowed}
    payload["vendor"] = vendor
    assert (
        await client.put("/admin/upstreams/responses", headers=admin, json=payload)
    ).status_code == 200
    native = {
        "type": "response.completed",
        "response": {
            "id": "ark-response",
            "status": "completed",
            "output": [
                {"type": "reasoning", "encrypted_content": "opaque-signature"},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                },
            ],
            "caching": {"type": "enabled"},
            "expire_at": 1893456000,
            "usage": {
                "input_tokens": 3000,
                "output_tokens": 12,
                "input_tokens_details": {"cached_tokens": 2048},
            },
        },
    }
    raw = b"data: " + orjson.dumps(native) + b"\n\ndata: [DONE]\n\n"

    async def backend(request):
        if request.path == "/api/v3/responses":
            return web.Response(body=raw, content_type="text/event-stream")
        return None

    app.state.test_backend["handler"] = backend
    response = await client.post(
        "/api/v3/responses",
        headers={"Authorization": "Bearer " + key["key"]},
        json={"model": "model", "store": False, "stream": True, "input": "hello there"},
    )
    assert response.content == raw
    log = (await client.get("/admin/logs", headers=admin)).json()[0]
    assert log["cached_tokens"] == 2048 and log["input_tokens"] == 3000
    assert log["cache_eligible"] is True


@pytest.mark.parametrize("max_field", ["max_tokens", "max_completion_tokens"])
async def test_large_first_tool_request_keeps_client_token_limit(running_gateway, max_field):
    _, client, admin, key, seen, _ = running_gateway
    await enable_tools(client, admin, tool_total_tokens=1024)
    body = {
        "model": "model",
        "messages": [{"role": "user", "content": "hello"}],
        "system": "fixed instructions " * 20000,
        max_field: 6000,
    }
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "large-tool"},
        json=body,
    )
    assert response.status_code == 200
    forwarded = orjson.loads(seen[-1][1])
    assert forwarded[max_field] == 6000
    assert ("max_completion_tokens" if max_field == "max_tokens" else "max_tokens") not in forwarded


@pytest.mark.parametrize("vendor", ["ark", "byteplus"])
async def test_ark_connection_test_lists_models_under_api_v3(running_gateway, vendor):
    app, client, admin, _, seen, _ = running_gateway
    upstream = await app.state.management.get("tenant", "upstreams", "chat")
    await app.state.management.save("tenant", "upstreams", "chat", {**upstream, "vendor": vendor})
    response = await client.post("/admin/upstreams/chat/test", headers=admin)
    assert response.json() == {"ok": True, "status": 200}
    assert seen[-1][0] == "/api/v3/models"


async def test_ark_burst_is_forwarded_without_local_429(running_gateway):
    app, client, admin, key, seen, _ = running_gateway
    upstream = await app.state.management.get("tenant", "upstreams", "chat")
    await app.state.management.save("tenant", "upstreams", "chat", {**upstream, "vendor": "ark"})
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "ark-burst"}
    for _ in range(20):
        response = await client.post(
            "/api/v3/chat/completions",
            headers=headers,
            json={"model": "model", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200
    forwarded = [orjson.loads(raw) for path, raw, _ in seen if path == "/api/v3/chat/completions"]
    assert len(forwarded) == 20
    assert len({body["prompt_cache_key"] for body in forwarded}) == 1


async def test_responses_store_false_has_no_routing_mapping(running_gateway):
    app, client, _, key, _, _ = running_gateway
    response = await client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer " + key["key"]},
        json={"model": "model", "store": False, "input": "hello"},
    )
    assert response.status_code == 200
    assert await app.state.management.list("tenant", "responses") == []


async def test_storage_failure_still_forwards_plain_chat(running_gateway, monkeypatch):
    app, client, _, key, seen, _ = running_gateway

    async def unavailable(*_args, **_kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(app.state.store, "load", unavailable)
    raw = b'{"model":"model", "messages":[{"role":"user","content":"hello"}]}'
    response = await client.post(
        "/v1/chat/completions", headers={"Authorization": "Bearer " + key["key"]}, content=raw
    )
    assert response.status_code == 200
    assert seen[-1][1] == raw


@pytest.mark.parametrize("limit", [{}, {"max_tokens": 1024}, {"max_completion_tokens": 2048}])
async def test_long_context_tool_continuation_preserves_output_limit(running_gateway, limit):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin)
    requests, writes = [], []

    async def backend(request):
        if request.path not in {"/mcp", "/v1/chat/completions"}:
            return None
        payload = await request.json()
        if request.path == "/mcp":
            writes.append(payload)
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "saved"}]},
                }
            )
        if request.path != "/v1/chat/completions":
            return None
        requests.append(payload)
        if len(requests) == 1:
            value = completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        tool_call(
                            "openviking_write",
                            arguments={"uri": "viking://~/notes/test", "content": "saved"},
                        )
                    ],
                },
                "tool_calls",
            )
        else:
            value = completion({"role": "assistant", "content": "saved"})
        value["usage"] = {"prompt_tokens": 110000, "completion_tokens": 20}
        return web.json_response(value)

    app.state.test_backend["handler"] = backend
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "long-tools"},
        json={"model": "model", "messages": [{"role": "user", "content": "save this"}], **limit},
    )
    assert response.status_code == 200, response.text
    assert len(writes) == 1 and len(requests) == 2
    assert {
        k: v for k, v in requests[1].items() if k in {"max_tokens", "max_completion_tokens"}
    } == limit


@pytest.mark.parametrize("streaming", [False, True])
async def test_tool_budget_finishes_after_results_without_usage(running_gateway, streaming):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin, tool_total_tokens=1024)
    requests, calls = [], []

    async def backend(request):
        if request.path not in {"/mcp", "/v1/chat/completions"}:
            return None
        payload = await request.json()
        if request.path == "/mcp":
            calls.append(payload)
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "result " * 1000}]},
                }
            )
        if request.path != "/v1/chat/completions":
            return None
        requests.append(payload)
        first = len(requests) == 1
        message = (
            {"role": "assistant", "content": None, "tool_calls": [tool_call()]}
            if first
            else {"role": "assistant", "content": "done"}
        )
        finish = "tool_calls" if first else "stop"
        if not streaming:
            value = completion(message, finish)
            value.pop("usage")
            return web.json_response(value)
        if first:
            message["tool_calls"][0]["index"] = 0
        event = {"choices": [{"index": 0, "delta": message, "finish_reason": finish}]}
        return web.Response(
            body=b"data: " + orjson.dumps(event) + b"\n\ndata: [DONE]\n\n",
            content_type="text/event-stream",
        )

    app.state.test_backend["handler"] = backend
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "budget-tools"},
        json={
            "model": "model",
            "stream": streaming,
            "messages": [{"role": "user", "content": "search"}],
        },
    )
    assert response.status_code == 200 and "gateway_tool_error" not in response.text
    assert len(calls) == 1 and len(requests) == 2
    assert "tool_choice" not in requests[1] and "max_tokens" not in requests[1]
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert logs[0]["tool_stop_reason"] == "token_budget"


async def test_capture_reset_is_scoped_to_the_key_account(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "reset"},
        json={"model": "model", "messages": [{"role": "user", "content": "hello"}]},
    )
    log = (await client.get("/admin/logs", headers=admin)).json()[0]
    path = "/admin/keys/" + log["credential_id"] + "/capture/reset"
    body = {"session": log["session"], "protocol": "chat"}
    wrong = await client.post(path, headers={**admin, "X-OpenViking-Account": "other"}, json=body)
    assert wrong.status_code == 404
    assert (await client.post(path, headers=admin, json=body)).status_code == 200
    # The session header value the client sent names the same session.
    raw = {**body, "session": "reset"}
    assert (await client.post(path, headers=admin, json=raw)).status_code == 200
    assert (
        await client.post(path, headers=admin, json={**body, "session": "unknown"})
    ).status_code == 404


async def test_guides_report_the_client_facing_address(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    response = await client.get("/admin/guides", headers=admin)
    assert response.json() == {"base_url": "http://127.0.0.1:1935", "public_url_configured": False}
    app.state.config.public_url = "https://ov.example.com"
    response = await client.get("/admin/guides", headers=admin)
    assert response.json() == {"base_url": "https://ov.example.com", "public_url_configured": True}


async def test_upstream_save_keeps_blank_secrets_per_header(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    upstream = {"name": "chat", "protocol": "chat", "base_url": "https://api.example.com/v1"}

    async def save(**fields):
        response = await client.put(
            "/admin/upstreams/headers", headers=admin, json={**upstream, **fields}
        )
        assert response.status_code == 200, response.text
        stored = await app.state.management.get("tenant", "upstreams", "headers")
        return response.json(), stored

    await save(api_key="secret", headers={"A": "1", "B": "2"})
    public, stored = await save(api_key="", headers={"A": "", "C": "3", "D": ""})
    assert stored["headers"] == {"A": "1", "C": "3"} and stored["api_key"] == "secret"
    assert public["header_names"] == ["A", "C"] and public["has_api_key"]
    _, stored = await save()
    assert stored["headers"] == {"A": "1", "C": "3"}
    _, stored = await save(headers={"A": "new"})
    assert stored["headers"] == {"A": "new"}
    _, stored = await save(headers={})
    assert stored["headers"] == {} and stored["api_key"] == "secret"


async def test_overview_counts_model_requests_recalls_and_capture_issues(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    # Oldest first: the store returns the newest record first.
    for record in [
        {"kind": "user", "session": "a", "protocol": "chat", "capture_status": "retrying"},
        {"kind": "capture", "session": "b", "protocol": "chat", "capture_status": "paused"},
        {"kind": "capture", "session": "a", "protocol": "chat", "capture_status": "active"},
        {"kind": "user", "recall_reason": "recalled", "recall_ms": 30, "recall_count": 2},
        {"kind": "user", "recall_reason": "empty", "recall_ms": 10},
        {"kind": "user", "recall_reason": "disabled", "recall_ms": 0},
        {"kind": "continuation", "session": "c", "protocol": "chat", "capture_status": "retrying"},
        {"kind": "capture", "session": "c", "protocol": "anthropic", "capture_status": "paused"},
    ]:
        await app.state.management.log("tenant", record)
    overview = (await client.get("/admin/overview", headers=admin)).json()
    logs = (await client.get("/admin/logs", headers=admin)).json()
    newest_request = next(log for log in logs if log["kind"] != "capture")
    assert overview["requests"] == 5
    assert overview["last_request_at"] == newest_request["time"]
    assert overview["recall_requests"] == 2 and overview["recall_ms"] == 20
    assert overview["recall_count"] == 2
    assert overview["capture_issues"] == {"retrying": 1, "paused": 2}
    assert overview["log_retention_days"] == 30


async def test_overview_without_model_requests(running_gateway):
    _, client, admin, _, _, _ = running_gateway
    overview = (await client.get("/admin/overview", headers=admin)).json()
    assert overview["requests"] == 0 and overview["last_request_at"] is None
    assert overview["recall_ms"] == 0 and overview["recall_requests"] == 0
    assert overview["capture_issues"] == {"retrying": 0, "paused": 0}


async def test_user_and_account_data_deletion_routes(running_gateway):
    _, client, admin, key, _, _ = running_gateway
    response = await client.delete("/admin/users/bob/data", headers=admin)
    assert response.json() == {"deleted": True}
    assert (await client.get("/admin/keys", headers=admin)).json()
    response = await client.delete("/admin/account/data", headers=admin)
    assert response.status_code == 200 and response.json() == {"deleted": True}
    assert (await client.get("/admin/keys", headers=admin)).json() == []
    response = await client.get("/v1/models", headers={"Authorization": "Bearer " + key["key"]})
    assert response.status_code == 401


async def test_admin_tools_list_the_mcp_tools_for_profiles(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    backend = app.state.test_backend
    backend["catalog"] = [{**MCP_TOOLS[0], "annotations": {"readOnlyHint": True}}, MCP_TOOLS[3]]
    response = await client.get("/admin/tools", headers=admin)
    assert response.status_code == 200
    assert response.json() == [
        {"name": "find", "description": "OpenViking find", "annotations": {"readOnlyHint": True}},
        {"name": "list", "description": "OpenViking list"},
    ]
    assert backend["tools_calls"] == ["user-key"]
    # An account without gateway keys has no OpenViking key to list tools with.
    response = await client.get("/admin/tools", headers={**admin, "X-OpenViking-Account": "new"})
    assert response.status_code == 200 and response.json() == []
    assert backend["tools_calls"] == ["user-key"]


async def test_admin_tools_skip_rejected_keys_and_report_failures_cleanly(running_gateway):
    app, client, admin, _, _, _ = running_gateway
    backend = app.state.test_backend
    response = await client.post(
        "/admin/keys",
        headers=admin,
        json={
            "name": "second",
            "openviking_key": "second-key",
            "policy_id": "default",
            "upstream_ids": ["chat"],
        },
    )
    assert response.status_code == 200, response.text
    # A 401 here would read as the admin's own Studio sign-in failing.
    backend["tools_status"] = {"user-key": 401, "second-key": 401}
    response = await client.get("/admin/tools", headers=admin)
    assert response.status_code == 502 and "private server detail" not in response.text
    # An outage is not retried with every key.
    backend["tools_status"] = {"user-key": 503, "second-key": 503}
    backend["tools_calls"] = []
    response = await client.get("/admin/tools", headers=admin)
    assert response.status_code == 502 and len(backend["tools_calls"]) == 1
    backend["tools_status"] = {"user-key": 401}
    response = await client.get("/admin/tools", headers=admin)
    assert response.status_code == 200 and len(response.json()) == len(MCP_TOOLS)
    assert backend["tools_calls"][-1] == "second-key"


async def test_model_restricted_key_rejects_bodies_without_a_readable_model(running_gateway):
    import gzip

    _, client, admin, key, seen, _ = running_gateway
    minted = await client.post(
        "/admin/keys",
        headers=admin,
        json={
            "name": "restricted",
            "openviking_key": "user-key",
            "policy_id": "default",
            "upstream_ids": ["chat", "responses"],
            "models": ["model"],
        },
    )
    assert minted.status_code == 200, minted.text
    restricted = {"Authorization": "Bearer " + minted.json()["key"]}
    compressed = gzip.compress(b'{"model":"error","messages":[{"role":"user","content":"hi"}]}')
    gzipped = {**restricted, "content-encoding": "gzip", "content-type": "application/json"}
    before = len(seen)
    response = await client.post("/v1/chat/completions", headers=gzipped, content=compressed)
    assert response.status_code == 415
    for raw in (b"not json", b"[]", b'{"messages":[]}', b'{"model":["model"]}'):
        response = await client.post("/v1/chat/completions", headers=restricted, content=raw)
        assert response.status_code == 400, raw
    assert len(seen) == before

    response = await client.post(
        "/v1/responses", headers=restricted, json={"model": "model", "input": "hi"}
    )
    assert response.status_code == 200, response.text
    assert (await client.get("/v1/responses/resp-1", headers=restricted)).status_code == 200
    models = await client.get("/v1/models", headers=restricted)
    assert [m["id"] for m in models.json()["data"]] == ["model"]

    unrestricted = {"Authorization": "Bearer " + key["key"], "content-encoding": "gzip"}
    response = await client.post("/v1/chat/completions", headers=unrestricted, content=compressed)
    assert response.status_code != 415 and len(seen) > before + 1
    assert seen[-1][0] == "/v1/chat/completions"
