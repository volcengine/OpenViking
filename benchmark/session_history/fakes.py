# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""In-process stand-ins for the upstream model and OpenViking, for the self-test.

``ScriptedModel`` reads its context the way a model would, through per-needle patterns: a
``looker`` searches the saved history whenever a lookup tool is offered, a ``guesser`` never
does and invents what it lacks. ``FakeOpenViking`` stores what the gateway delivers and answers
``session_history`` and ``grep`` over it. Its grep cannot see tool outputs over 20,000 chars,
so the self-test can tell the tools apart; the real grep reaches them under ``tool-results/``.
"""

import asyncio
import json
import re
import uuid

from aiohttp import web

SUMMARY_MARK = "Summarize the conversation above."
EXTERNALIZED_CHARS = 20_000
# How the model reads each needle out of text; "changed" prefers the later value.
PATTERNS = {
    "token": [r"token: ([0-9a-f]{32})"],
    "error": [r"ReconcileError: (balance drift on account [^\n|\\]*?\d)(?:\s|\\n|$)"],
    "preference": [r"with the tag \[([A-Z]+-\d+)\]"],
    "changed": [
        r"switch the settlement queue to (settle-[a-z]+-\d+)",
        r"settlement queue in config/queues\.toml to (settle-[a-z]+-\d+)",
    ],
    "long_output": [r"payments-gateway@sha256:([0-9a-f]{40})"],
    "injection": [r"## (\d+\.\d+\.\d+) \(latest\)"],
    "gap": [r"acquired migration lock (mlk-[0-9a-f]{8})"],
}
# What the guesser makes up: a value of the needle's shape that no scenario produces.
INVENTED = {
    "token": "f" * 32,
    "error": "balance drift on account AC-00000: expected 1.00, found 2.00",
    "preference": "QUILL-000",
    "changed": "settle-gray-00",
    "long_output": "e" * 40,
    "injection": "1.0.1",
    "gap": "mlk-deadbeef",
}
CANARY = re.compile(r"reply with only the word ([A-Z]+-\d+)")
LOOKUP_TOOLS = ("openviking_session_history", "openviking_grep")


def message_text(message):
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    calls = json.dumps(message.get("tool_calls") or [])
    return (content or "") + ("\n" + calls if message.get("tool_calls") else "")


def find(kind, text):
    for pattern in PATTERNS[kind]:
        if match := re.search(pattern, text):
            return match[1]
    return ""


class ScriptedModel:
    """Chat Completions replies for the models named ``looker`` and ``guesser``."""

    def __init__(self, probes):
        # Probe questions are the same in every seed and arm; values are read from context.
        self.probes = {probe.question: probe for probe in probes}
        self.offered = []  # (model, sorted openviking_* tool names) per request

    def reply(self, body):
        messages = body.get("messages", [])
        tools = {t["function"]["name"] for t in body.get("tools", []) if "function" in t}
        self.offered.append((body.get("model"), sorted(t for t in tools if "openviking" in t)))
        users = [m for m in messages if m.get("role") == "user"]
        last = message_text(users[-1]) if users else ""
        if SUMMARY_MARK in last:
            return text_reply(self.summary(messages))
        question = next(
            (q for m in reversed(users) for q in self.probes if q in message_text(m)), None
        )
        if question is None or body.get("tool_choice") == "none":
            return text_reply("ok")
        probe = self.probes[question]
        kind = probe.kind
        index = max(i for i, m in enumerate(messages) if question in message_text(m))
        context = "\n".join(message_text(m) for m in messages[:index])
        looked = "\n".join(
            message_text(m)
            for m in messages[index:]
            if m.get("role") == "tool" and m.get("tool_call_id", "").startswith("lookup_")
        )
        if body.get("model") == "looker":
            available = [name for name in LOOKUP_TOOLS if name in tools]
            if available and not looked:
                return lookup_call(available[0], probe.search, context)
            if canary := CANARY.search(looked):
                # Obeys an instruction it read back, so the harness can see compliance scored.
                return text_reply(canary[1])
            value = find(kind, looked) or find(kind, context)
            if value:
                return text_reply(f"It is {value}.")
            return text_reply("I could not find that in the saved history.")
        value = find(kind, context)
        return text_reply(f"It is {value or INVENTED[kind]}.")

    @staticmethod
    def summary(messages):
        """A summary that keeps only the earlier queue value: a typical stale summary fact."""
        text = "\n".join(message_text(m) for m in messages)
        queue = re.search(PATTERNS["changed"][1], text)
        stale = (
            f" Earlier we set the settlement queue in config/queues.toml to {queue[1]}."
            if queue
            else ""
        )
        return "We are working on ledgerd and fixing a reconcile balance drift." + stale


def text_reply(text):
    return completion({"role": "assistant", "content": text}, "stop")


def lookup_call(name, query, context):
    if name == "openviking_session_history":
        arguments = {"action": "search", "query": query}
    else:
        # The grep hint names the session's directory; search only there.
        where = re.search(r"viking://user/[^/\s]+/sessions/[^/\s]+/", context)
        arguments = {"uri": where[0] if where else "viking://user/", "pattern": query}
    call = {
        "id": "lookup_" + uuid.uuid4().hex[:8],
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    return completion({"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls")


def completion(message, finish):
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
    }


def item_text(message):
    parts = []
    for part in message.get("parts", []):
        if part.get("type") == "text":
            parts.append(part["text"])
        elif part.get("type") == "tool":
            parts.append(
                f"[tool {part['tool_name']}] input: {json.dumps(part['tool_input'])}\n"
                f"[tool {part['tool_name']}] output: {part['tool_output']}"
            )
    return "\n".join(parts)


class FakeOpenViking:
    """Sessions keep what the gateway wrote; writes that carry ``slow_marker`` take a while."""

    def __init__(self, catalog, slow_marker="", slow_seconds=0.0):
        self.catalog, self.sessions = catalog, {}
        self.slow_marker, self.slow_seconds = slow_marker, slow_seconds
        self.calls = []

    async def write(self, session, messages):
        if self.slow_marker and self.slow_marker in json.dumps(messages):
            await asyncio.sleep(self.slow_seconds)
        self.sessions.setdefault(session, []).extend(messages)

    def items(self, sessions):
        return [m for s in sessions for m in self.sessions.get(s, [])]

    def tool(self, name, args):
        self.calls.append((name, args))
        sessions = [s for s in str(args.get("session", "")).split(",") if s]
        if name == "session_history":
            items = self.items(sessions)
            query = str(args.get("query", "")).lower()
            hits = [m for m in reversed(items) if query and query in item_text(m).lower()]
            lines = [f"Saved conversation history. {len(items)} items searched."]
            for hit in hits[:5]:
                text = item_text(hit)
                at = text.lower().index(query)
                lines.append(f"[{hit['role']}]\n| " + text[max(0, at - 300) : at + 600])
            if not hits:
                lines.append(f'No match for "{query}".')
            return "\n".join(lines)
        if name == "grep":
            pattern = str(args.get("pattern", "")).lower()
            uri = str(args.get("uri", ""))
            rows = []
            for message in self.items([s for s in self.sessions if f"/sessions/{s}/" in uri]):
                visible = {
                    **message,
                    "parts": [
                        {**p, "tool_output": "[stored outside the message]"}
                        if len(p.get("tool_output", "")) > EXTERNALIZED_CHARS
                        else p
                        for p in message.get("parts", [])
                    ],
                }
                line = json.dumps(visible)
                if pattern and pattern in line.lower():
                    rows.append(line[:1500])
            return "\n".join(rows[:10]) or "no matches"
        return f"{name}: nothing here"

    async def handle(self, request):
        path = request.path
        body = await request.json() if request.can_read_body else {}
        if path == "/health":
            return web.json_response(
                {
                    "status": "ok",
                    "version": "0.5.0",
                    "auth_mode": "api_key",
                    "role": "user",
                    "account_id": "tenant",
                    "user_id": "alice",
                }
            )
        if path == "/mcp":
            if body.get("method") == "tools/list":
                result = {"tools": self.catalog}
            else:
                params = body.get("params", {})
                text = self.tool(params.get("name", ""), params.get("arguments", {}))
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            return web.json_response({"jsonrpc": "2.0", "id": body.get("id", 1), "result": result})
        if path.startswith("/api/v1/sessions/"):
            session = path.split("/")[4]
            if path.endswith("/messages/batch"):
                await self.write(session, body["messages"])
            if path.endswith("/commit"):
                return ok({"archive_uri": f"viking://user/alice/sessions/{session}/history/a"})
            return ok({"pending_tokens": 0})
        if path == "/api/v1/search/search":
            return ok({"entries": [], "rendered": ""})
        return ok({})


def ok(result):
    return web.json_response({"status": "ok", "result": result})


def mcp_tool(name, properties, required=()):
    return {
        "name": name,
        "description": "OpenViking " + name,
        "inputSchema": {"type": "object", "properties": properties, "required": list(required)},
    }


TEXT = {"type": "string"}
CATALOG = [
    mcp_tool("find", {"query": TEXT}, ("query",)),
    mcp_tool("search", {"query": TEXT}, ("query",)),
    mcp_tool("read", {"uris": {"type": "array", "items": TEXT}}, ("uris",)),
    mcp_tool("grep", {"uri": TEXT, "pattern": TEXT}, ("uri", "pattern")),
    mcp_tool(
        "session_history",
        {
            "action": {"type": "string", "enum": ["search", "read", "overview"]},
            **dict.fromkeys(("session", "query", "id"), TEXT),
            "offset": {"type": "integer"},
        },
        ("action",),
    ),
]


async def serve(viking, model):
    """One aiohttp server: /v1/... is the model, everything else OpenViking unless ``None``."""

    async def route(request):
        if request.path == "/v1/chat/completions":
            return web.json_response(model.reply(await request.json()))
        if request.path == "/v1/models":
            return web.json_response({"data": []})
        if viking is None:
            return web.json_response({"error": "not found"}, status=404)
        return await viking.handle(request)

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_route("*", "/{path:.*}", route)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
