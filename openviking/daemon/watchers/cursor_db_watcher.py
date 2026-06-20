"""
Cursor IDE SQLite database watcher.
Monitors Cursor's dual-SQLite storage for AI conversations:
- Workspace DB: workspaceStorage/<hash>/state.vscdb -> ItemTable (session metadata)
- Global DB: globalStorage/state.vscdb -> cursorDiskKV (bubble message content)

Key format: bubbleId:<composerId>:<bubbleId>
Value JSON: {_v, type(1=user/2=assistant), text, createdAt, allThinkingBlocks, ...}
"""
import json
import os
import sqlite3
from typing import Dict, List, Optional

from openviking.daemon.watchers.base_polling_watcher import BasePollingWatcher
from openviking.daemon.watchers.registry import register_watcher
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@register_watcher("cursor_db")
class CursorDBWatcher(BasePollingWatcher):
    """Watches Cursor IDE's dual-SQLite storage for AI conversations.

    Architecture:
    - Workspace DB: workspaceStorage/<hash>/state.vscdb -> ItemTable -> composer.composerData
    - Global DB: globalStorage/state.vscdb -> cursorDiskKV -> bubbleId:<composerId>:<bubbleId>

    watch_dir should point to the Cursor User root:
    - Windows: %APPDATA%\\Cursor\\User
    - macOS: ~/Library/Application Support/Cursor/User
    - Linux: ~/.config/Cursor/User
    """

    def __init__(self, watch_dir, cursor_manager, batch_callback,
                 poll_interval=30, batch_trigger_lines=50, batch_trigger_seconds=300,
                 extra=None, **kwargs):
        super().__init__(
            tool_name="cursor_db",
            watch_dir=watch_dir,
            cursor_manager=cursor_manager,
            batch_callback=batch_callback,
            poll_interval=poll_interval,
            batch_trigger_lines=batch_trigger_lines,
            batch_trigger_seconds=batch_trigger_seconds,
            extra=extra,
        )
        self._global_db_path = os.path.join(
            self.watch_dir, "globalStorage", "state.vscdb"
        )
        self._workspace_storage_dir = os.path.join(
            self.watch_dir, "workspaceStorage"
        )

    @property
    def tool_name(self) -> str:
        return "cursor_db"

    def resolve_db_path(self) -> Optional[str]:
        """Return global DB path (primary data source)."""
        if os.path.exists(self._global_db_path):
            return self._global_db_path
        return None

    def _discover_composer_ids(self) -> List[str]:
        """Scan all workspace DBs to collect composerId list.
        Useful for correlating bubble data in global DB.
        """
        composer_ids = []
        if not os.path.isdir(self._workspace_storage_dir):
            return composer_ids

        for ws_hash in os.listdir(self._workspace_storage_dir):
            ws_db = os.path.join(
                self._workspace_storage_dir, ws_hash, "state.vscdb"
            )
            if not os.path.exists(ws_db):
                continue
            try:
                conn = sqlite3.connect(f"file:{ws_db}?mode=ro", uri=True)
                try:
                    row = conn.execute(
                        "SELECT value FROM ItemTable "
                        "WHERE [key] = 'composer.composerData'"
                    ).fetchone()
                    if row and row[0]:
                        data = json.loads(row[0])
                        for c in data.get("allComposers", []):
                            cid = c.get("id")
                            if cid:
                                composer_ids.append(cid)
                finally:
                    conn.close()
            except Exception:
                continue
        return composer_ids

    def query_new_events(self, last_cursor: int) -> List[Dict]:
        """Query global DB cursorDiskKV for new bubble data.

        Strategy: scan all bubbleId:* keys (rowid > last_cursor).
        Does NOT depend on workspace DB composerId list (supports orphan conversations).
        """
        db_path = self.resolve_db_path()
        if not db_path:
            return []

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout = 3000")
            try:
                rows = conn.execute(
                    "SELECT rowid, [key], value FROM cursorDiskKV "
                    "WHERE rowid > ? AND [key] LIKE 'bubbleId:%' "
                    "ORDER BY rowid ASC LIMIT 500",
                    (last_cursor,),
                ).fetchall()

                events = []
                for rowid, key, value in rows:
                    # Parse key: bubbleId:<composerId>:<bubbleId>
                    parts = key.split(":", 2)
                    composer_id = parts[1] if len(parts) >= 3 else None

                    try:
                        parsed_value = (
                            json.loads(value) if isinstance(value, str) else value
                        )
                    except (json.JSONDecodeError, TypeError):
                        continue

                    events.append({
                        "rowid": rowid,
                        "key": key,
                        "value": parsed_value,
                        "composer_id": composer_id,
                        "_cursor_position": rowid,
                    })
                return events
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            logger.warning("[cursor_db] SQLite error (DB may be locked): %s", e)
            return []

    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        """Parse Cursor bubble format.

        Value JSON fields:
        - _v: schema version (currently 3)
        - type: 1=user, 2=assistant
        - text: message content
        - createdAt: timestamp
        - allThinkingBlocks: AI reasoning (assistant only)
        """
        value = raw_event.get("value")
        if not isinstance(value, dict):
            return None

        # Schema version check - warn but don't crash
        schema_version = value.get("_v", 0)
        if schema_version > 3:
            logger.debug(
                "[cursor_db] Unknown bubble schema v%d", schema_version
            )

        # type: 1=user, 2=assistant
        bubble_type = value.get("type")
        if bubble_type == 1:
            role = "user"
        elif bubble_type == 2:
            role = "assistant"
        else:
            return None

        # text: message content
        content = value.get("text", "")
        if not content or not content.strip():
            return None  # Filter empty streaming artifacts

        return {
            "role": role,
            "content": content,
            "type": "message",
            "timestamp": value.get("createdAt"),
            "session_id": raw_event.get("composer_id"),
        }

    def filter_event(self, event: Dict) -> bool:
        """Filter short content."""
        content = event.get("content", "")
        return len(content.strip()) >= 10
