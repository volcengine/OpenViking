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


def _strip_trailing_spaces(pattern: str) -> str:
    """Strip unescaped trailing spaces, mirroring git's ``trim_trailing_spaces``.

    A backslash escapes the character that follows it, so ``foo\\ `` keeps its
    trailing space (it names a file called ``foo ``) while ``foo  `` is
    trimmed to ``foo``.  A plain ``str.rstrip(" ")`` would also eat escaped
    spaces and leave a dangling backslash, which pathspec rejects.
    """
    last_space = None
    i = 0
    length = len(pattern)
    while i < length:
        char = pattern[i]
        if char == " ":
            if last_space is None:
                last_space = i
        else:
            if char == "\\":
                i += 1  # the next character is escaped, never a trailing space
                if i >= length:
                    return pattern  # lone trailing backslash: nothing to trim
            last_space = None
        i += 1
    return pattern if last_space is None else pattern[:last_space]


def _transform_gitignore_line(line: str, base_rel: str) -> str:
    """Transform a gitignore pattern line from a nested .gitignore to root-relative.

    Gitignore semantics (simplified)
    --------------------------------
    * Patterns without ``/`` match at any depth under the .gitignore's directory.
    * Patterns containing ``/`` (including a leading ``/``) are anchored to the
      .gitignore's directory.
    * A trailing ``/`` marks a **directory-only** pattern -- it must be stripped
      before the anchoring decision, then re-appended afterward.
      (Without this, ``build/`` would look like a path with a separator and be
      incorrectly anchored.)
    * Trailing spaces are ignored unless escaped with a backslash.
    * Negation prefix ``!`` is preserved through the transform.
    * Degenerate patterns that match nothing in git (``/``, ``//``,
      ``build///`` -- i.e. an empty body or an empty trailing path segment)
      are transformed to the empty string, which pathspec treats as a no-op.

    Parameters
    ----------
    line : str
        A single .gitignore line (may include leading/trailing whitespace).
    base_rel : str
        Root-relative path of the directory containing the .gitignore file,
        or ``""`` for the repository root.

    Returns
    -------
    str
        The transformed pattern line suitable for pathspec matching.
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

    # Strip trailing whitespace per gitignore spec: trailing spaces are ignored
    # unless escaped with a backslash.
    scoped = _strip_trailing_spaces(pattern)
    if not scoped:
        return ""  # whitespace-only line -> empty (no-op) pattern

    is_dir_only = scoped.endswith("/")
    # Strip the single directory-only marker before deciding anchoring, then
    # re-append it afterward.  Git strips exactly one trailing slash; if the
    # body still ends with '/' (e.g. "build///") the pattern has an empty
    # trailing segment and matches nothing in git, so emit a no-op.
    body = scoped[:-1] if is_dir_only else scoped
    if is_dir_only and (not body or body.endswith("/")):
        return ""  # "/", "//", "build///", ... match nothing in git

    if body.startswith("/"):
        # Leading '/' anchors the pattern to base_rel.
        body = body.lstrip("/")
        scoped = f"{base_rel}/{body}" if body else base_rel
    elif "/" in body:
        # A non-leading '/' means the pattern is anchored to base_rel.
        scoped = f"{base_rel}/{body}"
    else:
        # No '/' -- pattern matches at any depth under base_rel.
        scoped = f"{base_rel}/**/{body}" if body else base_rel

    if is_dir_only and scoped:
        scoped = f"{scoped}/"

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
