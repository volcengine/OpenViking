# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Small operational documents, separate from immutable replay decisions.

Response observations, relayed thinking digests and tool receipts use the same
single-key CAS primitive. No transaction spans a document and the queue. A
document not written within the retention period expires on its own.
"""

import time
from typing import Protocol

import orjson

from .capture_store import Document


class StateStore(Protocol):
    async def read(self, scope, keys) -> dict[str, Document]: ...
    async def swap(self, scope, key, previous: Document, value) -> bool: ...


class SQLiteStateStore:
    def __init__(self, db):
        self.db = db

    def read_in(self, c, scope, keys):
        return {
            r["key"]: Document(self.db.decode(r["value"]), r["version"])
            for r in c.execute(
                "SELECT key,value,version FROM state WHERE scope=? "
                "AND key IN (SELECT value FROM json_each(?))",
                (scope, orjson.dumps(keys).decode()),
            )
        }

    async def read(self, scope, keys):
        def read():
            with self.db.connect() as c:
                return self.read_in(c, scope, keys)

        return await self.db.run(read)

    async def swap(self, scope, key, previous, value):
        def swap():
            with self.db.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                self.db.check(c, scope)
                if previous.version:
                    changed = c.execute(
                        "UPDATE state SET value=?,version=version+1,touched=? "
                        "WHERE scope=? AND key=? AND version=?",
                        (self.db.encode(value), time.time(), scope, key, previous.version),
                    ).rowcount
                else:
                    changed = c.execute(
                        "INSERT OR IGNORE INTO state(scope,key,value,version,touched) "
                        "VALUES (?,?,?,1,?)",
                        (scope, key, self.db.encode(value), time.time()),
                    ).rowcount
                c.commit()
                return bool(changed)

        return await self.db.run(swap, write=True)


async def get_state(store, scope, key):
    return (await store.read(scope, [key])).get(key, Document())
