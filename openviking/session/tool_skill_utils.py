# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Iterable, Tuple, Union

Number = Union[int, float]


def normalize_name(name: str) -> str:
    return (name or "").lower().strip().replace("_", "").replace("-", "").replace(" ", "")


def extract_skill_name_from_uri(uri: str) -> str:
    uri = (uri or "").strip()
    if not uri:
        return ""
    return uri.rstrip("/").split("/")[-1]


def _calibrate_name(
    candidate_name: str,
    parts: Iterable[Any],
    name_getter: Callable[[Any], str],
    threshold: float,
) -> Tuple[str, str]:
    candidate_name = (candidate_name or "").strip()
    if not candidate_name:
        return ("", "completed")

    candidate_norm = normalize_name(candidate_name)
    best_ratio = -1.0
    best_name = ""
    best_status = "completed"

    for part in parts:
        part_name = (name_getter(part) or "").strip()
        if not part_name:
            continue

        part_norm = normalize_name(part_name)
        if part_name == candidate_name or (candidate_norm and part_norm == candidate_norm):
            return (part_name, getattr(part, "tool_status", None) or "completed")

        ratio = SequenceMatcher(None, candidate_norm, part_norm).ratio()
        # tie-break: prefer the last occurrence when multiple parts have the same similarity
        if ratio > best_ratio or (ratio == best_ratio and ratio >= 0):
            best_ratio = ratio
            best_name = part_name
            best_status = getattr(part, "tool_status", None) or "completed"

    if best_ratio >= threshold and best_name:
        return (best_name, best_status)
    return ("", "completed")


def calibrate_tool_name(candidate_tool_name: str, tool_parts: Iterable[Any]) -> Tuple[str, str]:
    return _calibrate_name(
        candidate_name=candidate_tool_name,
        parts=tool_parts,
        name_getter=lambda p: getattr(p, "tool_name", "") or "",
        threshold=0.8,
    )


def calibrate_skill_name(candidate_skill_name: str, tool_parts: Iterable[Any]) -> Tuple[str, str]:
    return _calibrate_name(
        candidate_name=candidate_skill_name,
        parts=tool_parts,
        name_getter=lambda p: extract_skill_name_from_uri(getattr(p, "skill_uri", "") or ""),
        threshold=0.8,
    )


def collect_tool_stats(tool_parts: Iterable[Any]) -> Dict[str, Dict[str, Number]]:
    stats_map: Dict[str, Dict[str, Number]] = {}
    for part in tool_parts:
        name = (getattr(part, "tool_name", "") or "").strip()
        if not name:
            continue

        if name not in stats_map:
            stats_map[name] = {
                "duration_ms": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "success_time": 0,
                "call_count": 0,
            }

        stats_map[name]["call_count"] += 1
        duration_ms = getattr(part, "duration_ms", None)
        if duration_ms is not None:
            stats_map[name]["duration_ms"] += duration_ms
        prompt_tokens = getattr(part, "prompt_tokens", None)
        if prompt_tokens is not None:
            stats_map[name]["prompt_tokens"] += int(prompt_tokens)
        completion_tokens = getattr(part, "completion_tokens", None)
        if completion_tokens is not None:
            stats_map[name]["completion_tokens"] += int(completion_tokens)
        if (getattr(part, "tool_status", None) or "") == "completed":
            stats_map[name]["success_time"] += 1

    return stats_map


def collect_skill_stats(tool_parts: Iterable[Any]) -> Dict[str, Dict[str, Number]]:
    stats_map: Dict[str, Dict[str, Number]] = {}
    for part in tool_parts:
        skill_uri = getattr(part, "skill_uri", "") or ""
        skill_name = extract_skill_name_from_uri(skill_uri)
        if not skill_name:
            continue

        if skill_name not in stats_map:
            stats_map[skill_name] = {
                "success_time": 0,
                "call_count": 0,
            }

        stats_map[skill_name]["call_count"] += 1
        if (getattr(part, "tool_status", None) or "") == "completed":
            stats_map[skill_name]["success_time"] += 1

    return stats_map
