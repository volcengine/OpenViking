# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for directory-level semantic summary caches."""

import json
from typing import Dict, List

SUMMARY_CACHE_FILENAME = ".summary_cache.json"
MANAGED_HIDDEN_SEMANTIC_FILES = frozenset(
    {
        ".abstract.md",
        ".overview.md",
        SUMMARY_CACHE_FILENAME,
    }
)


def build_summary_cache(file_summaries: List[Dict[str, str]]) -> Dict[str, str]:
    """Build a filename -> summary map from generated file summaries."""
    cache: Dict[str, str] = {}
    for item in file_summaries:
        name = item.get("name", "").strip()
        if not name:
            continue
        summary = item.get("summary", "")
        cache[name] = summary if isinstance(summary, str) else str(summary)
    return cache


def serialize_summary_cache(file_summaries: List[Dict[str, str]]) -> str:
    """Serialize summary cache content for storage on disk."""
    return json.dumps(build_summary_cache(file_summaries), ensure_ascii=False, sort_keys=True)


def parse_summary_cache(content: str) -> Dict[str, str]:
    """Parse a stored summary cache.

    Returns an empty mapping for missing or invalid content.
    """
    if not content or not content.strip():
        return {}

    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, dict):
        return {}

    cache: Dict[str, str] = {}
    for raw_name, raw_summary in payload.items():
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name:
            continue
        if isinstance(raw_summary, str):
            cache[name] = raw_summary
        elif raw_summary is None:
            cache[name] = ""
        else:
            cache[name] = str(raw_summary)
    return cache
