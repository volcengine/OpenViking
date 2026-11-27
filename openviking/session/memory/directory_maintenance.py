# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Apply scoped, non-overwriting memory directory maintenance."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from openviking.core.namespace import classify_uri
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.session.memory.dataclass import (
    DirectoryOperation,
    MemoryFile,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdater, MemoryUpdateResult
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
_LOCK_ATTEMPTS = 4
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


async def _linked_paths(fs, operations: ResolvedOperations, root: str, ctx) -> set[str]:
    paths = set()
    for operation in operations.upsert_operations:
        old_file = operation.old_memory_file_content
        for link in (*old_file.links, *old_file.backlinks):
            for uri in (link.get("from_uri"), link.get("to_uri")):
                if (
                    uri
                    and classify_uri(uri).is_memory
                    and uri != root
                    and not uri.startswith(f"{root}/")
                ):
                    await fs._ensure_access(uri, ctx, action=AclAction.WRITE)
                    paths.add(fs._uri_to_path(uri, ctx=ctx))
    return paths


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


def _combine_results(destination: MemoryUpdateResult, source: MemoryUpdateResult) -> None:
    destination.written_uris.extend(source.written_uris)
    destination.edited_uris.extend(source.edited_uris)
    destination.deleted_uris.extend(source.deleted_uris)
    destination.skipped_operations.extend(source.skipped_operations)
    destination.errors.extend(source.errors)


async def _apply_cards(
    updater, report: DirectoryMergeResult, ctx: RequestContext, *, stop_on_error: bool = False
) -> None:
    # Move one card at a time. A link to another card should point to its
    # current URI until that card's own move has actually succeeded.
    search_tags = {
        operation.uris[0]: operation.search_tags
        for operation in report.operations.upsert_operations
        if operation.search_tags
    }
    for operation in report.operations.upsert_operations:
        batch = ResolvedOperations(
            upsert_operations=[operation], delete_file_contents=[], errors=[]
        )
        applied = await run_to_completion(
            partial(updater.apply_operations, batch, ctx, search_tags_by_uri=search_tags)
        )
        _combine_results(report.result, applied)
        report.operations.delete_file_contents.extend(batch.delete_file_contents)
        report.operations.delete_replacements.update(batch.delete_replacements)
        if stop_on_error and (applied.errors or not applied.deleted_uris):
            break
    written_or_deleted = set(report.result.written_uris + report.result.deleted_uris)
    report.result.edited_uris = list(
        dict.fromkeys(uri for uri in report.result.edited_uris if uri not in written_or_deleted)
    )


async def merge_memory_directories(
    plan: DirectoryOperation,
    *,
    registry: MemoryTypeRegistry,
    vikingdb,
    ctx: RequestContext,
) -> DirectoryMergeResult:
    """Move validated cards via the ordinary memory migration path.

    The provider supplies the same-category or same-owner judgment. The
    executor enforces storage scope, identity consistency and absence of
    collisions. A partial migration never overwrites an occupied destination.
    """
    report = DirectoryMergeResult()
    try:
        schema, user_space, preference_owner = _check_plan(plan, registry, ctx)
        fs = get_viking_fs()
        root = plan.source_uri.rsplit("/", 1)[0]
        root_path = fs._uri_to_path(root, ctx=ctx)
        source_path = fs._uri_to_path(plan.source_uri, ctx=ctx)
        target_path = fs._uri_to_path(plan.target_uri, ctx=ctx)
        fs._validate_transfer_paths(source_path, target_path)
        await fs._ensure_access(plan.source_uri, ctx, action=AclAction.MANAGE)
        await fs._ensure_access(plan.target_uri, ctx, action=AclAction.WRITE)
        rename = plan.operation == "rename"
        if status := await _check_endpoints(fs, plan, ctx):
            return DirectoryMergeResult(status=status)

        exact_paths: set[str] = set()
        updater = MemoryUpdater(
            registry=registry,
            vikingdb=vikingdb,
            directory_migration=(plan.source_uri, plan.target_uri),
        )
        for _ in range(_LOCK_ATTEMPTS):
            lease = await fs._async_agfs.pathlock_acquire_exact_tree_batch(
                sorted(exact_paths),
                # RagFS's filesystem Tree locks create their lock directory if
                # absent. A child lease could recreate a concurrently deleted
                # merge endpoint. Lock their existing schema root and recheck.
                [root_path],
                timeout_secs=_LOCK_TIMEOUT_SECONDS,
            )
            async with AsyncExitStack() as cleanup:
                cleanup.push_async_callback(fs._async_agfs.pathlock_release, lease)
                if status := await _check_endpoints(fs, plan, ctx):
                    return DirectoryMergeResult(status=status)
                source_entries = await _entries(fs, source_path, ctx)
                target_entries = {} if rename else await _entries(fs, target_path, ctx)
                report = await _prepare_operations(
                    fs,
                    plan,
                    schema,
                    user_space,
                    preference_owner,
                    source_entries,
                    target_entries,
                    ctx,
                )
                if report.status == "blocked":
                    return report
                discovered = await _linked_paths(fs, report.operations, root, ctx)
                if not discovered.issubset(exact_paths):
                    exact_paths.update(discovered)
                    continue
                updater._transaction_handle = lease
                if rename:
                    cleanup.push_async_callback(
                        _cleanup_empty_rename_target, fs, plan, target_path, ctx, lease, report
                    )
                    if not report.operations.upsert_operations:
                        await fs.mkdir(plan.target_uri, ctx=ctx, lease_ref=lease)
                await _apply_cards(updater, report, ctx, stop_on_error=rename)
                await _finish_source(fs, plan, schema, source_path, ctx, lease, report)
                break
        else:
            raise RuntimeError("Directory merge could not stabilize linked-memory lock coverage")

        changed = set(report.result.written_uris + report.result.deleted_uris)
        if report.pruned:
            changed.difference_update(report.result.deleted_uris)
        if changed:
            await updater._submit_directory_changes(changed, ctx)
        if report.pruned:
            from openviking.storage.queuefs.semantic_ops.directory_refresh import (
                enqueue_directory_refresh,
            )

            await enqueue_directory_refresh(root, 1, ctx=ctx)
        if report.pruned:
            report.status = "renamed" if rename else "merged"
        else:
            report.status = "partial"
    except Exception as exc:
        report.status = "failed"
        report.result.add_error(plan.source_uri, exc)
    return report
