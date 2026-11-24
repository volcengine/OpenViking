"""Compile output paths, Markdown validation and pending write operations."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import yaml

from openviking.utils.path_safety import sanitize_relative_viking_path
from vikingbot.compile.models import COMPILE_STAGING_ROOT

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_FRONTMATTER_START_RE = re.compile(rb"\A---[ \t]*\r?\n")
_FRONTMATTER_END_RE = re.compile(rb"\r?\n---[ \t]*(?:\r?\n|\Z)")
_OKF_TYPE_DECLARATION_RE = re.compile(rb"""(?m)^[ \t]*(?:type|["']type["'])[ \t]*:""")
_RESERVED_FILENAMES = frozenset({".abstract.md", ".overview.md", ".relations.json", ".source.json"})


@dataclass(slots=True)
class RenderedBundle:
    operations: list[dict[str, Any]] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    wiki_uris: list[str] = field(default_factory=list)
    link_count: int = 0


def _format_yaml_frontmatter_error(exc: yaml.YAMLError) -> str:
    problem = getattr(exc, "problem", None)
    context = getattr(exc, "context", None)
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if context and problem:
        reason = f"{context}: {problem}"
    else:
        reason = problem or context or str(exc).splitlines()[0].strip()
    if mark is not None:
        # match.group(1) starts on line 2 of the Markdown file (after opening "---").
        location = f"line {mark.line + 2}, column {mark.column + 1}"
        return f"{reason} ({location})" if reason else location
    return reason


def validate_declared_okf_markdown(path: str, content: bytes) -> str | None:
    """Validate a Markdown artifact and return its declared OKF type, if any."""
    if not path.casefold().endswith(".md"):
        return
    opening = _FRONTMATTER_START_RE.match(content)
    if opening is None:
        return

    remainder = content[opening.end() :]
    closing = _FRONTMATTER_END_RE.search(remainder)
    raw_frontmatter = remainder[: closing.start()] if closing else remainder
    raw_declares_type = _OKF_TYPE_DECLARATION_RE.search(raw_frontmatter) is not None

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        if raw_declares_type:
            raise ValueError(f'OKF Markdown file "{path}" must be UTF-8') from exc
        return

    match = _FRONTMATTER_RE.match(text)
    if match is None:
        if raw_declares_type:
            raise ValueError(f'OKF Markdown file "{path}" has unterminated YAML frontmatter')
        return
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        if raw_declares_type:
            detail = _format_yaml_frontmatter_error(exc)
            suffix = f": {detail}" if detail else ""
            raise ValueError(
                f'OKF Markdown file "{path}" has invalid YAML frontmatter{suffix}'
            ) from exc
        return
    if not isinstance(frontmatter, dict):
        if raw_declares_type:
            raise ValueError(f'OKF Markdown file "{path}" frontmatter must be a YAML object')
        return
    if "type" not in frontmatter:
        return
    if not isinstance(frontmatter["type"], str) or not frontmatter["type"].strip():
        raise ValueError(
            f'OKF Markdown file "{path}" frontmatter field "type" must be a non-empty string'
        )
    return frontmatter["type"].strip()


def validate_relative_file_path(path: str) -> str:
    """Validate an output-relative path, rejecting metadata and internal staging directories."""
    relative = sanitize_relative_viking_path(path).strip("/")
    segments = relative.split("/")
    if (
        not relative
        or any(not segment or segment in {".", ".."} for segment in segments)
        or any(segment.startswith(".") for segment in segments)
    ):
        raise ValueError(f"invalid output file path: {path}")
    if segments[-1].lower() in _RESERVED_FILENAMES:
        raise ValueError(f"reserved output file path: {path}")
    if COMPILE_STAGING_ROOT.casefold() in (segment.casefold() for segment in segments):
        raise ValueError(f"reserved staging directory in output path: {path}")
    return relative


def output_path_key(path: str) -> str:
    """Identify case and Unicode aliases before writing to portable storage."""
    return unicodedata.normalize("NFC", validate_relative_file_path(path)).casefold()


__all__ = [
    "RenderedBundle",
    "output_path_key",
    "validate_declared_okf_markdown",
    "validate_relative_file_path",
]
