# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Convert ``NormalizedMessage`` -> OV ``AddMessageRequest`` payload dict.

Mirrors vikingbot's ``_normalize_session_messages`` shape (text/tool parts, peer_id
safe-naming). Conversation memory is driven by user/assistant TEXT; tool I/O is treated
as low-value and dropped by the adapters, so most messages carry a single text part.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openviking.core.peer_id import safe_peer_id
from openviking.ingest.models import NormalizedMessage


def to_add_message_request(msg: NormalizedMessage) -> Optional[Dict[str, Any]]:
    """Return an ``AddMessageRequest`` dict, or ``None`` for an empty turn."""
    role = "user" if msg.role == "user" else "assistant"

    parts: List[Dict[str, Any]] = []
    text = (msg.text or "").strip()
    if text:
        parts.append({"type": "text", "text": text})
    if msg.parts:
        parts.extend(msg.parts)

    if not parts:
        return None  # nothing worth replaying

    payload: Dict[str, Any] = {"role": role, "parts": parts}
    if text:
        payload["content"] = text
    if msg.created_at:
        payload["created_at"] = msg.created_at

    pid = safe_peer_id(msg.peer_id)
    if pid:
        payload["peer_id"] = pid

    return payload


def to_add_message_requests(messages: List[NormalizedMessage]) -> List[Dict[str, Any]]:
    """Batch convert, dropping empty turns."""
    out: List[Dict[str, Any]] = []
    for m in messages:
        payload = to_add_message_request(m)
        if payload is not None:
            out.append(payload)
    return out
