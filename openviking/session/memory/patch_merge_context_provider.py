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
        original_file_uris: list[str],
        patches: list[PatchMergePatch],
    ):
        super().__init__(messages=[])
        self.memory_type = memory_type
        self.original_file_uris = list(original_file_uris)
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
        for uri in self.original_file_uris:
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
