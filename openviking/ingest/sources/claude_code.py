# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Claude Code adapter.

Logs: ``~/.claude/projects/<project-slug>/<session-uuid>.jsonl`` (append-only JSONL).
Each record has a top-level ``type``; conversation turns are ``type in {user, assistant}``
with a nested ``message{role, content, model}``. ``content`` is a string or a list of
blocks (text / tool_use / tool_result). cwd + gitBranch are per record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from openviking.ingest.models import NormalizedMessage, SessionRef
from openviking.ingest.registry import register_source
from openviking.ingest.sources.base import JsonlLogSource


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
        return "\n".join(chunks).strip()
    return ""


@register_source("claude_code")
class ClaudeCodeSource(JsonlLogSource):
    file_glob = "*/*.jsonl"

    def default_paths(self) -> List[Path]:
        return [Path.home() / ".claude" / "projects"]

    def parse_line(self, obj: Dict[str, Any], ref: SessionRef) -> List[NormalizedMessage]:
        if obj.get("type") not in ("user", "assistant"):
            return []
        if obj.get("isSidechain") or obj.get("isMeta"):
            return []  # sub-agent / synthetic records are low-value for memory
        message = obj.get("message")
        if not isinstance(message, dict):
            return []
        role = message.get("role")
        if role not in ("user", "assistant"):
            return []
        text = _extract_text(message.get("content"))
        if not text:
            return []  # tool-only turn -> dropped

        model = message.get("model")
        cwd = obj.get("cwd")
        peer = self.assistant_peer(model) if role == "assistant" else self.user_peer(cwd=cwd)
        return [
            NormalizedMessage(
                role=role,
                text=text,
                created_at=obj.get("timestamp"),
                peer_id=peer,
                meta={"model": model, "cwd": cwd, "git_branch": obj.get("gitBranch")},
            )
        ]
