# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Extract sensitive values from skill content."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openviking.config.vlm import VLMHandle
from openviking.privacy.rules import PrivacyMatch, is_sensitive_value
from openviking.privacy.sanitizer import (
    DEFAULT_SANITIZER_MAX_CONTENT_BYTES,
    DEFAULT_SANITIZER_MAX_REPLACEMENTS,
)
from openviking.privacy.skill_placeholder import build_placeholder
from openviking.prompts import render_prompt
from openviking.pyagfs import get_privacy_matcher_class
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

    fields = {}
    for name, value in values.items():
        fields.setdefault(value, name)
    chunks: list[str] = []
    replaced: dict[str, str] = {}
    cursor = 0
    for match in find_skill_privacy_matches(content, fields):
        value = content[match.start : match.end]
        name = fields[value]
        chunks.extend((content[cursor : match.start], build_placeholder(skill_name, name)))
        replaced[name] = value
        cursor = match.end
    chunks.append(content[cursor:])
    return SkillPrivacyExtractionResult(
        values=replaced,
        original_content=content,
        sanitized_content="".join(chunks),
        original_content_blocks=list(replaced.values()),
        replacement_content_blocks=[build_placeholder(skill_name, name) for name in replaced],
    )


def find_skill_privacy_matches(content: str, values: Iterable[str]) -> list[PrivacyMatch]:
    """Locate selected literal values using the shared bounded matcher."""
    matcher = get_privacy_matcher_class()({value: ["vlm"] for value in values})
    try:
        matches, _, exceeded = matcher.find_limited(
            content, DEFAULT_SANITIZER_MAX_REPLACEMENTS, DEFAULT_SANITIZER_MAX_CONTENT_BYTES
        )
    except OverflowError:
        raise ResourceExhaustedError("Content exceeds the Privacy extractor size limit") from None
    if exceeded:
        raise ResourceExhaustedError("Content exceeds the Privacy extractor match limit")
    return [PrivacyMatch(*match) for match in matches]
