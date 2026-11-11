"""Native coordination contract, exercised against SQLite and real Redis."""

import asyncio
import json
import os
import selectors
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from openviking.pyagfs import get_binding_client
from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_queue import SemanticQueue


@pytest.fixture
def redis_server(tmp_path):
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("redis-server is required for the shared coordination contract")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [
            executable,
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 10
            output = b""
            while b"Ready to accept connections" not in output:
                assert selector.select(max(0, deadline - time.monotonic())), output.decode()
                chunk = process.stdout.read1(4096)
                assert chunk, output.decode()
                output += chunk
        yield process, f"redis://127.0.0.1:{port}"
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGCONT)
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def redis_url(redis_server):
    return redis_server[1]


@pytest.fixture(params=["memory", "sqlite", "cache"])
def coordination_config(request, tmp_path):
    config = {
        "service_name": f"coordination-{uuid4().hex}",
        "coordination": {"provider": request.param},
    }
    if request.param == "sqlite":
        config["coordination"]["sqlite_path"] = str(tmp_path / "local-disk" / "coordination.db")
    if request.param == "cache":
        config["cache"] = {
            "runtime_enabled": True,
            "provider": "redis",
            "redis": {
                "endpoints": [request.getfixturevalue("redis_url")],
                "pool_size": 2,
                "command_timeout_ms": 2000,
            },
        }
    return config


@pytest.fixture
def clients():
    client_class, _ = get_binding_client()
    opened = []

    def create(config):
        client = client_class(config=config)
        opened.append(client)
        return client

    yield create
    for client in reversed(opened):
        client.close()


@pytest.mark.asyncio
async def test_terms_takeover_namespace_and_close(coordination_config, clients):
    first = clients(coordination_config)
    second = clients(coordination_config)
    other = clients({**coordination_config, "service_name": "another-service"})
    lease = await first.leader_try_acquire_async("watch", "first", 0.05)
    assert lease
    assert await second.leader_try_acquire_async("watch", "second", 1) is None
    assert await other.leader_try_acquire_async("watch", "other", 1)
    await asyncio.sleep(0.08)
    successor = await second.leader_try_acquire_async("watch", "second", 1)
    assert successor
    assert not await first.leader_renew_async(lease, 1)
    await first.leader_release_async(lease)
    assert await second.leader_renew_async(successor, 1)
    retained = await first.leader_try_acquire_async("other-watch", "first", 1)
    second.close()
    assert await first.leader_renew_async(retained, 1)
    assert await first.leader_try_acquire_async("watch", "first", 1)
    with pytest.raises(RuntimeError, match="closed"):
        await second.revision_allocate_async("work")


@pytest.mark.asyncio
async def test_revisions_concurrent_publish_and_reopen(coordination_config, clients):
    first = AsyncAGFSClient(clients(coordination_config))
    second_native = clients(coordination_config)
    second = AsyncAGFSClient(second_native)
    key = "semantic:账户:文档/a"
    assert await first.revision_latest(key) == 0
    keys = [key if i % 2 else f"other-work:{i}" for i in range(32)]
    revisions = await asyncio.gather(
        *[(first if i % 2 else second).revision_allocate(work) for i, work in enumerate(keys)]
    )
    assert sorted(revisions) == list(range(1, 33))
    assert await first.revision_latest(key) == 0
    assert await first.revision_publish(key, 31) == 31
    assert await second.revision_publish(key, 2) == 31
    assert await first.revision_latest(key) == 31
    with pytest.raises(RuntimeError, match="not allocated"):
        await first.revision_publish(key, 33)
    with pytest.raises(RuntimeError, match="positive"):
        await first.revision_publish(key, 0)
    second_native.close()
    reopened = AsyncAGFSClient(clients(coordination_config))
    assert await reopened.revision_allocate("new-key-after-reopen") == 33
    assert await reopened.revision_latest(key) == 31
    isolated = AsyncAGFSClient(clients({**coordination_config, "service_name": "isolated"}))
    assert await isolated.revision_latest(key) == 0
    assert await isolated.revision_allocate(key) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("coordination_config", ["sqlite", "cache"], indirect=True)
async def test_revision_ttl_refresh_expiry_and_counter_continuity(coordination_config, clients):
    native = clients(coordination_config)
    client = AsyncAGFSClient(native)
    namespace = coordination_config["service_name"]
    provider = coordination_config["coordination"]["provider"]
    cache = None
    if provider == "cache":
        redis = pytest.importorskip("redis.asyncio")
        cache = redis.Redis.from_url(coordination_config["cache"]["redis"]["endpoints"][0])
    else:
        database = coordination_config["coordination"]["sqlite_path"]

    async def expires_at():
        if cache:
            return await cache.pexpiretime(f"{{{namespace}}}:ov:revision:latest:work")
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT expires_at FROM coordination_revisions WHERE namespace=? AND key='work'",
                (namespace,),
            ).fetchone()
            return row[0] if row else -2

    async def set_expiry(deadline):
        if cache:
            await cache.pexpireat(f"{{{namespace}}}:ov:revision:latest:work", deadline)
        else:
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE coordination_revisions SET expires_at=? WHERE namespace=? AND key='work'",
                    (deadline, namespace),
                )

    try:
        assert await client.revision_allocate("work") == 1
        assert await client.revision_allocate("other-work") == 2
        assert await expires_at() == -2  # Allocation retains no per-key record.
        if cache:
            assert await cache.pttl(f"{{{namespace}}}:ov:revision:counter") == -1
        assert await client.revision_allocate("work") == 3
        before_publish = int(time.time() * 1000)
        assert await client.revision_publish("work", 3) == 3
        deadline = await expires_at()
        assert before_publish + 86_400_000 <= deadline <= int(time.time() * 1000) + 86_400_000

        await set_expiry(int(time.time() * 1000) + 5000)
        shortened = await expires_at()
        assert await client.revision_latest("work") == 3
        assert await expires_at() == shortened
        assert await client.revision_publish("work", 1) == 3
        assert await expires_at() > shortened
        await set_expiry(1)
        assert await client.revision_latest("work") == 0
        assert await expires_at() == -2
        # A retry can still publish its retained revision after expiry.
        assert await client.revision_publish("work", 1) == 1
        assert await client.revision_allocate("fresh-key") == 4
    finally:
        if cache:
            await cache.aclose()


@pytest.mark.asyncio
async def test_sqlite_startup_cleanup_preserves_live_leases_and_release_deletes(tmp_path, clients):
    database = str(tmp_path / "coordination.db")
    namespace = uuid4().hex
    config = {
        "service_name": namespace,
        "coordination": {"provider": "sqlite", "sqlite_path": database},
    }
    first = clients(config)
    expired = await first.leader_try_acquire_async("expired", "first", 60)
    live = await first.leader_try_acquire_async("live", "first", 60)
    closing = await first.leader_try_acquire_async("closing", "first", 60)
    assert expired and live and closing
    assert await first.revision_allocate_async("expired-work") == 1
    assert await first.revision_publish_async("expired-work", 1) == 1
    assert await first.revision_allocate_async("live-work") == 2
    assert await first.revision_publish_async("live-work", 2) == 2
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE coordination_leaders SET expires_at=0 WHERE key='expired'")
        connection.execute(
            "INSERT INTO coordination_leaders VALUES ('crashed-other-service', 'watch', 'token', 0)"
        )
        connection.execute(
            "UPDATE coordination_revisions SET expires_at=0 WHERE key='expired-work'"
        )
    reopened = clients(config)
    assert await reopened.revision_latest_async("expired-work") == 0  # Initializes SQLite.
    assert await reopened.revision_latest_async("live-work") == 2
    assert await reopened.revision_allocate_async("new-work") == 3
    assert await reopened.leader_try_acquire_async("live", "second", 60) is None
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT key FROM coordination_leaders ORDER BY key"
        ).fetchall() == [("closing",), ("live",)]
        assert connection.execute("SELECT key FROM coordination_revisions").fetchall() == [
            ("live-work",)
        ]
    await first.leader_release_async(live)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT key FROM coordination_leaders").fetchall() == [
            ("closing",)
        ]
    first.close()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM coordination_leaders").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_cache_revision_integers_remain_exact_above_lua_precision(redis_url, clients):
    redis = pytest.importorskip("redis.asyncio")
    config = cache_config(redis_url, deadline=2000)
    client = clients(config)
    namespace = config["service_name"]
    initial = 2**53
    async with redis.Redis.from_url(redis_url) as cache:
        await cache.set(f"{{{namespace}}}:ov:revision:counter", str(initial))
        older = await client.revision_allocate_async("work")
        newer = await client.revision_allocate_async("work")
        assert (older, newer) == (initial + 1, initial + 2)
        assert await client.revision_publish_async("work", newer) == newer
        assert await client.revision_publish_async("work", older) == newer
        assert await client.revision_latest_async("work") == newer


_CHILD = """
import asyncio, json, sys
from openviking.pyagfs import get_binding_client
async def main():
    cls, _ = get_binding_client()
    client = cls(config=json.loads(sys.argv[1]))
    lease = await client.leader_try_acquire_async("process-watch", "child", 0.8)
    revision = await client.revision_allocate_async("process-work")
    await client.revision_publish_async("process-work", revision)
    print(json.dumps({"lease": lease, "revision": revision}), flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""


@pytest.mark.asyncio
async def test_process_visibility_and_recovery_after_crash(coordination_config, clients):
    parent = clients(coordination_config)
    shared = coordination_config["coordination"]["provider"] != "memory"
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        json.dumps(coordination_config),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        line = await asyncio.wait_for(child.stdout.readline(), 20)
        assert line, (await child.stderr.read()).decode()
        result = json.loads(line)
        assert result["lease"]
        assert result["revision"] == 1
        lease = await parent.leader_try_acquire_async("process-watch", "parent", 1)
        assert (lease is None) == shared
        if lease:
            await parent.leader_release_async(lease)
        assert await parent.revision_latest_async("process-work") == (1 if shared else 0)
        assert await parent.revision_allocate_async("different-process-work") == (
            2 if shared else 1
        )
    finally:
        if child.returncode is None:
            child.kill()
        await child.wait()
    # Crash cleanup uses lease expiry, with no Python process-local registry.
    await asyncio.sleep(0.85)
    assert await parent.leader_try_acquire_async("process-watch", "parent", 1)
    reopened = clients(coordination_config)
    assert await reopened.revision_allocate_async("process-work") == (3 if shared else 2)
    assert await reopened.revision_latest_async("process-work") == (1 if shared else 0)


@pytest.mark.asyncio
async def test_default_memory_does_not_follow_cache_configuration(
    redis_url, clients, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    config = {
        "service_name": uuid4().hex,
        "cache": {"runtime_enabled": True, "redis": {"endpoints": [redis_url], "pool_size": 2}},
    }
    first = clients(config)
    second = clients({"service_name": config["service_name"]})
    lease = await first.leader_try_acquire_async("watch", "first", 1)
    assert lease
    assert await second.leader_try_acquire_async("watch", "second", 1) is None
    assert await first.revision_allocate_async("work") == 1
    assert await second.revision_allocate_async("work") == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "coordination",
    [
        {"provider": "sqlite"},
        {"provider": "sqlite", "sqlite_path": "relative.db"},
        {"provider": "cache"},
        {"provider": "unknown"},
        {"provider": "memory", "sqlite_path": "/tmp/unexpected.db"},
    ],
)
def test_invalid_native_coordination_config_fails_at_construction(coordination):
    client_class, _ = get_binding_client()
    with pytest.raises(ValueError):
        client_class(config={"service_name": "invalid", "coordination": coordination})


@pytest.mark.asyncio
async def test_sqlite_initialization_recovers_on_same_client_after_lock_release(tmp_path, clients):
    database = str(tmp_path / "coordination.db")
    blocker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        """
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute("CREATE TABLE unrelated (value INTEGER)")
connection.commit()
connection.execute("BEGIN EXCLUSIVE")
print("locked", flush=True)
sys.stdin.readline()
connection.rollback()
connection.close()
""",
        database,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        assert await asyncio.wait_for(blocker.stdout.readline(), 5) == b"locked\n"
        client = clients(
            {
                "service_name": uuid4().hex,
                "coordination": {"provider": "sqlite", "sqlite_path": database},
            }
        )
        with pytest.raises(RuntimeError, match="database is locked"):
            await asyncio.wait_for(client.revision_allocate_async("work"), 10)
        blocker.stdin.write(b"release\n")
        await blocker.stdin.drain()
        assert await asyncio.wait_for(blocker.wait(), 5) == 0
    finally:
        if blocker.returncode is None:
            blocker.kill()
        await blocker.wait()
    assert await client.revision_allocate_async("work") == 1
    assert await client.revision_publish_async("work", 1) == 1
    assert await client.revision_latest_async("work") == 1


def cache_config(endpoint, *, deadline=100):
    return {
        "service_name": uuid4().hex,
        "coordination": {"provider": "cache", "operation_timeout_ms": deadline},
        "cache": {
            "runtime_enabled": True,
            "redis": {"endpoints": [endpoint], "pool_size": 1, "command_timeout_ms": 0},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "deadline"), [("allocate", 100), ("acquire", None), ("renew", 100)]
)
async def test_native_cache_operations_have_deadlines_without_command_timeouts(
    operation, deadline, redis_server, clients
):
    server, endpoint = redis_server
    config = cache_config(endpoint)
    if deadline is None:
        config["coordination"].pop("operation_timeout_ms")
    client = clients(config)
    revision = await client.revision_allocate_async("work")
    lease = await client.leader_try_acquire_async("watch", "node", 2)
    assert lease
    operations = {
        "allocate": lambda: client.revision_allocate_async("other-work"),
        "acquire": lambda: client.leader_try_acquire_async("other-watch", "node", 2),
        "renew": lambda: client.leader_renew_async(lease, 2),
    }
    os.kill(server.pid, signal.SIGSTOP)
    try:
        with pytest.raises(RuntimeError, match=f"coordination .* exceeded {deadline or 5000} ms"):
            await asyncio.wait_for(operations[operation](), 8)
    finally:
        os.kill(server.pid, signal.SIGCONT)
    assert await client.revision_allocate_async("recovered") > revision


@pytest.mark.asyncio
async def test_persisted_enqueue_cancels_and_native_close_finishes_while_redis_is_paused(
    redis_server, clients
):
    server, endpoint = redis_server
    client = clients(cache_config(endpoint))
    client.mount("queuefs", "/queue", {"backend": "memory"})
    queue = SemanticQueue(client, "/queue", "Semantic")
    publishing = asyncio.Event()
    publish = queue._async_agfs.revision_publish

    async def pause_before_publish(key, revision):
        os.kill(server.pid, signal.SIGSTOP)
        publishing.set()
        return await publish(key, revision)

    queue._async_agfs.revision_publish = pause_before_publish
    msg = SemanticMsg(
        uri="viking://resources/docs", context_type="resource", coalesce_key="resource|docs"
    )
    pending = asyncio.create_task(queue.enqueue(msg))
    try:
        await asyncio.wait_for(publishing.wait(), 5)
        assert (await queue.peek()).id == msg.id
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 2)
        # Keep Redis paused: the native barrier and connection shutdown must
        # both finish even though the peer never answers the publish or QUIT.
        with pytest.raises(RuntimeError, match="QUIT exceeded 5000 ms"):
            await asyncio.wait_for(asyncio.to_thread(client.close), 8)
        with pytest.raises(RuntimeError, match="closed"):
            await client.revision_latest_async("work")
        client.close()  # Closing again remains idempotent.
    finally:
        os.kill(server.pid, signal.SIGCONT)
        if not pending.done():
            pending.cancel()
            await asyncio.wait([pending], timeout=5)


@pytest.mark.asyncio
async def test_canonical_config_loads_explicit_sqlite_path(tmp_path):
    client_class, _ = get_binding_client()
    database = str(tmp_path / "host-disk" / "coordination.db")
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": "/efs/workspace",
                    "agfs": {"coordination": {"provider": "sqlite", "sqlite_path": database}},
                },
            }
        )
    )
    first = client_class(config_path=str(config_path), config={"service_name": "canonical"})
    second = client_class(
        config={
            "service_name": "canonical",
            "coordination": {"provider": "sqlite", "sqlite_path": database},
        }
    )
    try:
        assert await first.leader_try_acquire_async("watch", "first", 1)
        assert await second.leader_try_acquire_async("watch", "second", 1) is None
        assert await first.revision_allocate_async("work") == 1
        assert await second.revision_allocate_async("work") == 2
    finally:
        first.close()
        second.close()
