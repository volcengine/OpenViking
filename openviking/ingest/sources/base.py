# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Log-source abstraction: one ABC + two intermediates so a new harness is a thin subclass.

- ``JsonlLogSource``  — append-only JSONL (Claude Code, Codex, Hermes, OpenClaw); byte-offset cursor.
- ``SqliteLogSource`` — relational SQLite (OpenCode); (time, id) cursor, polled read-only.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Iterable, List, Optional, Tuple

from openviking.ingest.models import (
    BYTE_OFFSET,
    ROWID_TIME,
    Cursor,
    NormalizedMessage,
    SessionRef,
)
from openviking.ingest.peer import (
    assistant_peer_id,
    resolve_git_human_peer,
    safe_external_peer,
)
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking_cli.utils.config.ingest_config import IngestHarnessConfig

logger = get_logger(__name__)

# Max messages returned per read_messages() call. Bounds memory and lets the orchestrator
# append+confirm in idempotent batches (<= the server's 100-message batch cap).
DEFAULT_READ_LIMIT = 100
_READ_BLOCK = 262144  # 256 KiB


class NotSupportedError(RuntimeError):
    """Raised by an adapter that is registered but not usable in the current config."""


class LogSource(ABC):
    """Base adapter for one agent harness's conversation logs."""

    name: ClassVar[str] = ""  # set by @register_source
    cursor_kind: ClassVar[str] = BYTE_OFFSET
    is_group_chat: ClassVar[bool] = False  # group agents map user turns to original usernames

    def __init__(self, harness_cfg: "IngestHarnessConfig", *, fallback_user: str = "default"):
        self.cfg = harness_cfg
        self.fallback_user = fallback_user

    # --- paths -------------------------------------------------------------
    @abstractmethod
    def default_paths(self) -> List[Path]:
        """Default discovery roots (files/dirs/db paths) when config gives no override."""

    def roots(self) -> List[Path]:
        if self.cfg.paths:
            return [Path(p).expanduser() for p in self.cfg.paths]
        return self.default_paths()

    # --- discovery / reading ----------------------------------------------
    @abstractmethod
    def discover_sessions(self) -> Iterable[SessionRef]:
        """Enumerate conversations available in this harness's storage."""

    @abstractmethod
    def read_messages(
        self, ref: SessionRef, cursor: Optional[Cursor], limit: int = DEFAULT_READ_LIMIT
    ) -> Tuple[List[NormalizedMessage], Cursor]:
        """Read up to ``limit`` messages after ``cursor``; return (messages, advanced cursor)."""

    # --- peer_id helpers (used by adapters) -------------------------------
    def assistant_peer(self, model: Optional[str], provider: Optional[str] = None) -> Optional[str]:
        return assistant_peer_id(self.name, model, provider)

    def user_peer(
        self, *, cwd: Optional[str] = None, raw_user: Optional[str] = None
    ) -> Optional[str]:
        if self.is_group_chat:
            return safe_external_peer(raw_user) or safe_external_peer(self.fallback_user)
        return resolve_git_human_peer(cwd, self.fallback_user)


# =============================================================================
# Append-only JSONL
# =============================================================================
class JsonlLogSource(LogSource):
    cursor_kind = BYTE_OFFSET
    file_glob: ClassVar[str] = "*.jsonl"

    def discover_sessions(self) -> Iterable[SessionRef]:
        for root in self.roots():
            root = root.expanduser()
            if not root.exists():
                continue
            for path in sorted(root.glob(self.file_glob)):
                if path.is_file():
                    yield self.session_ref_for_file(path)

    def session_ref_for_file(self, path: Path) -> SessionRef:
        return SessionRef(
            harness=self.name,
            native_session_id=self.session_id_for_file(path),
            locator=str(path),
        )

    def session_id_for_file(self, path: Path) -> str:
        return path.stem

    @staticmethod
    def _peek_first_json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "rb") as f:
                for raw in f:
                    raw = raw.strip()
                    if raw:
                        return json.loads(raw)
        except (OSError, ValueError):
            return None
        return None

    def read_messages(
        self, ref: SessionRef, cursor: Optional[Cursor], limit: int = DEFAULT_READ_LIMIT
    ) -> Tuple[List[NormalizedMessage], Cursor]:
        path = Path(ref.locator)
        cur = cursor or Cursor.zero(self.cursor_kind)
        try:
            st = path.stat()
        except OSError:
            return [], cur

        start = int(cur.value.get("offset", 0))
        stored_inode = cur.value.get("inode")
        # Rotation (file replaced) or truncation -> re-read from the top.
        if (stored_inode is not None and stored_inode != st.st_ino) or start > st.st_size:
            start = 0

        messages: List[NormalizedMessage] = []
        consumed = 0  # bytes of fully-processed (newline-terminated) lines
        buf = b""
        with open(path, "rb") as f:
            f.seek(start)
            while len(messages) < limit:
                chunk = f.read(_READ_BLOCK)
                if not chunk:
                    break  # EOF; any partial trailing line stays in buf, unconsumed
                buf += chunk
                stop = False
                while True:
                    nl = buf.find(b"\n")
                    if nl == -1:
                        break
                    line = buf[: nl + 1]
                    buf = buf[nl + 1 :]
                    consumed += len(line)
                    stripped = line.strip()
                    if stripped:
                        try:
                            obj = json.loads(stripped)
                        except ValueError:
                            obj = None
                        if obj is not None:
                            try:
                                messages.extend(self.parse_line(obj, ref))
                            except Exception as exc:  # one bad record must not abort the file
                                logger.debug(
                                    "[ingest:%s] skip bad record in %s: %s",
                                    self.name,
                                    path.name,
                                    exc,
                                )
                    if len(messages) >= limit:
                        stop = True
                        break
                if stop:
                    break

        new_offset = start + consumed
        return messages, Cursor(self.cursor_kind, {"offset": new_offset, "inode": st.st_ino})

    @abstractmethod
    def parse_line(self, obj: Dict[str, Any], ref: SessionRef) -> List[NormalizedMessage]:
        """Map one JSONL record to zero or more normalized messages."""


# =============================================================================
# Relational SQLite
# =============================================================================
class SqliteLogSource(LogSource):
    cursor_kind = ROWID_TIME

    @abstractmethod
    def db_path(self) -> Path:
        """Path to the harness's SQLite database (config override honored upstream)."""

    def _connect(self, *, immutable: bool = False) -> sqlite3.Connection:
        path = self.db_path()
        if not path.exists():
            raise NotSupportedError(f"{self.name}: database not found at {path}")
        # mode=ro honors the WAL for live polling; immutable=1 (static snapshot) ignores it.
        suffix = "mode=ro&immutable=1" if immutable else "mode=ro"
        conn = sqlite3.connect(f"file:{path}?{suffix}", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def read_messages(
        self, ref: SessionRef, cursor: Optional[Cursor], limit: int = DEFAULT_READ_LIMIT
    ) -> Tuple[List[NormalizedMessage], Cursor]:
        cur = cursor or Cursor.zero(self.cursor_kind)
        conn = self._connect(immutable=False)
        try:
            rows = list(self.fetch_rows(conn, ref, cur, limit))
            # Advance only past rows that are fully written. A still-incomplete trailing
            # row (e.g. a message whose `part` text has not been flushed yet) is left for
            # the next poll so we never skip its content.
            complete: List[sqlite3.Row] = []
            for row in rows:
                if self.row_complete(conn, row):
                    complete.append(row)
                else:
                    break
            messages = self.rows_to_messages(conn, ref, complete)
            if complete:
                last = complete[-1]
                new_cur = Cursor(self.cursor_kind, {"time": last["time_created"], "id": last["id"]})
            else:
                new_cur = cur
            return messages, new_cur
        finally:
            conn.close()

    def row_complete(self, conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
        """Whether a fetched row is fully written and safe to advance past (default: yes)."""
        return True

    @abstractmethod
    def fetch_rows(
        self, conn: sqlite3.Connection, ref: SessionRef, cursor: Cursor, limit: int
    ) -> List[sqlite3.Row]:
        """Up to ``limit`` rows after the cursor, ordered by (time_created, id)."""

    @abstractmethod
    def rows_to_messages(
        self, conn: sqlite3.Connection, ref: SessionRef, rows: List[sqlite3.Row]
    ) -> List[NormalizedMessage]:
        """Map rows to normalized messages (may issue extra queries via ``conn``)."""
