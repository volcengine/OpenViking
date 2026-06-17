"""
Claude Code JSONL log watcher.
Monitors ~/.claude/projects/<project>/<session>.jsonl files.
"""
import json
from typing import Dict, List, Optional

from openviking.daemon.watchers.base_file_watcher import BaseFileWatcher
from openviking.daemon.watchers.registry import register_watcher
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@register_watcher("claude_code")
class ClaudeCodeWatcher(BaseFileWatcher):
    """Watches Claude Code JSONL log files and extracts conversation events."""

    @property
    def tool_name(self) -> str:
        return "claude_code"

    def __init__(self, watch_dir, cursor_manager, batch_callback,
                 batch_trigger_lines=50, batch_trigger_seconds=300, **kwargs):
        super().__init__(
            watch_dir=watch_dir,
            cursor_manager=cursor_manager,
            batch_callback=batch_callback,
            file_pattern="*.jsonl",
            batch_trigger_lines=batch_trigger_lines,
            batch_trigger_seconds=batch_trigger_seconds,
        )

    def parse_line(self, line: str) -> Optional[Dict]:
        """Parse a JSONL line into a raw event dict."""
        if not line:
            return None
        try:
            return json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None

    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        """Normalize Claude Code event to common format."""
        role = raw_event.get("role")
        event_type = raw_event.get("type", "")

        if role not in ("user", "assistant"):
            return None
        if event_type and event_type != "message":
            return None

        return {
            "role": role,
            "content": raw_event.get("content", ""),
            "type": "message",
            "timestamp": raw_event.get("timestamp"),
            "session_id": raw_event.get("session_id"),
            "project_name": raw_event.get("project_name"),
        }
