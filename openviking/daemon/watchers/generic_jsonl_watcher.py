# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Generic JSONL log watcher.
Supports arbitrary JSONL logs with customizable field mapping.
"""
import json
from typing import Dict, List, Optional

from openviking.daemon.watchers.base_file_watcher import BaseFileWatcher
from openviking.daemon.watchers.registry import register_watcher
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@register_watcher("generic_jsonl")
class GenericJSONLWatcher(BaseFileWatcher):
    """
    Generic JSONL watcher with customizable field mapping.

    Extra config options (passed via constructor **kwargs or extra dict):
    - role_field (str): JSON key for role. Default: "role"
    - user_role_value (str): Value that indicates user. Default: "user"
    - assistant_role_value (str): Value that indicates assistant. Default: "assistant"
    - content_field (str): JSON key for content. Default: "content"
    - timestamp_field (str): JSON key for timestamp. Default: "timestamp"
    - session_id_field (str): JSON key for session_id. Default: "session_id"
    - project_name_field (str): JSON key for project_name. Default: "project_name"
    - type_field (str): JSON key for event type. Default: "type"
    - message_type_value (str): Value that indicates a message event. Default: "message"
    """

    # Default field mappings
    DEFAULTS = {
        "role_field": "role",
        "user_role_value": "user",
        "assistant_role_value": "assistant",
        "content_field": "content",
        "timestamp_field": "timestamp",
        "session_id_field": "session_id",
        "project_name_field": "project_name",
        "type_field": "type",
        "message_type_value": "message",
    }

    def __init__(self, watch_dir, cursor_manager, batch_callback,
                 file_pattern="*.jsonl",
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
        self.mapping = {**self.DEFAULTS, **self.extra}

    @property
    def tool_name(self) -> str:
        return "generic_jsonl"

    def parse_line(self, line: str) -> Optional[Dict]:
        if not line:
            return None
        try:
            return json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None

    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        m = self.mapping

        role_field = m["role_field"]
        raw_role = raw_event.get(role_field, "")

        # Map raw role to normalized role
        if raw_role == m["user_role_value"]:
            role = "user"
        elif raw_role == m["assistant_role_value"]:
            role = "assistant"
        else:
            return None

        content = raw_event.get(m["content_field"], "")
        if not content:
            return None

        # Optional type check
        type_field = m.get("type_field")
        message_type = m.get("message_type_value")
        if type_field and type_field in raw_event:
            if raw_event[type_field] != message_type:
                return None

        return {
            "role": role,
            "content": content,
            "type": "message",
            "timestamp": raw_event.get(m["timestamp_field"]),
            "session_id": raw_event.get(m["session_id_field"]),
            "project_name": raw_event.get(m["project_name_field"]),
        }
