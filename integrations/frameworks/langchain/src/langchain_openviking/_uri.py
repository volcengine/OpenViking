# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Narrow Viking URI parsing used by the LangGraph store adapter."""

from __future__ import annotations

from dataclasses import dataclass

_CONTENT_SEGMENTS = frozenset({"memories", "resources", "skills"})
_PEER_CONTENT_SEGMENTS = frozenset({"memories", "resources"})
# ``viking://~`` is the home alias for the caller's own user space. The server expands it
# at the request boundary, so responses always come back as ``viking://user/<uid>/...``;
# here we only need it to classify with the same shape as an explicit user URI.
_HOME_ALIAS = "~"


@dataclass(frozen=True)
class UriClassification:
    """Structural URI information needed to validate store record paths."""

    parts: tuple[str, ...]
    scope: str
    content_index: int | None


def _uri_parts(uri: str) -> tuple[str, ...]:
    value = str(uri or "").split("?", 1)[0].strip()
    if not value:
        return ()
    if value == "/":
        value = "viking://"
    elif not value.startswith("viking://"):
        value = f"viking://{value.lstrip('/')}"
    if value.rstrip("/") == "viking:":
        return ()
    return tuple(part for part in value[len("viking://") :].split("/") if part)


def _content_segment_index(parts: tuple[str, ...]) -> int | None:
    if parts and parts[0] == _HOME_ALIAS:
        if len(parts) >= 2 and parts[1] in _CONTENT_SEGMENTS:
            return 1
        if len(parts) >= 4 and parts[1] == "peers" and parts[3] in _PEER_CONTENT_SEGMENTS:
            return 3
        return None
    if len(parts) >= 2 and parts[:2] == ("agent", "skills"):
        return 1
    if len(parts) >= 3 and parts[0] == "agent" and parts[2] in _CONTENT_SEGMENTS:
        return 2
    if len(parts) < 2 or parts[0] != "user":
        return None
    if parts[1] in _CONTENT_SEGMENTS:
        return 1
    if len(parts) >= 4 and parts[1] == "peers" and parts[3] in _PEER_CONTENT_SEGMENTS:
        return 3
    if len(parts) >= 5 and parts[2] == "peers" and parts[4] in _PEER_CONTENT_SEGMENTS:
        return 4
    if len(parts) >= 3 and parts[2] in _CONTENT_SEGMENTS:
        return 2
    return None


def classify_uri(uri: str) -> UriClassification:
    """Classify the user/agent namespace shape of a Viking URI."""

    parts = _uri_parts(uri)
    scope = parts[0] if parts else ""
    if scope == _HOME_ALIAS:
        scope = "user"
    return UriClassification(
        parts=parts,
        scope=scope,
        content_index=_content_segment_index(parts),
    )
