"""Small durable index for managed connections and platform delivery history."""

import json
import os
import sqlite3
from pathlib import Path


class StudioStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS onboarding (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS connections (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, connection_id TEXT NOT NULL,
                conversation TEXT NOT NULL, event_id TEXT NOT NULL, value TEXT NOT NULL,
                UNIQUE(connection_id, event_id));
        """)

    def connections(self, account: str | None = None):
        rows = self.db.execute(
            "SELECT value FROM connections" + (" WHERE account=?" if account else ""),
            (account,) if account else (),
        )
        return [json.loads(row[0]) for row in rows]

    def save(self, record: dict):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO connections VALUES (?, ?, ?)",
                (record["id"], record["account"], json.dumps(record)),
            )

    def append(self, connection: str, conversation: str, event_id: str, value: dict):
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO messages(connection_id, conversation, event_id, value) "
                "VALUES (?, ?, ?, ?)",
                (connection, conversation, event_id, json.dumps(value)),
            )

        return cursor.rowcount == 1

    def history(self, connection: str, conversation: str = "", before: int = 0):
        query = "SELECT id, conversation, value FROM messages WHERE connection_id=?"
        args: list = [connection]
        if conversation:
            query += " AND conversation=?"
            args.append(conversation)
        if before:
            query += " AND id<?"
            args.append(before)
        rows = self.db.execute(query + " ORDER BY id DESC LIMIT 101", args).fetchall()
        return [
            dict(json.loads(row["value"]), id=row["id"], conversation=row["conversation"])
            for row in rows
        ]

    def conversations(self, connection: str):
        rows = self.db.execute(
            "SELECT conversation, MAX(id) AS latest FROM messages WHERE connection_id=? "
            "GROUP BY conversation ORDER BY latest DESC LIMIT 200",
            (connection,),
        ).fetchall()
        result = []
        for row in rows:
            first = self.db.execute(
                "SELECT value FROM messages WHERE connection_id=? AND conversation=? "
                "ORDER BY id LIMIT 1",
                (connection, row["conversation"]),
            ).fetchone()
            result.append(dict(row) | {"title": json.loads(first[0]).get("title", "")})
        return result

    def onboarding_runs(self, account=None):
        rows = self.db.execute(
            "SELECT value FROM onboarding"
            + (" WHERE account=?" if account else "")
            + " ORDER BY rowid",
            (account,) if account else (),
        )
        return [json.loads(row[0]) for row in rows]

    def save_onboarding(self, run):
        with self.db:
            self.db.execute(
                "INSERT INTO onboarding VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",
                (run["id"], run["account"], json.dumps(run)),
            )
