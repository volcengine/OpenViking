"""
Continue.dev log watcher.
Monitors ~/.continue/ JSON log files for AI conversation events.
"""
import json
from typing import Dict, List, Optional

from openviking.daemon.watchers.base_file_watcher import BaseFileWatcher
from openviking.daemon.watchers.registry import register_watcher
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@register_watcher("continue_dev")
class ContinueDevWatcher(BaseFileWatcher):
    """Watches Continue.dev JSON log files."""

    def __init__(self, watch_dir, cursor_manager, batch_callback,
                 file_pattern="*.json",
                 batch_trigger_lines=50, batch_trigger_seconds=300, **kwargs):
        super().__init__(
            watch_dir=watch_dir,
            cursor_manager=cursor_manager,
            batch_callback=batch_callback,
            file_pattern=file_pattern,
            batch_trigger_lines=batch_trigger_lines,
            batch_trigger_seconds=batch_trigger_seconds,
        )

    @property
    def tool_name(self) -> str:
        return "continue_dev"

    def parse_line(self, line: str) -> Optional[Dict]:
        """Parse a Continue.dev JSON log line."""
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
        Normalize Continue.dev event.
        Continue.dev uses format:
        {"role": "user"|"assistant", "content": "...", "timestamp": "..."}
        """
        role = raw_event.get("role", "")
        content = raw_event.get("content", "")

        if role not in ("user", "assistant"):
            return None
        if not content:
            return None

        return {
            "role": role,
            "content": content,
            "type": "message",
            "timestamp": raw_event.get("timestamp"),
            "session_id": raw_event.get("sessionId") or raw_event.get("session_id"),
            "project_name": raw_event.get("workspaceDirectory"),
        }
