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

import re
from dataclasses import dataclass
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


def render_labels(normalized: tuple[tuple[str, str], ...]) -> str:
    """
    Render normalized labels into Prometheus exposition syntax.

    Args:
        normalized: Canonical normalized label tuple.

    Returns:
        An empty string when no labels are present, or a `{k="v",...}` block ready for text
        exposition output.
    """
    if not normalized:
        return ""
    parts = [f'{k}="{_escape_label_value(v)}"' for k, v in normalized]
    return "{" + ",".join(parts) + "}"


def _escape_label_value(value: str) -> str:
    """Escape a label value according to Prometheus text exposition rules."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


@dataclass(frozen=True, slots=True)
class MetricSeriesId:
    """
    Immutable identifier for a single metric series in the in-process registry.

    Attributes:
        name: Metric family name.
        labels: Canonical normalized label tuple uniquely identifying one concrete series within
            that metric family.
    """

    name: str
    labels: tuple[tuple[str, str], ...]
