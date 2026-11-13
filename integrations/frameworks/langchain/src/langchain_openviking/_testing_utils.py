# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Private deterministic helpers for the in-memory integration test client."""

from __future__ import annotations

import math
import re

_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_.@-]+$")


def normalize_peer_id(peer_id: str | None) -> str | None:
    """Normalize a peer identifier using OpenViking's public wire constraints."""

    if peer_id is None:
        return None
    if not isinstance(peer_id, str):
        raise ValueError("Invalid peer_id: peer_id must be a string.")
    normalized = peer_id.strip()
    if not normalized:
        return None
    if normalized in {".", ".."}:
        raise ValueError("Invalid peer_id: peer_id must not be '.' or '..'")
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid peer_id: peer_id must be alpha_numeric string.")
    if normalized.count("@") > 1:
        raise ValueError("Invalid peer_id: peer_id must have at most one @.")
    return normalized


def _is_cjk_code_point(code_point: int) -> bool:
    return (
        0x3400 <= code_point <= 0x4DBF
        or 0x4E00 <= code_point <= 0x9FFF
        or 0xF900 <= code_point <= 0xFAFF
        or 0x20000 <= code_point <= 0x2EBEF
        or 0x3040 <= code_point <= 0x30FF
        or 0x31F0 <= code_point <= 0x31FF
        or 0xAC00 <= code_point <= 0xD7AF
        or 0x1100 <= code_point <= 0x11FF
        or 0x3130 <= code_point <= 0x318F
        or 0xFF00 <= code_point <= 0xFFEF
        or 0x3000 <= code_point <= 0x303F
    )


def estimate_text_tokens(text: str | None) -> int:
    """Estimate tokens conservatively without depending on server internals."""

    if not text:
        return 0
    weight = 0.0
    for char in text:
        code_point = ord(char)
        if _is_cjk_code_point(code_point):
            weight += 1.5
        elif code_point > 0xFFFF:
            weight += 2.0
        else:
            weight += 0.25
    return math.ceil(weight)
