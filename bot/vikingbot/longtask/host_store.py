"""Host ownership, execution budgets and operation journal, not a second Goal DB."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class HostStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS bindings (
                task_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                origin TEXT NOT NULL,
                request_id TEXT NOT NULL,
                objective TEXT NOT NULL,
                metadata TEXT NOT NULL,
                authorized INTEGER NOT NULL DEFAULT 0,
                initialized INTEGER NOT NULL DEFAULT 0,
                cancelled INTEGER NOT NULL DEFAULT 0,
                terminal INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT 'creating',
                rounds INTEGER NOT NULL DEFAULT 0,
                no_progress INTEGER NOT NULL DEFAULT 0,
                next_wake REAL NOT NULL DEFAULT 0,
                inflight TEXT,
                last_decision TEXT,
                last_result TEXT,
                latest_input TEXT,
                UNIQUE(owner, origin, request_id)
            );
            CREATE TABLE IF NOT EXISTS host_events (
                operation_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES bindings(task_id),
                turn_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                intent TEXT NOT NULL,
                result TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES bindings(task_id),
                content TEXT NOT NULL,
                enqueued INTEGER NOT NULL DEFAULT 0
            );
        """)

    def close(self) -> None:
        self.db.close()

    def get(self, task_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM bindings WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown long task")
        return dict(row)

    def create(
        self, owner: str, origin: str, request_id: str, objective: str, metadata: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        with self.db:
            row = self.db.execute(
                "SELECT * FROM bindings WHERE owner=? AND origin=? AND request_id=?",
                (owner, origin, request_id),
            ).fetchone()
            if row:
                if row["objective"] != objective:
                    raise ValueError("The request_id was already used for a different objective")
                return dict(row), False
            task_id = "lt_" + uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO bindings(task_id,owner,origin,request_id,objective,metadata,authorized) "
                "VALUES(?,?,?,?,?,?,1)",
                (task_id, owner, origin, request_id, objective, json.dumps(metadata)),
            )
        return self.get(task_id), True

    def update(self, task_id: str, **fields: Any) -> None:
        allowed = {
            "authorized",
            "initialized",
            "cancelled",
            "terminal",
            "reason",
            "rounds",
            "no_progress",
            "next_wake",
            "inflight",
            "last_decision",
            "last_result",
            "latest_input",
        }
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid host fields")
        with self.db:
            self.db.execute(
                "UPDATE bindings SET "
                + ",".join(f"{key}=?" for key in fields)
                + " WHERE task_id=?",
                (*fields.values(), task_id),
            )

    def assert_authorized(self, task_id: str) -> dict[str, Any]:
        row = self.get(task_id)
        if not row["authorized"] or row["cancelled"] or row["terminal"]:
            raise RuntimeError("Long task execution is not authorized: " + row["reason"])
        return row

    def due(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM bindings WHERE authorized=1 AND cancelled=0 AND terminal=0 "
                "AND next_wake<=? ORDER BY next_wake,task_id",
                (time.time(),),
            )
        ]

    def begin_operation(self, task_id: str, turn_id: str, kind: str, intent: Any) -> str:
        self.assert_authorized(task_id)
        return self._record_operation(task_id, turn_id, kind, intent)

    def begin_control_operation(self, task_id: str, owner: str, origin: str, intent: Any) -> str:
        """Owner-authorized control writes may run while execution is paused."""
        row = self.get(task_id)
        if (row["owner"], row["origin"]) != (owner, origin):
            raise ValueError("Unknown long task in this conversation")
        return self._record_operation(task_id, uuid.uuid4().hex, "control", intent)

    def _record_operation(self, task_id: str, turn_id: str, kind: str, intent: Any) -> str:
        operation_id = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO host_events VALUES(?,?,?,?,?,NULL,?)",
                (
                    operation_id,
                    task_id,
                    turn_id,
                    kind,
                    json.dumps(intent, ensure_ascii=False),
                    time.time(),
                ),
            )
        return operation_id

    def latest_waiting_todo(self, task_id: str) -> str | None:
        row = self.db.execute(
            "SELECT result FROM host_events WHERE task_id=? AND kind='settlement' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        result = json.loads(row[0])
        return result.get("todo_id") if result.get("blocked") else None

    def end_operation(self, operation_id: str, result: Any) -> None:
        with self.db:
            self.db.execute(
                "UPDATE host_events SET result=? WHERE operation_id=?",
                (
                    json.dumps(result, ensure_ascii=False, default=str),
                    operation_id,
                ),
            )

    def unresolved(self, task_id: str) -> list[str]:
        return [
            row[0]
            for row in self.db.execute(
                "SELECT operation_id FROM host_events WHERE task_id=? AND result IS NULL",
                (task_id,),
            )
        ]

    def recover(self) -> None:
        # Never repeat an interrupted tool or settlement with an unknown outcome.
        with self.db:
            self.db.execute(
                "UPDATE bindings SET authorized=0,reason='Interrupted execution; inspect operation "
                "journal and external effects before resuming' WHERE inflight IS NOT NULL"
            )
            self.db.execute(
                "UPDATE bindings SET reason='Creation interrupted; inspect LoopX before retrying' "
                "WHERE initialized=0 AND inflight IS NOT NULL"
            )

    def notify(self, task_id: str, content: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO deliveries(id,task_id,content) VALUES(?,?,?)",
                (uuid.uuid4().hex, task_id, content),
            )

    def pending_deliveries(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM deliveries WHERE enqueued=0")]

    def mark_enqueued(self, delivery_id: str) -> None:
        # This confirms only the Bot bus handoff, not receipt by the remote user.
        with self.db:
            self.db.execute("UPDATE deliveries SET enqueued=1 WHERE id=?", (delivery_id,))
