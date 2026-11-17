# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Skill installation destinations and filesystem package boundaries."""

from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Literal

from openviking.core.namespace import AGENT_SKILLS_ROOT, canonical_user_root, classify_uri
from openviking.pyagfs.exceptions import AGFSNotADirectoryError
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import InvalidArgumentError


def validate_skill_parent_uri(uri: str, ctx: RequestContext) -> str:
    """Accept the caller's private or account-shared skills tree."""
    normalized = uri.rstrip("/")
    roots = (f"{canonical_user_root(ctx)}/skills", AGENT_SKILLS_ROOT)
    if any(normalized == root or normalized.startswith(root + "/") for root in roots):
        return normalized
    raise InvalidArgumentError(f"Skill destination must be under {' or '.join(roots)}")


@dataclass(frozen=True)
class SkillDestination:
    uri: str
    mode: Literal["parent", "parent_auto_create", "to"] = "parent_auto_create"

    def package_uri(self, name: str, ctx: RequestContext) -> str:
        if self.mode == "to":
            parent, _, target_name = self.uri.rpartition("/")
            validate_skill_parent_uri(parent, ctx)
            if target_name != name:
                raise InvalidArgumentError(
                    f"Skill target directory name '{target_name}' must match skill name '{name}'"
                )
            return self.uri
        return f"{validate_skill_parent_uri(self.uri, ctx)}/{name}"


class SkillPackageResolver:
    """Resolve the outermost SKILL.md owner, including packages below category directories.

    Marker metadata is inspected inside the request's namespace and account,
    independently of ACL grants. Callers authorize the resulting package before
    exposing it; an unreadable category must not hide a separately granted package.
    """

    def __init__(self, fs: Any, ctx: RequestContext):
        self.fs = fs
        self.ctx = replace(ctx, bypass_acl=True)
        self._definitions: dict[str, bool] = {}

    async def resolve(self, uri: str) -> str:
        shape = classify_uri(uri)
        index = shape.content_index
        if not shape.is_skill or index is None:
            return ""
        for depth in range(index + 2, len(shape.parts) + 1):
            if shape.parts[depth - 1].startswith("."):
                return ""
            root = "viking://" + "/".join(shape.parts[:depth])
            if root not in self._definitions:
                try:
                    info = await self.fs.stat(f"{root}/SKILL.md", ctx=self.ctx, skip_count=True)
                except Exception as exc:
                    if not is_not_found_error(exc) and not isinstance(exc, AGFSNotADirectoryError):
                        raise
                    self._definitions[root] = False
                else:
                    self._definitions[root] = not info["isDir"]
            if self._definitions[root]:
                return root
        return ""


async def list_skill_packages(
    viking_fs: Any, ctx: RequestContext, root: str, *, limit: int
) -> list[dict[str, Any]]:
    """List readable packages through category directories using VikingFS pages."""
    packages = SkillPackageResolver(viking_fs, ctx)
    pending = deque([root])
    result: list[dict[str, Any]] = []
    while pending and len(result) < limit:
        current = pending.popleft()
        offset = 0
        while len(result) < limit:
            try:
                entries = await viking_fs.ls(
                    current, ctx=ctx, output="agent", abs_limit=1024, node_limit=1000, offset=offset
                )
            except Exception as exc:
                if not is_not_found_error(exc):
                    raise
                break
            for entry in entries:
                if not entry.get("isDir") or entry.get("access") == "denied":
                    continue
                uri = entry["uri"]
                owner = await packages.resolve(uri)
                if owner == uri:
                    result.append(entry)
                    if len(result) == limit:
                        break
                elif not owner:
                    pending.append(uri)
            if len(entries) < 1000:
                break
            offset += len(entries)
    return result
