"""Standalone gateway tests intentionally need no vector engine or LLM SDK."""

import copy
import json
import os
import uuid
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
import orjson
import pytest
import pytest_asyncio
from aiohttp import web

from openviking.crypto import FileEncryptor, LocalFileProvider
from openviking_gateway.app import create_app
from openviking_gateway.config import GatewayConfig
from openviking_gateway.kernel import MemoryKernel
from openviking_gateway.models import Policy
from openviking_gateway.storage import SQLiteKernelStore, SQLiteManagementStore


def local_encryptor(key_file):
    """OpenViking's local-provider encryptor; it creates the root key file on first use."""
    return FileEncryptor(LocalFileProvider(str(key_file)))


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

    async def identify(self, key=""):
        return await self.health(key)

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
    encryptor = local_encryptor(tmp_path / "master.key")
    store = SQLiteKernelStore(tmp_path / "kernel.sqlite3", encryptor)
    await store.initialize()
    viking = FakeViking()
    kernel = MemoryKernel(store, viking)
    yield kernel, store, viking, encryptor
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
    from openviking_gateway.capture import ready_at

    old = await store.capture.get(request.scope, request.session)
    value = {**old.value, **changes}
    assert await store.capture.swap(request.scope, request.session, old, value, ready_at(value))
    return value


async def make_due(store):
    from openviking_gateway.capture import ready_at

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
async def gateway_backend():
    captured = []
    writes = []
    override = {}
    viking = FakeViking()

    async def backend(request):
        if request.path == "/mcp" and (await request.json()).get("method") == "tools/list":
            key = request.headers.get("X-API-Key")
            override.setdefault("tools_calls", []).append(key)
            # Maps an OpenViking key to the HTTP status its tools/list gets.
            # Like OV, /mcp refuses root keys.
            status = override.get("tools_status", {}).get(key, 403 if key == "root" else 200)
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
            key = request.headers.get("X-API-Key", "")
            status = override.get("health_status", {}).get(key, 200)
            if status != 200:
                return web.json_response({}, status=status)
            if override.get("auth_mode") in {"dev", "trusted"}:
                return web.json_response({"version": "0.4.16", "auth_mode": override["auth_mode"]})
            return web.json_response(
                {
                    "version": "0.4.16",
                    "status": "ok",
                    "auth_mode": "api_key",
                    "role": "root" if key == "root" else "admin" if key == "admin-key" else "user",
                    "account_id": "other" if key == "other" else "tenant",
                    "user_id": "bob" if key == "bob-key" else "alice",
                }
            )
        if request.path == "/api/v1/admin/accounts":
            return web.json_response({"status": "ok", "result": [{"account_id": "tenant"}]})
        if request.path.startswith("/api/v1/admin/accounts/") and request.path.endswith("/users"):
            override.setdefault("users_calls", []).append(
                (str(request.rel_url), request.headers.get("X-API-Key"))
            )
            users = override.get(
                "users",
                [
                    {"user_id": "alice", "role": "user", "api_key": "user-key"},
                    {"user_id": "bob", "role": "user", "api_key": "bob-key"},
                ],
            )
            if name := request.query.get("name"):
                users = [u for u in users if u["user_id"] == name]
            if request.query.get("include_credentials") == "false":
                users = [
                    {
                        "user_id": u["user_id"],
                        "role": u["role"],
                        "api_key_available": bool(u.get("api_key")),
                    }
                    for u in users
                ]
            return web.json_response({"status": "ok", "result": users})
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
        if request.path.endswith("/models"):
            return web.json_response({"data": []})
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
    try:
        yield base, captured, writes, override
    finally:
        await runner.cleanup()


def default_upstreams(base):
    """One upstream per protocol on the fake backend."""
    return {
        protocol: {
            "name": protocol,
            "protocol": protocol,
            "base_url": base if protocol == "anthropic" else base + "/v1",
            "api_key": "model-secret",
            "models": ["model", "error"],
        }
        for protocol in ("chat", "anthropic", "responses")
    }


@pytest.fixture
def gateway_config():
    return {}


@asynccontextmanager
async def serve_gateway(tmp_path, gateway_backend, gateway_config):
    base, captured, writes, override = gateway_backend
    settings = {"storage_path": str(tmp_path), **gateway_config}
    config = GatewayConfig.model_validate(settings)
    app = create_app(
        config, openviking_url=base, encryptor=local_encryptor(tmp_path / "master.key")
    )
    app.state.test_backend = override
    async with app.router.lifespan_context(app):
        # Requests arrive as the hosts mount the gateway: below root_path /gateway.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, root_path="/gateway"),
            base_url="http://gateway/gateway",
        ) as client:
            admin = {"X-API-Key": "admin-key"}
            minted = None
            if config.management_api:
                for protocol, upstream in default_upstreams(base).items():
                    response = await client.put(
                        f"/admin/upstreams/{protocol}", headers=admin, json=upstream
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
                minted = minted.json()
            yield app, client, admin, minted, captured, writes


@pytest_asyncio.fixture
async def running_gateway(tmp_path, gateway_backend, gateway_config):
    async with serve_gateway(tmp_path, gateway_backend, gateway_config) as gateway:
        yield gateway


@pytest.fixture
def storage_encryptor(request, tmp_path):
    """Plaintext unless a test parametrizes it indirectly with True."""
    if getattr(request, "param", False):
        return local_encryptor(tmp_path / "storage-root.key")
    return None


def redis_settings(provider):
    """``storage.redis`` settings for "redis" or "redis-cluster"; skips when unconfigured."""
    variable = "OV_TEST_REDIS_CLUSTER" if provider == "redis-cluster" else "OV_TEST_REDIS"
    if not (endpoints := os.environ.get(variable)):
        pytest.skip(f"{variable} is not set")
    pytest.importorskip("redis")
    return {
        "mode": "cluster" if provider == "redis-cluster" else "standalone",
        "endpoints": endpoints.split(","),
        "username": os.environ.get("OV_TEST_REDIS_USERNAME", ""),
        "password": os.environ.get("OV_TEST_REDIS_PASSWORD", ""),
    }


@asynccontextmanager
async def open_redis_backend(provider, encryptor=None):
    from openviking_gateway.redis_store import RedisBackend, connect
    from openviking_gateway.storage import AsyncCodec

    handle = connect({**redis_settings(provider), "command_timeout_ms": 5000, "pool_size": 128})
    backend = RedisBackend(handle.client, f"ovgw:test:{uuid.uuid4().hex}:", AsyncCodec(encryptor))
    try:
        await handle.client.ping()
        yield backend
    finally:
        await backend.delete_matching(backend.prefix + "*")
        await handle.close()


@asynccontextmanager
async def open_kernel_store(provider, tmp_path, encryptor=None):
    if provider == "sqlite":
        store = SQLiteKernelStore(tmp_path / "kernel-contract.sqlite3", encryptor)
        await store.initialize()
        try:
            yield store
        finally:
            store.close()
        return
    from openviking_gateway.redis_store import RedisKernelStore

    async with open_redis_backend(provider, encryptor) as backend:
        store = RedisKernelStore(backend, ttl=7 * 86400)
        await store.initialize()
        yield store


@asynccontextmanager
async def open_management_store(provider, tmp_path, encryptor=None):
    if provider == "sqlite":
        store = SQLiteManagementStore(tmp_path / "management.sqlite3", encryptor)
        await store.initialize()
        try:
            yield store
        finally:
            store.close()
        return
    from openviking_gateway.redis_store import RedisManagementStore

    async with open_redis_backend(provider, encryptor) as backend:
        store = RedisManagementStore(backend, log_ttl=7 * 86400)
        await store.initialize()
        yield store


# Each port lists its own providers, so a backend for one port adds one entry here.
@pytest_asyncio.fixture(params=["sqlite", "redis", "redis-cluster"])
async def kernel_store(request, tmp_path, storage_encryptor):
    async with open_kernel_store(request.param, tmp_path, storage_encryptor) as store:
        yield store


@pytest_asyncio.fixture(params=["sqlite", "redis", "redis-cluster"])
async def management_store(request, tmp_path, storage_encryptor):
    async with open_management_store(request.param, tmp_path, storage_encryptor) as store:
        yield store


@pytest_asyncio.fixture(params=["redis", "redis-cluster"])
async def redis_kernel_store(request, tmp_path):
    async with open_kernel_store(request.param, tmp_path) as store:
        yield store


@pytest_asyncio.fixture(params=["redis", "redis-cluster"])
async def redis_management_store(request, tmp_path):
    async with open_management_store(request.param, tmp_path) as store:
        yield store


async def corrupt_root(store, scope, session, value):
    """Overwrite the stored bytes of a session's root replay record."""
    if isinstance(store, SQLiteKernelStore):

        def corrupt():
            with store.connect() as connection:
                connection.execute("UPDATE replay SET value=? WHERE scope=?", (value, scope))

        await store.run(corrupt, write=True)
    else:
        await store.client.hset(store.replay_key(scope, session), "root:", value)


async def evict_state(store, scope, key):
    """Drop a state record behind the store's back, as Redis eviction would."""
    if isinstance(store, SQLiteKernelStore):

        def remove():
            with store.connect() as connection:
                connection.execute("DELETE FROM state WHERE scope=? AND key=?", (scope, key))

        await store.run(remove, write=True)
    else:
        await store.client.execute_command("DEL", store.state_key(scope, key))
