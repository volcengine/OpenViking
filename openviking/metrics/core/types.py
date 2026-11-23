# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Shared metric utility types.

This module enforces label safety rules that protect Prometheus from cardinality explosions:
- Only allow label keys that match Prometheus label naming rules.
- Explicitly forbid label keys that are known to be high-cardinality in OpenViking
  (e.g., session_id, resource_uri, query, prompt, url).

All labels are normalized into a sorted tuple of (key, value) pairs to provide:
- A stable internal series identifier
- Deterministic output ordering in Prometheus exposition
"""

from __future__ import annotations

import hashlib
import re
import struct
from typing import Mapping

_LABEL_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

FORBIDDEN_LABEL_KEYS: set[str] = {
    "user_id",
    "session_id",
    "resource_uri",
    "error_message",
    "query",
    "prompt",
    "path",
    "url",
}


def histogram_schema(bounds: tuple[float, ...]) -> str:
    """Identify the actual bucket layout so rolling upgrades cannot mix layouts."""
    return hashlib.sha256(struct.pack(f"!{len(bounds)}d", *bounds)).hexdigest()[:16]


def normalize_labels(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    """
    Normalize a label mapping into the canonical internal representation.

    Args:
        labels: Optional label dictionary provided by a collector write path.

    Returns:
        A tuple of `(key, value)` pairs sorted by key, suitable for use as a stable internal
        series identifier.

    Raises:
        ValueError: If a label key is invalid under Prometheus naming rules or belongs to the
        forbidden high-cardinality set.
    """
    if not labels:
        return ()
    items: list[tuple[str, str]] = []
    for k, v in labels.items():
        key = str(k)
        if not _LABEL_KEY_RE.match(key):
            raise ValueError(f"invalid label key: {key}")
        if key in FORBIDDEN_LABEL_KEYS:
            raise ValueError(f"forbidden label key: {key}")
        items.append((key, str(v)))
    items.sort(key=lambda x: x[0])
    return tuple(items)
