# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Hermes adapter (group-chat agent).

Logs: ``~/.hermes/sessions/<ts>_<id>.jsonl`` (append-only JSONL). Records are keyed by
``role``: a leading ``session_meta`` (carries ``model`` + ``platform``), then ``user`` /
``assistant`` turns with ``content`` (str) + ``timestamp``.

Group-chat agent: user turns map to the original username when present in the log
(``IngestHarnessConfig.user_field``); otherwise the configured OV user.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from openviking.ingest.models import NormalizedMessage, SessionRef
from openviking.ingest.registry import register_source
from openviking.ingest.sources.base import JsonlLogSource


@register_source("hermes")
class HermesSource(JsonlLogSource):
    is_group_chat = True
    file_glob = "*.jsonl"

    def default_paths(self) -> List[Path]:
        return [Path.home() / ".hermes" / "sessions"]

    def session_ref_for_file(self, path: Path) -> SessionRef:
        first = self._peek_first_json(path) or {}
        model = first.get("model") if first.get("role") == "session_meta" else None
        return SessionRef(
            harness=self.name,
            native_session_id=path.stem,
            locator=str(path),
            started_at=first.get("timestamp"),
            meta={"model": model, "platform": first.get("platform")},
        )

    def parse_line(self, obj: Dict[str, Any], ref: SessionRef) -> List[NormalizedMessage]:
        role = obj.get("role")
        if role not in ("user", "assistant"):
            return []  # drop session_meta and any other control record
        text = (obj.get("content") or "").strip()
        if not text:
            return []

        model = ref.meta.get("model")
        if role == "assistant":
            peer = self.assistant_peer(model)
        else:
            raw_user = obj.get(self.cfg.user_field) if self.cfg.user_field else None
            peer = self.user_peer(raw_user=raw_user)
        return [
            NormalizedMessage(
                role=role,
                text=text,
                created_at=obj.get("timestamp"),
                peer_id=peer,
                meta={"model": model, "platform": ref.meta.get("platform")},
            )
        ]
