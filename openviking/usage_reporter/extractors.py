# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Usage event extractors."""

from __future__ import annotations

import json
from typing import Any, Iterable, Protocol

from openviking.message import Message, ToolPart

from .models import UsageContext, UsageEvent, utc_now_iso


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


def _memory_type_from_uri(uri: str) -> str:
    if "/experiences/" in uri:
        return "experience"
    if "/trajectories/" in uri:
        return "trajectory"
    return "memory"


def _is_memory_uri(uri: str) -> bool:
    return uri.startswith("viking://") and "/memories/" in uri


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
        for message_index, message in enumerate(messages):
            for part_index, part in enumerate(message.parts):
                if not isinstance(part, ToolPart):
                    continue
                if part.tool_status != "completed":
                    continue
                if part.tool_name == "search_experience":
                    events.extend(
                        self._extract_search_events(
                            part,
                            context=context,
                            message=message,
                            message_index=message_index,
                            part_index=part_index,
                        )
                    )
                elif part.tool_name == "read_experience":
                    event = self._extract_read_event(
                        part,
                        context=context,
                        message=message,
                        message_index=message_index,
                        part_index=part_index,
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
        message_index: int,
        part_index: int,
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
            if not uri or not _is_memory_uri(uri):
                continue
            events.append(
                self._build_event(
                    event_type="memory.recalled",
                    memory_uri=uri,
                    part=part,
                    context=context,
                    message=message,
                    message_index=message_index,
                    part_index=part_index,
                )
            )
        return events

    def _extract_read_event(
        self,
        part: ToolPart,
        *,
        context: UsageContext,
        message: Message,
        message_index: int,
        part_index: int,
    ) -> UsageEvent | None:
        tool_input = part.tool_input if isinstance(part.tool_input, dict) else {}
        output = _load_mapping(part.tool_output)
        uri = str(tool_input.get("uri") or output.get("uri") or "").strip()
        if not uri or not _is_memory_uri(uri):
            return None
        return self._build_event(
            event_type="memory.injected",
            memory_uri=uri,
            part=part,
            context=context,
            message=message,
            message_index=message_index,
            part_index=part_index,
        )

    def _build_event(
        self,
        *,
        event_type: str,
        memory_uri: str,
        part: ToolPart,
        context: UsageContext,
        message: Message,
        message_index: int,
        part_index: int,
    ) -> UsageEvent:
        return UsageEvent(
            event_type=event_type,
            memory_uri=memory_uri,
            memory_type=_memory_type_from_uri(memory_uri),
            account_id=context.account_id,
            user_id=context.user_id,
            session_id=context.session_id,
            archive_uri=context.archive_uri,
            task_id=context.task_id,
            occurred_at=utc_now_iso(),
            source={"tool_name": part.tool_name, "tool_status": part.tool_status},
            evidence={
                "message_index": message_index,
                "message_id": message.id,
                "part_index": part_index,
                "tool_call_id": part.tool_id,
            },
        )
