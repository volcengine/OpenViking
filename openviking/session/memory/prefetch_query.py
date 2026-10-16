# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Plan searches for existing memories that incoming messages may affect."""

from __future__ import annotations

import json
from time import perf_counter
from typing import TYPE_CHECKING, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from openviking.message import Message, TextPart
from openviking.prompts import render_prompt
from openviking_cli.utils import get_logger
from openviking_cli.utils.llm import parse_json_from_response

if TYPE_CHECKING:
    from openviking.config.vlm import VLMHandle

MAX_MAINTENANCE_QUERIES = 10
logger = get_logger(__name__)


class MaintenanceTarget(TypedDict):
    subject: str
    aspect: str
    scope: str
    query: str
    message_indices: list[int]


class _MaintenanceTarget(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True, extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    aspect: str = Field(min_length=1, max_length=120)
    scope: str = Field(default="", max_length=160)
    # Keep the observed value separate so it need not be squeezed into the
    # retrieval slot. It is not forwarded to search.
    new_information: str = Field(default="", max_length=500)
    message_indices: list[int] = Field(min_length=1)


class _MaintenancePlan(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    targets: list[_MaintenanceTarget] = Field(max_length=MAX_MAINTENANCE_QUERIES)


async def build_maintenance_queries(messages: list[Message], vlm: VLMHandle) -> list[str]:
    return [target["query"] for target in await build_maintenance_targets(messages, vlm)]


async def build_maintenance_targets(
    messages: list[Message], vlm: VLMHandle
) -> list[MaintenanceTarget]:
    """Generate retrieval hints, without deciding any memory writes or ownership.

    Use full text in conversation order, including role, peer and source time.
    The original extraction input is never modified. Tool results are excluded.
    Invalid plans raise so the caller can fall back to its original query.
    """
    conversation = []
    for index, message in enumerate(messages):
        text = "\n".join(
            part.text for part in message.parts if isinstance(part, TextPart) and part.text
        )
        if not text.strip():
            continue
        conversation.append(
            {
                "message_index": index,
                "role": message.role,
                "peer_id": message.peer_id,
                "created_at": message.created_at,
                "text": text,
            }
        )
    if not conversation:
        logger.info("Memory prefetch planning skipped: no text messages")
        return []

    started = perf_counter()
    response = await vlm.get_completion_async(
        messages=[
            {
                "role": "system",
                "content": render_prompt(
                    "retrieval.memory_maintenance", {"max_queries": MAX_MAINTENANCE_QUERIES}
                ),
            },
            {"role": "user", "content": json.dumps(conversation, ensure_ascii=False, default=str)},
        ],
        thinking=False,
        max_tokens=4096,
    )
    plan = _MaintenancePlan.model_validate(parse_json_from_response(response))
    source_indices = {message["message_index"] for message in conversation}
    targets = []
    seen = {}
    for target in plan.targets:
        if not set(target.message_indices) <= source_indices:
            raise ValueError("Maintenance target references a missing text message")
        subject = " ".join(target.subject.split())
        aspect = " ".join(target.aspect.split())
        scope = " ".join(target.scope.split())
        key = (subject.casefold(), aspect.casefold(), scope.casefold())
        indices = sorted(set(target.message_indices))
        if key in seen:
            previous = targets[seen[key]]
            previous["message_indices"] = sorted(set(previous["message_indices"]) | set(indices))
        else:
            seen[key] = len(targets)
            # The model chooses a maintenance slot, never a second free-form
            # restatement of the new facts to use as the search query.
            query = " ".join(part for part in (subject, aspect, scope) if part)
            targets.append(
                MaintenanceTarget(
                    subject=subject,
                    aspect=aspect,
                    scope=scope,
                    query=query,
                    message_indices=indices,
                )
            )
    logger.info(
        "Memory prefetch planning: target_count=%d query_count=%d elapsed_ms=%.1f",
        len(plan.targets),
        len(targets),
        (perf_counter() - started) * 1000,
    )
    logger.debug(
        "Memory prefetch maintenance targets: %s",
        json.dumps([target.model_dump() for target in plan.targets], ensure_ascii=False),
    )
    return targets
