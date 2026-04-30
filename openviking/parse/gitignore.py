# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Gitignore-aware matching helpers for directory scanning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from pathspec import GitIgnoreSpec


def _normalize_rel_path(rel_path: str) -> str:
    return rel_path.replace("\\", "/")


def _is_comment_line(line: str) -> bool:
    return line.startswith("#")


def _transform_gitignore_line(line: str, base_rel: str) -> str:
    """
    Transform a gitignore pattern line from a nested .gitignore to root-relative.

    - Patterns without '/' should match anywhere under base_rel.
    - Patterns with '/' are scoped to base_rel.
    - Leading '/' anchors to base_rel.
    """
    raw = line.rstrip("\n")
    if not raw or _is_comment_line(raw):
        return raw

    negated = False
    pattern = raw
    if raw.startswith("!"):
        negated = True
        pattern = raw[1:]

    if not base_rel:
        return raw

    scoped = pattern
    if scoped.startswith("/"):
        scoped = scoped.lstrip("/")
        scoped = f"{base_rel}/{scoped}" if scoped else base_rel
    elif "/" in scoped:
        scoped = f"{base_rel}/{scoped}"
    else:
        scoped = f"{base_rel}/**/{scoped}" if scoped else base_rel

    return f"!{scoped}" if negated else scoped


@dataclass
class GitignoreMatcher:
    """
    Helper class for matching files and directories against gitignore specs.
    It maintains an in-memory cache of gitignore specs per directory for faster matching.
    """

    root: Path
    _spec_cache: Dict[Path, Optional[GitIgnoreSpec]]

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._spec_cache = {}

    def spec_for_dir(self, dir_path: Path) -> Optional[GitIgnoreSpec]:
        """
        Resolve the gitignore spec for the given directory (including parent specs recursively).
        """
        dir_path = dir_path.resolve()
        if dir_path in self._spec_cache:
            return self._spec_cache[dir_path]

        parent_spec = None
        # try to resolve all parent specs recursively
        if dir_path != self.root:
            parent_spec = self.spec_for_dir(dir_path.parent)

        local_spec = self._load_local_spec(dir_path)

        if parent_spec and local_spec:
            spec = parent_spec + local_spec
        else:
            spec = local_spec or parent_spec

        self._spec_cache[dir_path] = spec
        return spec

    def is_ignored_file(self, file_path: Path, spec: Optional[GitIgnoreSpec]) -> bool:
        if not spec:
            return False

        rel_path = self._rel_path(file_path)
        return spec.match_file(rel_path)

    def is_ignored_dir(self, dir_path: Path, spec: Optional[GitIgnoreSpec]) -> bool:
        if not spec:
            return False

        rel_path = self._rel_path(dir_path)
        return spec.match_file(f"{rel_path}/")

    def _rel_path(self, path: Path) -> str:
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            rel = path

        rel_norm = _normalize_rel_path(str(rel))
        return "" if rel_norm == "." else rel_norm

    def _load_local_spec(self, dir_path: Path) -> Optional[GitIgnoreSpec]:
        """
        Load the local .gitignore spec in the given directory.
        """
        gitignore_path = dir_path / ".gitignore"
        if not gitignore_path.is_file():
            return None
        try:
            content = gitignore_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

        lines = content.splitlines()
        if not lines:
            return None

        base_rel = self._rel_path(dir_path)
        transformed = self._transform_lines(lines, base_rel)
        if not transformed:
            return None

        return GitIgnoreSpec.from_lines(transformed)

    def _transform_lines(self, lines: Iterable[str], base_rel: str) -> List[str]:
        if not base_rel:
            return list(lines)

        return [_transform_gitignore_line(line, base_rel) for line in lines]
