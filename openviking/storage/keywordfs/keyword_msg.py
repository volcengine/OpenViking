# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Keyword index mutation message, consumed by the KeywordQueue worker."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict
from uuid import uuid4

Upsert = "upsert"
Delete = "delete"
DeletePrefix = "delete_prefix"
Move = "move"


@dataclass
class KeywordMsg:
    """A single keyword-sidecar mutation.

    ``kind`` is one of:
      - ``upsert``: index ``uri`` with ``text`` (level/context_type/owner metadata).
      - ``delete``: remove ``uri`` from the index.
      - ``delete_prefix``: remove ``uri`` and every row whose URI starts with it.
      - ``move``: rewrite ``old_uri`` to ``new_uri`` in the index.

    The account is carried explicitly so the worker can route to the right DB
    file; the URI is always the canonical Viking URI (same value that the
    vector index stores for the record).
    """

    kind: str
    uri: str = ""
    account_id: str = "default"
    text: str = ""
    level: int = 2
    context_type: str = "resource"
    owner_user_id: str = ""
    old_uri: str = ""
    new_uri: str = ""
    telemetry_id: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KeywordMsg":
        return cls(
            kind=data["kind"],
            uri=data["uri"],
            account_id=data.get("account_id", "default"),
            text=data.get("text", ""),
            level=data.get("level", 2),
            context_type=data.get("context_type", "resource"),
            owner_user_id=data.get("owner_user_id", ""),
            old_uri=data.get("old_uri", ""),
            new_uri=data.get("new_uri", ""),
            telemetry_id=data.get("telemetry_id", ""),
            id=data.get("id", str(uuid4())),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "KeywordMsg":
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def from_embedding(cls, embedding_msg: Any) -> "KeywordMsg | None":
        """Build an upsert message from an EmbeddingMsg for coverage parity.

        The keyword sidecar indexes exactly the text that was embedded for the
        leaf document, so keyword recall stays in sync with vector coverage.
        """
        context_data = getattr(embedding_msg, "context_data", {}) or {}
        uri = context_data.get("uri", "")
        if not uri:
            return None
        text = _message_text(getattr(embedding_msg, "message", ""))
        if not text:
            return None
        return cls(
            kind=Upsert,
            uri=uri,
            account_id=context_data.get("account_id", "default"),
            text=text,
            level=int(context_data.get("level", 2) or 2),
            context_type=context_data.get("context_type", "resource"),
            owner_user_id=context_data.get("owner_user_id", ""),
            telemetry_id=getattr(embedding_msg, "telemetry_id", ""),
        )


def _message_text(message: Any) -> str:
    """Extract plain text from an EmbeddingMsg message (str or multimodal parts)."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = []
        for part in message:
            if isinstance(part, dict) and part.get("type") == "text":
                t = part.get("text", "")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""
