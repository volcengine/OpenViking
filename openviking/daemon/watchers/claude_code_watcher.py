"""
Claude Code JSONL log watcher.
Monitors ~/.claude/projects/<project>/<session>.jsonl files.

Real log format (verified against 288 sessions, 29541 lines):
- Top-level "type": "user" | "assistant" | "attachment" | "queue-operation" | "system" | ...
- "role" and "content" are nested inside "message" object
- "message.content" can be a plain string OR an array of content blocks
  (e.g. [{"type": "text", "text": "..."}, {"type": "tool_use", ...}])
- Session ID is at top-level "sessionId" (camelCase, no underscore)
- Project name is NOT in the JSON — derived from file path
"""
import json
import os
from typing import Dict, List, Optional

from openviking.daemon.watchers.base_file_watcher import BaseFileWatcher
from openviking.daemon.watchers.registry import register_watcher
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def _extract_text_from_content(content) -> str:
    """Extract plain text from Claude Code message content.

    content can be:
    - str: a plain text message
    - list: array of content blocks (text, tool_use, thinking, tool_result)
    Returns concatenated text from all "text" blocks, or "" if none.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    text_parts.append(block["text"])
        return "\n".join(text_parts)
    return ""


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
        """Normalize Claude Code event to common format.

        Only extracts events where top-level type is "user" or "assistant"
        and message.role is "user" or "assistant".
        Skips tool_use-only assistant messages (no text content).
        """
        event_type = raw_event.get("type", "")

        # Only process conversation messages
        if event_type not in ("user", "assistant"):
            return None

        msg = raw_event.get("message")
        if not isinstance(msg, dict):
            return None

        role = msg.get("role")
        if role not in ("user", "assistant"):
            return None

        # Extract text content — skip if empty (e.g. tool_use-only messages)
        raw_content = msg.get("content", "")
        content = _extract_text_from_content(raw_content)
        if not content:
            return None

        # Skip tool_result messages (type="user" but content is tool_result array)
        if isinstance(raw_content, list):
            has_tool_result = any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in raw_content
            )
            if has_tool_result:
                return None

        return {
            "role": role,
            "content": content,
            "type": "message",
            "timestamp": raw_event.get("timestamp"),
            "session_id": raw_event.get("sessionId"),
            "project_name": None,  # injected by _post_normalize
        }

    def _post_normalize(self, event: Dict, file_path: str) -> Dict:
        """Derive project_name from file path: ~/.claude/projects/<project>/<session>.jsonl"""
        if not event.get("project_name"):
            parts = file_path.replace("\\", "/").split("/")
            try:
                projects_idx = parts.index("projects")
                if projects_idx + 1 < len(parts) - 1:
                    event["project_name"] = parts[projects_idx + 1]
            except ValueError:
                pass
        return event
