# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SKILL.md loader and parser."""

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml


class SkillLoader:
    """Load and parse SKILL.md files."""

    FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

    @staticmethod
    def _normalize_allowed_tools(value: Any) -> list[str]:
        """Normalize standard scalar and legacy list forms of ``allowed-tools``."""
        if isinstance(value, list):
            if any(not isinstance(tool, str) for tool in value):
                raise ValueError(
                    "Skill 'allowed-tools' must be a space-separated string or an array of strings"
                )
            return value
        if not isinstance(value, str):
            raise ValueError(
                "Skill 'allowed-tools' must be a space-separated string or an array of strings"
            )

        tools: list[str] = []
        current: list[str] = []
        depth = 0
        for char in value.strip():
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    raise ValueError("Skill 'allowed-tools' has unbalanced parentheses")

            if depth == 0 and (char.isspace() or char == ","):
                if current:
                    tools.append("".join(current))
                    current = []
                continue
            current.append(char)

        if depth != 0:
            raise ValueError("Skill 'allowed-tools' has unbalanced parentheses")
        if current:
            tools.append("".join(current))
        return tools

    @classmethod
    def load(cls, path: str) -> Dict[str, Any]:
        """Load Skill from file and return as dict."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Skill file not found: {path}")

        content = file_path.read_text(encoding="utf-8")
        return cls.parse(content, source_path=str(file_path))

    @classmethod
    def parse(cls, content: str, source_path: str = "") -> Dict[str, Any]:
        """Parse SKILL.md content and return as dict."""
        frontmatter, body = cls._split_frontmatter(content)

        if not frontmatter:
            raise ValueError("SKILL.md must have YAML frontmatter")

        meta = yaml.safe_load(frontmatter)
        if not isinstance(meta, dict):
            raise ValueError("Invalid YAML frontmatter")

        if "name" not in meta:
            raise ValueError("Skill must have 'name' field")
        if "description" not in meta:
            raise ValueError("Skill must have 'description' field")

        allowed_tools_declared = "allowed-tools" in meta
        allowed_tools = meta.get("allowed-tools", [])
        allowed_tools = cls._normalize_allowed_tools(allowed_tools)

        return {
            "name": meta["name"],
            "description": meta["description"],
            "content": body.strip(),
            "source_path": source_path,
            "allowed_tools": allowed_tools,
            "allowed_tools_declared": allowed_tools_declared,
            "tags": meta.get("tags", []),
        }

    @classmethod
    def _split_frontmatter(cls, content: str) -> Tuple[Optional[str], str]:
        """Split frontmatter and body."""
        match = cls.FRONTMATTER_PATTERN.match(content)
        if match:
            return match.group(1), match.group(2)
        return None, content

    @classmethod
    def to_skill_md(cls, skill_dict: Dict[str, Any]) -> str:
        """Convert skill dict to SKILL.md format."""
        frontmatter: dict = {
            "name": skill_dict["name"],
            "description": skill_dict.get("description", ""),
        }

        allowed_tools = skill_dict.get("allowed_tools")
        if allowed_tools is None:
            allowed_tools = skill_dict.get("allowed-tools")
        if allowed_tools is None:
            allowed_tools = []
        allowed_tools = cls._normalize_allowed_tools(allowed_tools)
        if allowed_tools or skill_dict.get("allowed_tools_declared"):
            frontmatter["allowed-tools"] = " ".join(allowed_tools)

        tags = skill_dict.get("tags") or []
        if tags:
            frontmatter["tags"] = tags

        yaml_str = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)

        return f"---\n{yaml_str}---\n\n{skill_dict.get('content', '')}"


_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _validation_issue(rule: str, message: str, field: str = "") -> Dict[str, str]:
    issue = {"rule": rule, "message": message}
    if field:
        issue["field"] = field
    return issue


def _parse_skill_for_validation(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        parsed = dict(data)
        parsed["content"] = parsed.get("content") or ""
    elif isinstance(data, str):
        frontmatter, body = SkillLoader._split_frontmatter(data)
        if not frontmatter:
            raise ValueError("SKILL.md must have YAML frontmatter")
        try:
            meta = yaml.safe_load(frontmatter)
        except Exception as exc:
            raise ValueError(f"Invalid YAML frontmatter: {exc}") from exc
        if not isinstance(meta, dict):
            raise ValueError("Invalid YAML frontmatter")
        parsed = dict(meta)
        parsed["content"] = body.strip()
    else:
        raise ValueError(f"Unsupported data type: {type(data)}")

    allowed_tools = parsed.get("allowed_tools")
    if not allowed_tools:
        allowed_tools = parsed.get("allowed-tools")
    if allowed_tools is not None:
        parsed["allowed_tools"] = (
            allowed_tools if isinstance(allowed_tools, list) else [allowed_tools]
        )
    parsed.pop("allowed-tools", None)

    tags = parsed.get("tags")
    if tags is not None and not isinstance(tags, list):
        parsed["tags"] = [tags]

    return parsed


def validate_skill_format(
    data: Any,
    *,
    strict: bool,
    skill_dir_name: Optional[str] = None,
    source_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a SKILL.md payload using the shared Agent Skills format rules."""
    errors: list[Dict[str, str]] = []
    warnings: list[Dict[str, str]] = []

    try:
        parsed = _parse_skill_for_validation(data)
    except Exception as exc:
        return {
            "valid": False,
            "strict": strict,
            "errors": [_validation_issue("yaml_format", str(exc), "data")],
            "warnings": [],
            "source_path": source_path or "",
        }

    name = parsed.get("name")
    description = parsed.get("description")
    content = parsed.get("content") or ""

    if not isinstance(name, str) or not name.strip():
        errors.append(_validation_issue("name_required", "name is required", "name"))
    if not isinstance(description, str) or not description.strip():
        errors.append(
            _validation_issue("description_required", "description is required", "description")
        )

    def add_mode_issue(rule: str, message: str, field: str) -> None:
        issue = _validation_issue(rule, message, field)
        if strict:
            errors.append(issue)
        else:
            warnings.append(issue)

    if isinstance(name, str) and name.strip():
        normalized_name = name.strip()
        normalized_dir_name = (skill_dir_name or "").strip()
        if normalized_dir_name and normalized_name != normalized_dir_name:
            add_mode_issue(
                "name_matches_directory",
                f"name '{normalized_name}' does not match directory name '{normalized_dir_name}'",
                "name",
            )
        if len(normalized_name) > 64:
            add_mode_issue("name_max_length", "name must not exceed 64 characters", "name")
        if not _SKILL_NAME_PATTERN.match(normalized_name):
            add_mode_issue(
                "name_allowed_characters",
                "name may only contain letters, numbers, underscores, and hyphens",
                "name",
            )

    if isinstance(description, str) and len(description) > 1024:
        add_mode_issue(
            "description_max_length",
            "description must not exceed 1024 characters",
            "description",
        )

    body_lines = len(content.splitlines())
    if strict and body_lines > 500:
        warnings.append(
            _validation_issue(
                "body_max_lines",
                "SKILL.md body exceeds 500 lines",
                "content",
            )
        )

    return {
        "valid": not errors,
        "strict": strict,
        "name": name or "",
        "description": description or "",
        "tags": parsed.get("tags") or [],
        "allowed_tools": parsed.get("allowed_tools") or [],
        "body_lines": body_lines,
        "source_path": source_path or "",
        "skill_dir_name": skill_dir_name or "",
        "errors": errors,
        "warnings": warnings,
    }
