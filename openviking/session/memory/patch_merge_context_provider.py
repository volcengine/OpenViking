# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Context provider for merging structured memory-file patches via ExtractLoop."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryFile, MemoryTypeSchema
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.utils.language import resolve_output_language_from_text

_SYSTEM_HIDDEN_FIELDS = {
    "source_extraction_id",
    "source_extraction_ids",
    "last_update_trace_id",
}
_MAX_EXTRA_CANDIDATE_FILES = 10
_PATCH_METADATA_KEYS = ("confidence",)


@dataclass(slots=True)
class PatchMergePatch:
    """A before/after memory-file patch rendered as field-level line diffs."""

    before_file: MemoryFile | None
    after_file: MemoryFile
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def target_uri(self) -> str | None:
        return self.after_file.uri or (
            self.before_file.uri if self.before_file is not None else None
        )

    @property
    def memory_type(self) -> str:
        return str(
            self.after_file.memory_type
            or (self.before_file.memory_type if self.before_file is not None else "")
            or self.after_file.extra_fields.get("memory_type")
            or (
                self.before_file.extra_fields.get("memory_type")
                if self.before_file is not None
                else ""
            )
        )

    @property
    def target_name(self) -> str:
        fields = self.after_file.extra_fields or {}
        memory_type = self.memory_type
        type_specific_key = f"{str(memory_type).rstrip('s')}_name"
        name = (
            fields.get(type_specific_key)
            or fields.get("experience_name")  # backward compat
            or fields.get("name")
        )
        if name:
            return str(name)
        uri = self.target_uri
        if uri:
            # For SKILL.md-style paths, use the directory name.
            if uri.endswith("/SKILL.md"):
                parts = uri.rstrip("/").split("/")
                if len(parts) >= 2:
                    return parts[-2]
            return uri.rstrip("/").split("/")[-1].removesuffix(".md")
        return "unknown"


def _resolve_patch_output_language(patches: list[PatchMergePatch]) -> str:
    return resolve_output_language_from_text(_patch_language_text(patches), fallback_language="en")


def _patch_language_text(patches: list[PatchMergePatch]) -> str:
    parts: list[str] = []
    for patch in patches:
        parts.extend(_memory_file_language_text(patch.after_file))
    return "\n".join(part for part in parts if part)


def _memory_file_language_text(file: MemoryFile) -> list[str]:
    parts: list[str] = []
    for key, value in (file.extra_fields or {}).items():
        if key in _SYSTEM_HIDDEN_FIELDS or key in {"memory_type", "version"}:
            continue
        parts.extend(_string_values(value))
    if file.content:
        parts.append(file.content)
    return parts


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for entry in value for item in _string_values(entry)]
    if isinstance(value, dict):
        return [item for entry in value.values() for item in _string_values(entry)]
    return []


class PatchMergeContextProvider(SessionExtractContextProvider):
    """Provide original memory files and structured field diffs to ExtractLoop.

    The provider is generic: callers decide which patches to pass in; this class
    only exposes original files as prefetched read results and memory-file field
    diffs as compact merge context.
    """

    def __init__(
        self,
        *,
        memory_type: str,
        patches: list[PatchMergePatch],
        required_file_uris: list[str] | None = None,
        output_language: str | None = None,
    ):
        super().__init__(messages=[])
        self.memory_type = memory_type
        self.required_file_uris = list(required_file_uris or [])
        self.patches = list(patches)
        self._output_language = output_language or _resolve_patch_output_language(self.patches)

    def instruction(self) -> str:
        output_language = self._output_language
        return f"""You are a memory patch merge agent.

You are given original memory files and structured memory-file field diffs. Merge them by producing final memory operations that follow the provided JSON schema.

Do not call tools. Output JSON only.

All memory content must be written in {output_language}.

Reconcile independent extraction patch proposals: merge duplicate/overlapping
memories into one canonical file patch, and keep distinct memories separate.
Normalize URI/path variants for directory/filename fields. Treat path segment
fields as stable schema identifiers, not free-form labels. Reuse existing
equivalent directories across singular/plural, synonym, or language/script
variants. For new segments, use singular snake_case for English and one concise
canonical term for Chinese; e.g. book not books, 书籍 not 书/图书. If a loser URI
is an existing file, put it in delete_ids; if it is only a new proposal, omit it.
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
                "content": _render_field_diff_patches(self.patches),
            }
        )
        return messages

    async def _resolve_prefetch_file_uris(self) -> list[str]:
        """Resolve required files plus semantic-search candidates for this merge."""

        required_uris = _dedupe_uris(self.required_file_uris)
        max_extra_candidate_files = min(_MAX_EXTRA_CANDIDATE_FILES, max(5, len(required_uris)))
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


def _render_field_diff_patches(patches: list[PatchMergePatch]) -> str:
    if not patches:
        return "# Memory File Patches\n\nNo patches provided."
    rendered = [
        _render_one_field_diff_patch(index, patch) for index, patch in enumerate(patches, start=1)
    ]
    return "# Memory File Patches\n\n" + "\n\n".join(rendered).rstrip()


def _render_one_field_diff_patch(index: int, patch: PatchMergePatch) -> str:
    lines = [f"Patch {index}"]
    if patch.metadata:
        compact_metadata = _compact_patch_metadata(patch.metadata)
        if compact_metadata:
            lines.append(f"  meta: {_compact_value(compact_metadata)}")
    field_diffs = _field_diffs(patch.before_file, patch.after_file)
    if not field_diffs:
        lines.append("  (no changes)")
        return "\n".join(lines)
    for field_name, diff in field_diffs:
        lines.append(f"  {field_name}:")
        # Strip unified diff headers (---, +++) but keep @@ hunk markers and content
        for diff_line in diff.splitlines():
            if diff_line.startswith("---") or diff_line.startswith("+++"):
                continue
            lines.append(f"    {diff_line}")
    return "\n".join(lines)


def _field_diffs(before_file: MemoryFile | None, after_file: MemoryFile) -> list[tuple[str, str]]:
    before_fields = _memory_file_fields(before_file) if before_file is not None else {}
    after_fields = _memory_file_fields(after_file)
    diffs: list[tuple[str, str]] = []
    for field_name in sorted(set(before_fields) | set(after_fields)):
        before_value = before_fields.get(field_name)
        after_value = after_fields.get(field_name)
        if before_value == after_value:
            continue
        diff = _value_unified_diff(
            field_name=field_name,
            before_value=before_value,
            after_value=after_value,
        )
        if diff.strip():
            diffs.append((field_name, diff))
    return diffs


def _memory_file_fields(file: MemoryFile) -> dict[str, Any]:
    fields = dict(file.extra_fields or {})
    for hidden_field in _SYSTEM_HIDDEN_FIELDS:
        fields.pop(hidden_field, None)
    if file.memory_type is not None:
        fields["memory_type"] = file.memory_type
    if file.content:
        fields["content"] = file.content
    if file.links:
        fields["links"] = file.links
    if file.backlinks:
        fields["backlinks"] = file.backlinks
    return fields


def _value_unified_diff(*, field_name: str, before_value: Any, after_value: Any) -> str:
    before_lines = _value_lines(before_value)
    after_lines = _value_lines(after_value)
    diff_lines = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"{field_name}.before",
        tofile=f"{field_name}.after",
        n=1,
        lineterm="",
    )
    return "\n".join(diff_lines)


def _value_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return value.splitlines()
    import json

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).splitlines()


def _compact_value(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _hide_system_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _hide_system_fields(item)
            for key, item in value.items()
            if key not in _SYSTEM_HIDDEN_FIELDS
        }
    if isinstance(value, list):
        return [_hide_system_fields(item) for item in value]
    return value


def _compact_patch_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep only metadata that helps reconcile patch proposals.

    Full gradient metadata can contain large duplicated fields (links, uris, and
    memory_fields). The patch body already renders the target URI and field
    changes, while source links are merged outside the LLM response. Keep only
    decision signals that help the merge model rank or reconcile proposals.
    """

    cleaned = _hide_system_fields(dict(metadata or {}))
    result = {
        key: cleaned[key]
        for key in _PATCH_METADATA_KEYS
        if key in cleaned and _metadata_value_is_useful(cleaned[key])
    }

    return result


def _metadata_value_is_useful(value: Any) -> bool:
    if value is None:
        return False
    if value == "":
        return False
    if isinstance(value, (list, dict, tuple, set)) and not value:
        return False
    return True


def _dedupe_uris(uris: list[str] | None) -> list[str]:
    return list(dict.fromkeys(uri for uri in (uris or []) if uri))


def _build_patch_search_query(patches: list[PatchMergePatch]) -> str:
    parts: list[str] = []
    for patch in patches:
        if patch.target_name:
            parts.append(str(patch.target_name))
        if patch.target_uri:
            parts.append(str(patch.target_uri).rstrip("/").split("/")[-1].removesuffix(".md"))
        content = str(patch.after_file.content or "")
        if content:
            parts.append(_truncate_query_text(content, 1200))
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
