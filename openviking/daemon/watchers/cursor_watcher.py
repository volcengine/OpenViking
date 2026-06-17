"""
Cursor IDE log watcher.
Monitors Cursor log files (JSON format) for AI conversation events.
"""
import json
from typing import Dict, List, Optional

from openviking.daemon.watchers.base_file_watcher import BaseFileWatcher
from openviking.daemon.watchers.registry import register_watcher
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@register_watcher("cursor")
class CursorWatcher(BaseFileWatcher):
    """
    Watches Cursor IDE log files.

    Cursor stores AI conversations in log files under its workspace storage.
    The format varies by version, so we implement flexible parsing.
    """

    def __init__(self, watch_dir, cursor_manager, batch_callback,
                 file_pattern="*.log",
                 batch_trigger_lines=50, batch_trigger_seconds=300,
                 extra=None, **kwargs):
        super().__init__(
            watch_dir=watch_dir,
            cursor_manager=cursor_manager,
            batch_callback=batch_callback,
            file_pattern=file_pattern,
            batch_trigger_lines=batch_trigger_lines,
            batch_trigger_seconds=batch_trigger_seconds,
        )
        self.extra = extra or {}

    @property
    def tool_name(self) -> str:
        return "cursor"

    def parse_line(self, line: str) -> Optional[Dict]:
        """Parse a Cursor log line (JSON format)."""
        if not line:
            return None
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                return None
            return data
        except (json.JSONDecodeError, ValueError):
            return None

    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        """
        Normalize Cursor log event.
        Cursor logs use various schemas. We look for common patterns:
        - {"type": "chat", "role": "user"|"assistant", "message": "..."}
        - {"event": "ai_response", "content": "..."}
        """
        # Try standard chat format
        event_type = raw_event.get("type", "")
        role = raw_event.get("role", "")

        if role in ("user", "human", "human_turn"):
            content = raw_event.get("message") or raw_event.get("content") or raw_event.get("text") or ""
            if content:
                return {
                    "role": "user",
                    "content": content,
                    "type": "message",
                    "timestamp": raw_event.get("timestamp") or raw_event.get("ts"),
                    "session_id": raw_event.get("conversationId") or raw_event.get("session_id"),
                }

        if role in ("assistant", "ai", "ai_response", "bot"):
            content = raw_event.get("message") or raw_event.get("content") or raw_event.get("text") or ""
            if content:
                return {
                    "role": "assistant",
                    "content": content,
                    "type": "message",
                    "timestamp": raw_event.get("timestamp") or raw_event.get("ts"),
                    "session_id": raw_event.get("conversationId") or raw_event.get("session_id"),
                }

        return None
