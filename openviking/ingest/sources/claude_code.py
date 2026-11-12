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
from typing import Any, Dict, Iterable, List

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

    def discover_sessions(self) -> Iterable[SessionRef]:
        """Select one authoritative transcript when project paths split a session.

        Claude Code can leave the same session UUID under both real-path and
        symlink-derived project directories. Yielding both files makes them share one
        byte-offset cursor; their different inodes then look like alternating rotations
        and both files are replayed on every poll. Claude transcripts are append-only, so
        the most recently modified file is authoritative. Size and path make ties stable.
        """
        selected: Dict[str, tuple[tuple[int, int, str], SessionRef]] = {}
        for ref in super().discover_sessions():
            path = Path(ref.locator)
            try:
                stat = path.stat()
                freshness = (stat.st_mtime_ns, stat.st_size, ref.locator)
            except OSError:
                freshness = (-1, -1, ref.locator)
            current = selected.get(ref.native_session_id)
            if current is None or freshness > current[0]:
                selected[ref.native_session_id] = (freshness, ref)

        for session_id in sorted(selected):
            yield selected[session_id][1]

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
