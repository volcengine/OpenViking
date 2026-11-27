# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Apply scoped, non-overwriting memory directory maintenance."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openviking.core.namespace import classify_uri
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import (
    DirectoryOperation,
    MemoryFile,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdateResult
from openviking.session.memory.tools import verified_preference_directory_owner
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.uri import generate_uri, render_template
from openviking.storage.abstract_overview import parse_abstract_overview
from openviking.storage.acl import AclAction
from openviking.storage.expr import And, Eq
from openviking.storage.internal_names import (
    MULTIWRITE_EXACT_LOCK_FILE_PREFIX,
    MULTIWRITE_PATH_LOCK_FILE,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils.tags import normalize_search_tags
from openviking_cli.exceptions import NotFoundError

if TYPE_CHECKING:
    from openviking.session.memory.dataclass import MemoryTypeSchema

_IDENTITY_FIELDS = {"entities": ("category", "name"), "preferences": ("user", "topic")}
_DERIVED_FILES = frozenset({".abstract.md", ".overview.md", ".relations.json"})
_LOCK_TIMEOUT_SECONDS = 5


@dataclass
class DirectoryMergeResult:
    operations: ResolvedOperations = field(
        default_factory=lambda: ResolvedOperations(
            upsert_operations=[], delete_file_contents=[], errors=[]
        )
    )
    result: MemoryUpdateResult = field(default_factory=MemoryUpdateResult)
    status: str = "pending"
    remaining: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    pruned: bool = False


def _check_plan(
    plan: DirectoryOperation, registry: MemoryTypeRegistry, ctx: RequestContext
) -> tuple[MemoryTypeSchema, str, str | None]:
    if plan.operation not in {"merge", "rename"}:
        raise ValueError("Unsupported memory directory operation")
    schema = registry.get(plan.memory_type)
    if plan.memory_type not in _IDENTITY_FIELDS or schema is None or not schema.enabled:
        raise ValueError(f"Unsupported merge memory type: {plan.memory_type}")
    source_root, _, source_name = plan.source_uri.rpartition("/")
    target_root, _, target_name = plan.target_uri.rpartition("/")
    if (
        source_root != target_root
        or source_name.rstrip(" .").casefold() == target_name.rstrip(" .").casefold()
        or not all(name and not name.startswith(".") for name in (source_name, target_name))
    ):
        raise ValueError("Directory merge requires distinct sibling memory directories")
    for uri in (plan.source_uri, plan.target_uri):
        parts = uri.removeprefix("viking://").split("/")
        if (
            not uri.startswith("viking://")
            or any(not part or part in {".", ".."} for part in parts)
            or any(char in uri for char in ("\\", "?", "#", "\x00"))
        ):
            raise ValueError("Directory merge requires explicit canonical Viking URIs")
    scope = classify_uri(plan.source_uri)
    if (
        not scope.is_memory
        or scope.content_index is None
        or len(scope.parts) != scope.content_index + 3
        or scope.parts[-2] != plan.memory_type
        or scope.parts[1] != ctx.user.user_id
        or (scope.content_index == 4 and not schema.peer_enabled)
    ):
        raise ValueError("Directory merge must stay at the first level of this user's memory type")
    owner = None
    if plan.memory_type == "preferences":
        owner = verified_preference_directory_owner(plan.source_uri, plan.target_uri, ctx)
        if owner is None:
            raise ValueError(
                "Preference directory merge requires a verified alias of the same owner"
            )
    return schema, "/".join(scope.parts[1 : scope.content_index]), owner


async def _directory_stat(fs, uri: str, ctx: RequestContext) -> dict[str, Any] | None:
    try:
        return await fs.stat(uri, ctx=ctx, skip_count=True, include_vector_fields=False)
    except (NotFoundError, FileNotFoundError):
        return None


async def _check_endpoints(fs, plan: DirectoryOperation, ctx: RequestContext) -> str | None:
    source = await _directory_stat(fs, plan.source_uri, ctx)
    if source is None:
        return "source_missing"
    if not source.get("isDir"):
        raise ValueError("Memory directory maintenance requires a source directory")
    target = await _directory_stat(fs, plan.target_uri, ctx)
    if plan.operation == "rename":
        return "target_exists" if target is not None else None
    if target is None:
        return "target_missing"
    if not target.get("isDir"):
        raise ValueError("Memory directory merge requires a destination directory")
    return None


async def _entries(fs, path: str, ctx: RequestContext) -> dict[str, bool]:
    # A browseable listing can omit ACL-filtered, hidden or internal files.
    # The caller has authorized the directory, and each card is authorized on use.
    entries = await fs._ls_entries(path, filter_internal=False, ctx=ctx)
    return {
        entry["name"]: bool(fs._normalize_ls_entry(dict(entry)).get("isDir"))
        for entry in entries
        if entry["name"] != MULTIWRITE_PATH_LOCK_FILE
        and not entry["name"].startswith(MULTIWRITE_EXACT_LOCK_FILE_PREFIX)
    }


def _card_names(entries: dict[str, bool]) -> list[str]:
    return sorted(
        name
        for name, is_dir in entries.items()
        if not is_dir and not name.startswith(".") and name.endswith(".md")
    )


async def _read_verified_preferences(fs, directory, names, owner, ctx) -> dict[str, MemoryFile]:
    cards = {}
    for name in names:
        uri = f"{directory}/{name}"
        card = MemoryFileUtils.read(await fs.read_file(uri, ctx=ctx), uri=uri)
        if card.memory_type == "preferences":
            user = card.extra_fields.get("user")
            if not isinstance(user, str) or not user.isascii() or user.strip(" ").lower() != owner:
                raise ValueError("Stored preference owner does not match directory owner")
        cards[name] = card
    return cards


async def _get_search_tags(store, uri: str, ctx: RequestContext) -> list[str]:
    if store is None:
        return []
    records, cursor = await store.scroll(
        filter=And([Eq("uri", uri), Eq("level", 2)]),
        limit=2,
        output_fields=["search_tags"],
        ctx=ctx,
    )
    if len(records) > 1 or cursor is not None:
        raise ValueError("Multiple detail index records for one memory file")
    return (
        normalize_search_tags(records[0].get("search_tags"), discard_invalid=True)
        if records
        else []
    )


async def _prepare_operations(
    fs,
    plan: DirectoryOperation,
    schema: MemoryTypeSchema,
    user_space: str,
    preference_owner: str | None,
    source_entries: dict[str, bool],
    target_entries: dict[str, bool],
    ctx: RequestContext,
) -> DirectoryMergeResult:
    report = DirectoryMergeResult()
    target_names = {name.casefold() for name in target_entries}
    identity_field, leaf_field = _IDENTITY_FIELDS[plan.memory_type]
    target_identity = {identity_field: plan.target_uri.rsplit("/", 1)[1]}
    source_cards = _card_names(source_entries)
    prepared_preferences: dict[str, MemoryFile] = {}
    if preference_owner is not None:
        # A wrong owner in either directory makes the alias proposal unsafe.
        # Check every typed card before considering conflicts or any mutations.
        await _read_verified_preferences(
            fs, plan.target_uri, _card_names(target_entries), preference_owner, ctx
        )
        prepared_preferences = await _read_verified_preferences(
            fs, plan.source_uri, source_cards, preference_owner, ctx
        )
    store = fs._get_vector_store()
    for name in source_cards:
        source_uri = f"{plan.source_uri}/{name}"
        target_uri = f"{plan.target_uri}/{name}"
        if name.casefold() in target_names:
            report.conflicts.append(source_uri)
            continue
        try:
            await fs._ensure_access(source_uri, ctx, action=AclAction.WRITE)
            await fs._ensure_file_changes_allowed([source_uri, target_uri], ctx)
            old_file = prepared_preferences.get(name)
            if old_file is None:
                raw = await fs.read_file(source_uri, ctx=ctx)
                old_file = MemoryFileUtils.read(raw, uri=source_uri)
            fields = old_file.extra_fields
            if old_file.memory_type != plan.memory_type:
                continue
            if any(
                not isinstance(fields.get(key), str) or not fields[key].strip()
                for key in (identity_field, leaf_field)
            ):
                continue
            if (
                preference_owner is None
                and generate_uri(schema, fields, user_space=user_space) != source_uri
            ) or generate_uri(
                schema, fields | target_identity, user_space=user_space
            ) != target_uri:
                continue
            tags = await _get_search_tags(store, source_uri, ctx)
        except Exception as exc:
            report.result.add_error(source_uri, exc)
            continue
        target_names.add(name.casefold())
        report.operations.upsert_operations.append(
            ResolvedOperation(
                old_memory_file_content=old_file,
                memory_fields=target_identity,
                memory_type=plan.memory_type,
                uris=[target_uri],
                search_tags=tags or None,
            )
        )
    if plan.operation == "merge":
        return report
    moving = {op.old_memory_file_content.uri for op in report.operations.upsert_operations}
    await _record_remaining(fs, plan, schema, source_entries, ctx, report, moving=moving)
    if report.remaining or report.result.errors:
        report.status = "blocked"
        return report
    await fs._ensure_file_changes_allowed([plan.source_uri, plan.target_uri], ctx, recursive=True)
    if not moving:
        sample = generate_uri(
            schema, target_identity | {leaf_field: "directory_rename_probe"}, user_space
        )
        if sample.rsplit("/", 1)[0] != plan.target_uri:
            raise ValueError("Rename destination must match the memory URI template")
    return report


async def _is_discardable_sidecar(
    fs, uri: str, name: str, schema: MemoryTypeSchema, ctx: RequestContext
) -> bool:
    raw = await fs.read_file(uri, ctx=ctx)
    if not raw.strip():
        return True
    if name == ".relations.json":
        return json.loads(raw) in ([], {})
    document = parse_abstract_overview(raw)
    generated_by = document.metadata.get("generated_by", {})
    if document.legacy:
        return False
    generated_by_semantics = generated_by.get("component") == "SemanticProcessor"
    if document.is_manual_abstract:
        if name != ".abstract.md" or not generated_by_semantics or not schema.abstract_template:
            return False
        # The semantic processor also marks memory template presets as manual.
        # Keep any edit to that preset and only discard the reproducible text.
        category = uri.rsplit("/", 2)[1]
        preset = render_template(
            schema.abstract_template,
            {
                "memory_type": schema.memory_type,
                "directory_name": category,
                "subdirectory": category,
            },
        ).strip()
        return bool(preset) and document.body.strip() == preset
    return generated_by_semantics or generated_by == (
        {"component": "MemoryUpdater", "trigger": "memory_update"}
    )


async def _record_remaining(fs, plan, schema, entries, ctx, report, *, moving=frozenset()) -> None:
    report.remaining = []
    for name, is_dir in sorted(entries.items()):
        uri = f"{plan.source_uri}/{name}"
        if uri in moving:
            continue
        if not is_dir and name in _DERIVED_FILES:
            try:
                if await _is_discardable_sidecar(fs, uri, name, schema, ctx):
                    continue
            except Exception as exc:
                report.result.add_error(uri, exc)
        report.remaining.append(uri)


async def _finish_source(fs, plan, schema, source_path, ctx, lease, report) -> None:
    try:
        entries = await _entries(fs, source_path, ctx)
    except Exception as exc:
        if is_not_found_error(exc):
            report.pruned = True
            return
        raise
    await _record_remaining(fs, plan, schema, entries, ctx, report)
    if not report.remaining and not report.result.errors:
        await fs.rm(plan.source_uri, recursive=True, ctx=ctx, lease_ref=lease)
        report.pruned = True


async def _cleanup_empty_rename_target(fs, plan, target_path, ctx, lease, report) -> None:
    if report.pruned or report.result.written_uris:
        return
    # Writing a card can create its parent before a failed write. The name was
    # absent under this same lease, so we can remove a leftover empty directory.
    try:
        target = await _directory_stat(fs, plan.target_uri, ctx)
        if target and target.get("isDir") and not await _entries(fs, target_path, ctx):
            await fs.rm(plan.target_uri, recursive=True, ctx=ctx, lease_ref=lease)
    except Exception as exc:
        report.result.add_error(plan.target_uri, exc)


async def apply_directory_operation(
    plan: DirectoryOperation,
    *,
    registry: MemoryTypeRegistry,
    ctx: RequestContext,
    apply: Callable[[ResolvedOperations, dict[str, Any]], Awaitable[MemoryUpdateResult]],
) -> DirectoryMergeResult:
    """Expand one directory declaration into ordinary card writes, then prune its source."""
    report = DirectoryMergeResult()
    try:
        schema, user_space, preference_owner = _check_plan(plan, registry, ctx)
        fs = get_viking_fs()
        root = plan.source_uri.rsplit("/", 1)[0]
        source_path = fs._uri_to_path(plan.source_uri, ctx=ctx)
        target_path = fs._uri_to_path(plan.target_uri, ctx=ctx)
        fs._validate_transfer_paths(source_path, target_path)
        await fs._ensure_access(plan.source_uri, ctx, action=AclAction.MANAGE)
        await fs._ensure_access(plan.target_uri, ctx, action=AclAction.WRITE)
        rename = plan.operation == "rename"
        if status := await _check_endpoints(fs, plan, ctx):
            return DirectoryMergeResult(status=status)
        # Lock the existing parent. A Tree lock on a missing endpoint could create it.
        lease = await fs._async_agfs.pathlock_acquire_tree(
            fs._uri_to_path(root, ctx=ctx), timeout_secs=_LOCK_TIMEOUT_SECONDS
        )
        try:
            if status := await _check_endpoints(fs, plan, ctx):
                return DirectoryMergeResult(status=status)
            report = await _prepare_operations(
                fs,
                plan,
                schema,
                user_space,
                preference_owner,
                await _entries(fs, source_path, ctx),
                {} if rename else await _entries(fs, target_path, ctx),
                ctx,
            )
            if report.status == "blocked":
                return report
            try:
                if report.operations.upsert_operations:
                    applied = await apply(report.operations, lease)
                    applied.errors[:0] = report.result.errors
                    report.result = applied
                elif rename:
                    await fs.mkdir(plan.target_uri, ctx=ctx, lease_ref=lease)
                await _finish_source(fs, plan, schema, source_path, ctx, lease, report)
            finally:
                if rename:
                    await _cleanup_empty_rename_target(fs, plan, target_path, ctx, lease, report)
        finally:
            await fs._async_agfs.pathlock_release(lease)

        from openviking.storage.queuefs.semantic_ops.directory_refresh import (
            enqueue_directory_refresh,
        )

        if report.result.written_uris:
            await enqueue_directory_refresh(
                plan.target_uri, len(report.result.written_uris), ctx=ctx
            )
        if report.pruned:
            await enqueue_directory_refresh(root, 1, ctx=ctx)
        elif report.result.deleted_uris:
            await enqueue_directory_refresh(
                plan.source_uri, len(report.result.deleted_uris), ctx=ctx
            )
        report.status = ("renamed" if rename else "merged") if report.pruned else "partial"
    except Exception as exc:
        report.status = "failed"
        report.result.add_error(plan.source_uri, exc)
    return report
