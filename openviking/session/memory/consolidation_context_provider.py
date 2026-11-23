# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Consolidate selected memory types in one isolation space and extraction loop."""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from contextlib import nullcontext
from typing import Any, Dict, List, Optional

from openviking.core.namespace import classify_uri
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.models.vlm.base import VLMBase
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.merge_policy import MEMORY_MERGE_POLICY
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.tools import add_tool_call_pair_to_messages, get_tool
from openviking.session.memory.utils.language import resolve_output_language_from_text
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry.tracer import otel_trace
from openviking_cli.exceptions import NotFoundError

_RESERVED_NAMES = {".overview.md", ".abstract.md"}
_LANGUAGE_SAMPLE_FILES = 3
_LANGUAGE_SAMPLE_BYTES = 4096
_MEMORY_FIELDS_START = re.compile(r"<!--\s*MEMORY_FIELDS\b")


class ConsolidationExtractContextProvider(SessionExtractContextProvider):
    """Reorganize selected memory types using a recursive inventory and read/search tools."""

    is_consolidation = True
    include_tool_parts_in_conversation = False
    split_long_text_messages_for_extraction = False

    def __init__(
        self,
        *,
        memory_types: List[str],
        target_directory: Optional[str] = None,
        instruction: Optional[str] = None,
        memory_registry: Optional[MemoryTypeRegistry] = None,
        output_language: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
        viking_fs: Optional[VikingFS] = None,
        output_language_override: str | None = None,
    ):
        super().__init__(
            messages=[],
            ctx=ctx,
            viking_fs=viking_fs,
            memory_registry=memory_registry,
            output_language_override=output_language_override,
        )
        self.memory_types = list(dict.fromkeys(memory_types))
        # Canonical `--to` directory. When provided, prefetch lists exactly this
        # directory instead of re-deriving it from ctx.user_id, which can be
        # empty/wrong and collapse the URI (viking://user//memories/...).
        self.target_directory = target_directory.rstrip("/") if target_directory else None
        self._target_listing = ""
        self._instruction_text = (instruction or "").strip()
        self._output_language = resolve_output_language_from_text(
            "",
            output_language_override=self._output_language_override,
            fallback_language=output_language or "en",
        )
        self._language_resolved = bool(output_language or self._output_language_override.strip())

    def bind_isolation_handler(self, isolation_handler: MemoryIsolationHandler) -> None:
        self._isolation_handler = isolation_handler

    def get_memory_schemas(self, ctx: RequestContext) -> List[MemoryTypeSchema]:
        del ctx
        schemas = []
        for memory_type in self.memory_types:
            schema = self._get_registry().get(memory_type)
            if schema is None or not schema.enabled:
                raise ValueError(
                    "use ov compile memory to update all files, rollback with ov snapshot restore, "
                    "details in docs/en/context-compilation/06-memory-consolidation.md"
                )
            schemas.append(schema)
        return schemas

    def get_tools(self) -> List[str]:
        # Compile is an offline, user-initiated task, so favor an agentic loop:
        # give the model ls/search/read and let it explore the target directory
        # (including subdirectories) itself instead of relying on a one-shot,
        # possibly-incomplete prefetch. ExtractLoop's write-before-read guard
        # still forces a read of any file it intends to modify.
        return ["ls", "search", "read"]

    # ── Instruction ──

    def instruction(self) -> str:
        output_language = self._output_language
        extra = ""
        if self._instruction_text:
            extra = f"\n\n## User Instruction\n{self._instruction_text}\n"
        memory_types = ", ".join(self.memory_types)
        target = self.target_directory or "the selected memory directories"
        return f"""You are a memory consolidation agent. Reorganize the existing \
memories of these types: {memory_types}, under {target} in place so the final collection is \
clean, non-redundant, and conforms to each memory type's own schema.

## Workflow
1. Start with the provided `ls` result (`recursive=true`) for the target directory,
   which lists files as `relative/path size`.
   The listing may be incomplete even without a truncation warning. To discover
   omitted files, call `ls` with `recursive=false` once on the target, then use
   the same shallow listing to walk child directories (entries ending in `/`).
2. `read` the full content of every file you may merge, split, edit, or delete.
   You MUST read a file before changing it. Do not repeatedly `ls` the same path.
3. Output ONLY the memory operations (no extra text) once you have gathered enough
   context.

## What to do
- Merge only memories that share the same identity under the schema.
- Split files that mix multiple identities.
- Deduplicate repeated or paraphrased facts, preserving every distinct atomic fact.
- Normalize fields to each memory's own schema; do not invent new facts.
- Only modify the selected memory types under the target. Do not move memories into
  another user's or peer's space.
- Existing add_only memories may be deleted. A deletion replacement may be an existing
  memory or a new memory of another selected type when it preserves the relevant facts.
- Original conversation messages are unavailable. Do not create memory types whose
  rendering requires those messages; the output contract marks these types.

## Critical
- Only ls/search/read are available for exploration - there is no write tool; all
  changes are expressed as the final operations.
- Do NOT create memories from nothing; only reorganize what already exists.
- The system generates URIs from memory_type and fields; just provide correct fields.

## Target Output Language
All memory content MUST be written in {output_language}.
{extra}
{MEMORY_MERGE_POLICY}
"""

    async def review_coverage(self, vlm: VLMBase, tools_used: list[dict]) -> str | None:
        """Grade exploration inside the compile target. Return feedback to continue, or None
        to accept. Invalid replies and provider errors propagate to the fail-open caller.
        """
        target = self.target_directory
        assert target is not None
        prefix = target + "/"
        reads = dict.fromkeys(self.memory_types, 0)
        for uri, memory in self.read_file_contents.items():
            if uri.startswith(prefix) and not uri.rsplit("/", 1)[-1].startswith("."):
                kind = memory.memory_type
                if kind is None:
                    location = classify_uri(uri)
                    if location.content_index is not None:
                        kind = location.parts[location.content_index + 1].removesuffix(".md")
                if kind in reads:
                    reads[kind] += 1
        listed = set()
        for call in tools_used:
            if call["tool_name"] == "ls" and isinstance(call["result"], str):
                uri = validate_request_viking_uri(call["params"]["uri"], self._ctx).rstrip("/")
                if uri == target or uri.startswith(prefix):
                    listed.add(uri)
        # The capped inventory is evidence of visible candidates, not a total file count.
        visible = Counter()
        for line in self._target_listing.splitlines():
            path, _, _size = line.rpartition(" ")
            if path.endswith(".md"):
                branch, separator, _ = path.partition("/")
                visible[branch if separator else "."] += 1
        evidence = {
            "target": target,
            "unique_reads_by_type": reads,
            "listed": sorted(listed),
            "visible_files_by_branch_in_initial_listing": visible,
        }
        prompt = (
            "Review whether exploration of the target is sufficient to finish consolidation. "
            "Directory listings only guide navigation. Consider coverage of types and branches "
            "inside this target. Zero changes is valid. If more reading is warranted, give "
            "a concrete next step within the target. Return only JSON with a boolean pass "
            "and a string reason. Coverage evidence: " + json.dumps(evidence, ensure_ascii=False)
        )
        # Empty tools preserves finish_reason without exposing tools to the grader.
        # The backend can emit prompt/response trace events. Only the verdict is observable.
        with otel_trace.use_span(otel_trace.INVALID_SPAN) if otel_trace else nullcontext():
            response = await asyncio.wait_for(
                vlm.get_completion_async(prompt=prompt, tools=[], thinking=False, max_tokens=256),
                timeout=30,
            )
        if getattr(response, "finish_reason", "stop") != "stop":
            raise ValueError("Incomplete coverage review")
        verdict = json.loads(response if isinstance(response, str) else response.content)
        if (
            not isinstance(verdict, dict)
            or not isinstance(verdict.get("pass"), bool)
            or not isinstance(verdict.get("reason"), str)
        ):
            raise ValueError("Invalid coverage verdict")
        return None if verdict["pass"] else verdict["reason"]

    # ── Prefetch: seed the loop with a recursive file listing, not content ──

    async def prefetch(self) -> List[Dict[str, Any]]:
        directories = self._target_directories()
        prefetch_messages: List[Dict[str, Any]] = []
        call_id = 0
        # Seed exploration with capped path-and-size listings without fetching content.
        for directory in directories:
            listing = await self._recursive_listing(directory)
            if directory == self.target_directory and isinstance(listing, str):
                self._target_listing = listing
            add_tool_call_pair_to_messages(
                messages=prefetch_messages,
                call_id=call_id,
                tool_name="ls",
                params={"uri": directory, "recursive": True},
                result=listing,
            )
            call_id += 1

        prefetch_messages.append(
            {
                "role": "user",
                "content": (
                    f"Above is a recursive listing for memory types {', '.join(self.memory_types)} "
                    "(relative paths, no content). read the full content of "
                    "every file you intend to merge, split, edit, or delete (you MUST "
                    "read a file before changing it); use search only if you need more "
                    "context. Then output ALL consolidation operations in a single "
                    "response. If nothing needs reorganizing, return an empty operation set. "
                    "The listing may be incomplete. Follow the Workflow above to discover "
                    "omitted files."
                ),
            }
        )
        return prefetch_messages

    async def _recursive_listing(self, directory: str) -> Any:
        """Return a recursive 'relative/path size' listing via the shared ls tool."""
        tool = get_tool("ls")
        if tool is None:
            from openviking.session.memory.tools import MemoryLsTool

            tool = MemoryLsTool()
        return await tool.execute(self.create_tool_context(), uri=directory, recursive=True)

    def _target_directories(self) -> List[str]:
        if self.target_directory:
            return [self.target_directory]
        if self._isolation_handler is not None:
            return list(
                dict.fromkeys(
                    directory
                    for schema in self.get_memory_schemas(self._ctx)
                    for directory in self._isolation_handler.render_schema_directories(schema)
                )
            )
        return []

    async def prepare_extraction_messages(self) -> None:
        if self._language_resolved:
            return
        samples: list[str] = []
        sampled_uris: set[str] = set()
        for directory in self._target_directories():
            if len(sampled_uris) >= _LANGUAGE_SAMPLE_FILES:
                break
            listing = await self._viking_fs.glob(
                "**/*.md",
                uri=directory,
                node_limit=_LANGUAGE_SAMPLE_FILES - len(sampled_uris),
                extra_fields=[],
                ctx=self._ctx,
            )
            for entry in listing["matches"]:
                uri = entry["uri"]
                if (
                    entry.get("isDir")
                    or uri.rsplit("/", 1)[-1] in _RESERVED_NAMES
                    or uri in sampled_uris
                ):
                    continue
                sampled_uris.add(uri)
                try:
                    # A language sample must not satisfy ExtractLoop's read-before-write guard.
                    raw = await self._viking_fs.read(
                        uri, size=_LANGUAGE_SAMPLE_BYTES, ctx=self._ctx
                    )
                except NotFoundError:
                    continue
                text = raw.decode("utf-8", errors="ignore")
                # The byte cap can cut the metadata comment before its closing delimiter.
                samples.append(_MEMORY_FIELDS_START.split(text, maxsplit=1)[0])
        self._output_language = resolve_output_language_from_text(
            "\n".join(samples),
            output_language_override=self._output_language_override,
            fallback_language="en",
        )
        self._language_resolved = True

    def get_output_language(self) -> str:
        return self._output_language

    def _get_registry(self) -> MemoryTypeRegistry:
        if self._registry is None:
            self._registry = MemoryTypeRegistry(load_schemas=True)
        return self._registry


def build_consolidation_isolation_handler(
    ctx: RequestContext,
    extract_context: Any,
    *,
    memory_types: List[str],
    peer_id: Optional[str],
) -> MemoryIsolationHandler:
    """Build the isolation handler for one consolidation space.

    ``peer_id=None`` consolidates the caller's self memory; a set ``peer_id``
    consolidates only that peer's space. There is no cross-space mixing.
    """
    if peer_id:
        return MemoryIsolationHandler(
            ctx,
            extract_context,
            allowed_memory_types=set(memory_types),
            allow_self=False,
            allowed_peer_ids={peer_id},
        )
    return MemoryIsolationHandler(
        ctx,
        extract_context,
        allowed_memory_types=set(memory_types),
        allow_self=True,
    )


__all__ = [
    "ConsolidationExtractContextProvider",
    "build_consolidation_isolation_handler",
]
