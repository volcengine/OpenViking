# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Immutable replay storage: indexed batch reads and put-if-absent only."""

from typing import Protocol

import orjson

from .records import RecordKind as K

# Records that rewrite a history prefix. A request inherits them from the sessions
# whose replies its history contains: model output does not collide by chance,
# while client-authored text such as an opening prompt does.
INHERITED = {K.INJECTION, K.HIDDEN, K.REPLACEMENT, K.REASONING}


class ReplayStore(Protocol):
    async def read(self, scope, session, anchors, ancestors=()) -> dict: ...
    async def replies(self, scope, anchors) -> dict[str, set[str]]: ...
    async def put(self, scope, session, kind: K, anchor, value) -> dict: ...


class SQLiteReplayStore:
    def __init__(self, db):
        self.db = db

    def read_in(self, c, scope, session, anchors, ancestors=()):
        # Ancestors in a fixed order, then the session itself, whose records win.
        return {
            (row["kind"], row["anchor"]): self.db.decode(row["value"])
            for row in c.execute(
                "SELECT session,kind,anchor,value FROM replay WHERE scope=? "
                "AND session IN (SELECT value FROM json_each(?)) "
                "AND anchor IN (SELECT value FROM json_each(?)) AND kind<>? "
                "ORDER BY session=?, session",
                (
                    scope,
                    orjson.dumps([session, *ancestors]).decode(),
                    orjson.dumps(anchors).decode(),
                    K.REPLY,
                    session,
                ),
            )
            if row["session"] == session or row["kind"] in INHERITED
        }

    async def read(self, scope, session, anchors, ancestors=()):
        def read():
            with self.db.connect() as c:
                return self.read_in(c, scope, session, anchors, ancestors)

        return await self.db.run(read)

    async def replies(self, scope, anchors):
        def replies():
            owners = {}
            with self.db.connect() as c:
                for row in c.execute(
                    "SELECT anchor,session FROM replay WHERE scope=? AND kind=? "
                    "AND anchor IN (SELECT value FROM json_each(?))",
                    (scope, K.REPLY, orjson.dumps(anchors).decode()),
                ):
                    owners.setdefault(row["anchor"], set()).add(row["session"])
            return owners

        return await self.db.run(replies)

    async def put(self, scope, session, kind, anchor, value):
        kind = K(kind)

        def put():
            with self.db.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                self.db.touch(c, scope, session)
                c.execute(
                    "INSERT OR IGNORE INTO replay VALUES (?,?,?,?,?)",
                    (scope, session, kind, anchor, self.db.encode(value)),
                )
                row = c.execute(
                    "SELECT value FROM replay WHERE scope=? AND session=? AND kind=? AND anchor=?",
                    (scope, session, kind, anchor),
                ).fetchone()
                c.commit()
                return self.db.decode(row[0])

        return await self.db.run(put, write=True)
