# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Extract sensitive values from skill content."""

import re
from dataclasses import dataclass, field

from openviking.config.vlm import VLMHandle
from openviking.privacy.skill_placeholder import placeholderize_skill_content_with_blocks
from openviking.prompts import render_prompt
from openviking_cli.utils.llm import parse_json_from_response

# A bare resource URI or filesystem path identifies a location, not a
# credential. Registering one as a private value rewrites every occurrence in
# stored content — including system-generated ``.abstract.md`` frontmatter,
# where the substituted ``{{ov_privacy:...}}`` scalar breaks YAML parsing and
# silently strips the skill's description (#6909).
_RESOURCE_LOCATION_RE = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9+.-]*)://[^\"']*$|^[A-Za-z]:[\\/].*$|^/.*$"
)


def _is_resource_location(value: str) -> bool:
    """Return True when ``value`` is a URI/path reference rather than a secret."""
    return _RESOURCE_LOCATION_RE.match(value.strip()) is not None


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
                str(key): "" if value is None else str(value)
                for key, value in raw_values.items()
                if str(key).strip() and value is not None and not _is_resource_location(str(value))
            }

    placeholder_result = placeholderize_skill_content_with_blocks(content, skill_name, values)
    return SkillPrivacyExtractionResult(
        values=placeholder_result.replaced_values,
        original_content=content,
        sanitized_content=placeholder_result.sanitized_content,
        original_content_blocks=placeholder_result.original_content_blocks,
        replacement_content_blocks=placeholder_result.replacement_content_blocks,
    )
