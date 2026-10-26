# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Redis adapters: every write that must be atomic is one Lua script on one slot.

Keys below follow ``<key_prefix>v1:``; ``h`` is a 32-hex digest, ``{...}`` a Cluster hash
tag, TTLs are milliseconds, and the last column names the writing script or method.

m:{a<h(account)>}:o:<kind>:<h(id)>  hash    v, rev, u=h(user_id); object ttl  obj_save, obj_delete
m:{a<h(account)>}:ix:<kind>         zset    id -> expiry ms, or +inf          obj_save, obj_delete
m:{c<h(id)>}                        string  a key's account; the key's ttl    save, delete
m:{a<h(account)>}:log               list    ms NUL h(user_id) NUL value,      log_push,
                                            newest first; log_ttl             log_delete_user
kd:{<scope>}, kd:{cap}:<scope>      string  scope deletion tombstone          expire, allow_scope
k:{<scope>}:rp:<h(session)>         hash    <kind>:<anchor> -> value; ttl     kernel_replay_put,
                                                                              kernel_keep
k:{<scope>}:r:<anchor>              set     sessions with reply:<anchor>;     kernel_replay_put,
                                            ttl                               kernel_replies_prune
k:{<scope>}:st:<h(key)>             hash    v, ver; ttl                       kernel_state_swap,
                                                                              kernel_keep
k:{cap}:<scope>:<h(session)>        hash    v, ver, ready (epoch s), lease    kernel_capture_*
                                            (epoch ms), owner; ttl
k:{cap}:ready                       zset    scope NUL session -> due ms       kernel_capture_*

Capture keys share the ``{cap}`` slot so a claim can read the ready queue in one script; the
tombstone is mirrored there because a script may only touch one slot. For the same reason
the credential pointer is a plain SET after obj_save rather than part of it.
"""

import asyncio
import time
import uuid
from collections import OrderedDict, defaultdict
from pathlib import Path

import orjson
from redis import VERSION
from redis.asyncio.cluster import RedisCluster
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.crc import key_slot
from redis.exceptions import ResponseError

from openviking.server.account_stores.redis_client import create_redis_client

from .capture_store import Document, LeaseLost
from .records import RecordKind as K
from .replay_store import INHERITED
from .storage import AsyncCodec, digest

LOG_LIMIT = 10000
INDEXED_KINDS = {"keys", "upstreams", "policies", "users"}


def h(value):
    return digest(value)[:32]


def connect(settings):
    """Open the gateway's client handle from ``storage.redis`` settings."""
    handle = create_redis_client(
        settings,
        decode_responses=False,
        # None enables Cluster's default retries, including writes with unknown outcomes.
        retry=Retry(NoBackoff(), 0),
        # redis-py 5 Cluster retries by its own counter rather than ``retry``.
        **(
            {"cluster_error_retry_attempts": 0}
            if VERSION < (6,) and settings["mode"] == "cluster"
            else {}
        ),
    )
    # redis-py pools raise once ``pool_size`` connections are busy; queue commands instead.
    gate, execute = asyncio.Semaphore(settings["pool_size"]), handle.client.execute_command

    async def execute_command(*args, **kwargs):
        async with gate:
            return await execute(*args, **kwargs)

    handle.client.execute_command = execute_command
    return handle


class RedisBackend:
    def __init__(self, client, key_prefix, codec: AsyncCodec):
        self.client, self.prefix, self.codec = client, key_prefix + "v1:", codec
        self.scripts = {}

    def script(self, name):
        if name not in self.scripts:
            self.scripts[name] = self.client.register_script(
                (Path(__file__).parent / "lua" / f"{name}.lua").read_text()
            )
        return self.scripts[name]

    async def scan(self, pattern):
        if not isinstance(self.client, RedisCluster):
            async for key in self.client.scan_iter(match=pattern, count=500):
                yield key
            return
        await self.client.initialize()
        nodes = (
            [self.client.get_node_from_key(pattern)]
            if "{" in pattern
            else self.client.get_primaries()
        )
        for node in nodes:
            cursor = 0
            while True:
                cursors, keys = await self.client.scan(
                    cursor, match=pattern, count=500, target_nodes=node
                )
                for key in keys:
                    yield key
                cursor = cursors[node.name]
                if not cursor:
                    break

    async def unlink(self, keys):
        slots = defaultdict(list)
        for key in keys:
            slots[key_slot(key.encode() if isinstance(key, str) else key)].append(key)
        for batch in slots.values():
            for start in range(0, len(batch), 500):
                # The Cluster convenience method fans out; explicit commands stay in one slot.
                await self.client.execute_command("UNLINK", *batch[start : start + 500])

    async def delete_matching(self, pattern):
        batch = []
        async for key in self.scan(pattern):
            batch.append(key)
            if len(batch) == 500:
                await self.unlink(batch)
                batch.clear()
        await self.unlink(batch)


class RedisManagementStore:
    def __init__(self, backend: RedisBackend, log_ttl):
        self.backend, self.client, self.codec = backend, backend.client, backend.codec
        self.log_ttl = log_ttl

    def account_prefix(self, account):
        return self.backend.prefix + "m:{a" + h(account) + "}:"

    def object_key(self, account, kind, identifier):
        return self.account_prefix(account) + f"o:{kind}:{h(identifier)}"

    def credential_key(self, identifier):
        return self.backend.prefix + "m:{c" + h(identifier) + "}"

    def object_keys(self, account, kind, identifier):
        keys = [self.object_key(account, kind, identifier)]
        if kind in INDEXED_KINDS:
            keys.append(self.account_prefix(account) + f"ix:{kind}")
        return keys

    async def initialize(self):
        await self.client.ping()

    async def entry(self, identifier, value, revision):
        value = await self.codec.decode(value)
        if value.get("expires_at", float("inf")) <= time.time():
            return None
        return {**value, "id": identifier, "revision": int(revision)}

    async def get(self, account, kind, identifier):
        value, revision = await self.client.hmget(
            self.object_key(account, kind, identifier), "v", "rev"
        )
        return None if value is None else await self.entry(identifier, value, revision)

    async def list(self, account, kind, user_id=None):
        index = self.account_prefix(account) + f"ix:{kind}"
        identifiers = sorted(i.decode() for i in await self.client.zrange(index, 0, -1))
        if not identifiers:
            return []
        rows = await self.backend.script("hash_read")(
            keys=[self.object_key(account, kind, i) for i in identifiers], args=["v", "rev"]
        )
        values = [await self.entry(identifiers[i - 1], value, rev) for i, value, rev in rows]
        return [
            value
            for value in values
            if value is not None and (user_id is None or value.get("user_id") == user_id)
        ]

    async def save(self, account, kind, identifier, value, ttl=None):
        if ttl is not None:
            value = {**value, "expires_at": time.time() + ttl}
        revision, remaining = await self.backend.script("obj_save")(
            keys=self.object_keys(account, kind, identifier),
            args=[
                identifier,
                await self.codec.encode(value),
                h(value.get("user_id") or ""),
                "" if ttl is None else int(ttl * 1000),
            ],
        )
        if kind == "keys":
            # PTTL is -1 without an expiry and may reach 0 on an update that sets none.
            await self.client.set(
                self.credential_key(identifier),
                account,
                **({"px": max(1, remaining)} if remaining >= 0 else {}),
            )
        return {**value, "id": identifier, "revision": revision}

    async def delete(self, account, kind, identifier):
        await self.backend.script("obj_delete")(
            keys=self.object_keys(account, kind, identifier), args=[identifier]
        )
        if kind == "keys":
            await self.client.execute_command("DEL", self.credential_key(identifier))

    async def authenticate(self, key):
        return await self.credential(digest(key))

    async def credential(self, identifier):
        account = await self.client.get(self.credential_key(identifier))
        if account is None:
            return None
        value = await self.get(account.decode(), "keys", identifier)
        if value is None:
            return None
        value.pop("revision")
        return {**value, "account": account.decode()}

    async def log(self, account, value, user_id=None):
        key = self.account_prefix(account) + "log"
        prefix = f"{int(time.time() * 1000)}\0{h(user_id) if user_id is not None else ''}\0"
        await self.backend.script("log_push")(
            keys=[key],
            args=[
                prefix.encode() + await self.codec.encode(value),
                LOG_LIMIT,
                int(self.log_ttl * 1000),
            ],
        )

    async def logs(self, account, limit=200, user_id=None):
        key, values = self.account_prefix(account) + "log", []
        cutoff = (time.time() - self.log_ttl) * 1000
        user = None if user_id is None else h(user_id).encode()
        page = min(1000, limit) if user is None else 1000
        for start in range(0, LOG_LIMIT, page):
            rows = await self.client.lrange(key, start, start + page - 1)
            for row in rows:
                timestamp, owner, value = row.split(b"\0", 2)
                # Rows are newest first: the rest are older still.
                if int(timestamp) < cutoff:
                    return values
                if user is None or owner == user:
                    values.append({"time": int(timestamp) / 1000, **await self.codec.decode(value)})
                    if len(values) >= limit:
                        return values
            if len(rows) < page:
                break
        return values

    async def delete_user(self, account, user_id):
        for kind in ("keys", "users"):
            index = self.account_prefix(account) + f"ix:{kind}"
            async for identifier, _ in self.client.zscan_iter(index, count=500):
                identifier = identifier.decode()
                user = await self.client.hget(self.object_key(account, kind, identifier), "u")
                if user == h(user_id).encode():
                    await self.delete(account, kind, identifier)
        key = self.account_prefix(account) + "log"
        await self.backend.script("log_delete_user")(
            keys=[key, key + ":rebuild"], args=[h(user_id)]
        )

    async def delete_account(self, account):
        identifiers = await self.client.zrange(self.account_prefix(account) + "ix:keys", 0, -1)
        await self.backend.delete_matching(self.account_prefix(account) + "*")
        await self.backend.unlink(
            [self.credential_key(identifier.decode()) for identifier in identifiers]
        )

    async def expire_logs(self, before):
        pass


class RedisKernelStore:
    def __init__(self, backend, ttl):
        self.backend, self.client, self.codec = backend, backend.client, backend.codec
        self.prefix, self.ttl = backend.prefix, int(ttl * 1000)
        self.replay, self.state, self.capture = (
            RedisReplayStore(self),
            RedisStateStore(self),
            RedisCaptureQueue(self),
        )
        self.touched = OrderedDict()

    async def initialize(self):
        await self.client.ping()

    def tombstone(self, scope, capture=False):
        return f"{self.prefix}kd:{{cap}}:{scope}" if capture else f"{self.prefix}kd:{{{scope}}}"

    def replay_key(self, scope, session):
        return f"{self.prefix}k:{{{scope}}}:rp:{h(session)}"

    def state_key(self, scope, key):
        return f"{self.prefix}k:{{{scope}}}:st:{h(key)}"

    def capture_key(self, scope, session):
        return f"{self.prefix}k:{{cap}}:{scope}:{h(session)}"

    async def script(self, name, keys, args):
        try:
            return await self.backend.script(name)(keys=keys, args=args)
        except ResponseError as error:
            if "OVGW_DELETED" in str(error):
                raise RuntimeError("Gateway user data has been deleted") from error
            if "OVGW_LEASE_LOST" in str(error):
                raise LeaseLost from error
            raise

    async def touch(self, key):
        now = time.monotonic()
        if now - self.touched.get(key, float("-inf")) < 60:
            return
        self.touched[key] = now
        self.touched.move_to_end(key)
        if len(self.touched) > 2048:
            self.touched.popitem(last=False)
        await self.client.pexpire(key, self.ttl)

    async def load(self, scope, session, anchors, ancestors=()):
        replay, document = await asyncio.gather(
            self.replay.load(scope, session, anchors, ancestors, state=True),
            self.capture.get(scope, session),
        )
        return replay[0], replay[1], document

    async def keep(self, scope, sessions, keys):
        sessions = list(sessions)
        await self.script(
            "kernel_keep",
            [
                self.tombstone(scope),
                *[self.replay_key(scope, sid) for sid in sessions],
                *[self.state_key(scope, key) for key in keys],
            ],
            [self.ttl],
        )
        await asyncio.gather(*(self.touch(self.capture_key(scope, sid)) for sid in sessions))

    async def expire(self, before, scope=None):
        if scope is None:
            return
        await self.client.set(self.tombstone(scope), 1)
        await self.client.set(self.tombstone(scope, capture=True), 1)
        for pattern in (f"{self.prefix}k:{{{scope}}}:*", f"{self.prefix}k:{{cap}}:{scope}:*"):
            await self.backend.delete_matching(pattern)

    async def allow_scope(self, scope):
        await self.client.execute_command("DEL", self.tombstone(scope))
        await self.client.execute_command("DEL", self.tombstone(scope, capture=True))


class RedisReplayStore:
    def __init__(self, store):
        self.store = store

    def index(self, scope, anchor):
        return f"{self.store.prefix}k:{{{scope}}}:r:{anchor}"

    async def load(self, scope, session, anchors, ancestors=(), state=False):
        owners = [*sorted(set(ancestors) - {session}), session]
        keys = [self.store.replay_key(scope, sid) for sid in owners]
        if state:
            keys.append(self.store.state_key(scope, session))
        rows, observation = await self.store.script(
            "kernel_load",
            keys,
            [len(owners), orjson.dumps(list(anchors)), orjson.dumps(sorted(INHERITED)), int(state)],
        )
        values = {}
        for field, value in rows:
            kind, anchor = field.decode().split(":", 1)
            values[kind, anchor] = await self.store.codec.decode(value)
        states = {}
        if observation:
            states[session] = Document(
                await self.store.codec.decode(observation[0]), int(observation[1])
            )
        return values, states

    async def read(self, scope, session, anchors, ancestors=()):
        return (await self.load(scope, session, anchors, ancestors))[0]

    async def replies(self, scope, anchors):
        # A script rather than a pipeline: with cluster_error_retry_attempts=0, redis-py 5's
        # ClusterPipeline.execute makes no attempts at all.
        indexes = [self.index(scope, anchor) for anchor in anchors]
        rows = await self.store.script("kernel_replies_read", indexes, [])
        indexed = [
            (anchor, index, sorted(members))
            for anchor, index, members in zip(anchors, indexes, rows, strict=True)
            if members
        ]
        if not indexed:
            return {}
        keys = [index for _, index, _ in indexed]
        args = [self.store.ttl, len(indexed)]
        for anchor, _, members in indexed:
            keys += [self.store.replay_key(scope, sid.decode()) for sid in members]
            args += [anchor, len(members), *members]
        live = await self.store.script("kernel_replies_prune", keys, args)
        return {
            anchor: {sid.decode() for sid in sids}
            for (anchor, _, _), sids in zip(indexed, live, strict=True)
            if sids
        }

    async def put(self, scope, session, kind, anchor, value):
        kind = K(kind)
        keys = [self.store.tombstone(scope), self.store.replay_key(scope, session)]
        if kind == K.REPLY:
            keys.append(self.index(scope, anchor))
        result = await self.store.script(
            "kernel_replay_put",
            keys,
            [
                f"{kind.value}:{anchor}",
                await self.store.codec.encode(value),
                self.store.ttl,
                session,
            ],
        )
        await self.store.touch(self.store.capture_key(scope, session))
        return await self.store.codec.decode(result)


class RedisStateStore:
    def __init__(self, store):
        self.store = store

    async def read(self, scope, keys):
        keys = list(keys)
        if not keys:
            return {}
        rows = await self.store.script(
            "hash_read", [self.store.state_key(scope, key) for key in keys], ["v", "ver"]
        )
        return {
            keys[index - 1]: Document(await self.store.codec.decode(value), int(version))
            for index, value, version in rows
        }

    async def swap(self, scope, key, previous, value):
        return bool(
            await self.store.script(
                "kernel_state_swap",
                [self.store.tombstone(scope), self.store.state_key(scope, key)],
                [previous.version, await self.store.codec.encode(value), self.store.ttl],
            )
        )


class RedisCaptureQueue:
    def __init__(self, store):
        self.store = store
        self.ready = f"{store.prefix}k:{{cap}}:ready"

    def keys(self, scope, session):
        return [
            self.store.tombstone(scope, capture=True),
            self.store.capture_key(scope, session),
            self.ready,
        ]

    async def get(self, scope, session):
        value, version = await self.store.client.hmget(
            self.store.capture_key(scope, session), "v", "ver"
        )
        return (
            Document(await self.store.codec.decode(value), int(version))
            if value is not None
            else Document()
        )

    async def swap(self, scope, session, previous, value, ready, owner=None):
        result = await self.store.script(
            "kernel_capture_swap",
            self.keys(scope, session),
            [
                previous.version,
                await self.store.codec.encode(value),
                "" if ready is None else repr(ready),
                owner or "",
                self.store.ttl,
                scope + "\0" + session,
            ],
        )
        if result:
            await self.store.touch(self.store.replay_key(scope, session))
        return bool(result)

    async def claim(self, lease_seconds=120):
        candidates = await self.store.client.zrangebyscore(
            self.ready, "-inf", time.time() * 1000, start=0, num=8
        )
        for member in candidates:
            scope, session = member.decode().split("\0", 1)
            owner = uuid.uuid4().hex
            result = await self.store.script(
                "kernel_capture_claim",
                self.keys(scope, session),
                [owner, lease_seconds * 1000, member],
            )
            if result:
                return {
                    "scope": scope,
                    "session": session,
                    "owner": owner,
                    "document": Document(await self.store.codec.decode(result[0]), int(result[1])),
                }
        return None

    async def release(self, item):
        await self.store.script(
            "kernel_capture_release",
            self.keys(item["scope"], item["session"]),
            [item["owner"], item["scope"] + "\0" + item["session"]],
        )
