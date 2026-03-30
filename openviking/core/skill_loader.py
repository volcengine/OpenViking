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

        return {
            "name": meta["name"],
            "description": meta["description"],
            "content": body.strip(),
            "source_path": source_path,
            "allowed_tools": meta.get("allowed-tools", []),
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

        yaml_str = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)

        return f"---\n{yaml_str}---\n\n{skill_dict.get('content', '')}"
