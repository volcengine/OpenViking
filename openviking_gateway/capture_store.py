# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""One versioned document per session. Every CAS affects exactly one document.

A document whose ``idle_at`` is set is listed by ``waiting`` once that time is reached,
until a swap clears the field. ``forget`` drops a listing unless the document changed
since the caller read it at ``version``; it is for listings with no due time behind them.
Neither replay keys nor capture branch rules participate in its transactions.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Document:
    value: dict = field(default_factory=dict)
    version: int = 0


class CaptureStore(Protocol):
    async def get(self, scope, session) -> Document: ...
    async def swap(self, scope, session, previous: Document, value) -> bool: ...
    async def waiting(self, before) -> list[tuple[str, str]]: ...
    async def forget(self, scope, session, version) -> None: ...


class SQLiteCaptureStore:
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

    async def swap(self, scope, session, previous, value):
        def swap():
            with self.db.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                self.db.touch(c, scope, session)
                due = value.get("idle_at")
                if previous.version:
                    changed = c.execute(
                        "UPDATE capture SET value=?,version=version+1,ready=? "
                        "WHERE scope=? AND session=? AND version=?",
                        (self.db.encode(value), due, scope, session, previous.version),
                    ).rowcount
                else:
                    changed = c.execute(
                        "INSERT OR IGNORE INTO capture(scope,session,value,version,ready) "
                        "VALUES (?,?,?,1,?)",
                        (scope, session, self.db.encode(value), due),
                    ).rowcount
                c.commit()
                return bool(changed)

        return await self.db.run(swap, write=True)

    async def waiting(self, before):
        def waiting():
            with self.db.connect() as c:
                rows = c.execute("SELECT scope,session FROM capture WHERE ready<=?", (before,))
                return [tuple(row) for row in rows]

        return await self.db.run(waiting)

    async def forget(self, scope, session, version):
        def forget():
            with self.db.connect() as c:
                c.execute(
                    "UPDATE capture SET ready=NULL WHERE scope=? AND session=? AND version=?",
                    (scope, session, version),
                )

        await self.db.run(forget, write=True)
