# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Compressor V2 for OpenViking.

Uses the new Memory Templating System with ReAct orchestrator.
Maintains the service-facing compressor interface.
"""

import asyncio
import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from openviking.core.context import Context
from openviking.core.namespace import (
    to_agent_space,
    to_user_space,
)
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop, MemoryUpdater
from openviking.session.memory.dataclass import (
    MemoryFile,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import (
    ExtractContext,
    MemoryUpdateResult,
    write_stored_links,
)
from openviking.session.memory.merge_op.base import SearchReplaceBlock, StrPatch
from openviking.session.memory.merge_op.link_merge import merge_links
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.uri import render_template
from openviking.session.memory.versioning import MISSING_CONTENT_DIGEST, content_digest
from openviking.session.skill import SkillOperationUpdater, dedup_session_skill_operations
from openviking.session.skill.session_skill_context_provider import SESSION_SKILL_MEMORY_TYPE
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking.telemetry import get_current_telemetry, tracer
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

_MEMORY_LOCK_RETRY_WARNING_INTERVAL_SECONDS = 10.0

ExtractPostApply = Callable[
    [MemoryUpdateResult, Dict[str, List[str]], Any, Dict[str, List[str]]], Awaitable[None]
]


def _filename_has_variables(schema: Any) -> bool:
    checker = getattr(schema, "filename_has_variables", None)
    if callable(checker):
        return bool(checker())
    filename_template = getattr(schema, "filename_template", "") or ""
    return "{{" in filename_template and "}}" in filename_template


def _append_unique(paths: list[str], path: str) -> None:
    if path and path not in paths:
        paths.append(path)


def _log_memory_lock_retry(
    *,
    retry_count: int,
    max_retries: int,
    last_warning_at: float,
    phase_label: str = "",
) -> float:
    now = asyncio.get_running_loop().time()
    max_label = max_retries or "unlimited"
    prefix = f"[{phase_label}] " if phase_label else ""
    message = (
        f"{prefix}Failed to acquire memory locks, retrying "
        f"(attempt={retry_count}, max={max_label})..."
    )

    if retry_count == 1 or now - last_warning_at >= _MEMORY_LOCK_RETRY_WARNING_INTERVAL_SECONDS:
        logger.warning(message)
        return now

    return last_warning_at


def _render_memory_schema_locks(
    *,
    schemas: list[Any],
    ctx: RequestContext,
    viking_fs: VikingFS,
    user_ids: list[str],
    agent_ids: list[str],
) -> tuple[list[str], list[str]]:
    exact_paths: list[str] = []
    tree_paths: list[str] = []
    policy = ctx.namespace_policy
    user_ids = user_ids or ["default"]
    agent_ids = agent_ids or ["default"]

    for schema in schemas:
        directory_template = getattr(schema, "directory", "") or ""
        if not directory_template:
            continue

        filename_template = getattr(schema, "filename_template", "") or ""
        for user_id in user_ids:
            for agent_id in agent_ids:
                template_vars = {
                    "user_space": to_user_space(policy, user_id, agent_id),
                    "agent_space": to_agent_space(policy, user_id, agent_id),
                }
                directory_uri = render_template(directory_template, template_vars)
                if _filename_has_variables(schema) or not filename_template:
                    _append_unique(tree_paths, viking_fs._uri_to_path(directory_uri, ctx))
                    continue

                filename = render_template(filename_template, template_vars)
                file_uri = f"{directory_uri.rstrip('/')}/{filename.lstrip('/')}"
                _append_unique(exact_paths, viking_fs._uri_to_path(file_uri, ctx))

    return exact_paths, tree_paths


def _operation_exact_lock_enabled(config: Any, phase_label: str) -> bool:
    memory_config = getattr(config, "memory", None)
    if phase_label.startswith("experience("):
        return (
            getattr(memory_config, "agent_experience_apply_lock_mode", "tree") == "operation_exact"
        )
    if phase_label == "trajectory":
        return (
            getattr(memory_config, "agent_trajectory_apply_lock_mode", "tree") == "operation_exact"
        )
    if phase_label == "long_term":
        return getattr(memory_config, "long_term_apply_lock_mode", "tree") == "operation_exact"
    return False


class OperationExactVersionConflict(RuntimeError):
    def __init__(
        self,
        phase_label: str,
        conflicts: List[str],
        conflict_details: Optional[List[dict[str, str]]] = None,
    ):
        self.conflicts = conflicts
        self.conflict_details = conflict_details or []
        super().__init__(
            f"[{phase_label}] Memory files changed after prefetch; "
            f"operation-exact apply will retry with refreshed reads. conflicts={conflicts}"
        )


class _OperationExactRetrySignal:
    def __init__(self, next_attempt: int) -> None:
        self.next_attempt = next_attempt


@dataclass
class _OperationExactApplyWindowItem:
    lock_paths: list[str]
    phase_metric_key: str
    apply_func: Callable[[Any], Awaitable[Any]]
    future: asyncio.Future
    telemetry: Any
    enqueued_at: float
    coalesce_key: Optional[tuple[str, ...]] = None
    coalesce_payload: Any = None
    coalesce_func: Optional[Callable[[list[Any], Any], Awaitable[list[Any]]]] = None


@dataclass
class _OperationExactApplyWindowQueue:
    items: list[_OperationExactApplyWindowItem] = field(default_factory=list)
    key_paths: set[str] = field(default_factory=set)
    owner_task: Optional[asyncio.Task] = None


_OPERATION_EXACT_APPLY_WINDOW_QUEUES: dict[tuple[str, ...], _OperationExactApplyWindowQueue] = {}
_OPERATION_EXACT_APPLY_WINDOW_GUARD = asyncio.Lock()


def _metric_label(value: str) -> str:
    label = str(value or "unknown").strip().lower()
    return "".join(char if char.isalnum() else "_" for char in label).strip("_") or "unknown"


def _memory_bucket_from_uri(uri: str) -> str:
    marker = "/memories/"
    if marker not in uri:
        return "unknown"
    rest = uri.split(marker, 1)[1].lstrip("/")
    if not rest:
        return "unknown"
    return _metric_label(rest.split("/", 1)[0].removesuffix(".md"))


def _digest_state(digest: str) -> str:
    if digest == MISSING_CONTENT_DIGEST:
        return "missing"
    return "present"


def _short_digest(digest: str) -> str:
    if not digest or digest == MISSING_CONTENT_DIGEST:
        return digest or "unknown"
    return digest[:12]


def _record_provider_read_version(provider: Any, uri: str, content: str) -> None:
    """Track an operation-time read so exact apply can detect stale cleanup plans."""
    if not uri or not getattr(provider, "_track_read_file_versions", False):
        return

    digest = content_digest(content)
    for attr in ("read_file_versions", "_read_file_versions"):
        read_versions = getattr(provider, attr, None)
        if isinstance(read_versions, dict):
            read_versions[uri] = digest


def _parent_uri(uri: str) -> str:
    return uri.rsplit("/", 1)[0] if "/" in uri else uri


def _collect_operation_lock_uris(operations: ResolvedOperations) -> list[str]:
    uris: list[str] = []
    dirs: list[str] = []

    def add_uri(uri: str) -> None:
        if uri and uri not in uris:
            uris.append(uri)

    def add_dir(uri: str) -> None:
        directory = _parent_uri(uri)
        if directory and directory not in dirs:
            dirs.append(directory)

    for op in operations.upsert_operations:
        for uri in op.uris:
            add_uri(uri)
            add_dir(uri)

    for file_content in operations.delete_file_contents:
        if not file_content.uri:
            continue
        add_uri(file_content.uri)
        add_dir(file_content.uri)
        for link in [*(file_content.links or []), *(file_content.backlinks or [])]:
            from_uri = link.get("from_uri")
            to_uri = link.get("to_uri")
            add_uri(from_uri)
            add_dir(from_uri or "")
            add_uri(to_uri)
            add_dir(to_uri or "")

    for link in operations.resolved_links or []:
        add_uri(link.from_uri)
        add_dir(link.from_uri)
        add_uri(link.to_uri)
        add_dir(link.to_uri)

    for directory in dirs:
        add_uri(f"{directory.rstrip('/')}/.overview.md")

    return uris


def _collect_operation_write_uris(operations: ResolvedOperations) -> list[str]:
    uris: list[str] = []

    def add_uri(uri: str) -> None:
        if uri and uri not in uris:
            uris.append(uri)

    for op in operations.upsert_operations:
        for uri in op.uris:
            add_uri(uri)

    for file_content in operations.delete_file_contents:
        add_uri(file_content.uri)
        for link in [*(file_content.links or []), *(file_content.backlinks or [])]:
            add_uri(link.get("from_uri"))
            add_uri(link.get("to_uri"))

    return uris


def _order_upserts_for_coalesced_timeline(
    upsert_operations: list[ResolvedOperation],
) -> list[ResolvedOperation]:
    """Keep same-URI updates adjacent while preserving per-URI arrival order."""

    ordered_keys: list[tuple[str, str, str]] = []
    grouped: dict[tuple[str, str, str], list[ResolvedOperation]] = {}
    unique_index = 0
    for op in upsert_operations:
        if len(op.uris) == 1:
            key = ("uri", op.memory_type, op.uris[0])
        else:
            key = ("unique", str(unique_index), "")
            unique_index += 1
        if key not in grouped:
            grouped[key] = []
            ordered_keys.append(key)
        grouped[key].append(op)
    return [op for key in ordered_keys for op in grouped[key]]


def _same_uri_timeline_stats(
    upsert_operations: list[ResolvedOperation],
) -> tuple[int, int]:
    """Return same-URI timeline group count and item count."""

    group_count = 0
    item_count = 0
    current_key: tuple[str, str] | None = None
    current_len = 0

    def flush_current_group() -> None:
        nonlocal group_count, item_count, current_len
        if current_len > 1:
            group_count += 1
            item_count += current_len
        current_len = 0

    for op in upsert_operations:
        if len(op.uris) != 1:
            flush_current_group()
            current_key = None
            continue
        key = (op.memory_type, op.uris[0])
        if key == current_key:
            current_len += 1
            continue
        flush_current_group()
        current_key = key
        current_len = 1
    flush_current_group()
    return group_count, item_count


def _merge_op_value(field: Any) -> str:
    merge_op = getattr(field, "merge_op", "")
    return str(getattr(merge_op, "value", merge_op)).lower()


def _field_type_value(field: Any) -> str:
    field_type = getattr(field, "field_type", "")
    return str(getattr(field_type, "value", field_type)).lower()


def _is_structured_string_patch(value: Any) -> bool:
    if hasattr(value, "blocks"):
        return True
    return isinstance(value, dict) and isinstance(value.get("blocks"), list)


def _memory_field_current_value(old_content: Any, field_name: str) -> Optional[str]:
    if old_content is None:
        return None
    if field_name == "content":
        return str(old_content.plain_content())
    value = (old_content.extra_fields or {}).get(field_name)
    if value is None:
        return None
    return str(value)


def _line_diff_to_patch_blocks(old_value: str, new_value: str) -> List[SearchReplaceBlock]:
    if old_value == new_value:
        return []
    if not old_value:
        return [SearchReplaceBlock(search="", replace=new_value)]

    old_lines = old_value.splitlines(keepends=True)
    new_lines = new_value.splitlines(keepends=True)
    if not old_lines or not new_lines:
        return [SearchReplaceBlock(search=old_value, replace=new_value)]

    blocks: List[SearchReplaceBlock] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        if tag == "insert":
            inserted = "".join(new_lines[j1:j2])
            if i1 > 0 and i1 < len(old_lines):
                search = old_lines[i1 - 1] + old_lines[i1]
                replace = old_lines[i1 - 1] + inserted + old_lines[i1]
                blocks.append(SearchReplaceBlock(search=search, replace=replace))
            elif i1 > 0:
                anchor = old_lines[i1 - 1]
                blocks.append(SearchReplaceBlock(search=anchor, replace=anchor + inserted))
            elif i1 < len(old_lines):
                anchor = old_lines[i1]
                blocks.append(SearchReplaceBlock(search=anchor, replace=inserted + anchor))
            else:
                return [SearchReplaceBlock(search=old_value, replace=new_value)]
            continue

        search_start = max(0, i1 - 1)
        search_end = min(len(old_lines), i2 + 1)
        search = "".join(old_lines[search_start:search_end])
        replace = (
            "".join(old_lines[search_start:i1])
            + "".join(new_lines[j1:j2])
            + "".join(old_lines[i2:search_end])
        )
        if search:
            blocks.append(SearchReplaceBlock(search=search, replace=replace))

    return blocks or [SearchReplaceBlock(search=old_value, replace=new_value)]


def _convert_plain_string_patches_to_structured(
    operations: ResolvedOperations,
    registry: Any,
) -> list[dict[str, str]]:
    """Turn full-string updates into replayable SEARCH/REPLACE patches.

    Some memory prompts ask for complete field text even when the update is
    logically a delta. Converting that old-vs-new text into structured patches
    lets MemoryUpdater replay the change on the latest file content instead of
    treating it as a conflict-sensitive full replacement.
    """

    getter = getattr(registry, "get", None)
    conversions: list[dict[str, str]] = []
    for operation in operations.upsert_operations:
        schema = getter(operation.memory_type) if callable(getter) else None
        if schema is None or operation.old_memory_file_content is None:
            continue

        fields = {field.name: field for field in getattr(schema, "fields", []) or []}
        for name, value in list((operation.memory_fields or {}).items()):
            field = fields.get(name)
            if field is None:
                continue
            merge_op = _merge_op_value(field)
            if merge_op not in {"patch", "replace"} or _field_type_value(field) != "string":
                continue
            if _is_structured_string_patch(value) or not isinstance(value, str):
                continue

            old_value = _memory_field_current_value(operation.old_memory_file_content, name)
            if old_value is None:
                continue

            operation.memory_fields[name] = StrPatch(
                blocks=_line_diff_to_patch_blocks(old_value, value)
            )
            conversions.append(
                {
                    "uri": operation.uris[0] if operation.uris else "",
                    "memory_type": _metric_label(operation.memory_type),
                    "field": _metric_label(name),
                }
            )
    return conversions


def _jsonable_for_prompt(value: Any) -> Any:
    try:
        dumped = JsonUtils.dumps(value, indent=None)
        return JsonUtils.loads(dumped)
    except Exception:
        return str(value)


async def _synthesize_timeline_conflict_fields(
    *,
    vlm: Any,
    uri: str,
    memory_type: str,
    schema: Any,
    current_file: Optional[MemoryFile],
    resolved_ops: list[ResolvedOperation],
    conflicts: list[dict[str, Any]],
    phase_metric_key: str,
) -> Optional[MemoryFile]:
    """Use the model once to reconcile same-file patch conflicts.

    This is a narrow fallback for operation-exact apply windows. The normal
    path replays structured patches deterministically; synthesis only runs
    when replay hits a real SEARCH/REPLACE conflict in a same-URI timeline.
    """

    if current_file is None:
        return None

    conflicted_fields = list(
        dict.fromkeys(str(conflict.get("field") or "") for conflict in conflicts)
    )
    conflicted_fields = [field for field in conflicted_fields if field]
    if not conflicted_fields:
        return None

    schema_fields = {field.name: field for field in getattr(schema, "fields", []) or []}
    eligible_fields = [
        field
        for field in conflicted_fields
        if field in schema_fields and _field_type_value(schema_fields[field]) == "string"
    ]
    if not eligible_fields:
        return None

    def field_value(memory_file: Optional[MemoryFile], field: str) -> Optional[str]:
        if memory_file is None:
            return None
        if field == "content":
            return memory_file.plain_content()
        value = (memory_file.extra_fields or {}).get(field)
        return None if value is None else str(value)

    current_fields = {field: field_value(current_file, field) for field in eligible_fields}
    operations_payload = []
    for index, op in enumerate(resolved_ops):
        op_fields = {
            field: _jsonable_for_prompt(value)
            for field, value in (op.memory_fields or {}).items()
            if field in eligible_fields
        }
        if not op_fields:
            continue
        operations_payload.append(
            {
                "index": index,
                "base_fields_seen_by_model": {
                    field: field_value(op.old_memory_file_content, field)
                    for field in eligible_fields
                },
                "proposed_patch_fields": op_fields,
            }
        )

    if not operations_payload:
        return None

    payload = {
        "uri": uri,
        "memory_type": memory_type,
        "fields_to_reconcile": eligible_fields,
        "latest_fields_after_successful_patch_replay": current_fields,
        "patch_conflicts": conflicts,
        "queued_operations_in_arrival_order": operations_payload,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You reconcile concurrent memory updates for one memory file. "
                "The server has already applied all non-conflicting patches in arrival order. "
                "For the listed fields only, produce the final field values that preserve the "
                "latest current content and incorporate the intent of the queued patches when "
                "it is still applicable. Do not invent unrelated facts. If a patch conflicts "
                "semantically, prefer the latest current content and include only compatible "
                'information. Output JSON only: {"fields": {<field>: <full final string>}}.'
            ),
        },
        {
            "role": "user",
            "content": JsonUtils.dumps(payload, indent=2),
        },
    ]

    telemetry = get_current_telemetry()
    prefix = f"memory.agent.extract.phase.{phase_metric_key}"
    telemetry.count(f"{prefix}.operation_exact_apply_window_timeline_conflict_synthesis", 1)
    started_at = asyncio.get_running_loop().time()
    try:
        response = await vlm.get_completion_async(messages=messages)
        content = (
            response if isinstance(response, str) else (getattr(response, "content", "") or "")
        )
        parsed = JsonUtils.loads(content) or {}
        fields = parsed.get("fields") if isinstance(parsed, dict) else None
        if not isinstance(fields, dict):
            telemetry.count(
                f"{prefix}.operation_exact_apply_window_timeline_conflict_synthesis_failed",
                1,
            )
            return None

        synthesized = current_file.model_copy(deep=True)
        for field in eligible_fields:
            if field not in fields:
                continue
            value = fields[field]
            if value is None:
                continue
            if field == "content":
                synthesized.content = str(value)
            else:
                synthesized.extra_fields[field] = str(value)
        telemetry.add_duration(
            f"{prefix}.operation_exact_apply_window_timeline_conflict_synthesis",
            (asyncio.get_running_loop().time() - started_at) * 1000,
        )
        return synthesized
    except Exception:
        telemetry.count(
            f"{prefix}.operation_exact_apply_window_timeline_conflict_synthesis_failed",
            1,
        )
        logger.warning(
            "operation-exact apply-window timeline conflict synthesis failed for %s",
            uri,
            exc_info=True,
        )
        return None


def _create_new_experience_candidates(
    operations: ResolvedOperations,
) -> list[dict[str, Any]]:
    if operations.delete_file_contents or operations.errors:
        return []

    candidates: list[dict[str, Any]] = []
    source_links_by_uri: dict[str, list[str]] = {}
    for link in operations.resolved_links or []:
        if link.link_type != "derived_from":
            continue
        if "/trajectories/" not in link.to_uri:
            continue
        source_links_by_uri.setdefault(link.from_uri, []).append(link.to_uri)

    for op in operations.upsert_operations:
        if op.memory_type != "experiences":
            continue
        if op.old_memory_file_content is not None or len(op.uris) != 1:
            continue
        if str(op.memory_fields.get("supersedes") or "").strip():
            continue
        experience_name = str(op.memory_fields.get("experience_name") or "").strip()
        content = str(op.memory_fields.get("content") or "").strip()
        if not experience_name or not content:
            continue
        uri = op.uris[0]
        candidates.append(
            {
                "candidate_index": len(candidates),
                "operation": op,
                "uri": uri,
                "experience_name": experience_name,
                "content": content,
                "source_trajectory_uris": list(dict.fromkeys(source_links_by_uri.get(uri, []))),
            }
        )
    return candidates


def _remap_resolved_links_for_consolidation(
    operations: ResolvedOperations,
    uri_remap: dict[str, str],
) -> None:
    if not uri_remap:
        return

    remapped_links: list[StoredLink] = []
    seen_links: set[tuple[str, str, str, str]] = set()
    for link in operations.resolved_links or []:
        from_uri = uri_remap.get(link.from_uri, link.from_uri)
        to_uri = uri_remap.get(link.to_uri, link.to_uri)
        if not from_uri or not to_uri or from_uri == to_uri:
            continue
        next_link = link
        if from_uri != link.from_uri or to_uri != link.to_uri:
            next_link = link.model_copy(update={"from_uri": from_uri, "to_uri": to_uri})
        key = (next_link.from_uri, next_link.to_uri, next_link.link_type, next_link.match_text or "")
        if key in seen_links:
            continue
        seen_links.add(key)
        remapped_links.append(next_link)
    operations.resolved_links = remapped_links


def _create_new_experience_window_keys(operations: ResolvedOperations) -> list[str]:
    """Return synthetic apply-window keys for cross-URI experience creation.

    Exact lock paths still protect the concrete files. These keys only decide
    which concurrent create-new experience proposals are allowed to share one
    apply window, so semantic consolidation can see proposals with different
    generated filenames.
    """

    directories = sorted(
        {
            _parent_uri(item["uri"])
            for item in _create_new_experience_candidates(operations)
            if item.get("uri")
        }
    )
    return [f"create-new-experience-window:{directory}" for directory in directories]


async def _synthesize_create_new_experience_consolidation(
    *,
    vlm: Any,
    operations: ResolvedOperations,
    phase_metric_key: str,
) -> dict[str, str]:
    """Consolidate same-window cross-URI create-new experience proposals.

    The apply window makes several independently generated create-new proposals
    visible at once. This gate asks the model to cluster proposals that describe
    the same durable experience, then rewrites the batch so only the canonical
    experience is created and all source links point to it.
    """

    candidates = _create_new_experience_candidates(operations)
    if len(candidates) < 2:
        return {}

    payload = {
        "candidate_experiences": [
            {
                "candidate_index": item["candidate_index"],
                "experience_name": item["experience_name"],
                "content": item["content"],
                "source_trajectory_uris": item["source_trajectory_uris"],
            }
            for item in candidates
        ]
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You consolidate concurrent create-new experience memories. "
                "Group candidates only when they describe the same reusable user intent, "
                "same trigger boundary, and compatible tool/action sequence. Do not merge "
                "different intents, different lifecycle transitions, or cases where one card "
                "should remain a narrower exception. Choose one existing candidate_index as "
                "canonical for each merged group. Keep singletons out of groups. For each "
                "merged group, provide one final full content string that preserves all "
                "compatible guidance without inventing facts. Output JSON only: "
                "{\"groups\": [{\"canonical_index\": 0, \"member_indices\": [0, 2], "
                "\"content\": \"<full final content>\"}]}"
            ),
        },
        {"role": "user", "content": JsonUtils.dumps(payload, indent=2)},
    ]

    telemetry = get_current_telemetry()
    prefix = f"memory.agent.extract.phase.{phase_metric_key}"
    telemetry.count(
        f"{prefix}.operation_exact_apply_window_create_new_consolidation_input_uris",
        len(candidates),
    )
    started_at = asyncio.get_running_loop().time()
    try:
        response = await vlm.get_completion_async(messages=messages)
        content = (
            response if isinstance(response, str) else (getattr(response, "content", "") or "")
        )
        parsed = JsonUtils.loads(content) or {}
    except Exception:
        telemetry.count(f"{prefix}.operation_exact_apply_window_create_new_consolidation_failed", 1)
        logger.warning("operation-exact create-new experience consolidation failed", exc_info=True)
        return {}
    finally:
        telemetry.add_duration(
            f"{prefix}.operation_exact_apply_window_create_new_consolidation",
            (asyncio.get_running_loop().time() - started_at) * 1000,
        )

    groups = parsed.get("groups") if isinstance(parsed, dict) else None
    if not isinstance(groups, list):
        telemetry.count(f"{prefix}.operation_exact_apply_window_create_new_consolidation_failed", 1)
        return {}

    by_index = {item["candidate_index"]: item for item in candidates}
    used_duplicates: set[int] = set()
    duplicate_op_ids: set[int] = set()
    uri_remap: dict[str, str] = {}
    merged_group_count = 0

    for raw_group in groups:
        if not isinstance(raw_group, dict):
            continue
        try:
            canonical_index = int(raw_group.get("canonical_index"))
        except (TypeError, ValueError):
            continue
        raw_members = raw_group.get("member_indices")
        if not isinstance(raw_members, list):
            continue
        member_indices: list[int] = []
        for value in raw_members:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if index in by_index and index not in member_indices:
                member_indices.append(index)
        if canonical_index not in member_indices or len(member_indices) < 2:
            continue
        if any(index in used_duplicates for index in member_indices):
            continue
        canonical = by_index.get(canonical_index)
        if canonical is None:
            continue

        final_content = raw_group.get("content")
        if isinstance(final_content, str) and final_content.strip():
            canonical["operation"].memory_fields["content"] = final_content.strip()

        canonical_uri = canonical["uri"]
        for index in member_indices:
            if index == canonical_index:
                continue
            duplicate = by_index[index]
            duplicate_uri = duplicate["uri"]
            if duplicate_uri == canonical_uri:
                continue
            duplicate_op_ids.add(id(duplicate["operation"]))
            used_duplicates.add(index)
            uri_remap[duplicate_uri] = canonical_uri
        merged_group_count += 1

    if not uri_remap:
        return {}

    operations.upsert_operations = [
        op for op in operations.upsert_operations if id(op) not in duplicate_op_ids
    ]
    _remap_resolved_links_for_consolidation(operations, uri_remap)

    telemetry.count(
        f"{prefix}.operation_exact_apply_window_create_new_consolidation_groups",
        merged_group_count,
    )
    telemetry.count(
        f"{prefix}.operation_exact_apply_window_create_new_consolidation_merged_uris",
        len(uri_remap),
    )
    return uri_remap


def _operation_conflict_reason(operation: Any, registry: Any) -> str:
    getter = getattr(registry, "get", None)
    schema = getter(operation.memory_type) if callable(getter) else None
    if schema is None:
        return "unknown_schema"

    fields = {field.name: field for field in getattr(schema, "fields", []) or []}
    for name, value in (operation.memory_fields or {}).items():
        field = fields.get(name)
        if field is None:
            return "unknown_field"

        merge_op = _merge_op_value(field)
        if merge_op == "replace":
            if _field_type_value(field) == "string" and _is_structured_string_patch(value):
                continue
            return "replace"
        if merge_op == "patch" and _field_type_value(field) == "string":
            # Structured SEARCH/REPLACE patches are replayed against the latest
            # file inside MemoryUpdater. A plain string would become a full
            # replacement, so keep the conservative stale-read retry for it.
            if not _is_structured_string_patch(value):
                return "plain_string_patch"

    return ""


def _collect_conflict_sensitive_operation_diagnostics(
    operations: ResolvedOperations,
    registry: Any,
) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []

    def add(uri: str, *, memory_type: str, operation: str, reason: str) -> None:
        if not uri:
            return
        diagnostics.append(
            {
                "uri": uri,
                "bucket": _memory_bucket_from_uri(uri),
                "memory_type": _metric_label(memory_type),
                "operation": _metric_label(operation),
                "reason": _metric_label(reason),
            }
        )

    for operation in operations.upsert_operations:
        reason = _operation_conflict_reason(operation, registry)
        if not reason:
            continue
        for uri in operation.uris:
            add(uri, memory_type=operation.memory_type, operation="upsert", reason=reason)

    for file_content in operations.delete_file_contents:
        add(
            file_content.uri,
            memory_type=_memory_bucket_from_uri(file_content.uri),
            operation="delete",
            reason="delete",
        )

    return diagnostics


async def _enqueue_operation_exact_apply_window(
    *,
    lock_manager: Any,
    window_key_paths: list[str],
    lock_paths: list[str],
    window_seconds: float,
    phase_metric_key: str,
    apply_func: Callable[[Any], Awaitable[Any]],
    coalesce_key: Optional[tuple[str, ...]] = None,
    coalesce_payload: Any = None,
    coalesce_func: Optional[Callable[[list[Any], Any], Awaitable[list[Any]]]] = None,
) -> Any:
    if not lock_manager or not lock_paths or window_seconds <= 0:
        return await apply_func(None)

    key_path_set = set(dict.fromkeys(window_key_paths or lock_paths))
    lock_paths = list(dict.fromkeys(lock_paths))
    telemetry = get_current_telemetry()
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    item = _OperationExactApplyWindowItem(
        lock_paths=lock_paths,
        phase_metric_key=phase_metric_key,
        apply_func=apply_func,
        future=future,
        telemetry=telemetry,
        enqueued_at=loop.time(),
        coalesce_key=coalesce_key,
        coalesce_payload=coalesce_payload,
        coalesce_func=coalesce_func,
    )
    is_leader = False
    async with _OPERATION_EXACT_APPLY_WINDOW_GUARD:
        for stale_key, stale_queue in list(_OPERATION_EXACT_APPLY_WINDOW_QUEUES.items()):
            if stale_queue.owner_task is not None and stale_queue.owner_task.done():
                _OPERATION_EXACT_APPLY_WINDOW_QUEUES.pop(stale_key, None)

        key = tuple(sorted(key_path_set))
        queue: Optional[_OperationExactApplyWindowQueue] = None
        for candidate in _OPERATION_EXACT_APPLY_WINDOW_QUEUES.values():
            if candidate.key_paths & key_path_set:
                queue = candidate
                break

        if queue is None:
            queue = _OperationExactApplyWindowQueue(key_paths=set(key_path_set))
            _OPERATION_EXACT_APPLY_WINDOW_QUEUES[key] = queue
        else:
            queue.key_paths.update(key_path_set)

        queue.items.append(item)
        if queue.owner_task is None or queue.owner_task.done():
            is_leader = True
            queue.owner_task = loop.create_task(
                _drain_operation_exact_apply_window(
                    key=key,
                    lock_manager=lock_manager,
                    window_seconds=window_seconds,
                )
            )

    telemetry.count(
        f"memory.agent.extract.phase.{phase_metric_key}.operation_exact_apply_window_entered",
        1,
    )
    if is_leader:
        telemetry.count(
            f"memory.agent.extract.phase.{phase_metric_key}.operation_exact_apply_window_leader",
            1,
        )
    else:
        telemetry.count(
            f"memory.agent.extract.phase.{phase_metric_key}.operation_exact_apply_window_follower",
            1,
        )

    return await future


async def _drain_operation_exact_apply_window(
    *,
    key: tuple[str, ...],
    lock_manager: Any,
    window_seconds: float,
) -> None:
    if window_seconds > 0:
        await asyncio.sleep(window_seconds)

    async with _OPERATION_EXACT_APPLY_WINDOW_GUARD:
        queue = _OPERATION_EXACT_APPLY_WINDOW_QUEUES.pop(key, None)
    if queue is None or not queue.items:
        return

    items = queue.items
    lock_paths = list(dict.fromkeys(path for item in items for path in item.lock_paths if path))
    owner_handle = None
    loop = asyncio.get_running_loop()
    lock_wait_started_at = loop.time()
    lock_acquired = False
    try:
        owner_handle = lock_manager.create_handle()
        lock_acquired = await lock_manager.acquire_exact_path_batch(
            owner_handle,
            lock_paths,
            timeout=None,
        )
        lock_wait_ms = (loop.time() - lock_wait_started_at) * 1000
        for item in items:
            wait_ms = (loop.time() - item.enqueued_at) * 1000
            prefix = f"memory.agent.extract.phase.{item.phase_metric_key}"
            item.telemetry.add_duration(
                f"{prefix}.operation_exact_apply_window_wait",
                wait_ms,
            )
            item.telemetry.add_duration(
                f"{prefix}.operation_exact_apply_window_lock_wait",
                lock_wait_ms,
            )
            item.telemetry.count(f"{prefix}.operation_exact_apply_window_batch_items", 1)
            item.telemetry.count(
                f"{prefix}.operation_exact_apply_window_batch_size.{len(items)}",
                1,
            )
            item.telemetry.count(
                f"{prefix}.operation_exact_apply_window_lock_path_count_total",
                len(lock_paths),
            )
        if not lock_acquired:
            error = TimeoutError(
                f"Failed to acquire operation-exact apply-window locks for {len(lock_paths)} paths"
            )
            for item in items:
                if not item.future.done():
                    item.future.set_exception(error)
            return

        pending_items = list(items)
        while pending_items:
            item = pending_items.pop(0)
            if item.future.done():
                continue

            coalesced_items = [item]
            if item.coalesce_key and item.coalesce_func:
                item_lock_paths = set(item.lock_paths)
                remaining_items: list[_OperationExactApplyWindowItem] = []
                for candidate_index, candidate in enumerate(pending_items):
                    if candidate.future.done():
                        remaining_items.append(candidate)
                        continue

                    same_coalesce_group = (
                        candidate.coalesce_key == item.coalesce_key
                        and candidate.coalesce_func is not None
                    )
                    if same_coalesce_group:
                        coalesced_items.append(candidate)
                        continue

                    # Do not move a later item ahead of an overlapping write
                    # that cannot join this coalesced timeline. That preserves
                    # FIFO semantics for the same locked file while still
                    # allowing independent same-key payloads in the window to
                    # share one apply.
                    if item_lock_paths & set(candidate.lock_paths):
                        remaining_items.append(candidate)
                        remaining_items.extend(pending_items[candidate_index + 1 :])
                        break

                    remaining_items.append(candidate)
                pending_items = remaining_items

            if len(coalesced_items) > 1 and item.coalesce_func:
                try:
                    results = await item.coalesce_func(
                        [entry.coalesce_payload for entry in coalesced_items],
                        owner_handle,
                    )
                    if len(results) != len(coalesced_items):
                        raise RuntimeError(
                            "operation-exact apply-window coalescer returned "
                            f"{len(results)} results for {len(coalesced_items)} items"
                        )
                    prefix = f"memory.agent.extract.phase.{item.phase_metric_key}"
                    item.telemetry.count(
                        f"{prefix}.operation_exact_apply_window_coalesced_groups",
                        1,
                    )
                    item.telemetry.count(
                        f"{prefix}.operation_exact_apply_window_coalesced_items",
                        len(coalesced_items),
                    )
                    for entry, result in zip(coalesced_items, results, strict=True):
                        if not entry.future.done():
                            entry.future.set_result(result)
                except Exception as exc:
                    for entry in coalesced_items:
                        if not entry.future.done():
                            entry.future.set_exception(exc)
                continue

            try:
                item.future.set_result(await item.apply_func(owner_handle))
            except Exception as exc:
                item.future.set_exception(exc)
    except Exception as exc:
        for item in items:
            if not item.future.done():
                item.future.set_exception(exc)
    finally:
        if lock_acquired and owner_handle is not None:
            try:
                await lock_manager.release(owner_handle)
            except Exception:
                logger.warning(
                    "Failed to release operation-exact apply-window owner lock",
                    exc_info=True,
                )


def _collect_conflict_sensitive_operation_uris(
    operations: ResolvedOperations,
    registry: Any,
) -> list[str]:
    uris: list[str] = []

    def add_uri(uri: str) -> None:
        if uri and uri not in uris:
            uris.append(uri)

    for diagnostic in _collect_conflict_sensitive_operation_diagnostics(operations, registry):
        add_uri(diagnostic["uri"])

    return uris


def _phase_metric_key(phase_label: str) -> str:
    if phase_label == "trajectory":
        return "trajectory"
    if phase_label.startswith("experience("):
        return "experience_single"
    return "other"


class SessionCompressorV2:
    """Session memory extractor with v2 templating system."""

    def __init__(
        self,
        vikingdb: VikingDBManager,
        skill_processor: Optional[SkillProcessor] = None,
    ):
        """Initialize session compressor."""
        self.vikingdb = vikingdb
        self.skill_processor = skill_processor

    def _get_or_create_react(
        self,
        ctx: Optional[RequestContext] = None,
        messages: Optional[List] = None,
        latest_archive_overview: str = "",
        isolation_handler: Optional[MemoryIsolationHandler] = None,
        transaction_handle=None,
    ) -> ExtractLoop:
        """Create new ExtractLoop instance with current ctx.

        Note: Always create new instance to avoid cross-session isolation issues.
        The ctx contains request-scoped state that must not be shared across requests.
        """
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        viking_fs = get_viking_fs()

        # Create context provider with messages (provider 负责加载 schema)
        from openviking.session.memory.session_extract_context_provider import (
            SessionExtractContextProvider,
        )

        context_provider = SessionExtractContextProvider(
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            isolation_handler=isolation_handler,
            ctx=ctx,
            viking_fs=viking_fs,
            transaction_handle=transaction_handle,
        )

        return ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )

    def _get_or_create_updater(
        self,
        registry,
        transaction_handle=None,
        timeline_conflict_synthesizer: Optional[
            Callable[..., Awaitable[Optional[MemoryFile]]]
        ] = None,
    ) -> MemoryUpdater:
        """Create new MemoryUpdater instance for each request.

        Always create new instance to avoid cross-request state pollution.
        """
        return MemoryUpdater(
            registry=registry,
            vikingdb=self.vikingdb,
            transaction_handle=transaction_handle,
            timeline_conflict_synthesizer=timeline_conflict_synthesizer,
        )

    def _split_operations_by_memory_type(
        self,
        operations: ResolvedOperations,
    ) -> tuple[ResolvedOperations, ResolvedOperations, list[str]]:
        memory_upserts = []
        skill_upserts = []
        for operation in operations.upsert_operations:
            if operation.memory_type == SESSION_SKILL_MEMORY_TYPE:
                skill_upserts.append(operation)
            else:
                memory_upserts.append(operation)

        memory_deletes = []
        unsupported_skill_deletes = []
        for delete_file in operations.delete_file_contents:
            if delete_file.uri.endswith("/SKILL.md") and "/skills/" in delete_file.uri:
                unsupported_skill_deletes.append(delete_file.uri)
            else:
                memory_deletes.append(delete_file)

        operation_errors = list(getattr(operations, "errors", []))
        return (
            ResolvedOperations(
                upsert_operations=memory_upserts,
                delete_file_contents=memory_deletes,
                errors=list(operation_errors),
                resolved_links=list(getattr(operations, "resolved_links", [])),
            ),
            ResolvedOperations(
                upsert_operations=skill_upserts,
                delete_file_contents=[],
                errors=list(operation_errors),
                resolved_links=[],
            ),
            unsupported_skill_deletes,
        )

    @tracer()
    async def extract_long_term_memories(
        self,
        messages: List[Message],
        user: Optional["UserIdentifier"] = None,
        session_id: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
        archive_uri: Optional[str] = None,
    ) -> List[Context]:
        """Extract long-term memories from messages using v2 templating system.

        Note: Returns empty List[Context] because v2 directly writes to storage.
        The list length is used for stats in session.py.

        Args:
            messages: Messages to extract memories from.
            user: User identifier.
            session_id: Session ID.
            ctx: Request context.
            strict_extract_errors: If True, raise exceptions on extraction errors.
            latest_archive_overview: Overview of latest archive for context.
            archive_uri: Archive URI for writing memory_diff.json.
        """

        if not messages:
            return []

        if not ctx:
            logger.warning("No RequestContext provided, skipping memory extraction")
            return []

        tracer.info("Starting v2 memory extraction from conversation")
        tracer.info(f"origin_messages={JsonUtils.dumps(messages)}")
        config = get_openviking_config()

        # Initialize default memory files (soul.md, identity.md) if not exist
        from openviking.session.memory.memory_type_registry import create_default_registry

        registry = create_default_registry()
        await registry.initialize_memory_files(ctx)

        # Initialize telemetry counters before extraction.
        telemetry = get_current_telemetry()
        telemetry.set("memory.extract.candidates.total", 0)
        telemetry.set("memory.extract.candidates.standard", 0)
        telemetry.set("memory.extract.candidates.tool_skill", 0)
        telemetry.set("memory.extract.created", 0)
        telemetry.set("memory.extract.merged", 0)
        telemetry.set("memory.extract.deleted", 0)
        telemetry.set("memory.extract.skipped", 0)

        from openviking.storage.transaction import get_lock_manager, init_lock_manager
        from openviking.storage.viking_fs import get_viking_fs

        viking_fs = get_viking_fs()
        if getattr(config.memory, "long_term_apply_lock_mode", "tree") == "operation_exact":
            from openviking.session.memory.session_extract_context_provider import (
                SessionExtractContextProvider,
            )

            provider = SessionExtractContextProvider(
                messages=messages,
                latest_archive_overview=latest_archive_overview,
            )
            if archive_uri:
                provider._memory_diff_archive_uri = archive_uri

            phase_result = await self._run_extract_phase(
                provider=provider,
                messages=messages,
                ctx=ctx,
                strict_extract_errors=strict_extract_errors,
                phase_label="long_term",
            )
            if phase_result is None:
                return []

            _written_uris, _edited_uris, contexts, _inheritance_map, _skill_results = phase_result
            telemetry.set(
                "memory.extract.candidates.total",
                sum(
                    1 for context in contexts if context.category in {"memory_write", "memory_edit"}
                ),
            )
            telemetry.set(
                "memory.extract.created",
                sum(1 for context in contexts if context.category == "memory_write"),
            )
            telemetry.set(
                "memory.extract.merged",
                sum(1 for context in contexts if context.category == "memory_edit"),
            )
            telemetry.set(
                "memory.extract.deleted",
                sum(1 for context in contexts if context.category == "memory_delete"),
            )
            return contexts

        # 初始化锁管理器（仅在有 AGFS 时使用锁机制）
        lock_manager = None
        transaction_handle = None
        if viking_fs and hasattr(viking_fs, "agfs") and viking_fs.agfs:
            init_lock_manager(viking_fs.agfs)
            lock_manager = get_lock_manager()
            if lock_manager:
                transaction_handle = lock_manager.create_handle()
            else:
                logger.debug("AGFS unavailable, running memory extraction without locks")
        else:
            logger.debug("AGFS unavailable, running memory extraction without locks")

        try:
            # Create extract context from messages
            from openviking.session.memory.memory_updater import ExtractContext

            extract_context = ExtractContext(messages)

            # Create MemoryIsolationHandler
            isolation_handler = MemoryIsolationHandler(ctx, extract_context)
            isolation_handler.prepare_messages()
            # 获取所有记忆 schema 目录并加锁（仅在有锁管理器时）
            orchestrator = self._get_or_create_react(
                ctx=ctx,
                messages=messages,
                latest_archive_overview=latest_archive_overview,
                isolation_handler=isolation_handler,
                transaction_handle=transaction_handle,
            )
            read_scope = isolation_handler.get_read_scope()
            if lock_manager:
                schemas = orchestrator.context_provider.get_memory_schemas(ctx)
                exact_lock_paths, tree_lock_dirs = _render_memory_schema_locks(
                    schemas=schemas,
                    ctx=ctx,
                    viking_fs=viking_fs,
                    user_ids=read_scope.user_ids,
                    agent_ids=read_scope.agent_ids,
                )
                logger.debug(
                    f"Memory schema locks: exact={exact_lock_paths}, tree={tree_lock_dirs}"
                )

                retry_interval = config.memory.v2_lock_retry_interval_seconds
                max_retries = config.memory.v2_lock_max_retries
                retry_count = 0
                last_lock_retry_warning_at = 0.0

                # 循环重试获取锁（机制确保不会死锁）
                while True:
                    lock_acquired = await lock_manager.acquire_exact_tree_batch(
                        transaction_handle,
                        exact_paths=exact_lock_paths,
                        tree_paths=tree_lock_dirs,
                        timeout=None,
                    )
                    if lock_acquired:
                        break
                    retry_count += 1
                    if max_retries > 0 and retry_count >= max_retries:
                        raise TimeoutError(
                            "Failed to acquire memory locks after "
                            f"{retry_count} retries (max={max_retries})"
                        )

                    last_lock_retry_warning_at = _log_memory_lock_retry(
                        retry_count=retry_count,
                        max_retries=max_retries,
                        last_warning_at=last_lock_retry_warning_at,
                    )
                    if retry_interval > 0:
                        await asyncio.sleep(retry_interval)

            orchestrator._transaction_handle = transaction_handle  # 传递给 ExtractLoop

            # Run ReAct orchestrator
            operations, tools_used = await orchestrator.run()

            if operations is None:
                tracer.info("No memory operations generated")
                return []

            updater = self._get_or_create_updater(registry, transaction_handle)

            # Apply operations with isolation_handler
            result = await updater.apply_operations(
                operations,
                ctx,
                extract_context=extract_context,
                isolation_handler=isolation_handler,
            )

            tracer.info(
                f"Applied memory operations: written={len(result.written_uris)}, "
                f"edited={len(result.edited_uris)}, deleted={len(result.deleted_uris)}, "
                f"errors={len(result.errors)}"
            )

            # Write memory_diff.json to archive directory
            if archive_uri and viking_fs:
                memory_diff = await self._build_memory_diff(
                    result=result,
                    operations=operations,
                    viking_fs=viking_fs,
                    ctx=ctx,
                    archive_uri=archive_uri,
                )
                await viking_fs.write_file(
                    uri=f"{archive_uri}/memory_diff.json",
                    content=json.dumps(memory_diff, ensure_ascii=False, indent=4),
                    ctx=ctx,
                )
                logger.info(f"Wrote memory_diff.json to {archive_uri}")

            # Report telemetry stats.
            telemetry = get_current_telemetry()
            telemetry.set(
                "memory.extract.candidates.total",
                len(result.written_uris) + len(result.edited_uris),
            )
            telemetry.set("memory.extract.created", len(result.written_uris))
            telemetry.set("memory.extract.merged", len(result.edited_uris))
            telemetry.set("memory.extract.deleted", len(result.deleted_uris))
            telemetry.set("memory.extract.skipped", len(result.errors))

            # Build Context objects for stats in session.py
            contexts: List[Context] = []

            # Written memories
            for uri in result.written_uris:
                contexts.append(
                    Context(
                        uri=uri,
                        category="memory_write",
                        context_type="memory",
                    )
                )

            # Edited memories
            for uri in result.edited_uris:
                contexts.append(
                    Context(
                        uri=uri,
                        category="memory_edit",
                        context_type="memory",
                    )
                )

            # Deleted memories
            for uri in result.deleted_uris:
                contexts.append(
                    Context(
                        uri=uri,
                        category="memory_delete",
                        context_type="memory",
                    )
                )

            return contexts

        except Exception as e:
            logger.error(f"Failed to extract memories with v2: {e}", exc_info=True)
            if strict_extract_errors:
                raise
            return []
        finally:
            # 确保释放所有锁（仅在有锁管理器时）
            if lock_manager and transaction_handle:
                try:
                    await lock_manager.release(transaction_handle)
                except Exception as e:
                    logger.warning(f"Failed to release transaction lock: {e}")

    @tracer(ignore_result=True)
    async def extract_agent_memories(
        self,
        messages: List[Message],
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
        archive_uri: str = "",
    ) -> Dict[str, List[Any]]:
        """Two-phase agent-scope extraction for trajectories, experiences, and session skills."""
        config = get_openviking_config()
        include_trajectories = bool(getattr(config.memory, "agent_memory_enabled", False))
        include_session_skills = bool(
            getattr(config.memory, "session_skill_extraction_enabled", False)
        )
        empty_result: Dict[str, List[Any]] = {"contexts": [], "session_skills": []}
        if not (include_trajectories or include_session_skills):
            return empty_result
        if not messages or not ctx:
            return empty_result

        from openviking.session.memory.agent_experience_context_provider import (
            AgentExperienceContextProvider,
        )
        from openviking.session.memory.agent_trajectory_context_provider import (
            AgentTrajectoryContextProvider,
        )

        contexts: List[Context] = []
        session_skill_results: List[Dict[str, Any]] = []

        # Phase 1: trajectory extraction, optionally co-extracting session skills.
        traj_provider = AgentTrajectoryContextProvider(
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            include_trajectories=include_trajectories,
            include_session_skills=include_session_skills,
        )
        traj_result = await self._run_extract_phase(
            provider=traj_provider,
            messages=messages,
            ctx=ctx,
            strict_extract_errors=strict_extract_errors,
            phase_label="trajectory",
        )
        if traj_result is None:
            return empty_result

        written_trajectory_uris, _, traj_contexts, _, traj_skill_results = traj_result
        contexts.extend(traj_contexts)
        if archive_uri:
            for item in traj_skill_results:
                item["archive_uri"] = archive_uri
        session_skill_results.extend(traj_skill_results)

        # Deduplicate: LLM may output the same trajectory_name twice in one call,
        # producing identical URIs. Without this, experience extraction would run
        # once per duplicate and generate near-identical experiences.
        written_trajectory_uris = list(dict.fromkeys(written_trajectory_uris))
        telemetry = get_current_telemetry()
        telemetry.set("memory.agent.trajectories.created", len(written_trajectory_uris))

        if not include_trajectories or not written_trajectory_uris:
            if not written_trajectory_uris:
                tracer.info("No trajectories extracted; skipping experience phase")
            return {
                "contexts": contexts,
                "session_skills": session_skill_results,
            }

        # Phase 2: for each new trajectory, consolidate into experiences.
        viking_fs = get_viking_fs()
        trajectory_items: List[Dict[str, str]] = []
        for traj_uri in written_trajectory_uris:
            try:
                mf = MemoryFileUtils.read(await viking_fs.read_file(traj_uri, ctx=ctx) or "")
                trajectory_items.append({"uri": traj_uri, "content": mf.content})
            except Exception as e:
                logger.warning(f"Failed to read new trajectory {traj_uri}: {e}")

        if not trajectory_items:
            return {
                "contexts": contexts,
                "session_skills": session_skill_results,
            }

        per_trajectory_concurrency = 1
        if getattr(config.memory, "agent_experience_apply_lock_mode", "tree") == "operation_exact":
            per_trajectory_concurrency = int(
                getattr(config.memory, "agent_experience_per_trajectory_max_concurrency", 4) or 1
            )
        per_trajectory_concurrency = max(
            1,
            min(per_trajectory_concurrency, len(trajectory_items)),
        )
        telemetry.set(
            "memory.agent.experience.per_trajectory.max_concurrency",
            per_trajectory_concurrency,
        )
        telemetry.set(
            "memory.agent.experience.per_trajectory.input_trajectories",
            len(trajectory_items),
        )

        semaphore = asyncio.Semaphore(per_trajectory_concurrency)

        async def _run_per_trajectory_experience(item: Dict[str, str]) -> List[Context]:
            traj_uri = item["uri"]
            traj_content = item["content"]
            async with semaphore:
                exp_provider = AgentExperienceContextProvider(
                    messages=messages,
                    trajectory_summary=traj_content,
                    trajectory_uri=traj_uri,
                )
                exp_dir = exp_provider._render_experience_dir(ctx)

                async def _append_sources_before_unlock(
                    result: MemoryUpdateResult,
                    inheritance_map: Dict[str, List[str]],
                    lock_handle: Any,
                    source_attribution_map: Dict[str, List[str]],
                    exp_provider=exp_provider,
                    exp_dir=exp_dir,
                    traj_uri=traj_uri,
                ) -> None:
                    if getattr(exp_provider, "_source_links_attached_in_operations", False):
                        return
                    all_exp_uris = await self._resolve_source_target_experience_uris(
                        result=result,
                        provider=exp_provider,
                        exp_dir=exp_dir,
                        ctx=ctx,
                        viking_fs=viking_fs,
                    )
                    for exp_uri in all_exp_uris:
                        inherited = inheritance_map.get(exp_uri, [])
                        source_uris = list(dict.fromkeys([traj_uri] + inherited))
                        await self._append_trajectories_to_experiences(
                            [exp_uri],
                            source_uris,
                            ctx,
                            viking_fs,
                            lock_handle=lock_handle,
                        )

                exp_result = await self._run_extract_phase(
                    provider=exp_provider,
                    messages=messages,
                    ctx=ctx,
                    strict_extract_errors=strict_extract_errors,
                    phase_label=f"experience({traj_uri})",
                    post_apply=_append_sources_before_unlock,
                )
                if exp_result is None:
                    fallback_uris = await self._single_existing_experience_uris(
                        exp_dir=exp_dir,
                        ctx=ctx,
                        viking_fs=viking_fs,
                    )
                    if fallback_uris:
                        tracer.info(
                            f"[source_traj] phase2 failed; fallback append to sole experience: {fallback_uris[0]}"
                        )
                        await self._append_trajectories_to_experiences(
                            fallback_uris, [traj_uri], ctx, viking_fs
                        )
                    return []

                _, _, exp_contexts, _, _ = exp_result
                return exp_contexts

        if per_trajectory_concurrency == 1:
            for item in trajectory_items:
                contexts.extend(await _run_per_trajectory_experience(item))
        else:
            results = await asyncio.gather(
                *(_run_per_trajectory_experience(item) for item in trajectory_items)
            )
            for exp_contexts in results:
                contexts.extend(exp_contexts)

        return {
            "contexts": contexts,
            "session_skills": session_skill_results,
        }

    async def _resolve_source_target_experience_uris(
        self,
        *,
        result: MemoryUpdateResult,
        provider: Any,
        exp_dir: str,
        ctx: RequestContext,
        viking_fs,
    ) -> List[str]:
        all_exp_uris = list(result.written_uris) + list(result.edited_uris)
        if all_exp_uris:
            return all_exp_uris

        candidate_uris = list(dict.fromkeys(getattr(provider, "prefetched_uris", []) or []))
        candidate_exp_uris = [
            uri
            for uri in candidate_uris
            if uri.endswith(".md")
            and not uri.endswith("/.overview.md")
            and not uri.endswith("/.abstract.md")
            and "/memories/experiences/" in uri
        ]
        if len(candidate_exp_uris) == 1:
            tracer.info(
                f"[source_traj] fallback append to sole candidate experience: {candidate_exp_uris[0]}"
            )
            return candidate_exp_uris

        existing = await self._single_existing_experience_uris(
            exp_dir=exp_dir,
            ctx=ctx,
            viking_fs=viking_fs,
        )
        if existing:
            tracer.info(f"[source_traj] fallback append by directory scan: {existing[0]}")
        return existing

    async def _single_existing_experience_uris(
        self,
        *,
        exp_dir: str,
        ctx: RequestContext,
        viking_fs,
    ) -> List[str]:
        if not exp_dir:
            return []
        try:
            entries = await viking_fs.ls(exp_dir, output="original", ctx=ctx)
        except Exception:
            return []

        uris: List[str] = []
        for entry in entries or []:
            uri = str(entry.get("uri", "")) if isinstance(entry, dict) else ""
            name = str(entry.get("name", "")) if isinstance(entry, dict) else ""
            if not uri.endswith(".md"):
                continue
            if name in {".overview.md", ".abstract.md"}:
                continue
            if uri.endswith("/.overview.md") or uri.endswith("/.abstract.md"):
                continue
            uris.append(uri)
        uris = list(dict.fromkeys(uris))
        return uris if len(uris) == 1 else []

    def _render_operation_exact_paths(
        self,
        operations: ResolvedOperations,
        ctx: RequestContext,
        viking_fs,
    ) -> List[str]:
        exact_paths: List[str] = []
        for uri in _collect_operation_lock_uris(operations):
            try:
                _append_unique(exact_paths, viking_fs._uri_to_path(uri, ctx))
            except Exception as e:
                tracer.error(f"Failed to render operation lock path for {uri}: {e}")
        return exact_paths

    async def _find_operation_version_conflicts(
        self,
        *,
        operations: ResolvedOperations,
        provider,
        ctx: RequestContext,
        viking_fs,
    ) -> List[dict[str, str]]:
        read_versions = getattr(provider, "read_file_versions", {}) or {}
        if not read_versions:
            return []

        registry_getter = getattr(provider, "_get_registry", None)
        registry = registry_getter() if callable(registry_getter) else None
        conflict_sensitive_uris = _collect_conflict_sensitive_operation_uris(
            operations,
            registry,
        )
        if not conflict_sensitive_uris:
            return []

        conflicts: List[dict[str, str]] = []
        for uri in conflict_sensitive_uris:
            expected_digest = read_versions.get(uri)
            if not expected_digest:
                continue
            try:
                current_content = await viking_fs.read_file(uri, ctx=ctx)
                current_digest = content_digest(current_content)
            except Exception:
                current_digest = MISSING_CONTENT_DIGEST
            if current_digest != expected_digest:
                conflicts.append(
                    {
                        "uri": uri,
                        "base_digest": expected_digest,
                        "current_digest": current_digest,
                        "base_state": _digest_state(expected_digest),
                        "current_state": _digest_state(current_digest),
                    }
                )
        return conflicts

    def _clear_provider_prefetch_cache(self, provider) -> None:
        for attr in ("_read_file_versions", "_read_file_contents"):
            cache = getattr(provider, attr, None)
            if isinstance(cache, dict):
                cache.clear()

    async def _run_extract_phase(
        self,
        provider,
        messages: List[Message],
        ctx: RequestContext,
        strict_extract_errors: bool,
        phase_label: str,
        post_apply: Optional[ExtractPostApply] = None,
        force_tree_lock: bool = False,
        operation_exact_version_attempt: int = 0,
        _operation_exact_retry_driver: bool = False,
    ):
        """Run one ExtractLoop phase with its own lock scope, then apply operations.

        Returns (written_uris, edited_uris, contexts, inheritance_map, session_skill_results)
        on success, where inheritance_map maps new experience URI → inherited
        source_trajectory URIs (only populated for experiences that supersede an
        existing one).
        Returns None on failure (unless strict_extract_errors is True, in which case
        the exception is re-raised).
        """
        if not _operation_exact_retry_driver:
            next_attempt = operation_exact_version_attempt
            while True:
                result = await self._run_extract_phase(
                    provider=provider,
                    messages=messages,
                    ctx=ctx,
                    strict_extract_errors=strict_extract_errors,
                    phase_label=phase_label,
                    post_apply=post_apply,
                    force_tree_lock=force_tree_lock,
                    operation_exact_version_attempt=next_attempt,
                    _operation_exact_retry_driver=True,
                )
                if isinstance(result, _OperationExactRetrySignal):
                    next_attempt = result.next_attempt
                    continue
                return result

        from openviking.storage.transaction import get_lock_manager, init_lock_manager

        config = get_openviking_config()
        telemetry = get_current_telemetry()
        phase_metric_key = _phase_metric_key(phase_label)
        phase_started_at = asyncio.get_running_loop().time()
        lock_wait_ms = 0.0
        llm_ms = 0.0
        memory_apply_ms = 0.0
        post_apply_ms = 0.0
        skill_apply_ms = 0.0
        retry_count = 0
        operation_exact_apply = not force_tree_lock and _operation_exact_lock_enabled(
            config, phase_label
        )
        vlm = config.vlm.get_vlm_instance()
        viking_fs = get_viking_fs()

        # Build isolation_handler BEFORE creating the orchestrator so that
        # ExtractLoop.resolve_operations() can call fill_role_ids() correctly.
        extract_context = ExtractContext(messages)
        isolation_handler = MemoryIsolationHandler(ctx, extract_context)
        isolation_handler.prepare_messages()

        # Inject context into provider (mirrors extract_long_term_memories pattern)
        provider._isolation_handler = isolation_handler
        provider._ctx = ctx
        provider._viking_fs = viking_fs
        if operation_exact_apply and hasattr(provider, "_track_read_file_versions"):
            provider._track_read_file_versions = True

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=provider,
            isolation_handler=isolation_handler,
        )

        lock_manager = None
        transaction_handle = None
        if viking_fs and hasattr(viking_fs, "agfs") and viking_fs.agfs:
            init_lock_manager(viking_fs.agfs)
            lock_manager = get_lock_manager()
            if lock_manager:
                transaction_handle = lock_manager.create_handle()
            else:
                logger.debug("AGFS unavailable, running memory extraction without locks")

        try:
            if lock_manager and not operation_exact_apply:
                schemas = [
                    schema
                    for schema in provider.get_memory_schemas(ctx)
                    if getattr(schema, "memory_type", None) != SESSION_SKILL_MEMORY_TYPE
                ]
                user_ids = [ctx.user.user_id] if ctx and ctx.user else ["default"]
                agent_ids = [ctx.user.agent_id] if ctx and ctx.user else ["default"]
                exact_lock_paths, tree_lock_dirs = _render_memory_schema_locks(
                    schemas=schemas,
                    ctx=ctx,
                    viking_fs=viking_fs,
                    user_ids=user_ids,
                    agent_ids=agent_ids,
                )
                if exact_lock_paths or tree_lock_dirs:
                    tracer.info(
                        f"[{phase_label}] schema lock plan: "
                        f"exact_paths={exact_lock_paths}, tree_paths={tree_lock_dirs}"
                    )
                    telemetry.count(
                        f"memory.agent.extract.phase.{phase_metric_key}.schema_exact_lock_path_count",
                        len(exact_lock_paths),
                    )
                    telemetry.count(
                        f"memory.agent.extract.phase.{phase_metric_key}.schema_tree_lock_path_count",
                        len(tree_lock_dirs),
                    )

                retry_interval = config.memory.v2_lock_retry_interval_seconds
                max_retries = config.memory.v2_lock_max_retries
                last_lock_retry_warning_at = 0.0
                lock_wait_started_at = asyncio.get_running_loop().time()
                while True:
                    lock_acquired = await lock_manager.acquire_exact_tree_batch(
                        transaction_handle,
                        exact_paths=exact_lock_paths,
                        tree_paths=tree_lock_dirs,
                        timeout=None,
                    )
                    if lock_acquired:
                        break
                    retry_count += 1
                    if max_retries > 0 and retry_count >= max_retries:
                        raise TimeoutError(
                            f"[{phase_label}] Failed to acquire memory locks after "
                            f"{retry_count} retries (max={max_retries})"
                        )
                    last_lock_retry_warning_at = _log_memory_lock_retry(
                        retry_count=retry_count,
                        max_retries=max_retries,
                        last_warning_at=last_lock_retry_warning_at,
                        phase_label=phase_label,
                    )
                    if retry_interval > 0:
                        await asyncio.sleep(retry_interval)
                lock_wait_ms = (asyncio.get_running_loop().time() - lock_wait_started_at) * 1000

            provider._transaction_handle = transaction_handle
            orchestrator._transaction_handle = transaction_handle
            llm_started_at = asyncio.get_running_loop().time()
            operations, _ = await orchestrator.run()
            llm_ms = (asyncio.get_running_loop().time() - llm_started_at) * 1000

            if operations is None:
                tracer.info(f"[{phase_label}] No memory operations generated")
                return [], [], [], {}, []

            # Log raw LLM operations before applying.
            _op_items = [
                f"{op.memory_type}(uris={op.uris!r})"
                for op in getattr(operations, "upsert_operations", [])
            ]
            _delete_uris_raw = [dc.uri for dc in getattr(operations, "delete_file_contents", [])]
            tracer.info(
                f"[{phase_label}] LLM operations: ops={_op_items}, delete_uris={_delete_uris_raw}"
            )

            # Resolve supersedes fields (name-based Replace): find old experience URI,
            # queue for deletion, and return per-URI inheritance map so only the
            # superseding experience inherits the old source_trajectories.
            supersedes_requested = sum(
                1
                for op in operations.upsert_operations
                if op.memory_type == "experiences"
                and str(op.memory_fields.get("supersedes") or "").strip()
            )
            supersedes_delete_before = len(operations.delete_file_contents)
            supersedes_links_before = len(operations.resolved_links or [])
            inheritance_map = await self._resolve_supersedes(operations, ctx, viking_fs, provider)
            if supersedes_requested:
                supersedes_remaining = sum(
                    1
                    for op in operations.upsert_operations
                    if op.memory_type == "experiences"
                    and str(op.memory_fields.get("supersedes") or "").strip()
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.supersedes_requested",
                    supersedes_requested,
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.supersedes_resolved",
                    max(supersedes_requested - supersedes_remaining, 0),
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.supersedes_unresolved",
                    supersedes_remaining,
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.supersedes_delete_queued",
                    max(len(operations.delete_file_contents) - supersedes_delete_before, 0),
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.supersedes_inheritance_targets",
                    sum(len(uris) for uris in inheritance_map.values()),
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.supersedes_graph_links_delta",
                    max(len(operations.resolved_links or []) - supersedes_links_before, 0),
                )
            source_attribution_map: Dict[str, List[str]] = {}
            source_attribution_resolver = getattr(provider, "resolve_source_attribution", None)
            if callable(source_attribution_resolver):
                source_attribution_map = source_attribution_resolver(operations, ctx) or {}

            memory_operations, skill_operations, unsupported_skill_deletes = (
                self._split_operations_by_memory_type(operations)
            )
            source_links_attached = self._attach_source_trajectory_links_to_operations(
                memory_operations,
                provider=provider,
                inheritance_map=inheritance_map,
                source_attribution_map=source_attribution_map,
            )
            if source_links_attached:
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.source_links_attached_to_operations",
                    source_links_attached,
                )
            if unsupported_skill_deletes:
                logger.warning(
                    "[%s] Ignoring unsupported session skill deletes: %s",
                    phase_label,
                    unsupported_skill_deletes,
                )

            candidate_uris = list(dict.fromkeys(getattr(provider, "prefetched_uris", []) or []))
            registry_getter = getattr(provider, "_get_registry", None)
            provider_registry = registry_getter() if callable(registry_getter) else None
            structured_patch_conversions = _convert_plain_string_patches_to_structured(
                memory_operations,
                provider_registry,
            )
            for conversion in structured_patch_conversions:
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}."
                    f"plain_string_patch_converted.{conversion['memory_type']}"
                    f".{conversion['field']}",
                    1,
                )
            if structured_patch_conversions:
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.plain_string_patch_converted",
                    len(structured_patch_conversions),
                )
                tracer.info(
                    f"[{phase_label}] converted plain string patches to structured "
                    f"SEARCH/REPLACE patches: {structured_patch_conversions}"
                )

            operation_target_uris = _collect_operation_lock_uris(memory_operations)
            conflict_sensitive_diagnostics = _collect_conflict_sensitive_operation_diagnostics(
                memory_operations,
                provider_registry,
            )
            conflict_sensitive_uris = _collect_conflict_sensitive_operation_uris(
                memory_operations,
                provider_registry,
            )
            operation_target_uri_count = len(operation_target_uris)
            candidate_target_overlap_count = len(set(candidate_uris) & set(operation_target_uris))
            if candidate_uris or operation_target_uris:
                tracer.info(
                    f"[{phase_label}] memory target diagnostics: "
                    f"candidate_uris={candidate_uris}, "
                    f"operation_target_uris={operation_target_uris}, "
                    f"conflict_sensitive_uris={conflict_sensitive_uris}, "
                    f"candidate_target_overlap={candidate_target_overlap_count}"
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.candidate_uri_count",
                    len(candidate_uris),
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.operation_target_uri_count",
                    operation_target_uri_count,
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.operation_exact_conflict_sensitive_uri_count",
                    len(conflict_sensitive_uris),
                )
                for diagnostic in conflict_sensitive_diagnostics:
                    telemetry.count(
                        f"memory.agent.extract.phase.{phase_metric_key}."
                        f"operation_exact_conflict_sensitive_bucket."
                        f"{diagnostic['bucket']}",
                        1,
                    )
                    telemetry.count(
                        f"memory.agent.extract.phase.{phase_metric_key}."
                        f"operation_exact_conflict_sensitive_reason."
                        f"{diagnostic['reason']}",
                        1,
                    )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.candidate_target_overlap_count",
                    candidate_target_overlap_count,
                )

            async def _apply_generated_operations(lock_handle: Any):
                nonlocal memory_apply_ms, post_apply_ms, skill_apply_ms

                memory_result = MemoryUpdateResult()
                if (
                    memory_operations.upsert_operations
                    or memory_operations.delete_file_contents
                    or memory_operations.errors
                ):
                    registry = provider._get_registry()
                    updater = self._get_or_create_updater(registry, lock_handle)
                    apply_started_at = asyncio.get_running_loop().time()
                    memory_result = await updater.apply_operations(
                        memory_operations,
                        ctx,
                        extract_context=extract_context,
                        isolation_handler=isolation_handler,
                    )
                    memory_apply_ms = (asyncio.get_running_loop().time() - apply_started_at) * 1000

                tracer.info(
                    f"[{phase_label}] Applied memory ops: written={len(memory_result.written_uris)}, "
                    f"edited={len(memory_result.edited_uris)}, deleted={len(memory_result.deleted_uris)}, "
                    f"errors={len(memory_result.errors)}"
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.result_written_uris",
                    len(memory_result.written_uris),
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.result_edited_uris",
                    len(memory_result.edited_uris),
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.result_deleted_uris",
                    len(memory_result.deleted_uris),
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.result_error_count",
                    len(memory_result.errors),
                )

                archive_uri = getattr(provider, "_memory_diff_archive_uri", "") or ""
                if archive_uri and viking_fs:
                    memory_diff = await self._build_memory_diff(
                        result=memory_result,
                        operations=memory_operations,
                        viking_fs=viking_fs,
                        ctx=ctx,
                        archive_uri=archive_uri,
                    )
                    await viking_fs.write_file(
                        uri=f"{archive_uri}/memory_diff.json",
                        content=json.dumps(memory_diff, ensure_ascii=False, indent=4),
                        ctx=ctx,
                    )
                    logger.info(f"[{phase_label}] Wrote memory_diff.json to {archive_uri}")

                if post_apply:
                    post_apply_started_at = asyncio.get_running_loop().time()
                    await post_apply(
                        memory_result,
                        inheritance_map,
                        lock_handle,
                        source_attribution_map,
                    )
                    post_apply_ms = (
                        asyncio.get_running_loop().time() - post_apply_started_at
                    ) * 1000

                skill_results: List[Dict[str, Any]] = []
                if skill_operations.upsert_operations:
                    if not self.skill_processor:
                        raise RuntimeError(
                            "SkillProcessor is required for session skill extraction"
                        )
                    deduped_skill_operations = dedup_session_skill_operations(skill_operations)
                    skill_updater = SkillOperationUpdater(
                        registry=provider._get_registry(),
                        skill_processor=self.skill_processor,
                        viking_fs=viking_fs,
                    )
                    skill_apply_started_at = asyncio.get_running_loop().time()
                    skill_result = await skill_updater.apply_operations(
                        deduped_skill_operations,
                        ctx,
                    )
                    skill_apply_ms = (
                        asyncio.get_running_loop().time() - skill_apply_started_at
                    ) * 1000
                    tracer.info(
                        f"[{phase_label}] Applied session skill ops: written={len(skill_result.written_uris)}, "
                        f"edited={len(skill_result.edited_uris)}, errors={len(skill_result.errors)}"
                    )
                    if skill_result.errors:
                        logger.warning(
                            "[%s] Session skill extraction completed with %d errors",
                            phase_label,
                            len(skill_result.errors),
                        )
                    skill_results = list(skill_result.operation_results)

                contexts: List[Context] = []
                for uri in memory_result.written_uris:
                    contexts.append(
                        Context(uri=uri, category="memory_write", context_type="memory")
                    )
                for uri in memory_result.edited_uris:
                    contexts.append(Context(uri=uri, category="memory_edit", context_type="memory"))
                for uri in memory_result.deleted_uris:
                    contexts.append(
                        Context(uri=uri, category="memory_delete", context_type="memory")
                    )

                return (
                    list(memory_result.written_uris),
                    list(memory_result.edited_uris),
                    contexts,
                    inheritance_map,
                    skill_results,
                )

            def _window_coalesce_key() -> Optional[tuple[str, ...]]:
                """Return a coalesce key if this phase can join a window timeline."""

                def _skip(reason: str) -> None:
                    telemetry.count(
                        f"memory.agent.extract.phase.{phase_metric_key}."
                        f"operation_exact_apply_window_coalesce_skipped.{reason}",
                        1,
                    )

                archive_uri = getattr(provider, "_memory_diff_archive_uri", "") or ""
                if archive_uri:
                    _skip("archive")
                    return None
                if skill_operations.upsert_operations:
                    _skip("skill_operation")
                    return None
                if memory_operations.delete_file_contents or memory_operations.errors:
                    _skip("delete_or_error")
                    return None
                if post_apply and not getattr(
                    provider, "_source_links_attached_in_operations", False
                ):
                    _skip("post_apply")
                    return None
                if not memory_operations.upsert_operations:
                    _skip("empty")
                    return None
                # The queue already groups requests by overlapping exact lock
                # paths. At the window owner, coalesce safe memory-only payloads
                # for this phase and let MemoryUpdater replay same-URI patches
                # as a timeline. Delete/supersedes graph rewrites stay on the
                # ordinary FIFO path for now because endpoint cleanup has stricter
                # ordering constraints.
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}."
                    "operation_exact_apply_window_coalesce_eligible",
                    1,
                )
                return ("memory-upsert-window",)

            async def _apply_coalesced_window_operations(
                payloads: list[dict[str, Any]],
                lock_handle: Any,
            ) -> list[Any]:
                """Apply same-URI window payloads as one memory timeline."""

                if not payloads:
                    return []

                combined_operations = ResolvedOperations(
                    upsert_operations=[
                        op
                        for payload in payloads
                        for op in payload["memory_operations"].upsert_operations
                    ],
                    delete_file_contents=[],
                    errors=[],
                    resolved_links=[
                        link
                        for payload in payloads
                        for link in (payload["memory_operations"].resolved_links or [])
                    ],
                )
                leader_telemetry = payloads[0]["telemetry"]
                leader_phase_key = payloads[0]["phase_metric_key"]
                leader_prefix = f"memory.agent.extract.phase.{leader_phase_key}"
                create_new_uri_remap: dict[str, str] = {}
                if bool(
                    getattr(
                        config.memory,
                        "operation_exact_apply_window_create_new_consolidation_enabled",
                        True,
                    )
                ):
                    create_new_uri_remap = (
                        await _synthesize_create_new_experience_consolidation(
                            vlm=vlm,
                            operations=combined_operations,
                            phase_metric_key=leader_phase_key,
                        )
                    )
                combined_operations.upsert_operations = _order_upserts_for_coalesced_timeline(
                    combined_operations.upsert_operations
                )
                timeline_groups, timeline_items = _same_uri_timeline_stats(
                    combined_operations.upsert_operations
                )
                leader_telemetry.count(
                    f"{leader_prefix}.operation_exact_apply_window_timeline_groups",
                    timeline_groups,
                )
                leader_telemetry.count(
                    f"{leader_prefix}.operation_exact_apply_window_timeline_items",
                    timeline_items,
                )
                registry = payloads[0]["provider"]._get_registry()
                conflict_synthesis_enabled = bool(
                    getattr(
                        config.memory,
                        "operation_exact_apply_window_conflict_synthesis_enabled",
                        True,
                    )
                )

                async def timeline_conflict_synthesizer(
                    uri: str,
                    memory_type: str,
                    schema: Any,
                    current_file: Optional[MemoryFile],
                    resolved_ops: list[ResolvedOperation],
                    conflicts: list[dict[str, Any]],
                    _ctx: Any,
                    _extract_context: Any,
                ) -> Optional[MemoryFile]:
                    if conflicts:
                        leader_telemetry.count(
                            f"{leader_prefix}.operation_exact_apply_window_timeline_conflict_groups",
                            1,
                        )
                        leader_telemetry.count(
                            f"{leader_prefix}.operation_exact_apply_window_timeline_conflict_fields",
                            len(conflicts),
                        )
                        for conflict in conflicts:
                            field_name = str(conflict.get("field") or "unknown")
                            conflict_memory_type = str(conflict.get("memory_type") or memory_type)
                            leader_telemetry.count(
                                f"{leader_prefix}.operation_exact_apply_window_timeline_conflict_buckets.{conflict_memory_type}",
                                1,
                            )
                            leader_telemetry.count(
                                f"{leader_prefix}.operation_exact_apply_window_timeline_conflict_fields_by_name.{field_name}",
                                1,
                            )
                    if not conflict_synthesis_enabled:
                        return None
                    return await _synthesize_timeline_conflict_fields(
                        vlm=vlm,
                        uri=uri,
                        memory_type=memory_type,
                        schema=schema,
                        current_file=current_file,
                        resolved_ops=resolved_ops,
                        conflicts=conflicts,
                        phase_metric_key=payloads[0]["phase_metric_key"],
                    )

                updater = self._get_or_create_updater(
                    registry,
                    lock_handle,
                    timeline_conflict_synthesizer=timeline_conflict_synthesizer,
                )
                apply_started_at = asyncio.get_running_loop().time()
                memory_result = await updater.apply_operations(
                    combined_operations,
                    payloads[0]["ctx"],
                    extract_context=payloads[0]["extract_context"],
                    isolation_handler=payloads[0]["isolation_handler"],
                )
                coalesced_apply_ms = (asyncio.get_running_loop().time() - apply_started_at) * 1000

                written_set = set(memory_result.written_uris)
                edited_set = set(memory_result.edited_uris)
                leader_telemetry.count(
                    f"{leader_prefix}.operation_exact_apply_window_result_written_uris",
                    len(memory_result.written_uris),
                )
                leader_telemetry.count(
                    f"{leader_prefix}.operation_exact_apply_window_result_edited_uris",
                    len(memory_result.edited_uris),
                )
                leader_telemetry.count(
                    f"{leader_prefix}.operation_exact_apply_window_result_deleted_uris",
                    len(memory_result.deleted_uris),
                )
                leader_telemetry.count(
                    f"{leader_prefix}.operation_exact_apply_window_result_error_count",
                    len(memory_result.errors),
                )
                if len(written_set) > 1:
                    leader_telemetry.count(
                        f"{leader_prefix}.operation_exact_apply_window_cross_uri_create_new_groups",
                        1,
                    )
                    leader_telemetry.count(
                        f"{leader_prefix}.operation_exact_apply_window_cross_uri_create_new_uris",
                        len(written_set),
                    )
                results: list[Any] = []
                for payload in payloads:
                    payload_uris = [
                        create_new_uri_remap.get(uri, uri)
                        for uri in _collect_operation_write_uris(payload["memory_operations"])
                    ]
                    edited_uris = [uri for uri in payload_uris if uri in edited_set]
                    written_uris = [uri for uri in payload_uris if uri in written_set]

                    payload["telemetry"].add_duration(
                        f"memory.agent.extract.phase.{payload['phase_metric_key']}."
                        "operation_exact_apply_window_coalesced_memory_apply",
                        coalesced_apply_ms,
                    )
                    if payload["post_apply"]:
                        await payload["post_apply"](
                            memory_result,
                            payload["inheritance_map"],
                            lock_handle,
                            payload["source_attribution_map"],
                        )

                    contexts: List[Context] = []
                    for uri in written_uris:
                        contexts.append(
                            Context(uri=uri, category="memory_write", context_type="memory")
                        )
                    for uri in edited_uris:
                        contexts.append(
                            Context(uri=uri, category="memory_edit", context_type="memory")
                        )

                    results.append(
                        (
                            written_uris,
                            edited_uris,
                            contexts,
                            payload["inheritance_map"],
                            [],
                        )
                    )

                return results

            if lock_manager and operation_exact_apply:
                exact_lock_paths = self._render_operation_exact_paths(
                    memory_operations,
                    ctx,
                    viking_fs,
                )
                if exact_lock_paths:
                    tracer.info(
                        f"[{phase_label}] operation-exact lock plan: exact_paths={exact_lock_paths}"
                    )
                    telemetry.count(
                        f"memory.agent.extract.phase.{phase_metric_key}.operation_exact_lock_path_count",
                        len(exact_lock_paths),
                    )
                window_seconds = float(
                    getattr(config.memory, "operation_exact_apply_window_seconds", 0.0) or 0.0
                )
                if exact_lock_paths and window_seconds > 0:
                    window_key_uris = conflict_sensitive_uris or _collect_operation_write_uris(
                        memory_operations
                    )
                    window_key_paths = self._render_operation_exact_paths(
                        ResolvedOperations(
                            upsert_operations=[
                                op
                                for op in memory_operations.upsert_operations
                                if set(op.uris) & set(window_key_uris)
                            ],
                            delete_file_contents=[
                                item
                                for item in memory_operations.delete_file_contents
                                if item.uri in set(window_key_uris)
                            ],
                            errors=[],
                        ),
                        ctx,
                        viking_fs,
                    )
                    if bool(
                        getattr(
                            config.memory,
                            "operation_exact_apply_window_create_new_consolidation_enabled",
                            True,
                        )
                    ):
                        create_new_window_keys = _create_new_experience_window_keys(
                            memory_operations
                        )
                        window_key_paths.extend(create_new_window_keys)
                        if create_new_window_keys:
                            telemetry.count(
                                f"memory.agent.extract.phase.{phase_metric_key}."
                                "operation_exact_apply_window_create_new_key_count",
                                len(create_new_window_keys),
                            )
                    coalesce_key = _window_coalesce_key()
                    return await _enqueue_operation_exact_apply_window(
                        lock_manager=lock_manager,
                        window_key_paths=window_key_paths or exact_lock_paths,
                        lock_paths=exact_lock_paths,
                        window_seconds=window_seconds,
                        phase_metric_key=phase_metric_key,
                        apply_func=_apply_generated_operations,
                        coalesce_key=coalesce_key,
                        coalesce_payload={
                            "ctx": ctx,
                            "extract_context": extract_context,
                            "inheritance_map": inheritance_map,
                            "isolation_handler": isolation_handler,
                            "memory_operations": memory_operations,
                            "phase_metric_key": phase_metric_key,
                            "post_apply": post_apply,
                            "provider": provider,
                            "source_attribution_map": source_attribution_map,
                            "telemetry": telemetry,
                        },
                        coalesce_func=_apply_coalesced_window_operations if coalesce_key else None,
                    )
                retry_interval = config.memory.v2_lock_retry_interval_seconds
                max_retries = config.memory.v2_lock_max_retries
                last_lock_retry_warning_at = 0.0
                lock_wait_started_at = asyncio.get_running_loop().time()
                while True:
                    lock_acquired = await lock_manager.acquire_exact_path_batch(
                        transaction_handle,
                        exact_lock_paths,
                        timeout=None,
                    )
                    if lock_acquired:
                        break
                    retry_count += 1
                    if max_retries > 0 and retry_count >= max_retries:
                        raise TimeoutError(
                            f"[{phase_label}] Failed to acquire operation exact locks after "
                            f"{retry_count} retries (max={max_retries})"
                        )
                    last_lock_retry_warning_at = _log_memory_lock_retry(
                        retry_count=retry_count,
                        max_retries=max_retries,
                        last_warning_at=last_lock_retry_warning_at,
                        phase_label=phase_label,
                    )
                    if retry_interval > 0:
                        await asyncio.sleep(retry_interval)
                lock_wait_ms += (asyncio.get_running_loop().time() - lock_wait_started_at) * 1000

                conflicts = await self._find_operation_version_conflicts(
                    operations=memory_operations,
                    provider=provider,
                    ctx=ctx,
                    viking_fs=viking_fs,
                )
                if conflicts:
                    conflict_uris = [conflict["uri"] for conflict in conflicts]
                    diagnostics_by_uri = {
                        diagnostic["uri"]: diagnostic
                        for diagnostic in conflict_sensitive_diagnostics
                    }
                    retry_buckets: set[str] = set()
                    retry_reasons: set[str] = set()
                    stale_read_details = [
                        {
                            "uri": conflict["uri"],
                            "base": _short_digest(conflict["base_digest"]),
                            "current": _short_digest(conflict["current_digest"]),
                        }
                        for conflict in conflicts
                    ]
                    tracer.info(
                        f"[{phase_label}] operation-exact stale-read details: {stale_read_details}"
                    )
                    telemetry.count(
                        f"memory.agent.extract.phase.{phase_metric_key}."
                        "operation_exact_stale_read_uri_count",
                        len(conflicts),
                    )
                    for conflict in conflicts:
                        conflict_uri = conflict["uri"]
                        diagnostic = diagnostics_by_uri.get(conflict_uri) or {
                            "bucket": _memory_bucket_from_uri(conflict_uri),
                            "reason": "unknown_conflict",
                        }
                        bucket = diagnostic["bucket"]
                        reason = diagnostic["reason"]
                        retry_buckets.add(bucket)
                        retry_reasons.add(reason)
                        telemetry.count(
                            f"memory.agent.extract.phase.{phase_metric_key}."
                            f"operation_exact_conflict_bucket.{bucket}",
                            1,
                        )
                        telemetry.count(
                            f"memory.agent.extract.phase.{phase_metric_key}."
                            f"operation_exact_conflict_reason.{reason}",
                            1,
                        )
                        telemetry.count(
                            f"memory.agent.extract.phase.{phase_metric_key}."
                            f"operation_exact_stale_base_state.{conflict['base_state']}",
                            1,
                        )
                        telemetry.count(
                            f"memory.agent.extract.phase.{phase_metric_key}."
                            f"operation_exact_stale_current_state.{conflict['current_state']}",
                            1,
                        )
                    for bucket in retry_buckets:
                        telemetry.count(
                            f"memory.agent.extract.phase.{phase_metric_key}."
                            f"operation_exact_retry_bucket.{bucket}",
                            1,
                        )
                    for reason in retry_reasons:
                        telemetry.count(
                            f"memory.agent.extract.phase.{phase_metric_key}."
                            f"operation_exact_retry_reason.{reason}",
                            1,
                        )
                    telemetry.count(
                        f"memory.agent.extract.phase.{phase_metric_key}.operation_exact_conflicts",
                        len(conflicts),
                    )
                    raise OperationExactVersionConflict(
                        phase_label,
                        conflict_uris,
                        conflicts,
                    )

            return await _apply_generated_operations(transaction_handle)
        except Exception as e:
            if isinstance(e, OperationExactVersionConflict) and operation_exact_apply:
                if lock_manager and transaction_handle:
                    try:
                        await lock_manager.release(transaction_handle)
                    except Exception as release_error:
                        logger.warning(
                            "[%s] Failed to release exact locks before retry: %s",
                            phase_label,
                            release_error,
                        )
                    transaction_handle = None
                next_attempt = operation_exact_version_attempt + 1
                logger.warning(
                    "[%s] Operation-exact apply saw stale reads; retrying with "
                    "refreshed reads under exact locks (attempt=%s): %s",
                    phase_label,
                    next_attempt,
                    e.conflicts,
                )
                telemetry.count(
                    f"memory.agent.extract.phase.{phase_metric_key}.operation_exact_retries",
                    1,
                )
                telemetry.set(
                    f"memory.agent.extract.phase.{phase_metric_key}.operation_exact_retry_attempt",
                    next_attempt,
                )
                self._clear_provider_prefetch_cache(provider)
                retry_interval = getattr(config.memory, "v2_lock_retry_interval_seconds", 0.0)
                if retry_interval > 0:
                    await asyncio.sleep(retry_interval)
                return _OperationExactRetrySignal(next_attempt)
            logger.error(f"[{phase_label}] Failed to extract: {e}", exc_info=True)
            if strict_extract_errors:
                raise
            return None
        finally:
            total_ms = (asyncio.get_running_loop().time() - phase_started_at) * 1000
            telemetry.count("memory.agent.extract.phase.count", 1)
            telemetry.count(f"memory.agent.extract.phase.{phase_metric_key}.count", 1)
            telemetry.add_duration("memory.agent.extract.phase.total", total_ms)
            telemetry.add_duration(f"memory.agent.extract.phase.{phase_metric_key}.total", total_ms)
            telemetry.add_duration(
                f"memory.agent.extract.phase.{phase_metric_key}.lock_wait", lock_wait_ms
            )
            telemetry.add_duration(f"memory.agent.extract.phase.{phase_metric_key}.llm", llm_ms)
            telemetry.add_duration(
                f"memory.agent.extract.phase.{phase_metric_key}.memory_apply",
                memory_apply_ms,
            )
            telemetry.add_duration(
                f"memory.agent.extract.phase.{phase_metric_key}.post_apply",
                post_apply_ms,
            )
            telemetry.add_duration(
                f"memory.agent.extract.phase.{phase_metric_key}.skill_apply",
                skill_apply_ms,
            )
            telemetry.count(
                f"memory.agent.extract.phase.{phase_metric_key}.lock_retries",
                retry_count,
            )
            tracer.info(
                f"[{phase_label}] timings: total_ms={total_ms:.1f}, "
                f"lock_wait_ms={lock_wait_ms:.1f}, llm_ms={llm_ms:.1f}, "
                f"memory_apply_ms={memory_apply_ms:.1f}, post_apply_ms={post_apply_ms:.1f}, "
                f"skill_apply_ms={skill_apply_ms:.1f}, lock_retries={retry_count}"
            )
            if lock_manager and transaction_handle:
                try:
                    await lock_manager.release(transaction_handle)
                except Exception as e:
                    logger.warning(f"[{phase_label}] Failed to release transaction lock: {e}")

    async def _resolve_supersedes(
        self,
        operations: ResolvedOperations,
        ctx,
        viking_fs,
        provider,
    ) -> Dict[str, List[str]]:
        """Resolve supersedes fields in experience upsert operations.

        For each experience with a non-empty `supersedes` field, find the old
        experience file by name, append it to delete_file_contents so
        apply_operations handles deletion uniformly, then pop `supersedes` from
        memory_fields so it is not written to disk.

        Returns a mapping from new experience URI → inherited source_trajectory URIs,
        so the caller can apply inherited trajectories only to the superseding experience,
        not to every experience written in the same batch.
        """
        from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

        inheritance_map: Dict[str, List[str]] = {}
        replacement_map: Dict[str, str] = {}

        exp_dir: str = ""
        if hasattr(provider, "_render_experience_dir"):
            exp_dir = provider._render_experience_dir(ctx) or ""

        prefetched_uris = set(getattr(provider, "prefetched_uris", []) or [])
        read_versions = getattr(provider, "read_file_versions", {}) or {}

        def normalize_supersedes_candidate(value: str) -> str:
            name = value.strip().strip("`'\"")
            if name.endswith(".md"):
                name = name[:-3]
            return name

        def supersedes_candidate_groups(raw_value: str) -> List[List[str]]:
            raw_candidate = normalize_supersedes_candidate(raw_value)
            if not raw_candidate:
                return []

            split_candidates: List[str] = []
            for part in re.split(r"[,;\n]+", raw_value):
                name = normalize_supersedes_candidate(part)
                if name and name not in split_candidates:
                    split_candidates.append(name)

            if not split_candidates or split_candidates == [raw_candidate]:
                return [[raw_candidate]]
            # Try the exact raw name first so valid file names containing separators still work.
            return [[raw_candidate], split_candidates]

        def unique_append(candidates: List[str], value: str) -> None:
            if value and value not in candidates:
                candidates.append(value)

        def collect_inherited_source_links(old_mf: Any) -> List[str]:
            inherited: List[str] = []
            for link in old_mf.links:
                if link.get("link_type") == "derived_from":
                    unique_append(inherited, link.get("to_uri", ""))
            return inherited

        def attach_inherited_source_links(new_uri: str, source_uris: List[str]) -> None:
            if not source_uris:
                return
            current = inheritance_map.setdefault(new_uri, [])
            for uri in source_uris:
                unique_append(current, uri)

        def attach_inherited_graph_links(new_uri: str, old_mf: Any) -> None:
            if not new_uri or not old_mf.uri:
                return

            if operations.resolved_links is None:
                operations.resolved_links = []
            existing = {
                (link.from_uri, link.to_uri, link.link_type, link.match_text or "")
                for link in operations.resolved_links
            }

            for raw_link in [*(old_mf.links or []), *(old_mf.backlinks or [])]:
                if not isinstance(raw_link, dict):
                    continue
                from_uri = str(raw_link.get("from_uri") or "")
                to_uri = str(raw_link.get("to_uri") or "")
                if old_mf.uri not in {from_uri, to_uri}:
                    continue

                inherited = dict(raw_link)
                if from_uri == old_mf.uri:
                    inherited["from_uri"] = new_uri
                if to_uri == old_mf.uri:
                    inherited["to_uri"] = new_uri

                if not inherited.get("from_uri") or not inherited.get("to_uri"):
                    continue
                if inherited["from_uri"] == inherited["to_uri"]:
                    continue

                try:
                    link = StoredLink(**inherited)
                except Exception as e:
                    tracer.error(
                        f"[supersedes] failed to inherit graph link from {old_mf.uri}: {e}"
                    )
                    continue

                key = (link.from_uri, link.to_uri, link.link_type, link.match_text or "")
                if key in existing:
                    continue
                operations.resolved_links.append(link)
                existing.add(key)

        def remap_replacement_graph_links() -> None:
            if not replacement_map or not operations.resolved_links:
                return

            remapped: List[StoredLink] = []
            existing: set[tuple[str, str, str, str]] = set()
            for link in operations.resolved_links:
                from_uri = replacement_map.get(link.from_uri, link.from_uri)
                to_uri = replacement_map.get(link.to_uri, link.to_uri)
                if not from_uri or not to_uri or from_uri == to_uri:
                    continue
                next_link = link
                if from_uri != link.from_uri or to_uri != link.to_uri:
                    next_link = link.model_copy(update={"from_uri": from_uri, "to_uri": to_uri})

                key = (
                    next_link.from_uri,
                    next_link.to_uri,
                    next_link.link_type,
                    next_link.match_text or "",
                )
                if key in existing:
                    continue
                remapped.append(next_link)
                existing.add(key)

            operations.resolved_links = remapped

        def append_delete_target(old_mf: Any) -> None:
            if all(existing.uri != old_mf.uri for existing in operations.delete_file_contents):
                operations.delete_file_contents.append(old_mf)

        def should_warn_for_group(group_index: int, group_count: int) -> bool:
            return group_index == group_count - 1

        async def resolve_supersedes_candidate(
            candidate: str,
            *,
            warn_on_failure: bool,
        ) -> tuple[bool, bool, str | None]:
            old_uri = render_supersedes_uri(candidate)

            # Guard: never delete the file we are about to write (same-name edge case)
            if old_uri == new_uri or old_uri in (op.uris or []):
                tracer.info(f"[supersedes] skipping self-reference: {old_uri}")
                return False, True, None

            try:
                raw = await viking_fs.read_file(old_uri, ctx=ctx) or ""
                _record_provider_read_version(provider, old_uri, raw)
                old_mf = MemoryFileUtils.read(raw, uri=old_uri)
                append_delete_target(old_mf)
                tracer.info(f"[supersedes] '{candidate}' → queued for delete: {old_uri}")
                if new_uri:
                    replacement_map[old_uri] = new_uri
                    attach_inherited_source_links(new_uri, collect_inherited_source_links(old_mf))
                    attach_inherited_graph_links(new_uri, old_mf)
                return True, False, None
            except Exception as e:
                if old_uri in prefetched_uris:
                    base_digest = read_versions.get(old_uri) or "unknown"
                    raise OperationExactVersionConflict(
                        "experience_supersedes",
                        [old_uri],
                        [
                            {
                                "uri": old_uri,
                                "base_digest": base_digest,
                                "current_digest": MISSING_CONTENT_DIGEST,
                                "base_state": _digest_state(base_digest),
                                "current_state": _digest_state(MISSING_CONTENT_DIGEST),
                            }
                        ],
                    ) from e
                message = f"[supersedes] failed to resolve '{old_uri}': {e}"
                if warn_on_failure:
                    logger.warning(message)
                return False, False, message

        def render_supersedes_uri(name: str) -> str:
            if name.startswith("viking://"):
                return name if name.endswith(".md") else f"{name}.md"
            return f"{exp_dir.rstrip('/')}/{name}.md"

        for op in operations.upsert_operations:
            if op.memory_type != "experiences":
                continue
            supersedes_name = str(op.memory_fields.get("supersedes") or "").strip()
            if not supersedes_name:
                continue

            if not exp_dir:
                message = "[supersedes] could not resolve experience directory"
                logger.warning(message)
                operations.errors.append(message)
                continue

            # Derive the new URI from experience_name (filename_template: "{{ experience_name }}.md")
            new_name = (op.memory_fields.get("experience_name") or "").strip()
            new_uri = f"{exp_dir.rstrip('/')}/{new_name}.md" if new_name else None

            resolved_any = False
            unresolved_messages: List[str] = []
            groups = supersedes_candidate_groups(supersedes_name)
            for group_index, candidates in enumerate(groups):
                group_resolved = False
                group_self_references = 0
                group_unresolved_messages: List[str] = []
                warn_on_failure = should_warn_for_group(group_index, len(groups))
                for candidate in candidates:
                    (
                        resolved,
                        self_reference,
                        unresolved_message,
                    ) = await resolve_supersedes_candidate(
                        candidate,
                        warn_on_failure=warn_on_failure,
                    )
                    if resolved:
                        group_resolved = True
                    if self_reference:
                        group_self_references += 1
                    if unresolved_message:
                        group_unresolved_messages.append(unresolved_message)

                if group_resolved:
                    resolved_any = True
                    break
                if group_self_references and group_self_references == len(candidates):
                    resolved_any = True
                    break
                unresolved_messages = group_unresolved_messages

            if resolved_any:
                op.memory_fields.pop("supersedes", None)
            else:
                operations.errors.extend(unresolved_messages)

        remap_replacement_graph_links()
        return inheritance_map

    def _attach_source_trajectory_links_to_operations(
        self,
        operations: ResolvedOperations,
        *,
        provider: Any,
        inheritance_map: Dict[str, List[str]],
        source_attribution_map: Dict[str, List[str]],
    ) -> int:
        """Attach system-managed exp→trajectory links before operation apply.

        These links participate in the same exact-lock/window path as the
        experience upsert/delete. This keeps supersedes replacement,
        trajectory-source inheritance, and backlink updates as one graph update
        instead of a post-apply best-effort append.
        """

        current_traj_uri = str(getattr(provider, "trajectory_uri", "") or "").strip()
        existing = {
            (link.from_uri, link.to_uri, link.link_type)
            for link in (operations.resolved_links or [])
        }
        attached = 0
        now = datetime.now(timezone.utc).isoformat()

        def extend_unique(target: List[str], values: List[str]) -> None:
            for value in values:
                if value and value not in target:
                    target.append(value)

        for op in operations.upsert_operations:
            if op.memory_type != "experiences":
                continue
            experience_name = str(op.memory_fields.get("experience_name") or "").strip()
            for exp_uri in op.uris:
                source_uris: List[str] = []
                if current_traj_uri:
                    source_uris.append(current_traj_uri)
                extend_unique(source_uris, source_attribution_map.get(exp_uri, []))
                if experience_name:
                    extend_unique(source_uris, source_attribution_map.get(experience_name, []))
                extend_unique(source_uris, inheritance_map.get(exp_uri, []))

                for source_uri in source_uris:
                    if not source_uri or source_uri == exp_uri:
                        continue
                    key = (exp_uri, source_uri, "derived_from")
                    if key in existing:
                        continue
                    operations.resolved_links.append(
                        StoredLink(
                            from_uri=exp_uri,
                            to_uri=source_uri,
                            link_type="derived_from",
                            weight=1.0,
                            created_at=now,
                        )
                    )
                    existing.add(key)
                    attached += 1

        if attached:
            provider._source_links_attached_in_operations = True
        return attached

    async def _append_trajectories_to_experiences(
        self,
        exp_uris: List[str],
        traj_uris: List[str],
        ctx,
        viking_fs,
        lock_handle: Optional[Any] = None,
    ) -> None:
        """Write bidirectional StoredLinks between traj_uris and each exp file.

        Called after experience write/edit. The LLM never outputs these links;
        the pipeline appends them so the relationship is always system-managed.
        """
        normalized_traj_uris = [uri for uri in traj_uris if uri]
        if not normalized_traj_uris:
            return

        for exp_uri in exp_uris:
            try:
                try:
                    from openviking.storage.transaction import LockContext, get_lock_manager

                    lock_manager = get_lock_manager()
                except Exception:
                    await self._append_trajectory_metadata(
                        exp_uri,
                        normalized_traj_uris,
                        ctx,
                        viking_fs,
                    )
                    continue

                lock_path = viking_fs._uri_to_path(exp_uri, ctx=ctx)
                async with LockContext(
                    lock_manager,
                    [lock_path],
                    lock_mode="exact",
                    handle=lock_handle,
                ):
                    await self._append_trajectory_metadata(
                        exp_uri,
                        normalized_traj_uris,
                        ctx,
                        viking_fs,
                    )
            except Exception as e:
                logger.warning(f"Failed to append source trajectories to {exp_uri}: {e}")

    async def _append_trajectory_metadata(
        self,
        exp_uri: str,
        traj_uris: List[str],
        ctx,
        viking_fs,
    ) -> None:
        raw = await viking_fs.read_file(exp_uri, ctx=ctx) or ""
        mf = MemoryFileUtils.read(raw, uri=exp_uri)

        # exp→traj: one directed edge per trajectory.
        # write_stored_links writes it to exp.links (forward) and traj.backlinks (reverse) automatically.
        now = datetime.now(timezone.utc).isoformat()
        links = [
            StoredLink(
                from_uri=exp_uri, to_uri=t, link_type="derived_from", weight=1.0, created_at=now
            )
            for t in traj_uris
        ]

        new_exp_links = merge_links(mf.links, [l.model_dump() for l in links])
        links_changed = len(new_exp_links) != len(mf.links)
        mf.links = new_exp_links

        if links_changed:
            await viking_fs.write_file(exp_uri, MemoryFileUtils.write(mf), ctx=ctx)
            tracer.info(
                f"[agent_link] wrote exp→traj links -> {exp_uri} (traj_count={len(traj_uris)})"
            )
        else:
            tracer.info(f"[agent_link] links already present, skip: {exp_uri}")

        # Write traj.backlinks — exp_uri already handled above
        await write_stored_links(links, ctx, viking_fs, skip_uris={exp_uri})

    async def _build_memory_diff(
        self,
        result: MemoryUpdateResult,
        operations: ResolvedOperations,
        viking_fs: VikingFS,
        ctx: RequestContext,
        archive_uri: str = "",
    ) -> Dict[str, Any]:
        """Build memory_diff.json structure from operations and result.

        Args:
            result: Memory update result containing written/edited/deleted URIs.
            operations: Resolved operations containing original content.
            viking_fs: VikingFS instance for reading file contents.
            ctx: Request context.
            archive_uri: The archive URI for this extraction.

        Returns:
            Dictionary containing memory_diff structure.
        """
        adds = []
        updates = []
        deletes = []

        # Build lookup maps for efficient access
        # Handle multi-URI operations correctly
        upsert_by_uri = {}
        for op in operations.upsert_operations:
            for uri in op.uris:
                upsert_by_uri[uri] = op
        delete_by_uri = {dc.uri: dc for dc in operations.delete_file_contents}

        # Process written_uris - distinguish between add and update
        # Use old_memory_file_content from the operation to determine if this is
        # an update (old content existed) or a new add.
        for uri in result.written_uris:
            op = upsert_by_uri.get(uri)
            memory_type = op.memory_type if op else self._get_memory_type_from_uri(uri)
            old_file = op.old_memory_file_content if op else None

            if old_file:
                # Old content existed, this is an update
                before_content = old_file.content
                updates.append(
                    {
                        "uri": uri,
                        "memory_type": memory_type,
                        "before": before_content,
                        "after": "",  # Will be filled after
                    }
                )
            else:
                # No old content, this is a new add
                adds.append(
                    {
                        "uri": uri,
                        "memory_type": memory_type,
                        "after": "",  # Will be filled after
                    }
                )

        # Process edited_uris - these are updates
        for uri in result.edited_uris:
            op = upsert_by_uri.get(uri)
            memory_type = op.memory_type if op else self._get_memory_type_from_uri(uri)
            old_mf = op.old_memory_file_content if op and op.old_memory_file_content else None
            before_content = old_mf.content if old_mf else ""
            updates.append(
                {
                    "uri": uri,
                    "memory_type": memory_type,
                    "before": before_content,
                    "after": "",  # Will be filled after
                }
            )

        # Process deleted_uris - from delete_file_contents
        for uri in result.deleted_uris:
            deleted_content = None
            dc = delete_by_uri.get(uri)
            if dc:
                memory_type = dc.memory_type or "unknown"
                deleted_content = dc.content
            else:
                memory_type = "unknown"
                deleted_content = ""
            deletes.append(
                {
                    "uri": uri,
                    "memory_type": memory_type,
                    "deleted_content": deleted_content,
                }
            )

        # Read new content for adds and updates
        for item in adds + updates:
            try:
                content = await viking_fs.read_file(uri=item["uri"], ctx=ctx)
                mf = MemoryFileUtils.read(content)
                item["after"] = mf.content
            except Exception:
                pass

        return {
            "archive_uri": archive_uri,
            "extracted_at": datetime.utcnow().isoformat() + "Z",
            "operations": {
                "adds": adds,
                "updates": updates,
                "deletes": deletes,
            },
            "summary": {
                "total_adds": len(adds),
                "total_updates": len(updates),
                "total_deletes": len(deletes),
            },
        }

    def _get_memory_type_from_uri(self, uri: str) -> str:
        """Extract memory type from URI.

        Examples:
            memory/user/xxx/identity.md -> identity
            memory/user/xxx/context/project.md -> context

        Args:
            uri: Memory file URI.

        Returns:
            Memory type (filename without extension) or 'unknown'.
        """
        parts = uri.split("/")
        for part in parts:
            if part.endswith(".md"):
                return part.replace(".md", "")
        return "unknown"
