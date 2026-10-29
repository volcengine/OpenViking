"""Storage port contracts, run against every backend, plus Redis-specific behavior."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from conftest import (
    FakeViking,
    corrupt_root,
    evict_state,
    open_kernel_store,
    open_redis_backend,
    redis_settings,
)

from openviking_gateway.capture import CaptureWorker
from openviking_gateway.capture_store import Document, LeaseLost
from openviking_gateway.kernel import MemoryKernel
from openviking_gateway.models import Policy
from openviking_gateway.records import RecordKind as K
from openviking_gateway.replay_store import INHERITED
from openviking_gateway.storage import CachedManagement, SQLiteManagementStore, digest
from openviking_gateway.tool_protocols import ResponseCapture


async def exercise_kernel_capture(ports, credential, protocol):
    viking = FakeViking()
    kernel = MemoryKernel(ports, viking)

    class Management:
        async def get(self, *_):
            return credential

        async def log(self, *_, **__):
            pass

    worker = CaptureWorker(ports, Management(), viking)
    policy = Policy(commit_tokens=1, keep_recent_messages=0, context_window=1024).model_dump()
    field = "input" if protocol == "responses" else "messages"
    messages = [
        {"role": "user", "content": "How do I deploy?"},
        {"role": "assistant", "content": "Blue cluster"},
        {"role": "user", "content": "What next?"},
    ]
    summary = {
        "chat": {"choices": [{"message": {"content": "Deploy blue"}, "finish_reason": "stop"}]},
        "anthropic": {
            "content": [{"type": "text", "text": "Deploy blue"}],
            "stop_reason": "end_turn",
        },
        "responses": {
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Deploy blue"}]}
            ],
        },
    }[protocol]

    async def summarize(prepared, body):
        return summary

    async def prepare(session="client"):
        return await kernel.prepare(
            {field: messages},
            protocol,
            {"x-openviking-session": session},
            credential,
            {"id": "upstream"},
            policy,
            summarize=summarize,
        )

    p = await prepare()
    assert await worker.once()  # append and commit
    await kernel.completed(
        p,
        credential,
        ResponseCapture(
            protocol,
            {"role": "assistant", "content": "Ok"},
            usage={"input_tokens": 1000},
            complete=True,
        ),
    )
    messages += [{"role": "assistant", "content": "Ok"}, {"role": "user", "content": "And then?"}]
    compacted = await prepare()
    assert compacted.metrics["compaction_tokens"] > 0
    # Another kernel and another session continuing the reply replay the same cut.
    kernel = MemoryKernel(ports, viking)
    fork = await prepare("fork")
    assert fork.body[field][0]["content"].startswith(
        '<openviking-context source="gateway-compaction">'
    )
    assert fork.body[field][1:] == compacted.body[field][1:]
    assert fork.capture_target != p.capture_target
    assert await worker.once() and await worker.once()
    assert len(set(viking.write_sessions)) == 2


async def test_expired_lease_cannot_write_or_release_new_owner(kernel_store):
    store = kernel_store
    await store.capture.swap("scope", "session", Document(), {"work": 1}, 0)
    expired = await store.capture.claim(lease_seconds=-1)
    active = await store.capture.claim()
    with pytest.raises(LeaseLost):
        await store.capture.swap(
            "scope", "session", expired["document"], {"work": "stale"}, None, owner=expired["owner"]
        )
    await store.capture.release(expired)
    assert await store.capture.claim() is None
    assert await store.capture.swap(
        "scope", "session", active["document"], {"work": "done"}, None, owner=active["owner"]
    )
    await store.capture.release(active)
    assert (await store.capture.get("scope", "session")).value == {"work": "done"}


async def test_cache_coalesces_reads_and_reloads_after_invalidation():
    from openviking_gateway.cache import TTLCache

    cache = TTLCache(ttl=2, capacity=16)
    count = 0

    async def load():
        nonlocal count
        count += 1
        await asyncio.sleep(0)
        return count

    assert await asyncio.gather(*(cache.get("key", load) for _ in range(50))) == [1] * 50
    cache.clear()
    assert await cache.get("key", load) == 2


async def test_batched_reads_keep_scope_isolation_and_handle_cancellation(
    setup_kernel, monkeypatch
):
    _, store, _, _ = setup_kernel
    for account in ("one", "two"):
        await store.replay.put(account, "session", K.INJECTION, "shared-anchor", {"text": account})
    batches = []
    run = store.run

    async def counted(fn, *args, **kwargs):
        if fn.__name__ == "read_batch":
            batches.append(1)
        return await run(fn, *args, **kwargs)

    monkeypatch.setattr(store, "run", counted)
    scopes = ["one", "two"] * 50
    tasks = [
        asyncio.create_task(store.load(scope, "session", ["shared-anchor"])) for scope in scopes
    ]
    await asyncio.sleep(0)
    tasks[0].cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    for scope, result in zip(scopes[1:], results[1:], strict=True):
        assert result[0][K.INJECTION, "shared-anchor"] == {"text": scope}
    assert len(batches) == 2


async def test_new_session_does_not_freeze_other_process_stale_policy(setup_kernel, credential):
    kernel, store, _, encryption = setup_kernel
    writer = SQLiteManagementStore(store.path.parent / "management.sqlite3", encryption)
    reader = CachedManagement(SQLiteManagementStore(writer.path, encryption))
    await writer.initialize()
    await reader.initialize()
    try:
        original = Policy(recall=False).model_dump()
        await writer.save("tenant", "policies", "default", original)
        assert (await reader.get("tenant", "policies", "default"))["capture"]
        await writer.save("tenant", "policies", "default", {**original, "capture": False})
        policy = await reader.get("tenant", "policies", "default")
        request = await kernel.prepare(
            {"messages": [{"role": "user", "content": "new conversation"}]},
            "chat",
            {"x-openviking-session": "fresh"},
            credential,
            {"id": "upstream"},
            policy,
        )
        assert request.root["policy"]["capture"] is False
        assert not (await store.capture.get(request.scope, request.session)).value
    finally:
        writer.close()
        reader.port.close()


async def test_response_bookkeeping_preserves_configuration_cache(setup_kernel, monkeypatch):
    _, store, _, encryption = setup_kernel
    port = SQLiteManagementStore(store.path.parent / "management.sqlite3", encryption)
    management = CachedManagement(port)
    await management.initialize()
    try:
        await management.save("tenant", "keys", digest("downstream"), {"name": "key"})
        await management.save("tenant", "upstreams", "model", {"name": "model"})
        await management.authenticate("downstream")
        await management.list("tenant", "upstreams")
        await management.save("tenant", "responses", "response", {"upstream_id": "model"}, ttl=1)
        await management.expire_logs(0)
        credential = AsyncMock(wraps=port.credential)
        upstreams = AsyncMock(wraps=port.list)
        monkeypatch.setattr(port, "credential", credential)
        monkeypatch.setattr(port, "list", upstreams)
        assert (await management.authenticate("downstream"))["name"] == "key"
        assert (await management.list("tenant", "upstreams"))[0]["name"] == "model"
        credential.assert_not_awaited()
        upstreams.assert_not_awaited()
        await management.delete("tenant", "keys", digest("downstream"))
        assert await management.authenticate("downstream") is None
    finally:
        port.close()


@pytest.mark.parametrize("kind", ["keys", "upstreams"])
async def test_cached_lists_filter_users_and_reload_after_another_process_writes(
    tmp_path, monkeypatch, kind
):
    port = SQLiteManagementStore(tmp_path / "management.sqlite3")
    management = CachedManagement(port)
    await management.initialize()
    try:
        for user in ("alice", "bob"):
            await management.save("tenant", kind, user, {"user_id": user})
        assert [v["id"] for v in await management.list("tenant", kind, "alice")] == ["alice"]
        assert [v["id"] for v in await management.list("tenant", kind, "bob")] == ["bob"]
        await port.delete("tenant", kind, "bob")
        assert len(await management.list("tenant", kind)) == 2
        monotonic = time.monotonic
        monkeypatch.setattr(time, "monotonic", lambda: monotonic() + 3)
        assert [v["id"] for v in await management.list("tenant", kind)] == ["alice"]
    finally:
        port.close()


@pytest.mark.parametrize("operation", ["save", "delete", "delete_user", "delete_account"])
async def test_management_writes_invalidate_cached_credentials(tmp_path, operation):
    port = SQLiteManagementStore(tmp_path / "management.sqlite3")
    management = CachedManagement(port)
    await management.initialize()
    try:
        identifier = digest("key")
        await management.save("tenant", "keys", identifier, {"user_id": "alice"})
        assert (await management.authenticate("key"))["user_id"] == "alice"
        if operation == "save":
            await management.save("tenant", "keys", identifier, {"user_id": "bob"})
            assert (await management.authenticate("key"))["user_id"] == "bob"
        else:
            args = {
                "delete": ("tenant", "keys", identifier),
                "delete_user": ("tenant", "alice"),
                "delete_account": ("tenant",),
            }
            await getattr(management, operation)(*args[operation])
            assert await management.authenticate("key") is None
    finally:
        port.close()


@pytest.mark.parametrize("failing", ["SQLiteKernelStore", "SQLiteManagementStore"])
async def test_app_closes_both_stores_when_initialization_fails(tmp_path, monkeypatch, failing):
    import threading

    from openviking_gateway import app, storage
    from openviking_gateway.config import GatewayConfig

    closed = []
    loop_thread = threading.get_ident()
    close_database = storage.Database.close

    def close(self):
        assert threading.get_ident() != loop_thread
        closed.append(type(self).__name__)
        close_database(self)

    async def fail(self):
        raise RuntimeError("initialization failed")

    monkeypatch.setattr(storage.Database, "close", close)
    monkeypatch.setattr(getattr(storage, failing), "initialize", fail)
    gateway = app.create_app(GatewayConfig(storage_path=str(tmp_path)), openviking_url="http://ov")
    with pytest.raises(RuntimeError, match="initialization failed"):
        async with gateway.router.lifespan_context(gateway):
            pytest.fail("initialization should fail before serving requests")
    assert sorted(closed) == ["SQLiteKernelStore", "SQLiteManagementStore"]


async def test_management_order_filter_and_revision(management_store):
    for identifier in ("z", "é", "a", "{foreign}:*"):
        await management_store.save("account", "upstreams", identifier, {"user_id": "one"})
    await management_store.save("account", "upstreams", "other", {"user_id": "two"})
    rows = await management_store.list("account", "upstreams", user_id="one")
    assert [row["id"] for row in rows] == ["a", "z", "{foreign}:*", "é"]
    assert (await management_store.save("account", "upstreams", "a", {"name": "new"}))[
        "revision"
    ] == 2
    assert await management_store.list("elsewhere", "upstreams") == []


async def test_management_expiration_and_recreation(management_store):
    await management_store.save("account", "keys", "short", {"user_id": "one"}, ttl=0.08)
    await management_store.save("account", "keys", "short", {"user_id": "one", "updated": True})
    await asyncio.sleep(0.1)
    assert await management_store.get("account", "keys", "short") is None
    assert await management_store.credential("short") is None
    await management_store.expire_logs(0)
    assert await management_store.list("account", "keys") == []
    await management_store.save("account", "keys", "short", {"user_id": "one"}, ttl=1)
    await management_store.delete("account", "keys", "short")
    await management_store.save("account", "keys", "short", {"user_id": "one"})
    await asyncio.sleep(0.01)
    assert await management_store.credential("short") is not None


async def test_management_payload_expiry_applies_without_ttl(management_store):
    await management_store.save("account", "keys", "id", {"expires_at": time.time() - 1})
    assert await management_store.get("account", "keys", "id") is None
    assert await management_store.credential("id") is None


async def test_management_credential_and_cached_wrapper(management_store):
    from openviking_gateway.storage import CachedManagement

    cached = CachedManagement(management_store)
    identifier = digest("secret")
    await cached.save("other-account", "keys", identifier, {"user_id": "one"})
    assert (await cached.authenticate("secret"))["account"] == "other-account"
    await cached.delete("other-account", "keys", identifier)
    assert await cached.authenticate("secret") is None
    await cached.save("other-account", "keys", identifier, {"user_id": "one"})
    assert (await cached.credential(identifier))["id"] == identifier


@pytest.mark.parametrize(
    "storage_encryptor", [False, True], ids=["plaintext", "encrypted"], indirect=True
)
async def test_management_delete_user_and_account(management_store):
    for user in ("one", "two"):
        await management_store.save("account", "keys", user, {"user_id": user})
        await management_store.save("account", "users", user, {"user_id": user})
        await management_store.log("account", {"message": user}, user_id=user)
    await management_store.save("account", "policies", "default", {"name": "keep"})
    await management_store.save("other", "keys", "separate", {"user_id": "one"})
    assert (await management_store.logs("account", user_id="one"))[0]["message"] == "one"
    await management_store.delete_user("account", "one")
    assert await management_store.credential("one") is None
    assert [row["id"] for row in await management_store.list("account", "users")] == ["two"]
    assert [row["message"] for row in await management_store.logs("account")] == ["two"]
    assert await management_store.get("account", "policies", "default")
    await management_store.delete_account("account")
    assert await management_store.credential("two") is None
    assert await management_store.logs("account") == []
    assert await management_store.list("account", "policies") == []
    assert await management_store.credential("separate")


async def test_management_large_value(management_store):
    value = {"text": "v" * (8 * 1024 * 1024)}
    await management_store.save("account", "responses", "large", value, ttl=5)
    assert (await management_store.get("account", "responses", "large"))["text"] == value["text"]


async def test_redis_ttl_removes_partition_and_reverse_index(redis_management_store):
    store, backend = redis_management_store, redis_management_store.backend
    await store.save("expiring", "keys", "short", {"user_id": "one"}, ttl=0.08)
    await store.save("expiring", "users", "one", {"user_id": "one"}, ttl=0.08)
    await asyncio.sleep(0.12)
    assert [key async for key in backend.scan(backend.prefix + "*")] == []


async def test_redis_missing_credential_object_does_not_remove_index(redis_management_store):
    store, backend = redis_management_store, redis_management_store.backend
    await store.save("account", "keys", "key", {"user_id": "one"})
    await backend.client.execute_command("DEL", store.object_key("account", "keys", "key"))
    assert await store.credential("key") is None
    assert await backend.client.get(store.credential_key("key")) == b"account"
    await asyncio.gather(
        *(store.save("account", "keys", "key", {"user_id": "one"}) for _ in range(10))
    )
    assert all(await asyncio.gather(*(store.credential("key") for _ in range(20))))


async def test_redis_logs_are_bounded_and_user_deletion_preserves_ttl(redis_management_store):
    store, backend = redis_management_store, redis_management_store.backend
    key = store.account_prefix("account") + "log"
    # Seed old entries in one command; the next production write must enforce the cap.
    await backend.client.lpush(key, *([b"0\0\0{}"] * 10005))
    await store.log("account", {"message": "keep"}, user_id="two")
    await store.log("account", {"message": "remove"}, user_id="one")
    assert await backend.client.llen(key) == 10000
    await backend.client.pexpire(key, 2000)
    await store.delete_user("account", "one")
    assert 0 < await backend.client.pttl(key) <= 2000
    assert [r["message"] for r in await store.logs("account", user_id="two")] == ["keep"]
    assert await store.logs("account", user_id="one") == []


async def test_redis_script_reload_and_scoped_scan(redis_management_store):
    store, backend = redis_management_store, redis_management_store.backend
    await store.save("account", "keys", "key", {"user_id": "one"})
    script = backend.script("obj_save")
    script.sha = "0" * 40  # Force NOSCRIPT without flushing scripts used by other test workers.
    assert (await store.save("account", "keys", "key", {"user_id": "one"}))["revision"] == 2
    prefix = store.account_prefix("account")
    assert len([key async for key in backend.scan(prefix + "*")]) == 2


@pytest.mark.parametrize(
    "settings",
    [
        {"redis": {"pool_sze": 10}},
        {"key_prefix": "bad{tag}:"},
        {"key_prefix": "bad*"},
        {"redis": {"endpoints": ["redis://user:password@localhost:6379"]}},
    ],
)
def test_storage_configuration_fails_while_loading(settings, tmp_path, monkeypatch):
    import json

    from pydantic import ValidationError

    from openviking_gateway.cli import load_config

    path = tmp_path / "gateway.conf"
    path.write_text(json.dumps({"openviking_url": "http://ov", "storage": settings}))
    monkeypatch.setenv("OPENVIKING_GATEWAY_CONFIG_FILE", str(path))
    with pytest.raises(ValidationError):
        load_config()


def test_storage_redis_defaults():
    from openviking_gateway.config import GatewayConfig

    assert GatewayConfig().storage.redis["command_timeout_ms"] == 5000


@pytest.mark.parametrize("provider", ["redis", "redis-cluster"])
async def test_redis_commands_beyond_pool_size_wait_for_a_connection(provider):
    from openviking_gateway.redis_store import connect

    handle = connect({**redis_settings(provider), "command_timeout_ms": 5000, "pool_size": 2})
    try:
        assert await asyncio.gather(*(handle.client.ping() for _ in range(20))) == [True] * 20
    finally:
        await handle.close()


async def test_async_codec_reads_sqlite_ciphertext_and_plaintext(tmp_path):
    from conftest import local_encryptor

    from openviking_gateway.storage import AsyncCodec, SQLiteManagementStore

    encryptor = local_encryptor(tmp_path / "root.key")
    codec = AsyncCodec(encryptor)
    other_replica = AsyncCodec(local_encryptor(tmp_path / "root.key"))
    database = SQLiteManagementStore(tmp_path / "management.sqlite3", encryptor)
    try:
        value = {"secret": "memory"}
        encrypted = await database.run(database.encode, value)
        assert await codec.decode(encrypted) == value
        assert await other_replica.decode(await codec.encode(value)) == value
        assert await database.run(database.decode, await codec.encode(value)) == value
        assert await codec.decode(b'{"plain":true}') == {"plain": True}
        with pytest.raises(RuntimeError, match="Gateway data is encrypted"):
            await AsyncCodec().decode(encrypted)
    finally:
        database.close()


@pytest.mark.parametrize("operation", ["save", "log"])
async def test_redis_does_not_repeat_a_write_after_losing_its_response(
    redis_management_store, monkeypatch, operation
):
    from redis.asyncio import Redis
    from redis.asyncio.cluster import ClusterNode, RedisCluster
    from redis.exceptions import ConnectionError

    store, backend = redis_management_store, redis_management_store.backend
    await store.save("account", "upstreams", "object", {"value": "before"})
    target = ClusterNode if isinstance(backend.client, RedisCluster) else Redis
    parse_response = target.parse_response
    attempts = []

    async def lose_response(client, connection, command, **kwargs):
        response = await parse_response(client, connection, command, **kwargs)
        if command.upper() == "EVALSHA":
            attempts.append(command)
            if len(attempts) == 1:
                await connection.disconnect()
                raise ConnectionError("socket lost after Redis committed the write")
        return response

    monkeypatch.setattr(target, "parse_response", lose_response)
    with pytest.raises(ConnectionError):
        if operation == "save":
            await store.save("account", "upstreams", "object", {"value": "after"})
        else:
            await store.log("account", {"message": "once"})
    assert len(attempts) == 1
    if operation == "save":
        assert (await store.get("account", "upstreams", "object"))["revision"] == 2
    else:
        assert len(await store.logs("account")) == 1


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_kernel_capture_backend_contract(kernel_store, credential, protocol):
    await exercise_kernel_capture(kernel_store, credential, protocol)


async def test_replay_put_if_absent_is_atomic(kernel_store):
    store = kernel_store
    results = await asyncio.gather(
        *(store.replay.put("scope", "session", K.ROOT, "", {"winner": i}) for i in range(50))
    )
    assert all(value == results[0] for value in results)
    assert results[0] in [{"winner": i} for i in range(50)]
    assert await store.replay.put("scope", "session", K.ROOT, "", {"winner": -1}) == results[0]
    assert await store.replay.read("other", "session", [""]) == {}


async def test_replay_inheritance_and_reply_index(kernel_store):
    store = kernel_store
    for sid in ("z-parent", "a-parent", "child"):
        for kind in K:
            await store.replay.put("scope", sid, kind, "anchor", {"owner": sid})
    values = await store.replay.read("scope", "child", ["anchor"], ["z-parent", "a-parent"])
    assert set(values) == {(kind, "anchor") for kind in K if kind != K.REPLY}
    assert all(value == {"owner": "child"} for value in values.values())
    inherited = await store.replay.read("scope", "new-child", ["anchor"], ["z-parent", "a-parent"])
    assert inherited == {(kind, "anchor"): {"owner": "z-parent"} for kind in INHERITED}
    assert await store.replay.read("scope", "child", []) == {}
    assert await store.replay.replies("scope", ["anchor", "missing"]) == {
        "anchor": {"a-parent", "z-parent", "child"}
    }


async def test_state_cas_versions_and_load_snapshot(kernel_store):
    store = kernel_store
    results = await asyncio.gather(
        *(store.state.swap("scope", "session", Document(), {"winner": i}) for i in range(50))
    )
    assert sum(results) == 1
    first = (await store.state.read("scope", ["session"]))["session"]
    assert first.version == 1
    assert await store.state.swap("scope", "session", first, {"updated": True})
    assert not await store.state.swap("scope", "session", first, {"stale": True})
    assert not await store.state.swap("scope", "session", Document(), {})
    assert await store.capture.swap("scope", "session", Document(), {"pending": [1]}, None)
    capture = await store.capture.get("scope", "session")
    assert capture.version == 1
    assert await store.capture.swap("scope", "session", capture, {"pending": []}, None)
    assert not await store.capture.swap("scope", "session", capture, {"stale": True}, 0)
    assert not await store.capture.swap("scope", "session", Document(), {}, 0)
    await store.replay.put("scope", "session", K.ROOT, "", {"root": True})
    replay, state, capture = await store.load("scope", "session", [""])
    assert replay == {(K.ROOT, ""): {"root": True}}
    assert state == {"session": Document({"updated": True}, 2)}
    assert capture == Document({"pending": []}, 2)
    assert await store.state.read("other", ["session"]) == {}
    assert await store.state.read("scope", []) == {}


async def test_capture_ready_zero_lease_and_release(kernel_store):
    store = kernel_store
    for sid in ("one", "two"):
        assert await store.capture.swap("scope", sid, Document(), {"sid": sid}, 0)
    assert await store.capture.swap("scope", "idle", Document(), {}, None)
    assert await store.capture.swap("scope", "future", Document(), {}, time.time() + 3600)
    first, second = await asyncio.gather(store.capture.claim(), store.capture.claim())
    assert {first["session"], second["session"]} == {"one", "two"}
    assert first["owner"] != second["owner"]
    assert first["document"].version == second["document"].version == 1
    assert await store.capture.claim() is None
    await store.capture.release(first)
    again = await store.capture.claim()
    assert again["session"] == first["session"]
    assert again["owner"] != first["owner"]
    await store.capture.release(first)
    assert await store.capture.claim() is None
    assert await store.capture.swap(
        "scope", again["session"], again["document"], {"done": True}, None, owner=again["owner"]
    )
    await store.capture.release(again)
    assert await store.capture.claim() is None


async def test_scope_delete_blocks_concurrent_writes(kernel_store):
    store = kernel_store
    await store.replay.put("scope", "session", K.REPLY, "reply", {})
    await store.capture.swap("scope", "session", Document(), {"work": 1}, 0)
    await store.state.swap("scope", "session", Document(), {"state": 1})
    operations = [store.expire(0, "scope")]
    for i in range(20):
        operations.extend(
            (
                store.replay.put("scope", f"s{i}", K.ROOT, "", {}),
                store.capture.swap("scope", f"s{i}", Document(), {}, 0),
                store.state.swap("scope", f"s{i}", Document(), {}),
            )
        )
    results = await asyncio.gather(*operations, return_exceptions=True)
    assert all(
        not isinstance(value, Exception)
        or (isinstance(value, RuntimeError) and str(value) == "Gateway user data has been deleted")
        for value in results
    )
    for sid in ["session", *[f"s{i}" for i in range(20)]]:
        assert await store.load("scope", sid, ["", "reply"]) == ({}, {}, Document())
    for operation in (
        store.replay.put("scope", "late", K.ROOT, "", {}),
        store.state.swap("scope", "late", Document(), {}),
        store.capture.swap("scope", "late", Document(), {}, 0),
        store.keep("scope", ["late"], ["late"]),
    ):
        with pytest.raises(RuntimeError, match="Gateway user data has been deleted"):
            await operation
    await store.allow_scope("scope")
    await store.replay.put("scope", "late", K.ROOT, "", {})
    assert await store.state.swap("scope", "late", Document(), {})
    assert await store.capture.swap("scope", "late", Document(), {}, 0)


@pytest.mark.parametrize(
    "storage_encryptor", [False, True], ids=["plaintext", "encrypted"], indirect=True
)
async def test_large_kernel_value_and_load_failure_isolation(kernel_store, storage_encryptor):
    from openviking.crypto import CorruptedCiphertextError
    from openviking.crypto.encryptor import MAGIC

    store = kernel_store
    large = {"text": "x" * (8 * 1024 * 1024)}
    assert await store.replay.put("healthy", "session", K.ROOT, "", large) == large
    await store.replay.put("damaged", "session", K.ROOT, "", {})
    await corrupt_root(store, "damaged", "session", MAGIC + b"invalid")
    damaged, healthy = await asyncio.gather(
        store.load("damaged", "session", [""]),
        store.load("healthy", "session", [""]),
        return_exceptions=True,
    )
    if storage_encryptor is None:
        assert isinstance(damaged, RuntimeError)
        assert "configure the encryption settings" in str(damaged)
    else:
        assert isinstance(damaged, CorruptedCiphertextError)
    assert healthy[0][K.ROOT, ""] == large


async def test_redis_eviction_prunes_replies_and_loses_leases(redis_kernel_store, monkeypatch):
    store = redis_kernel_store
    await store.replay.put("scope", "parent", K.REPLY, "anchor", {})
    await store.replay.put("scope", "parent", K.REPLACEMENT, "anchor", {"summary": "old"})
    await store.replay.put("scope", "sibling", K.REPLY, "anchor", {})
    await store.replay.put("scope", "other", K.REPLY, "second", {})
    assert await store.replay.replies("scope", ["anchor", "second"]) == {
        "anchor": {"parent", "sibling"},
        "second": {"other"},
    }
    await store.client.execute_command("DEL", store.replay_key("scope", "parent"))
    await store.client.execute_command("DEL", store.replay_key("scope", "sibling"))
    await store.replay.put("scope", "parent", K.ROOT, "", {})
    scripts, script = [], store.script

    async def counted(name, keys, args):
        scripts.append(name)
        return await script(name, keys, args)

    monkeypatch.setattr(store, "script", counted)
    assert await store.replay.replies("scope", ["anchor", "missing", "second"]) == {
        "second": {"other"}
    }
    # Two round trips, whatever the number of anchors.
    assert scripts == ["kernel_replies_read", "kernel_replies_prune"]
    assert await store.client.smembers(store.replay.index("scope", "anchor")) == set()
    assert await store.replay.read("scope", "child", ["anchor"], ["parent"]) == {}
    assert await store.capture.swap("scope", "session", Document(), {"work": 1}, 0)
    item = await store.capture.claim()
    await store.client.execute_command("DEL", store.capture_key("scope", "session"))
    with pytest.raises(LeaseLost):
        await store.capture.swap(
            "scope", "session", item["document"], {}, None, owner=item["owner"]
        )
    await store.capture.release(item)
    assert await store.capture.get("scope", "session") == Document()
    await store.client.zadd(store.capture.ready, {"scope\0session": 0})
    assert await store.capture.claim() is None
    assert await store.client.zcard(store.capture.ready) == 0
    assert await store.capture.swap("scope", "session", Document(), {}, 0)
    assert (await store.capture.get("scope", "session")).version == 1
    assert await store.state.swap("scope", "state", Document(), {"old": True})
    old = (await store.state.read("scope", ["state"]))["state"]
    await store.client.execute_command("DEL", store.state_key("scope", "state"))
    assert not await store.state.swap("scope", "state", old, {})
    assert await store.state.swap("scope", "state", Document(), {"new": True})
    assert (await store.state.read("scope", ["state"]))["state"].version == 1


async def test_redis_ttl_refresh_is_throttled_and_reads_do_not_refresh(redis_kernel_store):
    store = redis_kernel_store
    await store.capture.swap("scope", "session", Document(), {}, None)
    await store.replay.put("scope", "session", K.REPLY, "anchor", {})
    await store.state.swap("scope", "session", Document(), {})
    rp, cap, state = (
        store.replay_key("scope", "session"),
        store.capture_key("scope", "session"),
        store.state_key("scope", "session"),
    )
    index = store.replay.index("scope", "anchor")
    for key in (rp, cap, state, index):
        await store.client.pexpire(key, 5000)
    await store.load("scope", "session", ["anchor"])
    await store.state.read("scope", ["session"])
    for key in (rp, cap, state, index):
        assert 0 < await store.client.pttl(key) <= 5000
    await store.replay.put("scope", "session", K.ROOT, "", {})
    assert await store.client.pttl(rp) > 5000
    assert await store.client.pttl(cap) <= 5000
    store.touched[cap] = time.monotonic() - 61
    await store.replay.put("scope", "session", K.ROOT, "", {})
    assert await store.client.pttl(cap) > 5000
    await store.client.pexpire(rp, 5000)
    store.touched[rp] = time.monotonic() - 61
    old = await store.capture.get("scope", "session")
    assert await store.capture.swap("scope", "session", old, {}, None)
    assert await store.client.pttl(rp) > 5000
    await store.client.pexpire(cap, 5000)
    store.touched[cap] = time.monotonic() - 61
    await store.keep("scope", ["session"], ["session"])
    assert await store.client.pttl(cap) > 5000
    assert await store.client.pttl(state) > 5000
    assert await store.replay.replies("scope", ["anchor"]) == {"anchor": {"session"}}
    assert await store.client.pttl(index) > 5000
    await store.expire(time.time() + 86400)
    assert (await store.capture.get("scope", "session")).version == 2
    for key in (rp, cap, state, index):
        await store.client.pexpire(key, 1)
    await asyncio.sleep(0.02)
    assert await store.load("scope", "session", ["", "anchor"]) == ({}, {}, Document())
    assert await store.replay.replies("scope", ["anchor"]) == {}


@pytest.mark.parametrize("evicted_read", [1, 2])
async def test_evicted_tool_receipt_returns_unknown_without_repeating(
    kernel_store, credential, monkeypatch, evicted_read
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import orjson

    from openviking_gateway.tool_executor import UNKNOWN_OUTCOME, ToolExecutor

    store = kernel_store
    call = {"id": "write-1", "function": {"name": "openviking_write", "arguments": "{}"}}
    prepared = SimpleNamespace(
        scope="scope",
        session="session",
        chain=[],
        root={
            "policy": {"tool_timeout_seconds": 30},
            "tools": [{"function": {"name": "openviking_write", "parameters": {}}}],
        },
    )
    receipt_key = "tool:session:" + digest(orjson.dumps(call).decode())
    await store.state.swap(
        "scope", receipt_key, Document(), {"owner": "another-worker", "time": time.time()}
    )
    read, reads = store.state.read, 0

    async def evict(scope, keys):
        nonlocal reads
        reads += 1
        if reads == evicted_read:
            await evict_state(store, scope, receipt_key)
        return await read(scope, keys)

    monkeypatch.setattr(store.state, "read", evict)
    executor = ToolExecutor(None, store, prepared, credential, "", 1024)
    execute = AsyncMock()
    monkeypatch.setattr(executor, "_call", execute)
    result = await executor.execute(call)
    assert result == {
        "role": "tool",
        "tool_call_id": "write-1",
        "content": UNKNOWN_OUTCOME,
        "failed": True,
    }
    assert reads == evicted_read
    execute.assert_not_called()
    assert await read("scope", [receipt_key]) == {}


async def test_redis_claim_uses_server_time_and_repairs_ready_score(
    redis_kernel_store, monkeypatch
):
    from types import SimpleNamespace

    from openviking_gateway import redis_store

    store, now = redis_kernel_store, time.time()
    monkeypatch.setattr(
        redis_store, "time", SimpleNamespace(time=lambda: now + 3600, monotonic=time.monotonic)
    )
    await store.capture.swap("scope", "later", Document(), {}, now + 600)
    await store.client.zadd(store.capture.ready, {"scope\0later": 0})
    assert await store.capture.claim() is None
    assert await store.client.zscore(store.capture.ready, "scope\0later") == pytest.approx(
        (now + 600) * 1000
    )
    await store.capture.swap("scope", "now", Document(), {}, 0)
    item = await store.capture.claim(lease_seconds=120)
    assert item["session"] == "now"
    seconds, micros = await store.client.time()
    lease = float(await store.client.hget(store.capture_key("scope", "now"), "lease"))
    assert 115_000 < lease - (seconds * 1000 + micros / 1000) <= 120_000
    old = item["document"]
    assert await store.capture.swap("scope", "now", old, {"pending": True}, 0, owner=item["owner"])
    assert await store.client.zscore(store.capture.ready, "scope\0now") == lease
    await store.capture.release(item)
    assert await store.client.zscore(store.capture.ready, "scope\0now") == 0


async def test_redis_scope_delete_unlinks_keys_and_keeps_tombstones(redis_kernel_store):
    store = redis_kernel_store
    await store.replay.put("scope", "session", K.ROOT, "", {})
    await store.capture.swap("scope", "session", Document(), {}, 0)
    await store.expire(0, "scope")
    assert [key async for key in store.backend.scan(f"{store.prefix}k:{{scope}}:*")] == []
    assert [key async for key in store.backend.scan(f"{store.prefix}k:{{cap}}:scope:*")] == []
    assert await store.client.pttl(store.tombstone("scope")) == -1
    assert await store.client.pttl(store.tombstone("scope", capture=True)) == -1


async def test_cluster_scope_delete_scans_only_own_primaries(tmp_path, monkeypatch):
    async with open_kernel_store("redis-cluster", tmp_path) as store:
        scope, sid = "scope", "{foreign}:*:?[]\\\0unicode-用户"
        await store.replay.put(scope, sid, K.ROOT, "", {})
        await store.capture.swap(scope, sid, Document(), {}, 0)
        await store.state.swap(scope, sid, Document(), {})
        await store.replay.put("other", sid, K.ROOT, "", {"survives": True})
        scans, scan = [], store.client.scan

        async def observed(*args, **kwargs):
            scans.append((kwargs["match"], kwargs["target_nodes"]))
            return await scan(*args, **kwargs)

        monkeypatch.setattr(store.client, "scan", observed)
        await store.expire(0, scope)
        assert scans
        for pattern, node in scans:
            assert node is store.client.get_node_from_key(pattern)
            assert pattern in {f"{store.prefix}k:{{scope}}:*", f"{store.prefix}k:{{cap}}:scope:*"}
        assert await store.load(scope, sid, [""]) == ({}, {}, Document())
        assert (await store.replay.read("other", sid, [""]))[K.ROOT, ""] == {"survives": True}


async def test_replay_load_handles_many_anchors(kernel_store):
    store = kernel_store
    for i in range(300):
        await store.replay.put("scope", "session", K.INJECTION, f"anchor:{i}", {"i": i})
    values = await store.replay.read("scope", "session", [f"anchor:{i}" for i in range(300)])
    assert values == {(K.INJECTION, f"anchor:{i}"): {"i": i} for i in range(300)}


@pytest.mark.parametrize("provider", ["redis", "redis-cluster"])
async def test_open_stores_shares_redis_and_reads_across_instances(provider, tmp_path, monkeypatch):
    from openviking_gateway import redis_store
    from openviking_gateway.app import open_stores
    from openviking_gateway.config import GatewayConfig

    handles, connect = [], redis_store.connect

    def opened(settings):
        handles.append(connect(settings))
        handles[-1].close = AsyncMock(wraps=handles[-1].close)
        return handles[-1]

    async with open_redis_backend(provider) as backend:
        monkeypatch.setattr(redis_store, "connect", opened)
        config = GatewayConfig(
            storage_path=str(tmp_path),
            session_ttl_seconds=2 * 86400,
            log_retention_days=3,
            storage={
                "kernel": "redis",
                "management": "redis",
                "key_prefix": backend.prefix.removesuffix("v1:"),
                "redis": redis_settings(provider),
            },
        )
        store, management, close = open_stores(config, None)
        try:
            await store.initialize()
            await management.initialize()
            assert store.client is management.port.client
            assert store.ttl == 2 * 86400 * 1000
            assert management.port.log_ttl == 3 * 86400
            assert not list(tmp_path.glob("*.sqlite3"))
            await store.replay.put("scope", "session", K.ROOT, "", {"created": "first"})
            await store.state.swap("scope", "session", Document(), {"observed": True})
            await store.capture.swap("scope", "session", Document(), {"pending": True}, 0)
            peer = redis_store.RedisKernelStore(backend, ttl=2 * 86400)
            assert await peer.load("scope", "session", [""]) == (
                {(K.ROOT, ""): {"created": "first"}},
                {"session": Document({"observed": True}, 1)},
                Document({"pending": True}, 1),
            )
            claimed = await peer.capture.claim()
            assert claimed["session"] == "session"
            assert await store.capture.claim() is None
        finally:
            await close()
    [handle] = handles
    handle.close.assert_awaited_once()
