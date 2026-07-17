# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .models import EntryRecord, MockRecord, ReplayCodecError


@dataclass(slots=True)
class ReplayInvocation:
    entry: EntryRecord
    mock_records: list[MockRecord]


def entries_from_jaeger_trace(data: dict[str, Any]) -> list[EntryRecord]:
    entries = []
    for trace in data.get("data", []):
        for span in sorted(trace.get("spans", []), key=lambda item: item.get("startTime", 0)):
            tags = _tag_map(span)
            if tags.get("replay.kind") != "entry":
                continue
            entries.append(_entry_record(span, tags))
    return entries


def select_replay_invocation(
    data: dict[str, Any],
    name: str,
    *,
    invocation_id: str | None = None,
) -> ReplayInvocation:
    matching = [entry for entry in entries_from_jaeger_trace(data) if entry.name == name]
    if invocation_id is not None:
        matching = [entry for entry in matching if entry.invocation_id == invocation_id]
    if not matching:
        suffix = f" with invocation {invocation_id!r}" if invocation_id else ""
        raise ValueError(f"Replay entry {name!r}{suffix} was not found")
    if len(matching) > 1:
        ids = ", ".join(str(entry.invocation_id) for entry in matching)
        raise ValueError(
            f"Replay entry {name!r} is ambiguous ({ids}); select one with --invocation"
        )
    entry = matching[0]
    selected_span_id = str(entry.invocation_id)
    mocks = []
    for trace in data.get("data", []):
        spans = trace.get("spans", [])
        span_by_id = {str(span.get("spanID")): span for span in spans}
        if selected_span_id not in span_by_id:
            continue
        descendant_ids = _descendant_ids(selected_span_id, spans)
        for span in sorted(spans, key=lambda item: item.get("startTime", 0)):
            if str(span.get("spanID")) not in descendant_ids:
                continue
            tags = _tag_map(span)
            if tags.get("replay.kind") == "mock":
                mocks.append(_mock_record(span, tags))
        break
    return ReplayInvocation(entry=entry, mock_records=mocks)


def _entry_record(span: dict[str, Any], tags: dict[str, Any]) -> EntryRecord:
    return EntryRecord(
        name=_required_string(tags, "replay.name"),
        module=_required_string(tags, "replay.module"),
        arguments=_required_json_object(tags, "replay.arguments"),
        outcome=_outcome(tags),
        result=_optional_json_object(tags, "replay.result"),
        exception=_optional_json_object(tags, "replay.exception"),
        invocation_id=str(span.get("spanID") or ""),
    )


def _mock_record(span: dict[str, Any], tags: dict[str, Any]) -> MockRecord:
    return MockRecord(
        name=_required_string(tags, "replay.name"),
        match_key=_required_json_object(tags, "replay.match"),
        outcome=_outcome(tags),
        result=_optional_json_object(tags, "replay.result"),
        exception=_optional_json_object(tags, "replay.exception"),
        invocation_id=str(span.get("spanID") or ""),
    )


def _tag_map(span: dict[str, Any]) -> dict[str, Any]:
    return {str(tag.get("key")): tag.get("value") for tag in span.get("tags", [])}


def _required_string(tags: dict[str, Any], key: str) -> str:
    value = tags.get(key)
    if not isinstance(value, str) or not value:
        raise ReplayCodecError(f"Replay span is missing {key!r}")
    return value


def _required_json_object(tags: dict[str, Any], key: str) -> dict[str, Any]:
    value = _optional_json_object(tags, key)
    if value is None:
        raise ReplayCodecError(f"Replay span is missing {key!r}")
    return value


def _optional_json_object(tags: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = tags.get(key)
    if value is None:
        return None
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as error:
        raise ReplayCodecError(f"Replay span {key!r} is not valid JSON") from error
    if not isinstance(decoded, dict):
        raise ReplayCodecError(f"Replay span {key!r} must contain a JSON object")
    return decoded


def _outcome(tags: dict[str, Any]):
    outcome = tags.get("replay.outcome")
    if outcome not in {"returned", "raised"}:
        raise ReplayCodecError(f"Invalid replay outcome {outcome!r}")
    return outcome


def _descendant_ids(parent_id: str, spans: list[dict[str, Any]]) -> set[str]:
    children: dict[str, list[str]] = {}
    for span in spans:
        span_id = str(span.get("spanID") or "")
        for reference in span.get("references", []):
            if reference.get("refType") == "CHILD_OF":
                children.setdefault(str(reference.get("spanID") or ""), []).append(span_id)
                break
    descendants = set()
    pending = list(children.get(parent_id, []))
    while pending:
        span_id = pending.pop()
        if span_id in descendants:
            continue
        descendants.add(span_id)
        pending.extend(children.get(span_id, []))
    return descendants
