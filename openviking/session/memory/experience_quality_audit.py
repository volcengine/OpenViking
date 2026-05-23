# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lightweight quality audit for generated agent experience memories.

The audit is intentionally heuristic and read-only. It is meant for benchmark
and rollout diagnostics before enabling faster consolidation paths broadly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_GENERIC_NAME_TOKENS = {
    "handling",
    "process",
    "processing",
    "procedure",
    "request",
    "workflow",
}


@dataclass(frozen=True)
class ExperienceAuditConfig:
    """Thresholds and optional terms for corpus-level diagnostics."""

    duplicate_name_jaccard: float = 0.6
    broad_source_threshold: int = 4
    long_content_threshold: int = 3500
    watch_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExperienceAuditItem:
    """Parsed data for one persisted experience memory file."""

    name: str
    path: str
    chars: int
    source_trajectories: tuple[str, ...]
    content: str

    @property
    def source_count(self) -> int:
        return len(self.source_trajectories)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in (match.group(0).lower() for match in _TOKEN_RE.finditer(text or ""))
        if token and token not in _GENERIC_NAME_TOKENS
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _as_source_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        return tuple(part.strip() for part in value.splitlines() if part.strip())
    return ()


def load_experience_items(experience_dir: Path) -> list[ExperienceAuditItem]:
    """Load concrete experience markdown files from a local directory."""

    items: list[ExperienceAuditItem] = []
    for path in sorted(experience_dir.glob("*.md")):
        if path.name in {".abstract.md", ".overview.md"}:
            continue
        raw = path.read_text(encoding="utf-8")
        memory_file = MemoryFileUtils.read(raw, uri=str(path))
        name = str(memory_file.extra_fields.get("experience_name") or path.stem)
        items.append(
            ExperienceAuditItem(
                name=name,
                path=str(path),
                chars=len(memory_file.content or ""),
                source_trajectories=_as_source_tuple(
                    memory_file.extra_fields.get("source_trajectories")
                ),
                content=memory_file.content or "",
            )
        )
    return items


def audit_experience_items(
    items: Iterable[ExperienceAuditItem],
    config: ExperienceAuditConfig | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable audit summary for experience items."""

    config = config or ExperienceAuditConfig()
    items = list(items)
    source_counts = [item.source_count for item in items]
    char_counts = [item.chars for item in items]

    duplicate_pairs: list[dict[str, Any]] = []
    token_cache = {item.name: _tokens(item.name) for item in items}
    for left_index, left in enumerate(items):
        for right in items[left_index + 1 :]:
            score = _jaccard(token_cache[left.name], token_cache[right.name])
            if score >= config.duplicate_name_jaccard:
                duplicate_pairs.append(
                    {
                        "left": left.name,
                        "right": right.name,
                        "name_jaccard": round(score, 4),
                    }
                )

    broad_source_items = [
        {
            "name": item.name,
            "source_count": item.source_count,
            "sources": list(item.source_trajectories),
        }
        for item in items
        if item.source_count >= config.broad_source_threshold
    ]
    missing_source_items = [item.name for item in items if item.source_count == 0]
    long_items = [
        {"name": item.name, "chars": item.chars}
        for item in items
        if item.chars >= config.long_content_threshold
    ]

    watch_report: list[dict[str, Any]] = []
    for term in config.watch_terms:
        normalized = term.strip().lower()
        if not normalized:
            continue
        name_matches = [
            item.name for item in items if normalized in item.name.lower().replace("_", " ")
        ]
        content_matches = [
            item.name for item in items if normalized in (item.content or "").lower()
        ]
        watch_report.append(
            {
                "term": normalized,
                "name_matches": name_matches,
                "content_matches": content_matches,
                "swallowed_in_content": bool(content_matches and not name_matches),
            }
        )

    avg_sources = sum(source_counts) / len(source_counts) if source_counts else 0.0
    avg_chars = sum(char_counts) / len(char_counts) if char_counts else 0.0
    return {
        "experience_count": len(items),
        "avg_chars": round(avg_chars, 2),
        "avg_sources": round(avg_sources, 2),
        "duplicate_name_pairs": duplicate_pairs,
        "broad_source_items": broad_source_items,
        "missing_source_items": missing_source_items,
        "long_items": long_items,
        "watch_terms": watch_report,
    }


def audit_experience_dir(
    experience_dir: Path,
    config: ExperienceAuditConfig | None = None,
) -> dict[str, Any]:
    """Load and audit a local experience memory directory."""

    return audit_experience_items(load_experience_items(experience_dir), config=config)


def audit_to_json(report: dict[str, Any]) -> str:
    """Serialize an audit report deterministically for benchmark artifacts."""

    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
