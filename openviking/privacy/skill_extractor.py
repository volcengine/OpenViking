# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Extract sensitive values from skill content."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from openviking.config.vlm import VLMHandle
from openviking.privacy.rules import PrivacyMatch, is_sensitive_value
from openviking.privacy.sanitizer import (
    DEFAULT_SANITIZER_MAX_CONTENT_BYTES,
    DEFAULT_SANITIZER_MAX_REPLACEMENTS,
)
from openviking.prompts import render_prompt
from openviking.pyagfs import get_privacy_matcher_class
from openviking_cli.exceptions import ResourceExhaustedError
from openviking_cli.utils.llm import parse_json_from_response

if TYPE_CHECKING:
    from openviking.server.user_config import ResolvedPrivacyConfig


async def extract_skill_privacy_values(
    *,
    skill_name: str,
    skill_description: str,
    content: str,
    vlm: VLMHandle,
    settings: ResolvedPrivacyConfig | None = None,
) -> dict[str, str]:
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

    return values


def find_skill_privacy_matches(content: str, values: Iterable[str]) -> list[PrivacyMatch]:
    """Locate only the values selected by VLM, using the shared literal matcher."""
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
