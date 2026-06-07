# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Context provider for merging memory patches via ExtractLoop."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)


@dataclass(slots=True)
class PatchMergePatch:
    """A generic before/after memory patch to expose as unified diff context."""

    target_name: str
    target_uri: str | None
    before_content: str | None
    after_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class PatchMergeContextProvider(SessionExtractContextProvider):
    """Provide original memory files and unified patches to ExtractLoop.

    The provider is intentionally generic and does not implement merge rules.
    Callers decide grouping/filtering/sorting before constructing it; this class
    only exposes original files as prefetched read results and patch proposals as
    unified diff text.
    """

    def __init__(
        self,
        *,
        memory_type: str,
        patches: list[PatchMergePatch],
        required_file_uris: list[str] | None = None,
        original_file_uris: list[str] | None = None,
    ):
        super().__init__(messages=[])
        self.memory_type = memory_type
        self.required_file_uris = list(required_file_uris or original_file_uris or [])
        self.patches = list(patches)

    def instruction(self) -> str:
        output_language = self._output_language
        return f"""You are a memory patch merge agent.

You are given original memory files and unified patches. Merge them by producing final memory operations that follow the provided JSON schema.

Do not call tools. Output JSON only.

All memory content must be written in {output_language}.
"""

    def get_tools(self) -> list[str]:
        return []

    def get_memory_schemas(self, ctx: RequestContext) -> list[MemoryTypeSchema]:
        del ctx
        schema = self._get_registry().get(self.memory_type)
        if schema is None or not schema.enabled:
            raise ValueError(f"Memory schema not found or disabled: {self.memory_type}")
        return [schema]

    async def prefetch(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        call_id = 0
        file_uris = await self._resolve_prefetch_file_uris()
        for uri in file_uris:
            call_id = await self._append_structured_read_result(
                messages,
                call_id,
                uri,
            )

        messages.append(
            {
                "role": "user",
                "content": _render_unified_patches(self.patches),
            }
        )
        return messages

    async def _resolve_prefetch_file_uris(self) -> list[str]:
        """Resolve required files plus semantic-search candidates for this merge."""

        required_uris = _dedupe_uris(self.required_file_uris)
        max_extra_candidate_files = max(5, len(required_uris))
        search_limit = max_extra_candidate_files * 2
        candidate_uris = await self._search_candidate_file_uris(limit=search_limit)
        extra_uris: list[str] = []
        required_set = set(required_uris)
        for uri in candidate_uris:
            if not uri or uri in required_set or uri in extra_uris:
                continue
            extra_uris.append(uri)
            if len(extra_uris) >= max_extra_candidate_files:
                break
        return [*required_uris, *extra_uris]

    async def _search_candidate_file_uris(self, *, limit: int) -> list[str]:
        schema = self._get_registry().get(self.memory_type)
        if schema is None or not schema.directory:
            return []
        search_dirs = self._render_search_directories(schema)
        if not search_dirs:
            return []
        query = _build_patch_search_query(self.patches)
        if not query:
            return []
        return await self.search_files(query=query, search_uris=search_dirs, limit=limit)

    def _render_search_directories(self, schema: MemoryTypeSchema) -> list[str]:
        if self._isolation_handler:
            return list(dict.fromkeys(self._isolation_handler.render_schema_directories(schema)))

        ctx = self._ctx
        user = getattr(ctx, "user", None)
        user_id = (
            getattr(ctx, "user_id", None)
            or getattr(user, "user_id", None)
            or _infer_user_space_from_uris(self.required_file_uris)
            or _infer_user_space_from_uris([patch.target_uri for patch in self.patches])
        )
        if not user_id:
            return []

        from openviking.session.memory.utils.uri import render_template

        return [render_template(schema.directory, {"user_space": user_id})]


def _render_unified_patches(patches: list[PatchMergePatch]) -> str:
    if not patches:
        return "```diff\n# No patches provided.\n```"
    rendered = [_to_unified_patch(patch).rstrip() for patch in patches]
    return "```diff\n" + "\n\n".join(rendered).rstrip() + "\n```"


def _to_unified_patch(patch: PatchMergePatch) -> str:
    target = _patch_target_path(patch)
    before_lines = [] if patch.before_content is None else patch.before_content.splitlines()
    after_lines = patch.after_content.splitlines()
    fromfile = "/dev/null" if patch.before_content is None else f"a/{target}"
    tofile = f"b/{target}"
    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    diff_git = f"diff --git {fromfile} {tofile}"
    if not diff_lines:
        return diff_git
    return "\n".join([diff_git, *diff_lines])


def _patch_target_path(patch: PatchMergePatch) -> str:
    target = patch.target_uri or patch.target_name
    target = str(target).strip().replace("\n", " ").replace("\r", " ")
    return target or "unknown"


def _dedupe_uris(uris: list[str] | None) -> list[str]:
    return list(dict.fromkeys(uri for uri in (uris or []) if uri))


def _build_patch_search_query(patches: list[PatchMergePatch]) -> str:
    parts: list[str] = []
    for patch in patches:
        if patch.target_name:
            parts.append(str(patch.target_name))
        if patch.target_uri:
            parts.append(str(patch.target_uri).rstrip("/").split("/")[-1].removesuffix(".md"))
        if patch.after_content:
            parts.append(_truncate_query_text(patch.after_content, 1200))
    return _truncate_query_text("\n\n".join(parts), 5000)


def _truncate_query_text(text: Any, max_chars: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _infer_user_space_from_uris(uris: list[str | None]) -> str | None:
    for uri in uris:
        if not uri:
            continue
        prefix = "viking://user/"
        if not uri.startswith(prefix):
            continue
        rest = uri.removeprefix(prefix)
        user_space = rest.split("/", 1)[0]
        if user_space and user_space != "memories":
            return user_space
    return None
