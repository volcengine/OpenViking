# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Codex (OpenAI Codex CLI) adapter.

Logs: ``~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`` (append-only JSONL).
Records: ``{timestamp, type, payload}``. Conversation turns are
``type=="response_item" & payload.type=="message"`` with ``payload.role`` and
``payload.content[].text`` (``input_text`` / ``output_text``). ``role=="developer"``/
``"system"`` are dropped. Session id / cwd / provider come from the first
``session_meta`` record (the model name is not reliably per-message).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from openviking.ingest.models import NormalizedMessage, SessionRef
from openviking.ingest.registry import register_source
from openviking.ingest.sources.base import JsonlLogSource


def _join_content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    chunks: List[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in ("input_text", "output_text"):
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks).strip()


@register_source("codex")
class CodexSource(JsonlLogSource):
    file_glob = "*/*/*/rollout-*.jsonl"

    def default_paths(self) -> List[Path]:
        return [Path.home() / ".codex" / "sessions"]

    def session_ref_for_file(self, path: Path) -> SessionRef:
        meta = self._peek_session_meta(path)
        return SessionRef(
            harness=self.name,
            native_session_id=meta.get("id") or path.stem,
            locator=str(path),
            started_at=meta.get("timestamp"),
            meta={"model": meta.get("model_provider"), "cwd": meta.get("cwd")},
        )

    @staticmethod
    def _peek_session_meta(path: Path) -> Dict[str, Any]:
        try:
            with open(path, "rb") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    obj = json.loads(raw)
                    if obj.get("type") == "session_meta":
                        return obj.get("payload", {}) or {}
        except (OSError, ValueError):
            pass
        return {}

    def parse_line(self, obj: Dict[str, Any], ref: SessionRef) -> List[NormalizedMessage]:
        if obj.get("type") != "response_item":
            return []
        payload = obj.get("payload") or {}
        if payload.get("type") != "message":
            return []
        role = payload.get("role")
        if role not in ("user", "assistant"):
            return []  # drop developer/system boilerplate
        text = _join_content(payload.get("content"))
        if not text:
            return []

        model = ref.meta.get("model")
        peer = (
            self.assistant_peer(model)
            if role == "assistant"
            else self.user_peer(cwd=ref.meta.get("cwd"))
        )
        return [
            NormalizedMessage(
                role=role,
                text=text,
                created_at=obj.get("timestamp"),
                peer_id=peer,
                meta={"model": model, "cwd": ref.meta.get("cwd")},
            )
        ]
