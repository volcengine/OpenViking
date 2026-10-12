"""Exercise the kernel with single-key KV primitives, without any SQL API."""

import asyncio
import copy
import time
import uuid

import pytest
from conftest import FakeViking

from openviking_gateway.capture import CaptureWorker
from openviking_gateway.capture_store import Document, LeaseLost
from openviking_gateway.kernel import MemoryKernel
from openviking_gateway.models import Policy
from openviking_gateway.records import RecordKind as K
from openviking_gateway.replay_store import INHERITED
from openviking_gateway.storage import ManagementStore, digest
from openviking_gateway.tool_protocols import ResponseCapture


class KVReplay:
    def __init__(self):
        self.values = {}

    async def read(self, scope, session, anchors, ancestors=()):
        rows = sorted(
            (owner == session, owner, k, a, v)
            for (s, owner, k, a), v in self.values.items()
            if s == scope
            and a in anchors
            and k != K.REPLY
            and (owner == session or owner in ancestors and k in INHERITED)
        )
        return {(k, a): copy.deepcopy(v) for _, _, k, a, v in rows}

    async def replies(self, scope, anchors):
        owners = {}
        for s, owner, k, a in self.values:
            if s == scope and k == K.REPLY and a in anchors:
                owners.setdefault(a, set()).add(owner)
        return owners

    async def put(self, scope, session, kind, anchor, value):
        return copy.deepcopy(
            self.values.setdefault((scope, session, kind, anchor), copy.deepcopy(value))
        )


class KVState:
    def __init__(self):
        self.values = {}

    async def read(self, scope, keys):
        return {
            key: copy.deepcopy(self.values[scope, key])
            for key in keys
            if (scope, key) in self.values
        }

    async def swap(self, scope, key, previous, value):
        current = self.values.get((scope, key), Document())
        if current.version != previous.version:
            return False
        self.values[scope, key] = Document(copy.deepcopy(value), previous.version + 1)
        return True


class KVQueue(KVState):
    def __init__(self):
        super().__init__()
        self.ready, self.leases = {}, {}

    async def get(self, scope, session):
        return copy.deepcopy(self.values.get((scope, session), Document()))

    async def swap(self, scope, session, previous, value, ready, owner=None):
        key = scope, session
        lease = self.leases.get(key, (None, 0))
        if owner is not None and (lease[0] != owner or lease[1] <= time.time()):
            raise LeaseLost
        if not await super().swap(scope, session, previous, value):
            return False
        self.ready[key] = ready
        return True

    async def claim(self, lease_seconds=120):
        for key, ready in self.ready.items():
            if (
                ready is not None
                and ready <= time.time()
                and self.leases.get(key, (None, 0))[1] < time.time()
            ):
                owner = uuid.uuid4().hex
                self.leases[key] = owner, time.time() + lease_seconds
                return {
                    "scope": key[0],
                    "session": key[1],
                    "owner": owner,
                    "document": await self.get(*key),
                }
        return None

    async def release(self, item):
        key = item["scope"], item["session"]
        if self.leases[key][0] == item["owner"]:
            self.leases.pop(key)


class KVPorts:
    def __init__(self):
        self.replay, self.state, self.capture = KVReplay(), KVState(), KVQueue()

    async def load(self, scope, session, anchors, ancestors=()):
        return (
            await self.replay.read(scope, session, anchors, ancestors),
            await self.state.read(scope, [session]),
            await self.capture.get(scope, session),
        )

    async def keep(self, scope, sessions, keys):
        pass


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
async def test_kernel_and_capture_work_with_kv_ports(credential, protocol):
    ports, viking = KVPorts(), FakeViking()
    kernel = MemoryKernel(ports, viking)

    class Management:
        async def get(self, *_):
            return credential

        async def log(self, *_):
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
    assert set(K) == {
        K.ROOT,
        K.INJECTION,
        K.DISABLED,
        K.HIDDEN,
        K.REPLACEMENT,
        K.REPLY,
        K.REASONING,
    }


async def test_expired_lease_cannot_write_or_release_new_owner(setup_kernel):
    _, store, _, _ = setup_kernel
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


async def test_corrupt_record_does_not_fail_other_reads_in_batch(setup_kernel):
    from cryptography.fernet import InvalidToken

    _, store, _, _ = setup_kernel
    for scope in ("damaged", "healthy"):
        await store.replay.put(scope, "session", K.ROOT, "", {"scope": scope})

    def corrupt():
        with store.connect() as connection:
            connection.execute(
                "UPDATE replay SET value=? WHERE scope=?", (b"invalid ciphertext", "damaged")
            )

    await store.run(corrupt, write=True)
    damaged, healthy = await asyncio.gather(
        store.load("damaged", "session", [""]),
        store.load("healthy", "session", [""]),
        return_exceptions=True,
    )
    assert isinstance(damaged, InvalidToken)
    assert healthy[0][K.ROOT, ""] == {"scope": "healthy"}


async def test_new_session_does_not_freeze_other_process_stale_policy(setup_kernel, credential):
    kernel, store, _, encryption = setup_kernel
    writer = ManagementStore(store.path.parent / "management.sqlite3", encryption)
    reader = ManagementStore(writer.path, encryption)
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
        reader.close()


async def test_response_bookkeeping_preserves_configuration_cache(setup_kernel, monkeypatch):
    _, store, _, encryption = setup_kernel
    management = ManagementStore(store.path.parent / "management.sqlite3", encryption)
    await management.initialize()
    try:
        await management.save("tenant", "keys", digest("downstream"), {"name": "key"})
        await management.save("tenant", "upstreams", "model", {"name": "model"})
        await management.authenticate("downstream")
        await management.list("tenant", "upstreams")
        await management.save("tenant", "responses", "response", {"upstream_id": "model"}, ttl=1)
        await management.expire_logs(0)
        read = management.run
        reads = []

        async def counted(fn, *args, **kwargs):
            if not kwargs.get("write"):
                reads.append(fn)
            return await read(fn, *args, **kwargs)

        monkeypatch.setattr(management, "run", counted)
        assert (await management.authenticate("downstream"))["name"] == "key"
        assert (await management.list("tenant", "upstreams"))[0]["name"] == "model"
        assert not reads
        await management.delete("tenant", "keys", digest("downstream"))
        assert await management.authenticate("downstream") is None
    finally:
        management.close()
