# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenClaw adapter (group-chat agent).

Logs: ``~/.openclaw/agents/<agent>/sessions/<uuid>.jsonl`` (append-only JSONL). Records
carry a top-level ``type``; conversation turns are ``type=="message"`` with a nested
``message{role, content[], timestamp}``. Assistant messages additionally carry
``model`` + ``provider``. ``content`` is a list of blocks (text / thinking / …).

Group-chat agent: user turns map to the original username when present
(``IngestHarnessConfig.user_field``); otherwise the configured OV user.

Format reference: openclaw-plugin session logs (see project memory on citing openclaw).
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


@register_source("openclaw")
class OpenClawSource(JsonlLogSource):
    is_group_chat = True
    file_glob = "*/sessions/*.jsonl"

    def default_paths(self) -> List[Path]:
        return [Path.home() / ".openclaw" / "agents"]

    def parse_line(self, obj: Dict[str, Any], ref: SessionRef) -> List[NormalizedMessage]:
        if obj.get("type") != "message":
            return []
        message = obj.get("message")
        if not isinstance(message, dict):
            return []
        role = message.get("role")
        if role not in ("user", "assistant"):
            return []
        text = _extract_text(message.get("content"))
        if not text:
            return []

        if role == "assistant":
            peer = self.assistant_peer(message.get("model"), message.get("provider"))
        else:
            raw_user = message.get(self.cfg.user_field) if self.cfg.user_field else None
            peer = self.user_peer(raw_user=raw_user)
        return [
            NormalizedMessage(
                role=role,
                text=text,
                created_at=obj.get("timestamp"),
                peer_id=peer,
                meta={"model": message.get("model"), "provider": message.get("provider")},
            )
        ]
