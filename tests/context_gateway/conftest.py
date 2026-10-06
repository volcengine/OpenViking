"""Standalone gateway tests intentionally need no vector engine or LLM SDK."""

import copy
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import orjson
import pytest
import pytest_asyncio
from aiohttp import web
from cryptography.fernet import Fernet

from context_gateway.app import create_app
from context_gateway.config import ContextGatewayConfig
from context_gateway.kernel import MemoryKernel
from context_gateway.models import Policy
from context_gateway.storage import SQLiteKernelStore


def mcp_tool(name, properties, required=()):
    return {
        "name": name,
        "description": "OpenViking " + name,
        "inputSchema": {"type": "object", "properties": properties, "required": list(required)},
    }


TEXT = {"type": "string"}
MCP_TOOLS = [
    mcp_tool("find", {"query": TEXT}, ("query",)),
    mcp_tool("search", {"query": TEXT, "session_id": TEXT}, ("query",)),
    mcp_tool("read", {"uris": {"type": "array", "items": TEXT}}, ("uris",)),
    mcp_tool("list", {"uri": TEXT}, ("uri",)),
    mcp_tool("write", {"uri": TEXT, "content": TEXT}, ("uri", "content")),
    mcp_tool("add_resource", {"path": TEXT, "description": TEXT}),
    mcp_tool("add_skill", {"path": TEXT, "data": TEXT, "target_uri": TEXT}),
    mcp_tool("grep", {"uri": TEXT, "pattern": TEXT}, ("uri", "pattern")),
    mcp_tool("glob", {"pattern": TEXT}, ("pattern",)),
    mcp_tool("health", {}),
]


class FakeViking:
    def __init__(self):
        self.catalog = copy.deepcopy(MCP_TOOLS)
        self.tool_keys = []
        self.tools_failure = None
        self.recalls = []
        self.writes = []
        self.write_sessions = []
        self.archive_status = "pending"
        self.live = {}
        self.archived = {}
        self.commits = []
        self.entries = [
            {
                "uri": "viking://user/alice/memories/deploy.md",
                "text": "Deploy using the blue cluster.",
            }
        ]
        self.failure = None
        self.rendered = None
        self.profile_requests = []
        self.profile = ""
        self.preferences = []
        self.entities = []
        self.skills = []

    async def tools(self, key):
        self.tool_keys.append(key)
        if self.tools_failure:
            raise self.tools_failure
        return copy.deepcopy(self.catalog)

    async def recall(self, key, query, policy, exclude, budget):
        self.recalls.append((key, query, copy.deepcopy(exclude), budget))
        if self.failure:
            raise self.failure
        entries = [x for x in self.entries if x["uri"] not in exclude]
        rendered = self.rendered
        if rendered is None:
            rendered = "\n\n".join(x["uri"] + "\n" + x.get("text", "") for x in entries)
        return {"entries": entries, "rendered": rendered}

    async def request(self, method, path, key, body=None, timeout=30):
        self.profile_requests.append((method, path, key, timeout))
        parsed = urlsplit(path)
        uri = parse_qs(parsed.query).get("uri", [""])[0]
        if parsed.path == "/api/v1/system/status":
            return {"user": "alice"}
        if parsed.path == "/api/v1/skills":
            return {"skills": self.skills}
        if parsed.path == "/api/v1/content/read":
            return self.profile
        if uri == "viking://user":
            return [{"name": "alice", "isDir": True}]
        return self.preferences if uri.endswith("/preferences") else self.entities

    async def health(self, key="", require_identity=True):
        return {"version": "0.4.16", "role": "user", "account_id": "tenant", "user_id": "alice"}

    async def create_session(self, key, session):
        return {}

    async def write(self, key, session, messages):
        self.writes.append(copy.deepcopy(messages))
        self.write_sessions.append(session)
        self.live.setdefault(session, []).extend(copy.deepcopy(messages))
        return {
            "pending_tokens": sum(len(orjson.dumps(m["parts"])) // 3 for m in self.live[session])
        }

    async def pending_tokens(self, key, session):
        return sum(len(orjson.dumps(m["parts"])) // 3 for m in self.live.get(session, []))

    async def commit(self, key, session, keep=0):
        self.commits.append((session, keep))
        index = sum(1 for a in self.archived if f"/{session}/" in a) + 1
        uri = f"viking://user/alice/sessions/{session}/history/archive_{index:03d}"
        messages = self.live.get(session, [])
        self.archived[uri] = messages[:-keep] if keep else messages
        self.live[session] = messages[-keep:] if keep else []
        return {"archive_uri": uri}

    async def archive_state(self, key, uri=""):
        return self.archive_status


@pytest.fixture
def credential():
    return {
        "account": "tenant",
        "user_id": "alice",
        "id": "key-id",
        "openviking_key": "user-secret",
    }


@pytest_asyncio.fixture
async def setup_kernel(tmp_path):
    key = Fernet.generate_key().decode()
    store = SQLiteKernelStore(tmp_path / "kernel.sqlite3", key)
    await store.initialize()
    viking = FakeViking()
    kernel = MemoryKernel(store, viking)
    yield kernel, store, viking, key
    store.close()


@pytest.fixture
def policy():
    # Tests opt in to OpenViking tools explicitly, and then get every tool.
    policy = Policy(gateway_tools=False, disabled_tools=[]).model_dump()
    return {**policy, "id": "policy", "revision": 1}


async def replay_records(store, request, kind):
    values = await store.replay.read(
        request.scope, request.session, ["", *request.chain, *request.body_chain]
    )
    return {key: value for key, value in values.items() if key[0] == kind}


async def update_capture(store, request, **changes):
    from context_gateway.capture import ready_at

    old = await store.capture.get(request.scope, request.session)
    value = {**old.value, **changes}
    assert await store.capture.swap(request.scope, request.session, old, value, ready_at(value))
    return value


async def make_due(store):
    from context_gateway.capture import ready_at

    with store.connect() as c:
        keys = list(c.execute("SELECT scope,session FROM capture"))
    for scope, session in keys:
        old = await store.capture.get(scope, session)
        value = copy.deepcopy(old.value)
        for turn in value.get("pending", []):
            turn["ready"] = 0
        if value.get("error"):
            value["error"]["retry_at"] = 0
        if value.get("archive"):
            value["archive"]["next_check"] = 0
        assert await store.capture.swap(scope, session, old, value, ready_at(value))


@pytest_asyncio.fixture
async def running_gateway(tmp_path, monkeypatch):
    captured = []
    writes = []
    override = {}
    viking = FakeViking()

    async def backend(request):
        if request.path == "/mcp" and (await request.json()).get("method") == "tools/list":
            key = request.headers.get("X-API-Key")
            override.setdefault("tools_calls", []).append(key)
            # Maps an OpenViking key to the HTTP status its tools/list gets.
            status = override.get("tools_status", {}).get(key, 200)
            if status != 200:
                return web.json_response({"error": "private server detail"}, status=status)
            return web.json_response(
                {"jsonrpc": "2.0", "id": 1, "result": {"tools": override.get("catalog", MCP_TOOLS)}}
            )
        if override.get("handler"):
            response = await override["handler"](request)
            if response is not None:
                return response
        raw = await request.read()
        body = json.loads(raw) if raw else {}
        if request.path == "/health":
            key = request.headers.get("X-API-Key")
            return web.json_response(
                {
                    "version": "0.4.16",
                    "status": "ok",
                    "auth_mode": "api_key",
                    "role": "root" if key == "root" else "user",
                    "account_id": "other" if key == "other" else "tenant",
                    "user_id": "alice",
                }
            )
        if request.path == "/api/v1/search/search":
            assert body["mode"] == "context" and "session_id" not in body
            return web.json_response(
                {
                    "status": "ok",
                    "result": {
                        "entries": [
                            {"uri": "viking://user/alice/memories/x", "text": "Memory data"}
                        ],
                        "rendered": "viking://user/alice/memories/x\nMemory data",
                    },
                }
            )
        if request.path in {"/api/v1/system/status", "/api/v1/fs/ls", "/api/v1/skills"}:
            result = await viking.request("GET", str(request.rel_url), "synthetic")
            return web.json_response({"status": "ok", "result": result})
        if request.path == "/api/v1/sessions":
            return web.json_response({"status": "ok", "result": {}})
        if request.path.startswith("/api/v1/sessions/"):
            session = request.path.split("/")[4]
            if request.path.endswith("/messages/batch"):
                writes.append(body)
                result = await viking.write("synthetic", session, body["messages"])
            elif request.path.endswith("/commit"):
                result = await viking.commit("synthetic", session, body["keep_recent_count"])
            else:
                result = {"pending_tokens": await viking.pending_tokens("synthetic", session)}
            return web.json_response({"status": "ok", "result": result})
        if request.path == "/api/v1/content/read":
            return web.json_response({"status": "error"}, status=404)
        captured.append((request.path, raw, dict(request.headers)))
        if body.get("model") == "error":
            return web.Response(
                body=b'{"error":{"raw":"provider error"}}',
                status=429,
                headers={
                    "Retry-After": "4",
                    "X-Should-Retry": "true",
                    "Request-Id": "upstream-id",
                    "Content-Type": "application/json",
                },
            )
        if body.get("stream"):
            response = web.StreamResponse(
                headers={"Content-Type": "text/event-stream", "Request-Id": "stream-id"}
            )
            await response.prepare(request)
            stream = b'data: {"id":"r-1","choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\r\n\r\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":123,"completion_tokens":1,"prompt_tokens_details":{"cached_tokens":100}}}\n\ndata: [DONE]\n\n'
            for start in range(0, len(stream), 7):
                await response.write(stream[start : start + 7])
            await response.write_eof()
            return response
        if "/responses" in request.path:
            return web.json_response(
                {
                    "id": "resp-1",
                    "object": "response",
                    "status": "completed",
                    "output": [
                        {
                            "role": "assistant",
                            "type": "message",
                            "content": [{"type": "output_text", "text": "hello"}],
                        }
                    ],
                }
            )
        if request.path.endswith("count_tokens"):
            return web.json_response({"input_tokens": 20})
        if request.path.endswith("messages"):
            return web.json_response(
                {
                    "id": "msg-1",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hello"}],
                    "stop_reason": "end_turn",
                }
            )
        return web.json_response(
            {
                "id": "r-1",
                "choices": [
                    {"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
                ],
            }
        )

    server = web.Application()
    server.router.add_route("*", "/{path:.*}", backend)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    monkeypatch.setenv("OPENVIKING_CONTEXT_GATEWAY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OPENVIKING_CONTEXT_GATEWAY_ADMIN_TOKEN", "admin-" + "x" * 32)
    config = ContextGatewayConfig(enabled=True, storage_path=str(tmp_path), openviking_url=base)
    app = create_app(config)
    app.state.test_backend = override
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            admin = {"Authorization": "Bearer admin-" + "x" * 32, "X-OpenViking-Account": "tenant"}
            for protocol in ("chat", "anthropic", "responses"):
                response = await client.put(
                    f"/admin/upstreams/{protocol}",
                    headers=admin,
                    json={
                        "name": protocol,
                        "protocol": protocol,
                        "base_url": base,
                        "api_key": "model-secret",
                        "models": ["model", "error"],
                    },
                )
                assert response.status_code == 200, response.text
            assert (
                await client.put(
                    "/admin/policies/default",
                    headers=admin,
                    json={"name": "Default", "gateway_tools": False},
                )
            ).status_code == 200
            minted = await client.post(
                "/admin/keys",
                headers=admin,
                json={
                    "name": "test",
                    "openviking_key": "user-key",
                    "policy_id": "default",
                    "upstream_ids": ["chat", "anthropic", "responses"],
                },
            )
            assert minted.status_code == 200, minted.text
            yield app, client, admin, minted.json(), captured, writes
    await runner.cleanup()
