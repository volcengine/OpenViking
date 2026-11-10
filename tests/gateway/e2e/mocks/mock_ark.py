"""Inspectable provider fixture: three protocols, delayed streams and hidden tools."""

import argparse
import asyncio
import hashlib
import json
import time
import zlib

from aiohttp import web

MODELS = ["mock", "mock-silent", "mock-long", "mock-tools", "mock-silent-gzip"]
TEXT = "mock response complete"


def tool_pending(body):
    """Request a gateway search once per user turn, including replayed histories."""
    for item in reversed(body.get("messages", [])):
        if item.get("role") == "tool":
            return False
        if item.get("role") == "user":
            break
    return any(
        tool.get("function", {}).get("name") == "openviking_search"
        for tool in body.get("tools", [])
    )


def reply(protocol, body, number):
    model = body.get("model", "mock")
    if protocol == "chat":
        message = {"role": "assistant", "content": TEXT}
        use_tool = "tools" in model and not body.get("stream") and tool_pending(body)
        if use_tool:
            users = [item for item in body["messages"] if item.get("role") == "user"]
            fingerprint = json.dumps(users, sort_keys=True).encode()
            call_id = "call_mock_" + hashlib.sha256(fingerprint).hexdigest()[:16]
            function = {"name": "openviking_search", "arguments": '{"query":"blue"}'}
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": call_id, "type": "function", "function": function}],
            }
        return {
            "id": f"chatcmpl_mock_{number}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if use_tool else "stop",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
        }
    if protocol == "anthropic":
        return {
            "id": f"msg_mock_{number}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": TEXT}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 20, "output_tokens": 3},
        }
    return {
        "id": f"resp_mock_{number}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": f"msg_mock_{number}",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": TEXT, "annotations": [], "logprobs": []}
                ],
            }
        ],
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "parallel_tool_calls": True,
        "tools": [],
        "tool_choice": "auto",
        "usage": {
            "input_tokens": 20,
            "output_tokens": 3,
            "total_tokens": 23,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def events(protocol, value):
    """Build complete provider event sequences without relying on gateway parsers."""
    parts = ("mock ", "response ", "complete")
    if protocol == "chat":
        base = {k: value[k] for k in ("id", "created", "model")}
        base["object"] = "chat.completion.chunk"

        def chunk(delta, finish=None):
            return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        return [
            chunk({"role": "assistant", "content": ""}),
            *(chunk({"content": part}) for part in parts),
            chunk({}, value["choices"][0]["finish_reason"]),
            {**base, "choices": [], "usage": value["usage"]},
        ]
    if protocol == "anthropic":
        block = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
        return [
            {
                "type": "message_start",
                "message": {
                    **value,
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 20, "output_tokens": 0},
                },
            },
            block,
            *(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": p},
                }
                for p in parts
            ),
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": value["stop_reason"], "stop_sequence": None},
                "usage": {"output_tokens": 3},
            },
            {"type": "message_stop"},
        ]
    item = value["output"][0]
    part = item["content"][0]
    fields = {"output_index": 0, "item_id": item["id"], "content_index": 0}
    result = [
        *(
            {"type": name, "response": {**value, "status": "in_progress", "output": []}}
            for name in ("response.created", "response.in_progress")
        ),
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**item, "status": "in_progress", "content": []},
        },
        {"type": "response.content_part.added", **fields, "part": {**part, "text": ""}},
        *(
            {"type": "response.output_text.delta", **fields, "delta": p, "logprobs": []}
            for p in parts
        ),
        {"type": "response.output_text.done", **fields, "text": TEXT, "logprobs": []},
        {"type": "response.content_part.done", **fields, "part": part},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": value},
    ]
    return [{**event, "sequence_number": number} for number, event in enumerate(result)]


def create_app():
    records = []
    responses = {}
    counter = 0

    async def handle(request):
        nonlocal counter
        if request.path == "/_records":
            return web.json_response({"requests": records})
        if request.path.endswith("/models"):
            return web.json_response(
                {
                    "object": "list",
                    "data": [
                        {"id": name, "object": "model", "created": 0, "owned_by": "mock"}
                        for name in MODELS
                    ],
                }
            )
        paths = {
            "/v1/chat/completions": "chat",
            "/v1/messages": "anthropic",
            "/v1/responses": "responses",
        }
        if request.path not in paths or request.method != "POST":
            raise web.HTTPNotFound()
        body = await request.json()
        counter += 1
        record = {
            "path": request.path,
            "key": request.headers.get("X-API-Key", request.headers.get("Authorization", "")),
            "source_pod": request.remote,
            "body": body,
            "status": 200,
        }
        records.append(record)
        protocol = paths[request.path]
        effective = body
        if protocol == "responses":
            items = body.get("input", [])
            items = [{"role": "user", "content": items}] if isinstance(items, str) else items
            if previous := body.get("previous_response_id"):
                prior = responses.get((record["key"], previous))
                if prior is None:
                    record["status"] = 404
                    return web.json_response(
                        {"error": {"message": "Unknown previous_response_id"}}, status=404
                    )
                items = [*prior, *items]
            record["effective_input"] = items
            effective = {**body, "input": items}
        value = reply(protocol, effective, counter)

        def remember():
            if protocol == "responses":
                responses[record["key"], value["id"]] = [
                    *record["effective_input"],
                    *value["output"],
                ]

        if not body.get("stream"):
            remember()
            return web.json_response(value)
        model = body.get("model", "mock")
        headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        compressor = None
        if "gzip" in model:
            headers["Content-Encoding"] = "gzip"
            compressor = zlib.compressobj(wbits=31)
        response = web.StreamResponse(headers=headers)
        await response.prepare(request)
        sequence = events(protocol, value)
        try:
            for index, event in enumerate(sequence):
                # Pause only after a complete SSE event has reached the client.
                if "silent" in model and index == 2:
                    await asyncio.sleep(40)
                if "long" in model and index:
                    await asyncio.sleep(60 / (len(sequence) - 1))
                frame = "" if protocol == "chat" else f"event: {event['type']}\n"
                data = (frame + "data: " + json.dumps(event) + "\n\n").encode()
                if compressor:
                    data = compressor.compress(data) + compressor.flush(zlib.Z_SYNC_FLUSH)
                await response.write(data)
            if protocol == "chat":
                data = b"data: [DONE]\n\n"
                await response.write(compressor.compress(data) if compressor else data)
            if compressor:
                await response.write(compressor.flush())
            await response.write_eof()
            remember()
        except ConnectionResetError:
            # Truncation tests disconnect mid-stream on purpose.
            pass
        return response

    app = web.Application(client_max_size=32 * 1024**2)
    app.router.add_route("*", "/{path:.*}", handle)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port, access_log=None)
