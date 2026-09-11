# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A globset-aligned glob matcher used to compute benchmark ground truth.

This implements the *safe subset* of glob semantics that OpenViking's local
``fs`` glob (Rust ``globset 0.4.18`` with ``literal_separator(true)``) and the
remote VikingDB ``PathGlob`` matcher agree on, per
``.vscode/agent_docs/open_viking/feat/f07_glob/design_v2.md`` and
``260826_self_test_conclusion.md``. The known-divergent edge cases (embedded
``**`` like ``foo**bar``, POSIX classes ``[[:digit:]]``, nested/empty braces,
``[]]``, escaped ``/`` inside classes, etc.) are intentionally *not* used by the
benchmark patterns, so this matcher only needs to cover the agreed subset:

  * ``*``  matches zero+ chars within a single path segment (never ``/``)
  * ``?``  matches exactly one char within a single path segment
  * ``**`` matches across segments (recursive)
  * ``[...]`` / ``[!...]`` / ``[^...]`` character classes (no ``/``)
  * ``{a,b}`` alternation (non-nested, no empty branch relied upon)

The matcher works on the relative path from the query root, applying the same
normalization the design specifies (drop empty and ``.`` segments), and treats a
hidden leaf (last segment starting with ``.``) as non-matching, mirroring the
remote ``path_glob`` hidden-leaf rule and OpenViking's ``show_hidden=False``.
"""

from __future__ import annotations

import re
from typing import List


def normalize_rel_path(path: str) -> str:
    """Drop empty and ``.`` segments, re-join with ``/`` (design section 3.2)."""
    return "/".join(seg for seg in path.split("/") if seg and seg != ".")


def _expand_alternations(pattern: str) -> List[str]:
    """Expand a single top-level ``{a,b,c}`` group into concrete alternatives.

    Only the first (leftmost) non-nested group is expanded; the result is fed
    back recursively so multiple groups in one pattern are all expanded. Nested
    braces are not part of the safe subset and are not expected here.
    """
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    end = pattern.find("}", start)
    if end == -1:
        return [pattern]

    prefix = pattern[:start]
    suffix = pattern[end + 1 :]
    branches = pattern[start + 1 : end].split(",")
    expanded: List[str] = []
    for branch in branches:
        expanded.extend(_expand_alternations(prefix + branch + suffix))
    return expanded


def _segment_token_to_regex(token: str) -> str:
    """Translate one path segment (no ``/``) of a glob into a regex fragment."""
    out: List[str] = []
    i = 0
    n = len(token)
    while i < n:
        ch = token[i]
        if ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            end = token.find("]", i + 1)
            if end == -1:
                out.append(re.escape(ch))
                i += 1
                continue
            body = token[i + 1 : end]
            if body.startswith(("!", "^")):
                body = "^" + body[1:]
            out.append("[" + body + "]")
            i = end + 1
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


def _pattern_to_regex(pattern: str) -> str:
    """Translate a ``/``-split glob (no alternation) into an anchored regex.

    ``**`` semantics depend on position (matching globset):
      * leading / middle ``**`` -> zero or more full path segments
      * trailing ``foo/**``      -> one or more segments (never ``foo`` itself)
      * lone ``**``              -> everything
    """
    segments = pattern.split("/")
    n = len(segments)
    parts: List[str] = []
    for idx, seg in enumerate(segments):
        last = idx == n - 1
        if seg == "**":
            if last:
                parts.append(".*" if idx == 0 else ".+")
            else:
                # Absorbs its own trailing separator: zero or more "segment/".
                parts.append("(?:[^/]+/)*")
                continue
        else:
            parts.append(_segment_token_to_regex(seg))
        if not last:
            parts.append("/")
    return "^" + "".join(parts) + "$"


class GlobMatcher:
    """Compiled matcher for one glob pattern over normalized relative paths."""

    def __init__(self, pattern: str):
        self.pattern = pattern
        normalized = normalize_rel_path(pattern)
        self._regexes = [
            re.compile(_pattern_to_regex(alt))
            for alt in _expand_alternations(normalized)
        ]

    def matches(self, rel_path: str) -> bool:
        norm = normalize_rel_path(rel_path)
        if not norm:
            return False
        # Hidden leaf files are filtered (show_hidden=False / path_glob rule).
        if norm.rsplit("/", 1)[-1].startswith("."):
            return False
        return any(rx.match(norm) for rx in self._regexes)


def expected_matches(pattern: str, rel_paths: List[str]) -> List[str]:
    """Return the sorted subset of ``rel_paths`` that match ``pattern``."""
    matcher = GlobMatcher(pattern)
    return sorted(rel for rel in rel_paths if matcher.matches(rel))
