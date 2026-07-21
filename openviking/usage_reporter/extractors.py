# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Usage event extractors."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Protocol

from openviking.message import Message, ToolPart
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime

from .models import UsageContext, UsageEvent, utc_now_iso

_EXPERIENCE_SIDECAR_FILENAMES = {".abstract.md", ".overview.md", ".relations.json"}


class UsageExtractor(Protocol):
    name: str

    async def extract(
        self,
        *,
        messages: list[Message],
        context: UsageContext,
    ) -> list[UsageEvent]: ...


def _load_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_experience_uri(uri: str, context: UsageContext) -> bool:
    prefix = f"viking://user/{context.user_id}/memories/experiences/"
    if not uri.startswith(prefix) or "?" in uri or "#" in uri:
        return False
    relative = uri.removeprefix(prefix)
    segments = relative.split("/")
    if not relative or any(not segment or segment in {".", ".."} for segment in segments):
        return False
    return segments[-1] not in _EXPERIENCE_SIDECAR_FILENAMES


def _event_time(message: Message) -> str:
    value = message.created_at
    try:
        if isinstance(value, datetime):
            return format_iso8601(value)
        if isinstance(value, str) and value.strip():
            return format_iso8601(parse_iso_datetime(value.strip()))
    except (TypeError, ValueError):
        pass
    return utc_now_iso()


class MemoryUsageExtractor:
    """Extract memory recalled/injected usage events from official tool parts."""

    name = "memory_usage"

    async def extract(
        self,
        *,
        messages: list[Message],
        context: UsageContext,
    ) -> list[UsageEvent]:
        events: list[UsageEvent] = []
        tool_inputs: dict[tuple[str, str], dict[str, Any]] = {}
        for message in messages:
            for part in message.parts:
                if (
                    isinstance(part, ToolPart)
                    and part.tool_id
                    and isinstance(part.tool_input, dict)
                    and part.tool_input
                ):
                    tool_inputs[(part.tool_id, part.tool_name)] = part.tool_input

        for message in messages:
            for part in message.parts:
                if not isinstance(part, ToolPart):
                    continue
                if not part.tool_id:
                    continue
                if part.tool_status != "completed":
                    continue
                if part.tool_name == "search_experience":
                    events.extend(
                        self._extract_search_events(
                            part,
                            context=context,
                            message=message,
                        )
                    )
                elif part.tool_name == "read_experience":
                    event = self._extract_read_event(
                        part,
                        context=context,
                        message=message,
                        fallback_input=tool_inputs.get((part.tool_id, part.tool_name), {}),
                    )
                    if event is not None:
                        events.append(event)
        return events

    def _extract_search_events(
        self,
        part: ToolPart,
        *,
        context: UsageContext,
        message: Message,
    ) -> Iterable[UsageEvent]:
        output = _load_mapping(part.tool_output)
        results = output.get("results", [])
        if not isinstance(results, list):
            return []

        events: list[UsageEvent] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            uri = str(result.get("uri") or "").strip()
            if not uri or not _is_experience_uri(uri, context):
                continue
            events.append(
                self._build_event(
                    event_type="memory.recalled",
                    resource_uri=uri,
                    part=part,
                    context=context,
                    message=message,
                )
            )
        return events

    def _extract_read_event(
        self,
        part: ToolPart,
        *,
        context: UsageContext,
        message: Message,
        fallback_input: dict[str, Any],
    ) -> UsageEvent | None:
        tool_input = part.tool_input if isinstance(part.tool_input, dict) else {}
        if not tool_input:
            tool_input = fallback_input
        output = _load_mapping(part.tool_output)
        uri = str(tool_input.get("uri") or output.get("uri") or "").strip()
        if not uri or not _is_experience_uri(uri, context):
            return None
        return self._build_event(
            event_type="memory.injected",
            resource_uri=uri,
            part=part,
            context=context,
            message=message,
        )

    def _build_event(
        self,
        *,
        event_type: str,
        resource_uri: str,
        part: ToolPart,
        context: UsageContext,
        message: Message,
    ) -> UsageEvent:
        return UsageEvent(
            event_type=event_type,
            resource_uri=resource_uri,
            resource_type="experience",
            account_id=context.account_id,
            user_id=context.user_id,
            session_id=context.session_id,
            task_id=context.task_id,
            occurred_at=_event_time(message),
            evidence={
                "archive_uri": context.archive_uri,
                "message_id": message.id,
                "tool_call_id": part.tool_id,
                "tool_name": part.tool_name,
            },
        )
