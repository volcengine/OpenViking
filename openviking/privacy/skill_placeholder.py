# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Placeholder helpers for skill privacy values."""

from dataclasses import dataclass, field


@dataclass
class SkillPrivacyPlaceholderizationResult:
    sanitized_content: str
    original_content_blocks: list[str] = field(default_factory=list)
    replacement_content_blocks: list[str] = field(default_factory=list)


def build_placeholder(skill_name: str, field_name: str) -> str:
    return f"{{{{ov_privacy:skill:{skill_name}:{field_name}}}}}"


def placeholderize_skill_content_with_blocks(
    content: str, skill_name: str, values: dict[str, str]
) -> SkillPrivacyPlaceholderizationResult:
    sanitized = content
    original_content_blocks: list[str] = []
    replacement_content_blocks: list[str] = []
    replacements = sorted(values.items(), key=lambda item: len(str(item[1])), reverse=True)

    for field_name, raw_value in replacements:
        if not raw_value:
            continue
        raw_value_str = str(raw_value)
        placeholder = build_placeholder(skill_name, field_name)
        if raw_value_str in sanitized:
            original_content_blocks.append(raw_value_str)
            replacement_content_blocks.append(placeholder)
        sanitized = sanitized.replace(raw_value_str, placeholder)

    return SkillPrivacyPlaceholderizationResult(
        sanitized_content=sanitized,
        original_content_blocks=original_content_blocks,
        replacement_content_blocks=replacement_content_blocks,
    )


def placeholderize_skill_content(
    content: str, skill_name: str, values: dict[str, str]
) -> str:
    return placeholderize_skill_content_with_blocks(content, skill_name, values).sanitized_content
