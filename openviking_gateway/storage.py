# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite adapters with optionally encrypted values. Writes have a dedicated executor."""

import asyncio
import hashlib
import os
import queue
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import orjson

from openviking.crypto.encryptor import MAGIC

from .cache import TTLCache
from .capture_store import CaptureQueue, SQLiteCaptureQueue
from .replay_store import ReplayStore, SQLiteReplayStore
from .state_store import SQLiteStateStore, StateStore

# Pre-release builds stamped versions 1-4 on incompatible layouts; reusing
# one of them would let old files through and fail on the first write.
SCHEMA_VERSION = 6
# Gateway data is not an OpenViking account's; it derives its own key from the root key.
ENCRYPTION_ACCOUNT = "_gateway"
LEGACY_KEY_ENVIRONMENT = "OPENVIKING_GATEWAY_ENCRYPTION_KEY"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class KernelStore(Protocol):
    """Composition port; adapters may pipeline the independent reads."""

    replay: ReplayStore
    capture: CaptureQueue
    state: StateStore

    async def load(self, scope, session, anchors, ancestors=()) -> tuple: ...
    async def keep(self, scope, sessions, keys) -> None: ...


class Database:
    def __init__(self, path: Path, encryptor=None):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        # Protect the file before SQLite opens it, not once per operation.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.encryptor = encryptor
        self.loop = None
        self.pool = queue.LifoQueue(maxsize=6)
        self.readers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gateway-read")
        self.writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gateway-write")

    @contextmanager
    def connect(self):
        try:
            connection = self.pool.get_nowait()
        except queue.Empty:
            connection = sqlite3.connect(
                self.path, timeout=30, isolation_level=None, check_same_thread=False
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            try:
                self.pool.put_nowait(connection)
            except queue.Full:
                connection.close()

    @staticmethod
    def use_wal(connection):
        # Leaving rollback mode needs an exclusive lock, and SQLite reports a concurrent
        # switch as "database is locked" at once instead of waiting out the busy timeout.
        # Workers opening a new or v5 store together retry until one has converted it.
        for _ in range(200):
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as error:
                if "locked" not in str(error):
                    raise
                time.sleep(0.05)
        connection.execute("PRAGMA journal_mode=WAL")

    @staticmethod
    def begin_migration(connection):
        """Serialize the one-time migration across standalone/server workers."""
        # Re-encoding a large v5 store outlasts the 30 s busy timeout of workers waiting here.
        connection.execute("PRAGMA busy_timeout=600000")
        try:
            connection.execute("BEGIN IMMEDIATE")
        finally:
            connection.execute("PRAGMA busy_timeout=30000")

    def close(self):
        self.readers.shutdown(wait=True)
        self.writer.shutdown(wait=True)
        while True:
            try:
                self.pool.get_nowait().close()
            except queue.Empty:
                return

    def encode(self, value: Any) -> bytes:
        data = orjson.dumps(value)
        if self.encryptor is None:
            return data
        return self.crypt(self.encryptor.encrypt(ENCRYPTION_ACCOUNT, data))

    def decode(self, value: bytes) -> Any:
        # Values written before encryption was enabled stay readable as plaintext.
        if value.startswith(MAGIC):
            if self.encryptor is None:
                raise RuntimeError(
                    "Gateway data is encrypted; configure the encryption settings it was "
                    "written with"
                )
            value = self.crypt(self.encryptor.decrypt(ENCRYPTION_ACCOUNT, value))
        return orjson.loads(value)

    def crypt(self, coroutine):
        """Run an encryptor coroutine on the app loop from a storage thread."""
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()

    async def run(self, fn, *args, write=False):
        self.loop = asyncio.get_running_loop()
        return await self.loop.run_in_executor(
            self.writer if write else self.readers, partial(fn, *args)
        )

    def migrate_legacy_values(self, c, tables):
        """Rewrite v0.5.0 values, which were Fernet-encrypted with a gateway-owned key."""
        from cryptography.fernet import Fernet

        cipher = None
        for table in tables:
            last = 0
            while rows := c.execute(
                f"SELECT rowid,value FROM {table} WHERE rowid>? ORDER BY rowid LIMIT 500",
                (last,),
            ).fetchall():
                cipher = cipher or Fernet(self.legacy_key())
                for rowid, value in rows:
                    c.execute(
                        f"UPDATE {table} SET value=? WHERE rowid=?",
                        (self.encode(orjson.loads(cipher.decrypt(value))), rowid),
                    )
                last = rows[-1][0]

    def legacy_key(self) -> bytes:
        if key := os.environ.get(LEGACY_KEY_ENVIRONMENT):
            return key.encode()
        try:
            return (self.path.parent / "encryption.key").read_bytes().strip()
        except FileNotFoundError:
            raise ValueError(
                f"{self.path} holds data encrypted by OpenViking Gateway 0.5; set "
                f"{LEGACY_KEY_ENVIRONMENT} or restore {self.path.parent / 'encryption.key'} "
                "with the original key to migrate it"
            ) from None


class SQLiteKernelStore(Database):
    def __init__(self, path, encryptor=None):
        super().__init__(path, encryptor)
        self.replay = SQLiteReplayStore(self)
        self.capture = SQLiteCaptureQueue(self)
        self.state = SQLiteStateStore(self)
        self._loads = []

    async def initialize(self):
        def initialize():
            with self.connect() as c:
                if c.execute("PRAGMA user_version").fetchone()[0] not in (0, 5, SCHEMA_VERSION):
                    raise ValueError("Unsupported gateway schema; configure a fresh storage_path")
                self.use_wal(c)
                c.executescript("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        scope TEXT, session TEXT, touched REAL NOT NULL,
                        PRIMARY KEY(scope,session));
                    CREATE TABLE IF NOT EXISTS replay (
                        scope TEXT, session TEXT, kind TEXT, anchor TEXT, value BLOB NOT NULL,
                        PRIMARY KEY(scope,session,kind,anchor),
                        FOREIGN KEY(scope,session) REFERENCES sessions ON DELETE CASCADE);
                    CREATE INDEX IF NOT EXISTS replay_anchors ON replay(scope,session,anchor);
                    CREATE INDEX IF NOT EXISTS replay_kinds ON replay(scope,kind,anchor);
                    CREATE TABLE IF NOT EXISTS state (
                        scope TEXT, key TEXT, value BLOB NOT NULL, version INTEGER NOT NULL,
                        touched REAL NOT NULL,
                        PRIMARY KEY(scope,key));
                    CREATE TABLE IF NOT EXISTS capture (
                        scope TEXT, session TEXT, value BLOB NOT NULL, version INTEGER NOT NULL,
                        ready REAL, lease REAL NOT NULL DEFAULT 0, owner TEXT,
                        PRIMARY KEY(scope,session),
                        FOREIGN KEY(scope,session) REFERENCES sessions ON DELETE CASCADE);
                    CREATE INDEX IF NOT EXISTS capture_ready ON capture(ready,lease);
                    CREATE TABLE IF NOT EXISTS deleted_scopes (scope TEXT PRIMARY KEY);
                """)
                self.begin_migration(c)
                if c.execute("PRAGMA user_version").fetchone()[0] == 5:
                    self.migrate_legacy_values(c, ("replay", "state", "capture"))
                c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                c.commit()

        await self.run(initialize, write=True)

    @staticmethod
    def check(c, scope):
        if c.execute("SELECT 1 FROM deleted_scopes WHERE scope=?", (scope,)).fetchone():
            raise RuntimeError("Gateway user data has been deleted")

    def touch(self, c, scope, session):
        self.check(c, scope)
        now = time.time()
        c.execute(
            "INSERT INTO sessions VALUES (?,?,?) ON CONFLICT(scope,session) "
            "DO UPDATE SET touched=excluded.touched WHERE sessions.touched<?",
            (scope, session, now, now - 60),
        )

    async def keep(self, scope, sessions, keys):
        """Keep sessions and state a live session reads, but does not write, from expiring."""

        def keep():
            with self.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                for session in sessions:
                    self.touch(c, scope, session)
                now = time.time()
                c.execute(
                    "UPDATE state SET touched=? WHERE scope=? AND touched<? "
                    "AND key IN (SELECT value FROM json_each(?))",
                    (now, scope, now - 86400, orjson.dumps(keys).decode()),
                )
                c.commit()

        await self.run(keep, write=True)

    async def load(self, scope, session, anchors, ancestors=()):
        """Coalesce concurrent reads for one loop turn; do not cache snapshots."""
        future = asyncio.get_running_loop().create_future()
        self._loads.append((future, (scope, session, anchors, ancestors)))
        if len(self._loads) == 1:
            asyncio.get_running_loop().call_soon(self._dispatch_loads)
        return await future

    def _dispatch_loads(self):
        batch, self._loads = self._loads[:64], self._loads[64:]
        if self._loads:
            asyncio.get_running_loop().call_soon(self._dispatch_loads)

        def read_batch():
            results = []
            with self.connect() as c:
                for _, (scope, session, anchors, ancestors) in batch:
                    try:
                        result = (
                            self.replay.read_in(c, scope, session, anchors, ancestors),
                            self.state.read_in(c, scope, [session]),
                            self.capture.read_in(c, scope, session),
                        )
                    except Exception as error:
                        result = error
                    results.append(result)
            return results

        def deliver(task):
            if task.cancelled():
                for future, _ in batch:
                    future.cancel()
                return
            error = task.exception()
            for i, (future, _) in enumerate(batch):
                if not future.done():
                    result = error if error is not None else task.result()[i]
                    if isinstance(result, Exception):
                        future.set_exception(result)
                    else:
                        future.set_result(result)

        task = asyncio.create_task(self.run(read_batch))
        task.add_done_callback(deliver)

    async def expire(self, before, scope=None):
        def expire():
            with self.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                if scope is not None:
                    c.execute("INSERT OR IGNORE INTO deleted_scopes VALUES (?)", (scope,))
                    c.execute("DELETE FROM sessions WHERE scope=?", (scope,))
                    c.execute("DELETE FROM state WHERE scope=?", (scope,))
                else:
                    c.execute("DELETE FROM sessions WHERE touched<?", (before,))
                    c.execute("DELETE FROM state WHERE touched<?", (before,))
                c.commit()

        await self.run(expire, write=True)

    async def allow_scope(self, scope):
        def allow():
            with self.connect() as c:
                c.execute("DELETE FROM deleted_scopes WHERE scope=?", (scope,))

        await self.run(allow, write=True)


class ManagementStore(Database):
    def __init__(self, path, encryptor=None):
        super().__init__(path, encryptor)
        self.cache = TTLCache(ttl=2, capacity=2048)

    async def initialize(self):
        def initialize():
            with self.connect() as c:
                if c.execute("PRAGMA user_version").fetchone()[0] not in (0, 5, SCHEMA_VERSION):
                    raise ValueError("Unsupported gateway schema; configure a fresh storage_path")
                self.use_wal(c)
                c.executescript("""
                    CREATE TABLE IF NOT EXISTS objects (
                        account TEXT, kind TEXT, id TEXT, revision INTEGER, value BLOB,
                        PRIMARY KEY(account,kind,id));
                    CREATE INDEX IF NOT EXISTS object_identity ON objects(kind,id);
                    CREATE TABLE IF NOT EXISTS request_logs (
                        id INTEGER PRIMARY KEY, account TEXT, time REAL, value BLOB, user TEXT);
                    CREATE TABLE IF NOT EXISTS object_expiry (
                        account TEXT, kind TEXT, id TEXT, expires REAL,
                        PRIMARY KEY(account,kind,id));
                    CREATE INDEX IF NOT EXISTS expiry_time ON object_expiry(expires);
                """)
                self.begin_migration(c)
                if c.execute("PRAGMA user_version").fetchone()[0] == 5:
                    c.execute("ALTER TABLE request_logs ADD COLUMN user TEXT")
                    self.migrate_legacy_values(c, ("objects", "request_logs"))
                    for row in c.execute("SELECT * FROM objects WHERE kind='upstreams'").fetchall():
                        value = self.decode(row["value"])
                        # Keep v5 request URLs: v5 appended /v1 to non-Ark bases lacking it.
                        # Bases already ending in another version (/api/paas/v4) produced
                        # .../v4/v1 in v5, which no provider serves; leave them as entered.
                        base = value["base_url"].rstrip("/")
                        path = urlsplit(base).path
                        ark = value.get("vendor") in {"ark", "byteplus"}
                        if value["protocol"] == "anthropic":
                            if ark and not path:
                                base += "/api/compatible"
                            if base.endswith("/v1"):
                                base = base[:-3]
                        elif ark:
                            if not path:
                                base += "/api/v3"
                        elif not re.search(r"/v\d+$", path):
                            base += "/v1"
                        if base != value["base_url"]:
                            value["base_url"] = base
                            c.execute(
                                "UPDATE objects SET value=?,revision=revision+1 "
                                "WHERE account=? AND kind=? AND id=?",
                                (self.encode(value), row["account"], row["kind"], row["id"]),
                            )
                c.execute("DROP INDEX IF EXISTS log_account")
                c.execute(
                    "CREATE INDEX IF NOT EXISTS log_account_user ON request_logs(account,user,time)"
                )
                c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                c.commit()

        await self.run(initialize, write=True)

    async def list(self, account, kind, user_id=None):
        def read():
            with self.connect() as c:
                return [
                    {**self.decode(r["value"]), "id": r["id"], "revision": r["revision"]}
                    for r in c.execute(
                        "SELECT * FROM objects WHERE account=? AND kind=? ORDER BY id",
                        (account, kind),
                    )
                ]

        if kind in {"keys", "upstreams"}:
            values = await self.cache.get(("list", account, kind), lambda: self.run(read))
        else:
            values = await self.run(read)
        return (
            [value for value in values if value.get("user_id") == user_id]
            if user_id is not None
            else values
        )

    async def get(self, account, kind, identifier):
        def read():
            with self.connect() as c:
                row = c.execute(
                    "SELECT o.value,o.revision,e.expires FROM objects o LEFT JOIN object_expiry e "
                    "ON (o.account,o.kind,o.id)=(e.account,e.kind,e.id) "
                    "WHERE o.account=? AND o.kind=? AND o.id=?",
                    (account, kind, identifier),
                ).fetchone()
                if not row or (row["expires"] is not None and row["expires"] <= time.time()):
                    return None
                value = self.decode(row["value"])
                if value.get("expires_at", float("inf")) <= time.time():
                    return None
                return {**value, "id": identifier, "revision": row["revision"]}

        # Policies become immutable session snapshots: never freeze a TTL-stale value.
        if kind == "keys":
            return await self.cache.get(("get", account, kind, identifier), lambda: self.run(read))
        return await self.run(read)

    async def save(self, account, kind, identifier, value, ttl=None):
        if ttl is not None:
            value = {**value, "expires_at": time.time() + ttl}

        def save():
            with self.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT INTO objects VALUES (?,?,?,1,?) ON CONFLICT(account,kind,id) "
                    "DO UPDATE SET revision=revision+1,value=excluded.value",
                    (account, kind, identifier, self.encode(value)),
                )
                if ttl is not None:
                    c.execute(
                        "INSERT OR REPLACE INTO object_expiry VALUES (?,?,?,?)",
                        (account, kind, identifier, value["expires_at"]),
                    )
                c.commit()

        await self.run(save, write=True)
        if kind in {"keys", "upstreams"}:
            self.cache.clear()
        return await self.get(account, kind, identifier)

    async def delete(self, account, kind, identifier):
        def delete():
            with self.connect() as c:
                c.execute(
                    "DELETE FROM objects WHERE account=? AND kind=? AND id=?",
                    (account, kind, identifier),
                )

        await self.run(delete, write=True)
        if kind in {"keys", "upstreams"}:
            self.cache.clear()

    async def authenticate(self, key):
        identifier = digest(key)

        def read():
            with self.connect() as c:
                row = c.execute(
                    "SELECT account,value FROM objects WHERE kind='keys' AND id=?", (identifier,)
                ).fetchone()
                return (
                    {**self.decode(row["value"]), "account": row["account"], "id": identifier}
                    if row
                    else None
                )

        return await self.cache.get(("auth", identifier), lambda: self.run(read))

    async def log(self, account, value, user_id=None):
        def log():
            with self.connect() as c:
                c.execute(
                    "INSERT INTO request_logs(account,time,value,user) VALUES (?,?,?,?)",
                    (account, time.time(), self.encode(value), user_id),
                )

        await self.run(log, write=True)

    async def logs(self, account, limit=200, user_id=None):
        def read():
            with self.connect() as c:
                where, args = "account=?", [account]
                if user_id is not None:
                    where += " AND user=?"
                    args.append(user_id)
                return [
                    {"time": r["time"], **self.decode(r["value"])}
                    for r in c.execute(
                        f"SELECT time,value FROM request_logs WHERE {where} ORDER BY id DESC LIMIT ?",
                        (*args, limit),
                    )
                ]

        return await self.run(read)

    async def delete_user(self, account, user_id):
        def delete():
            with self.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                for row in c.execute(
                    "SELECT kind,id,value FROM objects WHERE account=? "
                    "AND kind IN ('keys','users','responses')",
                    (account,),
                ).fetchall():
                    if self.decode(row["value"]).get("user_id") == user_id:
                        identity = (account, row["kind"], row["id"])
                        c.execute(
                            "DELETE FROM objects WHERE account=? AND kind=? AND id=?", identity
                        )
                        c.execute(
                            "DELETE FROM object_expiry WHERE account=? AND kind=? AND id=?",
                            identity,
                        )
                c.execute("DELETE FROM request_logs WHERE account=? AND user=?", (account, user_id))
                c.commit()

        await self.run(delete, write=True)
        self.cache.clear()

    async def expire_logs(self, before):
        def expire():
            with self.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                c.execute("DELETE FROM request_logs WHERE time<?", (before,))
                c.execute(
                    "DELETE FROM objects WHERE (account,kind,id) IN "
                    "(SELECT account,kind,id FROM object_expiry WHERE expires<=?)",
                    (time.time(),),
                )
                c.execute("DELETE FROM object_expiry WHERE expires<=?", (time.time(),))
                c.commit()

        await self.run(expire, write=True)

    async def delete_account(self, account):
        def delete():
            with self.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                c.execute("DELETE FROM objects WHERE account=?", (account,))
                c.execute("DELETE FROM request_logs WHERE account=?", (account,))
                c.execute("DELETE FROM object_expiry WHERE account=?", (account,))
                c.commit()

        await self.run(delete, write=True)
        self.cache.clear()


async def delete_user_data(management, store, account, user_id):
    """Revoke keys and erase gateway state; OpenViking memory is unaffected."""
    # Keys go first, so a response finishing meanwhile no longer finds one to record state for.
    await management.delete_user(account, user_id)
    for protocol in ("chat", "responses", "anthropic"):
        await store.expire(0, digest(account + "\0" + user_id + "\0" + protocol))


async def delete_account_data(management, store, account):
    users = {
        value["user_id"]
        for kind in ("users", "keys")
        for value in await management.list(account, kind)
        if value.get("user_id")
    }
    for user_id in users:
        await delete_user_data(management, store, account, user_id)
    await management.delete_account(account)
