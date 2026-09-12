# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Per-request VLM session identity for provider routing headers.

OpenCode Go (and similar OpenAI-compatible gateways) require a stable
``x-opencode-session`` header per conversation for routing / prompt caching.
OpenViking often issues several VLM calls inside one asyncio task (memory
extract, summarize, commit). This module keeps one sticky id for that task
and allows callers to bind an explicit OpenViking session id when known.
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

OPENCODE_SESSION_HEADER = "x-opencode-session"

_VLM_SESSION_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "openviking_vlm_session_id",
    default=None,
)


def get_bound_vlm_session_id() -> Optional[str]:
    """Return the currently bound VLM session id, if any."""
    value = _VLM_SESSION_ID.get()
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def get_or_create_vlm_session_id() -> str:
    """Return a sticky session id for the current context, minting one if needed."""
    current = get_bound_vlm_session_id()
    if current is not None:
        return current
    minted = f"ov-{uuid.uuid4().hex}"
    _VLM_SESSION_ID.set(minted)
    return minted


@contextmanager
def bind_vlm_session_id(session_id: Optional[str]) -> Iterator[Optional[str]]:
    """Bind an explicit session id for downstream OpenAI-compatible requests."""
    if session_id is None:
        yield None
        return
    normalized = str(session_id).strip()
    if not normalized:
        yield None
        return
    token = _VLM_SESSION_ID.set(normalized)
    try:
        yield normalized
    finally:
        _VLM_SESSION_ID.reset(token)


def header_name_ci_match(headers: dict, name: str) -> bool:
    """Return True if ``headers`` already contains ``name`` (case-insensitive)."""
    target = name.lower()
    return any(str(key).lower() == target for key in headers)


__all__ = [
    "OPENCODE_SESSION_HEADER",
    "bind_vlm_session_id",
    "get_bound_vlm_session_id",
    "get_or_create_vlm_session_id",
    "header_name_ci_match",
]
