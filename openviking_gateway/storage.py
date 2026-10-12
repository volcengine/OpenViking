# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite adapters and encrypted management data. Writes have a dedicated executor."""

import asyncio
import hashlib
import os
import queue
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any, Protocol

import orjson
from cryptography.fernet import Fernet

from .cache import TTLCache
from .capture_store import CaptureQueue, SQLiteCaptureQueue
from .replay_store import ReplayStore, SQLiteReplayStore
from .state_store import SQLiteStateStore, StateStore

# Pre-release builds stamped versions 1-4 on incompatible layouts; reusing
# one of them would let old files through and fail on the first write.
SCHEMA_VERSION = 5


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
    def __init__(self, path: Path, encryption_key: str):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        # Protect the file before SQLite opens it, not once per operation.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.cipher = Fernet(encryption_key.encode())
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

    def close(self):
        self.readers.shutdown(wait=True)
        self.writer.shutdown(wait=True)
        while True:
            try:
                self.pool.get_nowait().close()
            except queue.Empty:
                return

    def encode(self, value: Any) -> bytes:
        return self.cipher.encrypt(orjson.dumps(value))

    def decode(self, value: bytes) -> Any:
        return orjson.loads(self.cipher.decrypt(value))

    async def run(self, fn, *args, write=False):
        return await asyncio.get_running_loop().run_in_executor(
            self.writer if write else self.readers, partial(fn, *args)
        )


class SQLiteKernelStore(Database):
    def __init__(self, path, encryption_key):
        super().__init__(path, encryption_key)
        self.replay = SQLiteReplayStore(self)
        self.capture = SQLiteCaptureQueue(self)
        self.state = SQLiteStateStore(self)
        self._loads = []

    async def initialize(self):
        def initialize():
            with self.connect() as c:
                if c.execute("PRAGMA user_version").fetchone()[0] not in (0, SCHEMA_VERSION):
                    raise ValueError("Unsupported gateway schema; configure a fresh storage_path")
                c.execute("PRAGMA journal_mode=WAL")
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
                c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

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
    def __init__(self, path, encryption_key):
        super().__init__(path, encryption_key)
        self.cache = TTLCache(ttl=2, capacity=2048)

    async def initialize(self):
        def initialize():
            with self.connect() as c:
                if c.execute("PRAGMA user_version").fetchone()[0] not in (0, SCHEMA_VERSION):
                    raise ValueError("Unsupported gateway schema; configure a fresh storage_path")
                c.execute("PRAGMA journal_mode=WAL")
                c.executescript("""
                    CREATE TABLE IF NOT EXISTS objects (
                        account TEXT, kind TEXT, id TEXT, revision INTEGER, value BLOB,
                        PRIMARY KEY(account,kind,id));
                    CREATE INDEX IF NOT EXISTS object_identity ON objects(kind,id);
                    CREATE TABLE IF NOT EXISTS request_logs (
                        id INTEGER PRIMARY KEY, account TEXT, time REAL, value BLOB);
                    CREATE INDEX IF NOT EXISTS log_account ON request_logs(account,time);
                    CREATE TABLE IF NOT EXISTS object_expiry (
                        account TEXT, kind TEXT, id TEXT, expires REAL,
                        PRIMARY KEY(account,kind,id));
                    CREATE INDEX IF NOT EXISTS expiry_time ON object_expiry(expires);
                """)
                c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

        await self.run(initialize, write=True)

    async def list(self, account, kind):
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
            return await self.cache.get(("list", account, kind), lambda: self.run(read))
        return await self.run(read)

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

    async def log(self, account, value):
        def log():
            with self.connect() as c:
                c.execute(
                    "INSERT INTO request_logs(account,time,value) VALUES (?,?,?)",
                    (account, time.time(), self.encode(value)),
                )

        await self.run(log, write=True)

    async def logs(self, account, limit=200):
        def read():
            with self.connect() as c:
                return [
                    {"time": r["time"], **self.decode(r["value"])}
                    for r in c.execute(
                        "SELECT time,value FROM request_logs WHERE account=? ORDER BY id DESC LIMIT ?",
                        (account, limit),
                    )
                ]

        return await self.run(read)

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
