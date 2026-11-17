# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resolve package-level Skill results without changing other context types."""

from typing import Any, Dict, List, Sequence

from openviking.core.skill_package import SkillPackageResolver
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.retrieve.types import ContextType, MatchedContext

# Tail of the placeholder VikingFS returns for a directory whose .abstract.md
# has not been generated yet.
ABSTRACT_NOT_READY = "[Directory abstract is not ready]"


async def package_abstract(fs: Any, ctx: RequestContext, root: str) -> str:
    """Return a Skill package's own abstract, or "" when it has none yet."""
    try:
        text = str(await fs.abstract(root, ctx=ctx) or "").strip()
    except Exception:
        return ""
    if text.endswith(ABSTRACT_NOT_READY):
        return ""
    return text


def candidate_key(candidate: Dict[str, Any]) -> Any:
    """Keep Skill directory levels distinct until their final scores are known."""
    uri = candidate.get("uri", "")
    if candidate.get("context_type") == ContextType.SKILL.value:
        return uri, candidate.get("level", 2)
    return uri


def pagination_key(record: Dict[str, Any]) -> tuple:
    """Identify a stored layer even when result merging keeps only its URI."""
    return record.get("context_type", ""), record.get("uri", ""), record.get("level", 2)


def _skill_rank(matched: MatchedContext) -> tuple:
    return -matched.score, matched.uri, matched.level


class SkillResultResolver:
    """Keep each readable package's highest-scored hit, without rewriting it."""

    def __init__(self, fs: Any, ctx: RequestContext):
        self.fs = fs
        self.ctx = ctx
        self._root_access: Dict[str, bool] = {}
        self.packages = SkillPackageResolver(fs, ctx)

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
        if not any(matched.context_type == ContextType.SKILL for matched in matches):
            return list(matches)
        skills: Dict[str, MatchedContext] = {}
        others = []
        for matched in matches:
            if matched.context_type != ContextType.SKILL:
                others.append(matched)
                continue
            root = await self.packages.resolve(matched.uri)
            if not root or not await self._root_readable(root):
                continue
            previous = skills.get(root)
            if previous is None or _skill_rank(matched) < _skill_rank(previous):
                skills[root] = matched
        return sorted(
            [*others, *skills.values()],
            key=lambda item: (
                -item.score,
                item.uri if item.context_type == ContextType.SKILL else "",
            ),
        )
