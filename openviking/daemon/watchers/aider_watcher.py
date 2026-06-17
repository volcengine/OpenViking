"""
Aider chat history watcher.
Monitors .aider.chat.history.md files in project directories.
"""
import re
import time
import os
from typing import Dict, List, Optional

from openviking.daemon.watchers.base_file_watcher import BaseFileWatcher
from openviking.daemon.watchers.registry import register_watcher
from openviking.daemon.models import FileCursor
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@register_watcher("aider")
class AiderWatcher(BaseFileWatcher):
    """Watches Aider .aider.chat.history.md files."""

    # Regex patterns
    HEADER_RE = re.compile(r'^# aider chat started at (.+)$')
    PROJECT_RE = re.compile(r'^> (.+)$')
    USER_RE = re.compile(r'^#### user:\s*$')
    ASSISTANT_RE = re.compile(r'^#### assistant:\s*$')

    def __init__(self, watch_dir, cursor_manager, batch_callback,
                 file_pattern=".aider.chat.history.md",
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
        return "aider"

    def matches_file_pattern(self, file_path: str) -> bool:
        """Match .aider.chat.history.md files."""
        filename = os.path.basename(file_path)
        return filename == ".aider.chat.history.md"

    def parse_line(self, line: str) -> Optional[Dict]:
        """Not used - Aider uses multi-line parsing via _process_file override."""
        return None

    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        """Not used directly - _process_file creates normalized events."""
        return raw_event

    def _process_file(self, file_path: str):
        """
        Override: Parse Aider's multi-line markdown format.
        Extracts user/assistant conversation blocks from .aider.chat.history.md.
        """
        try:
            cursor = self.cursor_manager.get_cursor(file_path)
            file_size = os.path.getsize(file_path)

            if file_size <= cursor.last_position:
                return

            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(cursor.last_position)
                new_content = f.read()

            new_position = cursor.last_position + len(new_content.encode("utf-8"))

            # Parse conversation blocks
            events = self._parse_aider_content(new_content)

            for event in events:
                event["tool_name"] = self.tool_name
                byte_size = len(event.get("content", "").encode("utf-8"))
                self._buffer.add_line(event, byte_size)

            self.cursor_manager.update_cursor(file_path, new_position)
            self._check_batch_trigger()

        except Exception as e:
            logger.error("[%s] Error processing %s: %s", self.tool_name, file_path, e)

    def _parse_aider_content(self, content: str) -> List[Dict]:
        """Parse Aider markdown content into normalized events."""
        events = []
        lines = content.splitlines()

        current_role = None
        current_content = []
        current_timestamp = None
        project_name = None

        for line in lines:
            # Check for timestamp header
            header_match = self.HEADER_RE.match(line)
            if header_match:
                current_timestamp = header_match.group(1).strip()
                continue

            # Check for project path
            project_match = self.PROJECT_RE.match(line)
            if project_match:
                project_name = project_match.group(1).strip()
                continue

            # Check for role markers
            if self.USER_RE.match(line):
                # Flush previous block
                if current_role and current_content:
                    events.append(self._make_event(current_role, current_content, current_timestamp, project_name))
                current_role = "user"
                current_content = []
                continue

            if self.ASSISTANT_RE.match(line):
                if current_role and current_content:
                    events.append(self._make_event(current_role, current_content, current_timestamp, project_name))
                current_role = "assistant"
                current_content = []
                continue

            # Accumulate content for current role
            if current_role:
                current_content.append(line)

        # Flush last block
        if current_role and current_content:
            events.append(self._make_event(current_role, current_content, current_timestamp, project_name))

        return events

    def _make_event(self, role: str, content_lines: List[str],
                    timestamp: Optional[str], project_name: Optional[str]) -> Dict:
        """Create a normalized event dict from parsed content."""
        content = "\n".join(content_lines).strip()
        return {
            "role": role,
            "content": content,
            "type": "message",
            "timestamp": timestamp,
            "project_name": project_name,
        }
