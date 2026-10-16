# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A durable, per-session mailbox. Every CAS affects exactly one document.

Scheduling and leasing belong to the queue adapter. Neither replay keys nor
capture branch rules participate in the queue's transactions.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol


class LeaseLost(Exception):
    """The queue job is now owned by a different worker."""


@dataclass
class Document:
    value: dict = field(default_factory=dict)
    version: int = 0


class CaptureQueue(Protocol):
    async def get(self, scope, session) -> Document: ...
    async def swap(self, scope, session, previous: Document, value, ready, owner=None) -> bool: ...
    async def claim(self, lease_seconds=120) -> dict | None: ...
    async def release(self, item) -> None: ...


class SQLiteCaptureQueue:
    def __init__(self, db):
        self.db = db

    def read_in(self, c, scope, session):
        row = c.execute(
            "SELECT value,version FROM capture WHERE scope=? AND session=?", (scope, session)
        ).fetchone()
        return Document(self.db.decode(row[0]), row[1]) if row else Document()

    async def get(self, scope, session):
        def get():
            with self.db.connect() as c:
                return self.read_in(c, scope, session)

        return await self.db.run(get)

    async def swap(self, scope, session, previous, value, ready, owner=None):
        def swap():
            with self.db.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                if owner is not None:
                    lease = c.execute(
                        "SELECT owner,lease FROM capture WHERE scope=? AND session=?",
                        (scope, session),
                    ).fetchone()
                    if not lease or lease[0] != owner or lease[1] <= time.time():
                        raise LeaseLost
                self.db.touch(c, scope, session)
                if previous.version:
                    changed = c.execute(
                        "UPDATE capture SET value=?,version=version+1,ready=? "
                        "WHERE scope=? AND session=? AND version=?",
                        (self.db.encode(value), ready, scope, session, previous.version),
                    ).rowcount
                else:
                    changed = c.execute(
                        "INSERT OR IGNORE INTO capture(scope,session,value,version,ready) VALUES (?,?,?,1,?)",
                        (scope, session, self.db.encode(value), ready),
                    ).rowcount
                c.commit()
                return bool(changed)

        return await self.db.run(swap, write=True)

    async def claim(self, lease_seconds=120):
        def claim():
            now, owner = time.time(), uuid.uuid4().hex
            with self.db.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                row = c.execute(
                    "SELECT scope,session,value,version FROM capture "
                    "WHERE ready<=? AND lease<? ORDER BY ready LIMIT 1",
                    (now, now),
                ).fetchone()
                if row is None:
                    return None
                c.execute(
                    "UPDATE capture SET lease=?,owner=? WHERE scope=? AND session=?",
                    (now + lease_seconds, owner, row["scope"], row["session"]),
                )
                c.commit()
                return {
                    "scope": row["scope"],
                    "session": row["session"],
                    "owner": owner,
                    "document": Document(self.db.decode(row["value"]), row["version"]),
                }

        return await self.db.run(claim, write=True)

    async def release(self, item):
        def release():
            with self.db.connect() as c:
                c.execute(
                    "UPDATE capture SET lease=0,owner=NULL WHERE scope=? AND session=? AND owner=?",
                    (item["scope"], item["session"], item["owner"]),
                )

        await self.db.run(release, write=True)
