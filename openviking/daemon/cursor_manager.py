"""
Cursor Manager for tracking file read positions.
Persists state in SQLite so Daemon can resume after restart.
"""
import sqlite3
import time
from pathlib import Path
from typing import Dict

from openviking.daemon.models import FileCursor
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class CursorManager:
    """Manages file cursor state with SQLite persistence."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database and schema."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_cursors (
                    file_path TEXT PRIMARY KEY,
                    last_position INTEGER NOT NULL DEFAULT 0,
                    last_read_time REAL NOT NULL DEFAULT 0.0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def get_cursor(self, file_path: str) -> FileCursor:
        """Get the cursor state for a file. Returns zero-position cursor if not found."""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT last_position, last_read_time FROM file_cursors WHERE file_path = ?",
                (file_path,),
            ).fetchone()

            if row:
                return FileCursor(
                    file_path=file_path,
                    last_position=row[0],
                    last_read_time=row[1],
                )
            return FileCursor(file_path=file_path)
        finally:
            conn.close()

    def update_cursor(self, file_path: str, position: int):
        """Update the cursor position for a file."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO file_cursors (file_path, last_position, last_read_time)
                VALUES (?, ?, ?)
                """,
                (file_path, position, time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_all_cursors(self) -> Dict[str, FileCursor]:
        """Get all tracked cursor states."""
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT file_path, last_position, last_read_time FROM file_cursors"
            ).fetchall()

            return {
                row[0]: FileCursor(
                    file_path=row[0],
                    last_position=row[1],
                    last_read_time=row[2],
                )
                for row in rows
            }
        finally:
            conn.close()
