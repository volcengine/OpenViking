# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Extract sensitive values from skill content."""

from __future__ import annotations

from dataclasses import dataclass, field
from secrets import token_urlsafe
from typing import TYPE_CHECKING

from openviking.config.vlm import VLMHandle
from openviking.privacy.rules import find_privacy_matches, is_sensitive_value
from openviking.privacy.skill_placeholder import (
    build_placeholder,
    placeholderize_skill_content_with_blocks,
)
from openviking.prompts import render_prompt
from openviking_cli.exceptions import ResourceExhaustedError
from openviking_cli.utils.llm import parse_json_from_response

if TYPE_CHECKING:
    from openviking.server.user_config import ResolvedPrivacyConfig


@dataclass
class SkillPrivacyExtractionResult:
    values: dict[str, str]
    original_content: str
    sanitized_content: str
    original_content_blocks: list[str] = field(default_factory=list)
    replacement_content_blocks: list[str] = field(default_factory=list)


async def extract_skill_privacy_values(
    *,
    skill_name: str,
    skill_description: str,
    content: str,
    vlm: VLMHandle,
    settings: ResolvedPrivacyConfig | None = None,
) -> SkillPrivacyExtractionResult:
    if vlm is None:
        raise ValueError("Skill privacy extraction requires an explicitly resolved VLM")
    prompt = render_prompt(
        "skill.privacy_extraction",
        {
            "skill_name": skill_name,
            "skill_description": skill_description,
            "skill_content": content,
        },
    )
    response = await vlm.get_completion_async(prompt)
    data = parse_json_from_response(response) or {}

    values: dict[str, str] = {}
    if isinstance(data, dict):
        raw_values = data.get("values", {})
        if isinstance(raw_values, dict):
            values = {
                str(key): value
                for key, value in raw_values.items()
                if str(key).strip()
                and isinstance(value, str)
                and is_sensitive_value(value, settings=settings)
            }

    placeholder_result = placeholderize_skill_content_with_blocks(content, skill_name, values)
    return SkillPrivacyExtractionResult(
        values=placeholder_result.replaced_values,
        original_content=content,
        sanitized_content=placeholder_result.sanitized_content,
        original_content_blocks=placeholder_result.original_content_blocks,
        replacement_content_blocks=placeholder_result.replacement_content_blocks,
    )


def extract_skill_privacy_values_by_rules(
    *,
    skill_name: str,
    content: str,
    reserved_fields: set[str] | None = None,
    settings: ResolvedPrivacyConfig | None = None,
) -> SkillPrivacyExtractionResult:
    """Replace detected spans while preserving the surrounding Skill content."""
    values: dict[str, str] = {}
    references: dict[str, str] = {}
    chunks: list[str] = []
    cursor = 0
    for match in find_privacy_matches(content, settings=settings):
        value = content[match.start : match.end]
        reference = references.get(value)
        if reference is None:
            for _ in range(8):
                key = token_urlsafe(6)
                # Existing references must keep their field identity.
                if key not in values and (not reserved_fields or key not in reserved_fields):
                    break
            else:
                raise ResourceExhaustedError("Could not allocate unique Skill privacy field IDs")
            values[key] = value
            reference = build_placeholder(skill_name, key)
            references[value] = reference
        chunks.extend((content[cursor : match.start], reference))
        cursor = match.end
    chunks.append(content[cursor:])
    return SkillPrivacyExtractionResult(
        values=values,
        original_content=content,
        sanitized_content="".join(chunks),
        original_content_blocks=list(references),
        replacement_content_blocks=list(references.values()),
    )
