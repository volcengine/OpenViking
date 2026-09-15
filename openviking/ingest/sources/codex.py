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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from openviking.ingest.models import NormalizedMessage, SessionRef
from openviking.ingest.registry import register_source
from openviking.ingest.sources.base import JsonlLogSource

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _session_id_for_file(meta_id: Optional[str], path: Path) -> str:
    """Session id for a single rollout file.

    Codex keeps ``session_meta.id`` unchanged when a session is forked/continued, but
    writes the continuation to a *new* file whose name carries an extra UUID
    (``rollout-<ts>-<base-uuid>_<fork-uuid>.jsonl``). Using the shared id as the cursor
    key makes two files fight over one byte offset, and because the cursor also stores
    the inode, every poll then looks like log rotation and re-reads the file from the
    top. Append the extra UUID so each file owns its own cursor.
    """
    base = meta_id or path.stem
    uuids = _UUID_RE.findall(path.stem)
    extras = [u for u in uuids if u != base]
    if len(uuids) >= 2 and extras:
        return f"{base}_{extras[-1]}"
    return base


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
            native_session_id=_session_id_for_file(meta.get("id"), path),
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
