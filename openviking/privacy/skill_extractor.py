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
    r"^(?:[A-Za-z][A-Za-z0-9+.-]*)://[^\"']*$"  # scheme://… URI
    r"|^[A-Za-z]:[\\/].*$"  # Windows drive path
    r"|^/.*$"  # POSIX absolute path
    r"|^\\\\[^\\].*$"  # UNC share path
    r"|^\.{1,2}[\\/].*$"  # ./ or ../ relative path
)
# A URI is treated as a *credential carrier* — and therefore still registered —
# when it embeds userinfo or a secret-shaped query parameter. A bare location
# like ``viking://agent/skills`` is filtered; these are not.
_SECRET_IN_URI_RE = re.compile(
    r"://[^/\s]*@"  # userinfo (user:password@)
    r"|[?&](token|key|secret|password|passwd|sig|signature|credential|access_key|api_key)="  # secret query param
)


def _is_resource_location(value: str) -> bool:
    """Return True when ``value`` is a pure location reference, not a secret."""
    stripped = value.strip()
    # A quoted value still describes a location; strip quote pairs so
    # ``"viking://agent/skills"`` is filtered the same as the bare form.
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        stripped = stripped[1:-1]
    if not _RESOURCE_LOCATION_RE.match(stripped):
        return False
    # Credential-shaped URIs (userinfo or secret-looking query params) are
    # secrets that happen to be URIs — keep registering them (#6909 review).
    return _SECRET_IN_URI_RE.search(stripped) is None


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
