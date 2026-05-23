# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lightweight version helpers for optimistic memory updates."""

import hashlib

MISSING_CONTENT_DIGEST = "missing"


def content_digest(content: str | bytes | None) -> str:
    """Return a stable digest for memory file content read from storage."""
    if content is None:
        return MISSING_CONTENT_DIGEST
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()
