# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Placeholder helpers for skill privacy values."""

from dataclasses import dataclass, field
import re


@dataclass
class SkillPrivacyPlaceholderizationResult:
    sanitized_content: str
    original_content_blocks: list[str] = field(default_factory=list)
    replacement_content_blocks: list[str] = field(default_factory=list)
    replaced_values: dict[str, str] = field(default_factory=dict)


def build_placeholder(skill_name: str, field_name: str) -> str:
    return f"{{{{ov_privacy:skill:{skill_name}:{field_name}}}}}"


def _replace_structured_value(content: str, raw_value: str, placeholder: str) -> tuple[str, bool]:
    """Replace values only when they occupy a structured assignment position.

    A global ``str.replace`` is unsafe for privacy redaction: a credential such as
    ``prod`` can occur in prose or an unrelated field and would be replaced there.
    Match the value after ``:`` or ``=`` and stop at the logical end of that field.
    Quoted values are matched exactly; unquoted values are bounded by whitespace,
    a comma, or the end of the line.
    """
    escaped = re.escape(raw_value)
    pattern = re.compile(
        rf"(?P<prefix>^[ \t]*[^\n:#=]+?[ \t]*(?::|=)[ \t]*)"
        rf"(?P<quote>[\"'])?{escaped}(?P=quote)?(?P<suffix>[ \t]*(?:[,#].*)?$)",
        re.MULTILINE,
    )

    def replacement(match: re.Match[str]) -> str:
        quote = match.group("quote") or ""
        return f"{match.group('prefix')}{quote}{placeholder}{quote}{match.group('suffix')}"

    updated, count = pattern.subn(replacement, content)
    return updated, count > 0


def placeholderize_skill_content_with_blocks(
    content: str, skill_name: str, values: dict[str, str]
) -> SkillPrivacyPlaceholderizationResult:
    sanitized = content
    original_content_blocks: list[str] = []
    replacement_content_blocks: list[str] = []
    replaced_values: dict[str, str] = {}
    replacements = sorted(values.items(), key=lambda item: len(str(item[1])), reverse=True)

    for field_name, raw_value in replacements:
        if not raw_value:
            continue
        raw_value_str = str(raw_value)
        placeholder = build_placeholder(skill_name, field_name)
        sanitized, replaced = _replace_structured_value(sanitized, raw_value_str, placeholder)
        if replaced:
            original_content_blocks.append(raw_value_str)
            replacement_content_blocks.append(placeholder)
            replaced_values[field_name] = raw_value_str

    return SkillPrivacyPlaceholderizationResult(
        sanitized_content=sanitized,
        original_content_blocks=original_content_blocks,
        replacement_content_blocks=replacement_content_blocks,
        replaced_values=replaced_values,
    )


def placeholderize_skill_content(content: str, skill_name: str, values: dict[str, str]) -> str:
    return placeholderize_skill_content_with_blocks(content, skill_name, values).sanitized_content
