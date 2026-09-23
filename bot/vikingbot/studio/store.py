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

    def delete_connection(self, record):
        with self.db:
            self.db.execute("DELETE FROM messages WHERE connection_id=?", (record["id"],))
            for run in self.onboarding_runs(record["account"]):
                if run.get("connection_id") == record["id"]:
                    self.db.execute("DELETE FROM onboarding WHERE id=?", (run["id"],))
            self.db.execute(
                "DELETE FROM connections WHERE id=? AND account=?",
                (record["id"], record["account"]),
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

    def update_sender(self, connection, message_id, name):
        with self.db:
            self.db.execute(
                "UPDATE messages SET value=json_set(value, '$.sender', ?) "
                "WHERE connection_id=? AND id=?",
                (name, connection, message_id),
            )

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
            last = self.db.execute(
                "SELECT value FROM messages WHERE id=?", (row["latest"],)
            ).fetchone()
            message = json.loads(last[0])
            user = self.db.execute(
                "SELECT value FROM messages WHERE connection_id=? AND conversation=? "
                "AND json_extract(value, '$.role')='user' "
                "AND TRIM(COALESCE(json_extract(value, '$.content'), '')) != '' "
                "ORDER BY id LIMIT 1",
                (connection, row["conversation"]),
            ).fetchone()
            first_message = json.loads(user[0] if user else first[0])
            title = " ".join(
                str(first_message.get("topic_title") or first_message.get("content", "")).split()
            )[:60]
            named = self.db.execute(
                "SELECT value FROM messages WHERE connection_id=? AND conversation=? "
                "AND json_extract(value, '$.chat_type')='group' "
                "AND TRIM(COALESCE(json_extract(value, '$.title'), '')) != '' "
                "ORDER BY id DESC LIMIT 1",
                (connection, row["conversation"]),
            ).fetchone()
            group_name = ""
            if named:
                source = json.loads(named[0])
                group_name = source.get("group_name") or source["title"]
                if not source.get("group_name") and "#" in row["conversation"]:
                    group_name = group_name.removesuffix(" / " + source.get("content", "")[:40])
            result.append(
                dict(row)
                | {
                    "title": title,
                    "group_name": group_name,
                    "preview": str(message.get("content", ""))[:160],
                    "time": message.get("time", ""),
                }
            )
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
