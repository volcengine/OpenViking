# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from difflib import SequenceMatcher
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Tuple, Union

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


def collect_tool_parts_from_messages(messages: Iterable[Any]) -> List[Any]:
    """Collect tool-call records from structured ToolParts and text transcripts.

    Structured integrations store tool results as ToolPart objects. Some benchmark
    harnesses ingest a textual transcript using `tool-call:` / `tool-response:`
    blocks; for those, count completed/error responses and use the paired call
    name when the response omits it.
    """
    parts: List[Any] = []
    text_calls: Dict[str, Dict[str, str]] = {}
    text_call_order: List[Dict[str, str]] = []
    text_responses_seen: set[str] = set()
    text_consumed_call_indices: set[int] = set()

    try:
        message_iter = iter(messages or [])
    except TypeError:
        return []

    for message in message_iter:
        for part in getattr(message, "parts", []) or []:
            if getattr(part, "tool_name", "") or getattr(part, "skill_uri", ""):
                parts.append(part)
                continue

            text = getattr(part, "text", "") or ""
            parsed = _parse_tool_marker_text(text)
            if parsed is None:
                continue
            kind, fields = parsed
            call_id = fields.get("call_id", "")
            if kind == "tool-call":
                if call_id:
                    text_calls[call_id] = fields
                text_call_order.append(fields)
                continue

            if kind == "tool-response":
                call = text_calls.get(call_id, {}) if call_id else {}
                matched_call_index: int | None = None
                if not call_id:
                    response_name_norm = normalize_name(fields.get("name", ""))
                    for idx, candidate_call in enumerate(text_call_order):
                        if idx in text_consumed_call_indices:
                            continue
                        candidate_name_norm = normalize_name(candidate_call.get("name", ""))
                        if response_name_norm and candidate_name_norm != response_name_norm:
                            continue
                        call = candidate_call
                        matched_call_index = idx
                        break
                name = fields.get("name") or call.get("name") or ""
                if not name:
                    continue
                status = "error" if fields.get("error", "").lower() == "true" else "completed"
                parts.append(
                    SimpleNamespace(
                        tool_name=name,
                        skill_uri=fields.get("skill_uri", "") or call.get("skill_uri", ""),
                        tool_status=status,
                        duration_ms=None,
                        prompt_tokens=None,
                        completion_tokens=None,
                    )
                )
                if call_id:
                    text_responses_seen.add(call_id)
                elif matched_call_index is not None:
                    text_consumed_call_indices.add(matched_call_index)

    for idx, call in enumerate(text_call_order):
        if idx in text_consumed_call_indices:
            continue
        call_id = call.get("call_id", "")
        if call_id and call_id in text_responses_seen:
            continue
        name = call.get("name", "")
        if not name:
            continue
        parts.append(
            SimpleNamespace(
                tool_name=name,
                skill_uri=call.get("skill_uri", ""),
                tool_status="pending",
                duration_ms=None,
                prompt_tokens=None,
                completion_tokens=None,
            )
        )

    return parts


def _parse_tool_marker_text(text: str) -> Tuple[str, Dict[str, str]] | None:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return None
    marker = lines[0].lower()
    if marker not in {"tool-call:", "tool-response:"}:
        return None

    fields: Dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip().lower().replace("-", "_")] = value.strip()

    return marker[:-1], fields
