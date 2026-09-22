# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resolve package-level Skill results without changing other context types."""

from typing import Any, Dict, List, Sequence

from openviking.core.namespace import classify_uri
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.retrieve.types import ContextType, MatchedContext

LEVEL_SUFFIXES = ("/.abstract.md", "/.overview.md")


def skill_root_uri(uri: str) -> str:
    """Return the enclosing Skill root, including its full owning namespace."""
    if not isinstance(uri, str):
        return ""
    uri = uri.rstrip("/")
    for suffix in LEVEL_SUFFIXES:
        if uri.endswith(suffix):
            uri = uri[: -len(suffix)]
            break
    try:
        shape = classify_uri(uri)
    except (TypeError, ValueError):
        return ""
    index = shape.content_index
    if not shape.is_skill or index is None or len(shape.parts) <= index + 1:
        return ""
    if shape.parts[index + 1].startswith("."):
        # Internal update backups retain their original vectors, but are not
        # installed Skills. Hidden attachments inside a normal package remain valid.
        return ""
    return "viking://" + "/".join(shape.parts[: index + 2])


def candidate_key(candidate: Dict[str, Any]) -> Any:
    """Keep Skill directory levels distinct until their final scores are known."""
    uri = candidate.get("uri", "")
    if candidate.get("context_type") == ContextType.SKILL.value:
        return uri, candidate.get("level", 2)
    return uri


def pagination_key(record: Dict[str, Any]) -> tuple:
    """Identify a stored layer even when result merging keeps only its URI."""
    return record.get("context_type", ""), record.get("uri", ""), record.get("level", 2)


def merge_skill_results(matches: Sequence[MatchedContext]) -> List[MatchedContext]:
    """Merge Skill identities only; preserve other contexts and their relative order."""
    if not any(matched.context_type == ContextType.SKILL for matched in matches):
        return list(matches)
    skills: Dict[str, MatchedContext] = {}
    others = []
    for matched in matches:
        if matched.context_type != ContextType.SKILL:
            others.append(matched)
            continue
        root = skill_root_uri(matched.uri)
        if not root:
            continue
        previous = skills.get(root)
        if previous is None or _skill_rank(matched) < _skill_rank(previous):
            skills[root] = matched
    # Non-skill equal-score hits retain their original order.
    return sorted(
        [*others, *skills.values()],
        key=lambda item: (
            -item.score,
            item.uri if item.context_type == ContextType.SKILL else "",
        ),
    )


def _skill_rank(matched: MatchedContext) -> tuple:
    return -matched.score, matched.uri, matched.level


class SkillResultResolver:
    """Keep each readable package's highest-scored hit, without rewriting it."""

    def __init__(self, fs: Any, ctx: RequestContext):
        self.fs = fs
        self.ctx = ctx
        self._root_access: Dict[str, bool] = {}

    async def _root_readable(self, root: str) -> bool:
        if root not in self._root_access:
            try:
                # stat() checks read permission and existence; skip_count avoids
                # an unnecessary vector-store count query.
                info = await self.fs.stat(root, ctx=self.ctx, skip_count=True)
                self._root_access[root] = bool(info and info.get("isDir", False))
            except Exception as exc:
                if isinstance(exc, PermissionDeniedError) or is_not_found_error(exc):
                    self._root_access[root] = False
                else:
                    raise
        return self._root_access[root]

    async def resolve(self, matches: Sequence[MatchedContext]) -> List[MatchedContext]:
        resolved = []
        for matched in merge_skill_results(matches):
            if matched.context_type != ContextType.SKILL:
                resolved.append(matched)
                continue
            root = skill_root_uri(matched.uri)
            if await self._root_readable(root):
                resolved.append(matched)
        return resolved
