# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite FTS5 keyword sidecar.

One database file per account under ``<db_dir>/<account_id>.sqlite3``. The FTS5
table stores the pre-tokenized content plus an UNINDEXED canonical Viking URI.
``uri_map`` keeps a stable ``uri -> rowid`` mapping so single-document upserts,
deletes and moves stay cheap and idempotent.

The sidecar is a *recall accelerator*: ``lookup`` returns candidate URIs ranked
by SQLite's search-time ``bm25()``; callers are responsible for the final
matching decision (grep matches against on-disk content, hybrid retrieval fuses
with the dense scores).
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from openviking_cli.utils.logger import get_logger

from .config import KeywordConfig
from .tokenizer import tokenize

logger = get_logger(__name__)

_SCHEMA_VERSION = "1"
_TOKENIZER_VERSION = "1"

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_URI_MAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS uri_map (
    uri            TEXT PRIMARY KEY,
    kf_rowid       INTEGER NOT NULL,
    level          INTEGER NOT NULL,
    context_type   TEXT NOT NULL,
    owner_user_id  TEXT NOT NULL,
    byte_len       INTEGER NOT NULL,
    updated_at     TEXT NOT NULL
)
"""

_FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS kf USING fts5("
    "uri UNINDEXED, "
    "content, "
    "tokenize='unicode61'"
    ")"
)

_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KeywordFS:
    """Per-account SQLite FTS5 keyword index."""

    def __init__(self, db_dir: str | Path, config: Optional[KeywordConfig] = None):
        self._db_dir = Path(db_dir)
        self._db_dir.mkdir(parents=True, exist_ok=True)
        self._config = config or KeywordConfig()
        self._conns: Dict[str, sqlite3.Connection] = {}
        self._locks: Dict[str, threading.RLock] = {}
        self._closed = False

    # ------------------------------------------------------------------
    # connection / path management
    # ------------------------------------------------------------------

    def db_dir(self) -> Path:
        return self._db_dir

    def _safe_account_id(self, account_id: str) -> str:
        if account_id and _ACCOUNT_ID_RE.match(account_id):
            return account_id
        digest = hashlib.sha256((account_id or "default").encode("utf-8")).hexdigest()[:16]
        return f"acct_{digest}"

    def db_path(self, account_id: str) -> Path:
        return self._db_dir / f"{self._safe_account_id(account_id)}.sqlite3"

    def _lock(self, account_id: str) -> threading.RLock:
        lock = self._locks.get(account_id)
        if lock is None:
            lock = self._locks.setdefault(account_id, threading.RLock())
        return lock

    def _conn(self, account_id: str) -> sqlite3.Connection:
        """Return the per-account connection, creating/initializing it on first use."""
        conn = self._conns.get(account_id)
        if conn is None:
            path = self.db_path(account_id)
            conn = sqlite3.connect(str(path), check_same_thread=False, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema(conn)
            self._conns[account_id] = conn
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_META_SCHEMA)
        conn.execute(_URI_MAP_SCHEMA)
        conn.execute(_FTS_SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (_SCHEMA_VERSION,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('tokenizer_version', ?)",
            (_TOKENIZER_VERSION,),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # mutations
    # ------------------------------------------------------------------

    def upsert(
        self,
        account_id: str,
        uri: str,
        text: str,
        level: int = 2,
        context_type: str = "resource",
        owner_user_id: str = "",
    ) -> bool:
        """Index (or replace) one document. Returns True when a row was written.

        Oversized documents and documents that tokenize to nothing are dropped
        from the index (treated as delete).
        """
        if not uri or self._closed:
            return False
        raw_bytes = len((text or "").encode("utf-8"))
        if raw_bytes > self._config.max_doc_bytes:
            self.delete(account_id, uri)
            return False
        tokenized = tokenize(text or "", self._config.tokenizer, self._config.cjk_mode)
        if not tokenized:
            self.delete(account_id, uri)
            return False

        lock = self._lock(account_id)
        with lock:
            conn = self._conn(account_id)
            row = conn.execute("SELECT kf_rowid FROM uri_map WHERE uri=?", (uri,)).fetchone()
            now = _utc_now()
            if row is not None:
                rowid = int(row["kf_rowid"])
                self._delete_row(conn, rowid, uri)
                conn.execute(
                    "INSERT INTO kf(rowid, uri, content) VALUES(?, ?, ?)",
                    (rowid, uri, tokenized),
                )
                conn.execute(
                    "UPDATE uri_map SET level=?, context_type=?, owner_user_id=?, "
                    "byte_len=?, updated_at=? WHERE uri=?",
                    (int(level), context_type, owner_user_id, raw_bytes, now, uri),
                )
            else:
                cur = conn.execute("INSERT INTO kf(uri, content) VALUES(?, ?)", (uri, tokenized))
                rowid = int(cur.lastrowid)
                conn.execute(
                    "INSERT INTO uri_map(uri, kf_rowid, level, context_type, owner_user_id, "
                    "byte_len, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (uri, rowid, int(level), context_type, owner_user_id, raw_bytes, now),
                )
            conn.commit()
            return True

    def delete(self, account_id: str, uri: str) -> bool:
        """Remove one document (idempotent). Returns True when a row was removed."""
        if not uri or self._closed:
            return False
        lock = self._lock(account_id)
        with lock:
            conn = self._conn(account_id)
            row = conn.execute("SELECT kf_rowid FROM uri_map WHERE uri=?", (uri,)).fetchone()
            if row is None:
                return False
            self._delete_row(conn, int(row["kf_rowid"]), uri)
            conn.execute("DELETE FROM uri_map WHERE uri=?", (uri,))
            conn.commit()
            return True

    def move(self, account_id: str, old_uri: str, new_uri: str) -> bool:
        """Rewrite ``old_uri`` to ``new_uri`` (idempotent)."""
        if not old_uri or not new_uri or old_uri == new_uri or self._closed:
            return False
        lock = self._lock(account_id)
        with lock:
            conn = self._conn(account_id)
            row = conn.execute(
                "SELECT kf_rowid FROM uri_map WHERE uri=?", (old_uri,)
            ).fetchone()
            if row is None:
                return False
            rowid = int(row["kf_rowid"])
            conn.execute("UPDATE kf SET uri=? WHERE rowid=?", (new_uri, rowid))
            conn.execute("UPDATE uri_map SET uri=? WHERE uri=?", (new_uri, old_uri))
            conn.commit()
            return True

    def delete_prefix(self, account_id: str, scope_uri: str) -> int:
        """Delete all documents whose URI starts with ``scope_uri``. Returns row count."""
        if not scope_uri or self._closed:
            return 0
        lock = self._lock(account_id)
        with lock:
            conn = self._conn(account_id)
            rows = conn.execute(
                "SELECT kf_rowid, uri FROM uri_map WHERE uri LIKE ?", (scope_uri + "%",)
            ).fetchall()
            for r in rows:
                self._delete_row(conn, int(r["kf_rowid"]), r["uri"])
            if rows:
                conn.execute("DELETE FROM uri_map WHERE uri LIKE ?", (scope_uri + "%",))
                conn.commit()
            return len(rows)

    @staticmethod
    def _delete_row(conn: sqlite3.Connection, rowid: int, uri: str) -> None:
        """Delete a single FTS5 row by rowid."""
        del uri  # rowid is sufficient for a regular (non-external) FTS5 table
        conn.execute("DELETE FROM kf WHERE rowid=?", (rowid,))

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------

    def lookup(
        self,
        account_id: str,
        query: str,
        scope_uri: str = "",
        exclude_uri: str = "",
        limit: int = 10,
    ) -> List[Tuple[str, float]]:
        """Search-time BM25 recall: return ``[(uri, score), ...]`` ranked best-first.

        ``scope_uri`` and ``exclude_uri`` are canonical Viking URI prefixes that
        are applied as post-filters over the FTS candidates.
        """
        if self._closed or not query:
            return []
        tokenized = tokenize(query, self._config.tokenizer, self._config.cjk_mode)
        terms = [t for t in tokenized.split() if t]
        if not terms:
            return []
        match_expr = "(" + " OR ".join(f'content:"{_escape_fts_term(t)}"' for t in terms) + ")"

        limit = max(int(limit), 0)
        max_candidates = self._config.max_candidates
        lock = self._lock(account_id)
        with lock:
            conn = self._conn(account_id)
            results: List[Tuple[str, float]] = []
            internal = max(limit * 5, min(max_candidates, 2000))
            while True:
                rows = conn.execute(
                    "SELECT uri, bm25(kf) AS score FROM kf WHERE kf MATCH ? "
                    "ORDER BY bm25(kf) ASC LIMIT ?",
                    (match_expr, internal),
                ).fetchall()
                fetched = 0
                for r in rows:
                    fetched += 1
                    uri = r["uri"]
                    if not uri:
                        continue
                    if scope_uri and not uri.startswith(scope_uri):
                        continue
                    if exclude_uri and (
                        uri == exclude_uri or uri.startswith(exclude_uri + "/")
                    ):
                        continue
                    results.append((uri, float(r["score"])))
                    if len(results) >= limit:
                        break
                if len(results) >= limit or fetched < internal or internal >= max_candidates:
                    break
                internal = min(internal * 4, max_candidates)
            return results

    # ------------------------------------------------------------------
    # rebuild / maintenance
    # ------------------------------------------------------------------

    def rebuild_account(
        self,
        account_id: str,
        items: Iterable[Dict],
    ) -> int:
        """Atomically rebuild an account's sidecar from ``items``.

        Each item: ``{"uri", "text", "level", "context_type", "owner_user_id"}``.
        Returns the number of documents indexed.
        """
        lock = self._lock(account_id)
        with lock:
            # Close the live connection first so its WAL checkpoint is flushed and
            # no stale -wal/-shm sidecar files survive the atomic replacement.
            conn_old = self._conns.pop(account_id, None)
            if conn_old is not None:
                try:
                    conn_old.close()
                except Exception:  # pragma: no cover - defensive
                    pass
            self._remove_wal_sidecars(account_id)

            tmp_path = self.db_path(account_id).with_name(
                f"{self._safe_account_id(account_id)}.tmp.{uuid.uuid4().hex[:8]}.sqlite3"
            )
            count = 0
            try:
                conn = sqlite3.connect(str(tmp_path), timeout=15.0)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=OFF")
                conn.execute("PRAGMA synchronous=OFF")
                conn.execute(_META_SCHEMA)
                conn.execute(_URI_MAP_SCHEMA)
                conn.execute(_FTS_SCHEMA)
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                    (_SCHEMA_VERSION,),
                )
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('tokenizer_version', ?)",
                    (_TOKENIZER_VERSION,),
                )
                now = _utc_now()
                for item in items:
                    uri = item.get("uri", "")
                    text = item.get("text", "") or ""
                    if not uri:
                        continue
                    raw_bytes = len(text.encode("utf-8"))
                    if raw_bytes > self._config.max_doc_bytes:
                        continue
                    tokenized = tokenize(text, self._config.tokenizer, self._config.cjk_mode)
                    if not tokenized:
                        continue
                    cur = conn.execute(
                        "INSERT INTO kf(uri, content) VALUES(?, ?)", (uri, tokenized)
                    )
                    conn.execute(
                        "INSERT INTO uri_map(uri, kf_rowid, level, context_type, owner_user_id, "
                        "byte_len, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                        (
                            uri,
                            int(cur.lastrowid),
                            int(item.get("level", 2)),
                            item.get("context_type", "resource"),
                            item.get("owner_user_id", ""),
                            raw_bytes,
                            now,
                        ),
                    )
                    count += 1
                conn.commit()
                conn.close()
                os.replace(tmp_path, self.db_path(account_id))
                self._remove_wal_sidecars(account_id)
                return count
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:  # pragma: no cover - defensive
                        pass

    def clear(self, account_id: str) -> None:
        """Drop the account's sidecar file entirely."""
        lock = self._lock(account_id)
        with lock:
            conn = self._conns.pop(account_id, None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # pragma: no cover - defensive
                    pass
            try:
                self.db_path(account_id).unlink(missing_ok=True)
            except OSError:  # pragma: no cover - defensive
                pass
            self._remove_wal_sidecars(account_id)

    def _remove_wal_sidecars(self, account_id: str) -> None:
        """Remove orphaned -wal/-shm sidecar files for an account DB."""
        base = str(self.db_path(account_id))
        for suffix in ("-wal", "-shm"):
            try:
                Path(base + suffix).unlink(missing_ok=True)
            except OSError:  # pragma: no cover - defensive
                pass

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------

    def is_ready(self, account_id: str) -> bool:
        """True when the account sidecar file exists with a valid schema version."""
        path = self.db_path(account_id)
        if not path.exists():
            return False
        lock = self._lock(account_id)
        with lock:
            try:
                conn = self._conn(account_id)
                row = conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()
                return row is not None and row["value"] == _SCHEMA_VERSION
            except sqlite3.Error:
                return False

    def stats(self, account_id: str) -> Dict:
        """Return sidecar statistics for one account."""
        if not self.is_ready(account_id):
            return {
                "ready": False,
                "docs": 0,
                "tokenizer_version": _TOKENIZER_VERSION,
                "last_built_at": None,
            }
        lock = self._lock(account_id)
        with lock:
            try:
                conn = self._conn(account_id)
                docs = conn.execute("SELECT COUNT(*) AS c FROM uri_map").fetchone()["c"]
                built = conn.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
                return {
                    "ready": True,
                    "docs": int(docs),
                    "tokenizer_version": _TOKENIZER_VERSION,
                    "last_built_at": built["value"] if built else None,
                }
            except sqlite3.Error:
                return {
                    "ready": False,
                    "docs": 0,
                    "tokenizer_version": _TOKENIZER_VERSION,
                    "last_built_at": None,
                }

    def close(self) -> None:
        self._closed = True
        with threading.Lock():
            conns = list(self._conns.values())
            self._conns.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:  # pragma: no cover - defensive
                pass


def _escape_fts_term(term: str) -> str:
    """Escape a single token for use inside an FTS5 double-quoted phrase."""
    return term.replace('"', "").replace("\\", "")
