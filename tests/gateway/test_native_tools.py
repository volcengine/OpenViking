"""Native wire contracts: hidden calls, exact replay and one visible stream."""

import copy

import orjson
import pytest
from aiohttp import web
from test_app import enable_tools

from openviking_gateway.protocols import SSEDecoder
from openviking_gateway.tool_protocols.common import sse

PATHS = {"responses": "/v1/responses", "anthropic": "/v1/messages"}


def native_response(protocol, number, *, owned=False, mixed=False, custom=False):
    if protocol == "chat":
        calls = []
        if owned:
            function = {"name": "openviking_search", "arguments": '{"query":"blue"}'}
            calls.append({"id": f"gateway-{number}", "type": "function", "function": function})
        if mixed:
            function = {"name": "shell", "arguments": '{"command":"pwd"}'}
            calls.append({"id": "client-1", "type": "function", "function": function})
        message = {"role": "assistant", "content": f"answer-{number}"}
        if calls:
            message["tool_calls"] = calls
        return {
            "id": f"chatcmpl-{number}",
            "object": "chat.completion",
            "model": "model",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if calls else "stop",
                }
            ],
            "usage": {"prompt_tokens": 110000, "completion_tokens": 20, "total_tokens": 110020},
        }
    if protocol == "anthropic":
        output = [
            {
                "type": "thinking",
                "thinking": f"reason-{number}",
                "signature": f"opaque-signature-{number}",
            },
            {"type": "redacted_thinking", "data": f"opaque-redacted-{number}"},
            {"type": "text", "text": f"answer-{number}", "citations": []},
        ]
        if owned:
            output.append(
                {
                    "type": "tool_use",
                    "id": f"gateway-{number}",
                    "name": "openviking_search",
                    "input": {"query": "blue"},
                }
            )
        if mixed:
            output.append(
                {"type": "tool_use", "id": "client-1", "name": "shell", "input": {"command": "pwd"}}
            )
        return {
            "id": f"msg_{number}",
            "type": "message",
            "role": "assistant",
            "model": "model",
            "content": output,
            "stop_reason": "tool_use" if owned or mixed else "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 110000, "output_tokens": 20, "cache_read_input_tokens": 1000},
        }
    output = [
        {
            "type": "reasoning",
            "id": f"rs_{number}",
            "summary": [],
            "encrypted_content": f"opaque-encrypted-{number}",
        },
        {
            "type": "message",
            "role": "assistant",
            "id": f"msg_{number}",
            "status": "completed",
            "content": [{"type": "output_text", "text": f"answer-{number}", "annotations": []}],
        },
    ]
    if owned:
        output.append(
            {
                "type": "function_call",
                "id": f"fc_{number}",
                "call_id": f"gateway-{number}",
                "name": "openviking_search",
                "arguments": '{"query":"blue"}',
                "status": "completed",
            }
        )
    if mixed:
        output.append(
            {
                "type": "custom_tool_call" if custom else "function_call",
                "id": "fc_client",
                "call_id": "client-1",
                "name": "shell",
                "input" if custom else "arguments": "pwd" if custom else '{"command":"pwd"}',
                "status": "completed",
            }
        )
    return {
        "id": f"resp_{number}",
        "object": "response",
        "model": "model",
        "status": "completed",
        "output": output,
        "usage": {
            "input_tokens": 110000,
            "output_tokens": 20,
            "total_tokens": 110020,
            "input_tokens_details": {"cached_tokens": 1000},
        },
    }


def native_events(protocol, value):
    if protocol == "anthropic":
        events = [
            {
                "type": "message_start",
                "message": {
                    **value,
                    "content": [],
                    "stop_reason": None,
                    "usage": {**value["usage"], "output_tokens": 0},
                },
            }
        ]
        for index, block in enumerate(value["content"]):
            initial = copy.deepcopy(block)
            deltas = []
            for key in ("thinking", "signature", "text"):
                if key in initial:
                    text = initial[key]
                    initial[key] = ""
                    deltas.extend(
                        {"type": key + "_delta", key: fragment} for fragment in (text[:3], text[3:])
                    )
            if block["type"] == "tool_use":
                initial["input"] = {}
                raw = orjson.dumps(block["input"]).decode()
                deltas.extend(
                    {"type": "input_json_delta", "partial_json": fragment}
                    for fragment in (raw[:4], raw[4:])
                )
            events.append({"type": "content_block_start", "index": index, "content_block": initial})
            events.extend(
                {"type": "content_block_delta", "index": index, "delta": delta} for delta in deltas
            )
            events.append({"type": "content_block_stop", "index": index})
        return [
            *events,
            {
                "type": "message_delta",
                "delta": {"stop_reason": value["stop_reason"], "stop_sequence": None},
                "usage": {"output_tokens": value["usage"]["output_tokens"]},
            },
            {"type": "message_stop"},
        ]
    events = [
        {"type": event, "response": {**value, "status": "in_progress", "output": []}}
        for event in ("response.created", "response.in_progress")
    ]
    for index, item in enumerate(value["output"]):
        initial = copy.deepcopy(item)
        if item["type"] == "function_call":
            initial["arguments"] = ""
        if item["type"] == "message":
            initial["content"] = []
        events.append(
            {"type": "response.output_item.added", "output_index": index, "item": initial}
        )
        if item["type"] == "message":
            part = item["content"][0]
            events.extend(
                [
                    {
                        "type": "response.content_part.added",
                        "output_index": index,
                        "item_id": item["id"],
                        "content_index": 0,
                        "part": {**part, "text": ""},
                    },
                    {
                        "type": "response.output_text.delta",
                        "output_index": index,
                        "item_id": item["id"],
                        "content_index": 0,
                        "delta": part["text"],
                    },
                    {
                        "type": "response.output_text.done",
                        "output_index": index,
                        "item_id": item["id"],
                        "content_index": 0,
                        "text": part["text"],
                    },
                    {
                        "type": "response.content_part.done",
                        "output_index": index,
                        "item_id": item["id"],
                        "content_index": 0,
                        "part": part,
                    },
                ]
            )
        if item["type"] == "function_call":
            for fragment in (item["arguments"][:4], item["arguments"][4:]):
                events.append(
                    {
                        "type": "response.function_call_arguments.delta",
                        "output_index": index,
                        "item_id": item["id"],
                        "delta": fragment,
                    }
                )
            events.append(
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": index,
                    "item_id": item["id"],
                    "arguments": item["arguments"],
                }
            )
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    return [*events, {"type": "response.completed", "response": value}]


async def wire_response(request, protocol, value, streaming):
    if not streaming:
        return web.json_response(value)
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    data = b"".join(
        b"event: " + e["type"].encode() + b"\n" + sse(e) for e in native_events(protocol, value)
    )
    # Deliberately split names, JSON and signature deltas at byte boundaries.
    for offset in range(0, len(data), 19):
        await response.write(data[offset : offset + 19])
    await response.write_eof()
    return response


def request_body(protocol, streaming):
    messages = [{"role": "user", "content": "Find blue"}]
    if protocol == "chat":
        function = {"name": "shell", "parameters": {"type": "object"}}
        return {
            "model": "model",
            "messages": messages,
            "stream": streaming,
            "tools": [{"type": "function", "function": function}],
        }
    if protocol == "anthropic":
        return {
            "model": "model",
            "messages": messages,
            "max_tokens": 2048,
            "stream": streaming,
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "tool_choice": {"type": "auto"},
            "tools": [
                {"name": "shell", "description": "Run shell", "input_schema": {"type": "object"}}
            ],
        }
    return {
        "model": "model",
        "input": messages,
        "store": False,
        "max_output_tokens": 2048,
        "stream": streaming,
        "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}],
    }


def visible_response(protocol, response, streaming):
    if not streaming:
        return response.json()
    events = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
    assert all(e is not None for e in events)
    assert b"[DONE]" not in response.content
    if protocol == "responses":
        assert [e["sequence_number"] for e in events] == list(range(len(events)))
        assert sum(e["type"] == "response.created" for e in events) == 1
        assert sum(e["type"] == "response.in_progress" for e in events) == 1
        assert sum(e["type"] == "response.completed" for e in events) == 1
        value = events[-1]["response"]
        starts = [e for e in events if e["type"] == "response.output_item.added"]
        assert [e["output_index"] for e in starts] == list(range(len(value["output"])))
        for e in events:
            if "output_index" in e:
                item = value["output"][e["output_index"]]
                assert e.get("item_id", item["id"]) == item["id"]
            if "response" in e:
                assert e["response"]["id"] == value["id"]
        assert [e["item"] for e in events if e["type"] == "response.output_item.done"] == value[
            "output"
        ]
        return value
    assert sum(e["type"] == "message_start" for e in events) == 1
    assert sum(e["type"] == "message_delta" for e in events) == 1
    assert sum(e["type"] == "message_stop" for e in events) == 1
    value = copy.deepcopy(events[0]["message"])
    blocks, arguments = [], {}
    for e in events:
        if e["type"] == "content_block_start":
            assert e["index"] == len(blocks)
            blocks.append(copy.deepcopy(e["content_block"]))
        if e["type"] == "content_block_delta":
            delta, index = e["delta"], e["index"]
            for key in ("text", "thinking", "signature"):
                if key in delta:
                    blocks[index][key] += delta[key]
            if "partial_json" in delta:
                arguments[index] = arguments.get(index, "") + delta["partial_json"]
        if e["type"] == "message_delta":
            value.update(e["delta"])
            value["usage"] = e["usage"]
    for index, raw in arguments.items():
        blocks[index]["input"] = orjson.loads(raw)
    value["content"] = blocks
    return value


NOTICE = '\n\n> OpenViking search: "blue" — done\n\n'


def expected_output(protocol, upstream, show):
    """Visible items of every round, a notice after each round of gateway calls."""
    output = []
    for value in upstream:
        items = value["output"] if protocol == "responses" else value["content"]
        calls = [i for i in items if i["type"] in {"function_call", "custom_tool_call", "tool_use"}]
        output += [i for i in items if i not in calls]
        if show and any(c.get("call_id", c.get("id")).startswith("gateway-") for c in calls):
            output.append(
                {"notice": True} if protocol == "responses" else {"type": "text", "text": NOTICE}
            )
        output += [c for c in calls if not c.get("call_id", c.get("id")).startswith("gateway-")]
    return output


def check_notice_events(protocol, response, notices):
    """Each notice streams its head before the call runs, like a model's own text."""
    events = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
    head, tail = '\n\n> OpenViking search: "blue"', " — done"
    if protocol == "anthropic":
        starts = [e["index"] for e in events if e["type"] == "content_block_start"]
        assert sorted(e["index"] for e in events if e["type"] == "content_block_stop") == starts
        texts = [e for e in events if e.get("delta", {}).get("text") == head]
        assert len(texts) == notices
        for delta in texts:
            sequence = [e for e in events if e.get("index") == delta["index"]]
            assert [e["type"] for e in sequence] == [
                "content_block_start",
                *["content_block_delta"] * 3,
                "content_block_stop",
            ]
            assert sequence[0]["content_block"] == {"type": "text", "text": ""}
            assert [e["delta"]["text"] for e in sequence[1:4]] == [head, tail, "\n\n"]
        return
    items = [
        e["item"]
        for e in events
        if e["type"] == "response.output_item.done" and "role" in e["item"]
    ]
    items = [i for i in items if i["content"][0]["text"] == NOTICE]
    assert len(items) == notices
    for item in items:
        sequence = [
            e for e in events if e.get("item_id", e.get("item", {}).get("id")) == item["id"]
        ]
        assert [e["type"] for e in sequence] == [
            "response.output_item.added",
            "response.content_part.added",
            *["response.output_text.delta"] * 3,
            "response.output_text.done",
            "response.content_part.done",
            "response.output_item.done",
        ]
        assert len({e["output_index"] for e in sequence}) == 1
        assert all(e["content_index"] == 0 for e in sequence[1:-1])
        assert sequence[0]["item"] == {**item, "status": "in_progress", "content": []}
        assert [e["delta"] for e in sequence[2:5]] == [head, tail, "\n\n"]
        assert sequence[5]["text"] == NOTICE


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("show", [True, False])
async def test_native_hidden_rounds_exact_replay(running_gateway, protocol, streaming, mixed, show):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin, show_tool_calls=show)
    requests, calls, upstream = [], [], []

    async def backend(request):
        if request.path == "/mcp":
            calls.append(await request.json())
            assert request.headers["X-API-Key"] == "user-key"
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "blue"}]},
                }
            )
        if request.path != PATHS[protocol]:
            return None
        payload = await request.json()
        requests.append(copy.deepcopy(payload))
        number = len(requests)
        value = native_response(
            protocol, number, owned=number <= (1 if mixed else 2), mixed=mixed and number == 1
        )
        if protocol == "responses":
            value["tools"] = payload["tools"]
        upstream.append(value)
        return await wire_response(request, protocol, value, streaming)

    app.state.test_backend["handler"] = backend
    body = request_body(protocol, streaming)
    original = copy.deepcopy(body)
    # Anonymous clients must also reuse the hidden transcript after a tool handoff.
    headers = {"Authorization": "Bearer " + key["key"]}
    response = await client.post(PATHS[protocol], headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert "openviking_search" not in response.text and "gateway-" not in response.text
    visible = visible_response(protocol, response, streaming)
    rounds = 1 if mixed else 3
    assert len(requests) == rounds and len(calls) == (1 if mixed else 2)
    shown = visible["output"] if protocol == "responses" else visible["content"]
    if protocol == "responses":
        notices = [i for i in shown if i["type"] == "message" and i["content"][0]["text"] == NOTICE]
        assert all(i["id"].startswith("msg_") and i["status"] == "completed" for i in notices)
        shown = [{"notice": True} if i in notices else i for i in shown]
    # Without notices the reply is exactly the model's visible output.
    assert shown == expected_output(protocol, upstream, show)
    if streaming and show:
        check_notice_events(protocol, response, 1 if mixed else 2)
    assert visible["usage"]["input_tokens"] == rounds * 110000
    assert visible["usage"]["output_tokens"] == rounds * 20
    field = "input" if protocol == "responses" else "messages"
    output = (
        visible["output"]
        if protocol == "responses"
        else [{"role": "assistant", "content": visible["content"]}]
    )
    body[field].extend(output)
    if mixed:
        body[field].append(
            {"type": "function_call_output", "call_id": "client-1", "output": "cwd"}
            if protocol == "responses"
            else {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "client-1", "content": "cwd"}],
            }
        )
    else:
        body[field].append({"role": "user", "content": "Continue"})
    response = await client.post(PATHS[protocol], headers=headers, json=body)
    assert response.status_code == 200, response.text
    replay = requests[-1]
    assert requests[0]["tools"] == replay["tools"]
    limit = "max_output_tokens" if protocol == "responses" else "max_tokens"
    assert all(r[limit] == original[limit] for r in requests)
    if protocol == "responses":
        assert all("reasoning.encrypted_content" in r["include"] for r in requests)
        expected = [*requests[0][field], *upstream[0]["output"]]
        assert replay[field][: len(expected)] == expected
        assert replay[field][len(expected)]["call_id"] == "gateway-1"
        assert replay[field][len(expected)]["type"] == "function_call_output"
        if not mixed:
            assert requests[1][field] == replay[field][: len(requests[1][field])]
            assert requests[2][field] == replay[field][: len(requests[2][field])]
    else:
        assert replay[field][1] == {"role": "assistant", "content": upstream[0]["content"]}
        results = replay[field][2]["content"]
        assert [b["tool_use_id"] for b in results] == (
            ["gateway-1", "client-1"] if mixed else ["gateway-1"]
        )
        if not mixed:
            assert requests[1][field] == replay[field][: len(requests[1][field])]
            assert requests[2][field] == replay[field][: len(requests[2][field])]
    # Notices are visible history only; no model request ever contains one.
    assert not any("> OpenViking" in orjson.dumps(r).decode() for r in requests)
    # Capture remains on the same OV session as the final/client-tool continuation.
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert len({log["session"] for log in logs}) == 1


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_native_budget_keeps_client_tools_and_output_limit(
    running_gateway, protocol, streaming
):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin, tool_total_tokens=1024)
    requests, calls = [], []

    async def backend(request):
        if request.path == "/mcp":
            calls.append(await request.json())
            return web.json_response(
                {"id": 1, "result": {"content": [{"type": "text", "text": "large result " * 1500}]}}
            )
        if request.path != PATHS[protocol]:
            return None
        requests.append(await request.json())
        number = len(requests)
        value = native_response(protocol, number, owned=number == 1, mixed=number == 2)
        return await wire_response(request, protocol, value, streaming)

    app.state.test_backend["handler"] = backend
    body = request_body(protocol, streaming)
    response = await client.post(
        PATHS[protocol], headers={"Authorization": "Bearer " + key["key"]}, json=body
    )
    assert response.status_code == 200 and "gateway_tool_error" not in response.text
    visible = visible_response(protocol, response, streaming)
    assert len(requests) == 2 and len(calls) == 1
    # A spent budget leaves the client's tools callable; their call is handed off.
    assert requests[1].get("tool_choice") == body.get("tool_choice")
    shown = visible["output"] if protocol == "responses" else visible["content"]
    assert shown[-1]["name"] == "shell"
    limit = "max_tokens" if protocol == "anthropic" else "max_output_tokens"
    assert requests[1][limit] == body[limit]
    assert requests[1]["tools"] == requests[0]["tools"]
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert logs[0]["tool_stop_reason"] == "token_budget"


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
async def test_subagent_resending_tool_history_replays_it_but_cannot_call(
    running_gateway, protocol
):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin)
    requests, calls = [], []

    async def backend(request):
        if request.path == "/mcp":
            calls.append(await request.json())
            return web.json_response({"id": 1, "result": {"content": []}})
        if request.path != PATHS[protocol]:
            return None
        requests.append(await request.json())
        number = len(requests)
        value = native_response(protocol, number, owned=number != 2, mixed=number == 3)
        return await wire_response(request, protocol, value, False)

    app.state.test_backend["handler"] = backend
    body = request_body(protocol, False)
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "fork"}
    response = await client.post(PATHS[protocol], headers=headers, json=body)
    assert response.status_code == 200 and len(requests) == 2 and len(calls) == 1
    field = "input" if protocol == "responses" else "messages"
    visible = response.json()
    body[field].extend(
        visible["output"]
        if protocol == "responses"
        else [{"role": "assistant", "content": visible["content"]}]
    )
    body[field].append({"role": "user", "content": "Explore"})
    # A forked sub-agent resends the main history, including the hidden tool round.
    response = await client.post(
        PATHS[protocol], headers={**headers, "X-Claude-Code-Agent-Id": "child"}, json=body
    )
    assert response.status_code == 200, response.text
    replay = requests[2]
    assert replay["tools"] == requests[0]["tools"]
    assert replay.get("tool_choice") == body.get("tool_choice")
    assert replay[field][: len(requests[1][field])] == requests[1][field]
    # Its gateway call is refused, not run, and its client call is handed off.
    assert len(requests) == 3 and len(calls) == 1
    visible = response.json()
    shown = visible["output"] if protocol == "responses" else visible["content"]
    assert shown[-1]["name"] == "shell" and "— skipped" in response.text
    assert "gateway-3" not in response.text


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
@pytest.mark.parametrize("failure", ["truncated", "error", "incomplete_call", "continuation"])
async def test_native_stream_failure_uses_protocol_terminal(running_gateway, protocol, failure):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin)
    calls = []

    async def backend(request):
        if request.path == "/mcp":
            calls.append(await request.json())
            return web.json_response({"id": 1, "result": {"content": []}})
        if request.path != PATHS[protocol]:
            return None
        if failure == "continuation" and calls:
            return web.json_response({"error": "unavailable"}, status=503)
        value = native_response(protocol, 1, owned=True)
        events = native_events(protocol, value)
        if failure == "truncated":
            events.pop()
        elif failure == "error":
            events[-1] = {"type": "error", "error": {"message": "upstream failure"}}
        elif failure == "incomplete_call" and protocol == "responses":
            events[-1]["type"] = "response.incomplete"
            events[-1]["response"]["status"] = "incomplete"
        elif failure == "incomplete_call":
            events[-2]["delta"]["stop_reason"] = "max_tokens"
        return web.Response(body=b"".join(sse(e) for e in events), content_type="text/event-stream")

    app.state.test_backend["handler"] = backend
    response = await client.post(
        PATHS[protocol],
        headers={"Authorization": "Bearer " + key["key"]},
        json=request_body(protocol, True),
    )
    assert response.status_code == 200
    events = [SSEDecoder.data(f) for f in SSEDecoder().feed(response.content)]
    if protocol == "responses":
        assert events[-1]["type"] == "response.failed"
        failed = events[-1]["response"]
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "server_error"
        assert failed["id"] == events[0]["response"]["id"]
        assert [e["sequence_number"] for e in events] == list(range(len(events)))
        assert not any(item["type"] == "function_call" for item in failed["output"])
    else:
        assert events[-1]["type"] == "error"
        assert events[-1]["error"]["type"] == "gateway_tool_error"
    assert len(calls) == (1 if failure == "continuation" else 0)
    assert not any(e["type"] in {"response.completed", "message_stop"} for e in events)
    assert b"[DONE]" not in response.content


@pytest.mark.parametrize("streaming", [False, True])
async def test_responses_custom_tool_mixed_handoff(running_gateway, streaming):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin)
    requests = []

    async def backend(request):
        if request.path == "/mcp":
            return web.json_response({"id": 1, "result": {"content": []}})
        if request.path != "/v1/responses":
            return None
        requests.append(await request.json())
        return await wire_response(
            request,
            "responses",
            native_response("responses", len(requests), owned=True, mixed=True, custom=True),
            streaming,
        )

    app.state.test_backend["handler"] = backend
    body = request_body("responses", streaming)
    body["tools"] = [{"type": "custom", "name": "shell", "format": {"type": "text"}}]
    response = await client.post(
        "/v1/responses", headers={"Authorization": "Bearer " + key["key"]}, json=body
    )
    value = visible_response("responses", response, streaming)
    assert len(requests) == 1
    assert value["output"][-1]["type"] == "custom_tool_call"
    assert value["output"][-1]["input"] == "pwd"
    assert "openviking_search" not in response.text


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
async def test_native_incompatible_mode_keeps_only_visible_history(
    setup_kernel, credential, policy, protocol
):
    kernel, store, _, _ = setup_kernel
    policy.update(gateway_tools=True, recall=False)
    body = request_body(protocol, False)
    field = "input" if protocol == "responses" else "messages"
    header = {"x-openviking-session": "native-tools"}
    prepared = await kernel.prepare(body, protocol, header, credential, {"id": "u"}, policy)
    native = native_response(protocol, 1, owned=True)
    from openviking_gateway.tool_protocols import hidden_chain

    output = (
        native["output"][:-1]
        if protocol == "responses"
        else [{"role": "assistant", "content": native["content"][:-1]}]
    )
    anchor = hidden_chain([*body[field], *output], protocol)[-1]
    await store.replay.put(
        prepared.scope,
        prepared.session,
        "hidden",
        anchor,
        {
            "messages": native.get("output")
            or [{"role": "assistant", "content": native["content"]}],
            "visible_count": len(output),
        },
    )
    body[field].extend([*output, {"role": "user", "content": "Continue"}])
    body["tool_choice"] = (
        "required" if protocol == "responses" else {"type": "tool", "name": "shell"}
    )
    downgraded = await kernel.prepare(body, protocol, header, credential, {"id": "u"}, policy)
    assert not downgraded.tools_active
    # The opening note names the tools; no definition or hidden call may follow it.
    rest = {**downgraded.body, field: downgraded.body[field][1:]}
    assert "openviking_search" not in orjson.dumps(rest).decode()
    if protocol == "anthropic":
        assert "signature" not in orjson.dumps(downgraded.body).decode()
        assert downgraded.metrics["degradation"] == "hidden_tool_history_unavailable"
    # Re-enabling the compatible mode recovers the original immutable transcript.
    body["tool_choice"] = "auto" if protocol == "responses" else {"type": "auto"}
    restored = await kernel.prepare(body, protocol, header, credential, {"id": "u"}, policy)
    assert (
        restored.tools_active and "openviking_search" in orjson.dumps(restored.body[field]).decode()
    )


async def test_anthropic_count_tokens_uses_same_prompt_without_a_tool_loop(running_gateway):
    _, client, admin, key, seen, _ = running_gateway
    await enable_tools(client, admin)
    body = request_body("anthropic", False)
    header = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "count"}
    response = await client.post("/v1/messages", headers=header, json=body)
    assert response.status_code == 200
    response = await client.post("/v1/messages/count_tokens", headers=header, json=body)
    assert response.json() == {"input_tokens": 20}
    assert orjson.loads(seen[-1][1]) == orjson.loads(seen[-2][1])
    # A sub-agent's count matches its own request, which has no gateway tools.
    child = {**header, "X-Claude-Code-Agent-Id": "child"}
    for path in ("/v1/messages", "/v1/messages/count_tokens"):
        assert (await client.post(path, headers=child, json=body)).status_code == 200
    assert orjson.loads(seen[-1][1]) == orjson.loads(seen[-2][1])
    assert orjson.loads(seen[-1][1])["tools"] == body["tools"]


@pytest.mark.parametrize(
    "extra",
    [{"store": True}, {"previous_response_id": "old"}, {"background": True}, {"conversation": "c"}],
)
async def test_hosted_responses_history_remains_passthrough(running_gateway, extra):
    _, client, admin, key, seen, _ = running_gateway
    await enable_tools(client, admin)
    body = {**request_body("responses", False), **extra}
    response = await client.post(
        "/v1/responses", headers={"Authorization": "Bearer " + key["key"]}, json=body
    )
    assert response.status_code == 200
    assert orjson.loads(seen[-1][1]) == body


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
async def test_native_text_streams_before_round_finishes_and_cancel_closes(protocol):
    import asyncio
    from types import SimpleNamespace

    from openviking_gateway.tool_loop import HiddenToolLoop
    from openviking_gateway.tool_protocols import ResponseCapture

    value = native_response(protocol, 1, owned=True)
    events = native_events(protocol, value)
    closed = []
    pending = asyncio.Event()

    class Content:
        async def iter_any(self):
            for event in events:
                yield sse(event)
                if event["type"] in {"response.output_text.delta", "content_block_delta"}:
                    await pending.wait()
                    pytest.fail("Cancellation must stop reading the upstream")

    response = SimpleNamespace(
        status=200,
        headers={"content-type": "text/event-stream"},
        content=Content(),
        close=lambda: closed.append(True),
    )
    prepared = SimpleNamespace(
        body=request_body(protocol, True),
        protocol=protocol,
        root={"policy": {}},
        metrics={},
        tools_active=True,
        reply_lead="",
    )
    loop = HiddenToolLoop(
        prepared, SimpleNamespace(allowed={"openviking_search"}), None, ResponseCapture(protocol)
    )
    stream = loop.run(response, None)
    while True:
        chunk = await asyncio.wait_for(stream.__anext__(), 0.5)
        if b'"delta"' in chunk:
            break
    await stream.aclose()
    assert closed and not loop.capture.complete


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
async def test_native_replay_survives_restart_edits_and_archive_boundary(
    setup_kernel, credential, policy, protocol
):
    from openviking_gateway.compaction import apply_cut
    from openviking_gateway.storage import SQLiteKernelStore
    from openviking_gateway.tool_protocols import hidden_chain, replay_hidden

    kernel, store, _, encryption = setup_kernel
    policy.update(gateway_tools=True, recall=False)
    body = request_body(protocol, False)
    field = "input" if protocol == "responses" else "messages"
    native = native_response(protocol, 1, owned=True)
    output = (
        native["output"][:-1]
        if protocol == "responses"
        else [{"role": "assistant", "content": native["content"][:-1]}]
    )
    history = native.get("output") or [{"role": "assistant", "content": native["content"]}]
    header = {"x-openviking-session": "native-restart"}
    first = await kernel.prepare(body, protocol, header, credential, {"id": "u"}, policy)
    body[field].extend(output)
    anchor = hidden_chain(body[field], protocol)[-1]
    await store.replay.put(
        first.scope,
        first.session,
        "hidden",
        anchor,
        {"messages": history, "visible_count": len(output)},
    )
    reopened = SQLiteKernelStore(store.path, encryption)
    kernel.store = reopened
    try:
        body[field].append({"role": "user", "content": "Follow up"})
        restored = await kernel.prepare(body, protocol, header, credential, {"id": "u"}, policy)
        assert restored.body[field][1 : 1 + len(history)] == history
        edited = copy.deepcopy(body)
        target = edited[field][-2]
        for block in target.get("content", []):
            if block.get("type") in {"text", "output_text"}:
                block["text"] = "edited answer"
        branch = await kernel.prepare(edited, protocol, header, credential, {"id": "u"}, policy)
        assert branch.body[field] == [first.body[field][0], *edited[field][1:]]
        # The cut anchor is the endpoint of the visible span, never a native
        # hidden transcript index. An old record cannot expand the summary.
        restored.body[field] = copy.deepcopy(body[field])
        restored.body_chain = hidden_chain(body[field], protocol)
        restored.records["replacement", anchor] = {"source": "compaction", "text": "Archived"}
        apply_cut(restored)
        replay = replay_hidden(
            restored.body[field], restored.body_chain, restored.records, protocol
        )
        assert replay == [{"role": "user", "content": "Archived"}, body[field][-1]]
    finally:
        reopened.close()
        kernel.store = store


def test_custom_responses_tools_are_captured_as_complete_pairs():
    from openviking_gateway.capture import capture_messages
    from openviking_gateway.protocols import prefix_chain

    messages = [
        {"role": "user", "content": "check the directory"},
        {
            "type": "custom_tool_call",
            "id": "ct-1",
            "call_id": "c-1",
            "name": "shell",
            "input": "pwd",
        },
        {"type": "custom_tool_call_output", "call_id": "c-1", "output": "/work/project"},
    ]
    captured = capture_messages(messages, prefix_chain(messages))
    assert captured[1]["parts"] == [
        {
            "type": "tool",
            "tool_id": "c-1",
            "tool_name": "shell",
            "tool_input": {"raw": "pwd"},
            "tool_status": "completed",
            "tool_output": "/work/project",
        }
    ]


@pytest.mark.parametrize("archive", [False, True])
async def test_responses_notice_removal_follows_injection_replay_and_archive_cut(
    setup_kernel, credential, policy, archive
):
    from openviking_gateway.protocols import text_content
    from openviking_gateway.tool_protocols import hidden_chain

    kernel, store, viking, _ = setup_kernel
    policy.update(gateway_tools=True, capture=False)
    header = {"x-openviking-session": "notice-order"}
    upstream = {"id": "u"}
    body = {"store": False, "input": [{"role": "user", "content": "Deploy to blue?"}]}
    first = await kernel.prepare(body, "responses", header, credential, upstream, policy)
    body["input"] += [
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "> OpenViking read: viking://user/alice/memories/x — done\n",
                }
            ],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Use blue."}],
        },
        {"role": "user", "content": "What next?"},
    ]
    anchor = hidden_chain(body["input"], "responses")[2]
    await store.replay.put(
        first.scope, first.session, "hidden", anchor, {"messages": [], "visible_count": 2}
    )
    if archive:
        await store.replay.put(
            first.scope,
            first.session,
            "replacement",
            anchor,
            {"source": "compaction", "text": "Archived deployment.", "tokens": 5},
        )
    viking.entries.append({"uri": "viking://user/alice/memories/next.md", "text": "Then verify."})
    upstream["allow_gateway_tools"] = False
    second = await kernel.prepare(body, "responses", header, credential, upstream, policy)
    replay = await kernel.prepare(body, "responses", header, credential, upstream, policy)
    assert replay.body == second.body
    assert not replay.tools_active and "> OpenViking" not in str(replay.body)
    assert text_content(replay.body["input"][-1]).startswith("What next?\n<openviking-context")
    assert "Then verify." in text_content(replay.body["input"][-1])
    if archive:
        assert len(replay.body["input"]) == 2
        assert text_content(replay.body["input"][0]).startswith("Archived deployment.")
    else:
        assert replay.body["input"][0] == first.body["input"][0]
        assert len(replay.body["input"]) == 3
        assert text_content(replay.body["input"][1]) == "Use blue."
        assert replay.metrics["degradation"] == "hidden_tool_history_unavailable"


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
async def test_hidden_round_with_no_visible_anchor_does_not_write_a_root_record(
    setup_kernel, credential, policy, protocol
):
    from types import SimpleNamespace

    from openviking_gateway.tool_loop import HiddenToolLoop
    from openviking_gateway.tool_protocols import ResponseCapture

    kernel, store, _, _ = setup_kernel
    policy.update(gateway_tools=True, recall=False)
    prepared = await kernel.prepare(
        request_body(protocol, False), protocol, {}, credential, {"id": "u"}, policy
    )
    loop = HiddenToolLoop(
        prepared, SimpleNamespace(allowed={"openviking_search"}), store, ResponseCapture(protocol)
    )
    loop.hidden = True
    value = native_response(protocol, 1)
    value["output" if protocol == "responses" else "content"] = []
    loop.adapter.begin()
    loop.adapter.load(value)
    loop.adapter.end()
    await loop.persist()
    assert ("hidden", "") not in await store.replay.read(prepared.scope, prepared.session, [""])
    assert prepared.metrics["degradation"] == "hidden_reply_without_anchor"


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic"])
async def test_tool_loop_reply_reads_like_a_relayed_one(
    setup_kernel, credential, policy, protocol, mixed
):
    """Calls left for the client hand the turn off; only a final answer completes it."""
    from types import SimpleNamespace

    from openviking_gateway.tool_loop import HiddenToolLoop
    from openviking_gateway.tool_protocols import ResponseCapture

    kernel, store, _, _ = setup_kernel
    policy.update(gateway_tools=True, recall=False)
    prepared = await kernel.prepare(
        request_body(protocol, False), protocol, {}, credential, {"id": "u"}, policy
    )
    loop = HiddenToolLoop(
        prepared, SimpleNamespace(allowed={"openviking_search"}), store, ResponseCapture(protocol)
    )
    loop.adapter.begin()
    loop.adapter.load(native_response(protocol, 1, mixed=mixed))
    loop.round = loop.adapter.end()
    loop.adapter.publish_calls(loop.round.calls)
    await loop.persist()
    assert loop.capture.finished
    assert (loop.capture.handoff, loop.capture.complete) == (mixed, not mixed)


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
def test_native_file_attachments_preserve_bytes(protocol):
    from openviking_gateway.tool_executor import attachment_bytes, attachments

    part = (
        {"type": "input_file", "filename": "note.txt", "file_data": "aGVsbG8="}
        if protocol == "responses"
        else {
            "type": "document",
            "title": "note.txt",
            "source": {"type": "base64", "media_type": "text/plain", "data": "aGVsbG8="},
        }
    )
    body = {
        "input" if protocol == "responses" else "messages": [{"role": "user", "content": [part]}]
    }
    assert attachment_bytes(attachments(body, protocol)[0], 100) == ("note.txt", b"hello")


@pytest.mark.parametrize("blocked", ["disabled", "forced", "collision"])
async def test_anthropic_blocked_tools_preserve_client_thinking(
    setup_kernel, credential, policy, blocked
):
    kernel, store, _, _ = setup_kernel
    policy.update(gateway_tools=True, recall=False, capture=False)
    body = request_body("anthropic", False)
    header, upstream = {"x-openviking-session": "client-tools"}, {"id": "u"}
    prepared = await kernel.prepare(body, "anthropic", header, credential, upstream, policy)
    # A hidden round on another branch must not invalidate this request's signatures.
    await store.replay.put(
        prepared.scope,
        prepared.session,
        "hidden",
        "another-branch",
        {"messages": [], "visible_count": 1},
    )
    reply = native_response("anthropic", 1, mixed=True)
    body["messages"].extend(
        [
            {"role": "assistant", "content": reply["content"]},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "client-1", "content": "/work"}],
            },
        ]
    )
    if blocked == "disabled":
        upstream["allow_gateway_tools"] = False
    elif blocked == "forced":
        body["tool_choice"] = {"type": "tool", "name": "shell"}
    else:
        body["tools"].append({"name": "openviking_search", "input_schema": {"type": "object"}})
    for _ in range(2):
        result = await kernel.prepare(body, "anthropic", header, credential, upstream, policy)
        assert not result.tools_active
        assert result.body["messages"] == [prepared.body["messages"][0], *body["messages"][1:]]
        assert result.metrics.get("degradation") != "hidden_tool_history_unavailable"


@pytest.mark.parametrize("arguments", [None, "", "{}", "{broken"])
async def test_anthropic_stream_no_argument_tool(running_gateway, arguments):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin)
    requests, calls = [], []

    async def backend(request):
        if request.path == "/mcp":
            calls.append(await request.json())
            return web.json_response({"id": 1, "result": {"content": []}})
        if request.path != "/v1/messages":
            return None
        requests.append(await request.json())
        value = native_response("anthropic", len(requests), owned=len(requests) == 1)
        if len(requests) > 1:
            return await wire_response(request, "anthropic", value, True)
        value["content"][-1].update(name="openviking_health", input={})
        events = native_events("anthropic", value)
        events = [e for e in events if e.get("delta", {}).get("type") != "input_json_delta"]
        if arguments is not None:
            index = len(value["content"]) - 1
            position = next(
                i
                for i, e in enumerate(events)
                if e["type"] == "content_block_stop" and e["index"] == index
            )
            events.insert(
                position,
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": arguments},
                },
            )
        return web.Response(body=b"".join(sse(e) for e in events), content_type="text/event-stream")

    app.state.test_backend["handler"] = backend
    response = await client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer " + key["key"]},
        json=request_body("anthropic", True),
    )
    if arguments == "{broken":
        assert "gateway_tool_error" in response.text and not calls
    else:
        visible_response("anthropic", response, True)
        assert len(requests) == 2 and len(calls) == 1
        assert requests[1]["messages"][-2]["content"][-1]["input"] == {}


@pytest.mark.parametrize("used_gateway_tool", [False, True])
async def test_admin_disabling_anthropic_tools_preserves_history_and_logs(
    running_gateway, used_gateway_tool
):
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(client, admin, capture=False)
    requests = []
    disabled = False

    async def backend(request):
        if request.path == "/mcp":
            return web.json_response({"id": 1, "result": {"content": []}})
        if request.path != "/v1/messages":
            return None
        payload = await request.json()
        requests.append(payload)
        if disabled:
            # Anthropic requires thinking before the last assistant's tool_use
            # when the client returns its tool result with thinking enabled.
            assistant = next(m for m in reversed(payload["messages"]) if m["role"] == "assistant")
            blocks = assistant["content"]
            if any(b["type"] == "tool_use" for b in blocks) and blocks[0]["type"] != "thinking":
                return web.json_response({"error": "missing thinking before tool_use"}, status=400)
        return web.json_response(
            native_response(
                "anthropic",
                len(requests),
                owned=used_gateway_tool and len(requests) == 1,
                mixed=not used_gateway_tool and not disabled,
            )
        )

    app.state.test_backend["handler"] = backend
    body = request_body("anthropic", False)
    headers = {"Authorization": "Bearer " + key["key"], "X-OpenViking-Session": "admin-switch"}
    first = await client.post("/v1/messages", headers=headers, json=body)
    assert first.status_code == 200, first.text
    body["messages"].extend(
        [
            {"role": "assistant", "content": first.json()["content"]},
            {
                "role": "user",
                "content": "Continue"
                if used_gateway_tool
                else [{"type": "tool_result", "tool_use_id": "client-1", "content": "/work"}],
            },
        ]
    )
    upstreams = (await client.get("/admin/upstreams", headers=admin)).json()
    upstream = next(u for u in upstreams if u["id"] == "anthropic")
    changed = await client.put(
        "/admin/upstreams/anthropic",
        headers=admin,
        json={
            "name": "anthropic",
            "protocol": "anthropic",
            "base_url": upstream["base_url"],
            "models": ["model"],
            "allow_gateway_tools": False,
        },
    )
    assert changed.status_code == 200, changed.text
    disabled = True
    response = await client.post("/v1/messages", headers=headers, json=body)
    assert response.status_code == 200, response.text
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert logs[0]["tool_skip_reason"] == "upstream_tools_disabled"
    if used_gateway_tool:
        assert logs[0]["degradation"] == "hidden_tool_history_unavailable"
        assert "signature" not in orjson.dumps(requests[-1]["messages"]).decode()
        # The echoed notices go with the hidden history they described.
        assert "> OpenViking" in orjson.dumps(body["messages"]).decode()
        assert "> OpenViking" not in orjson.dumps(requests[-1]["messages"]).decode()
    else:
        assert not logs[0].get("degradation")
        assert requests[-1]["messages"] == [requests[0]["messages"][0], *body["messages"][1:]]
