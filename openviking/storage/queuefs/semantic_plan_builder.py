# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile committed resource changes into a compact semantic work plan."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping

from openviking.storage.queuefs.semantic_plan import (
    FileVectorSource,
    IndexedRecordSnapshot,
    SemanticOutputs,
    SemanticPlan,
    SemanticTreeEntry,
    SemanticTreeSnapshot,
    VectorRecordRef,
)
from openviking.storage.viking_fs._diff_plan import DiffPlan, NewEntry, TargetFile
from openviking.utils.ingest_options import IngestOptions


def _parent(rel_path: str) -> str:
    parent = str(PurePosixPath(rel_path).parent)
    return "" if parent == "." else parent


def _join_uri(root_uri: str, rel_path: str) -> str:
    return root_uri.rstrip("/") if not rel_path else f"{root_uri.rstrip('/')}/{rel_path}"


def _with_ancestor_dirs(paths: set[str]) -> set[str]:
    """Return directory paths plus every ancestor through the resource root."""
    result = set(paths)
    pending = list(paths)
    while pending:
        path = pending.pop()
        if not path:
            continue
        parent = _parent(path)
        if parent not in result:
            result.add(parent)
            pending.append(parent)
    return result


def _current_tree(new: Mapping[str, NewEntry]) -> dict[str, str]:
    kinds: dict[str, str] = {"": "directory"}
    for rel_path, item in new.items():
        rel_path = rel_path.strip("/")
        if not rel_path:
            kinds[""] = "directory" if item.is_dir else "file"
            continue
        kinds[rel_path] = "directory" if item.is_dir else "file"
        parent = _parent(rel_path)
        while parent:
            kinds.setdefault(parent, "directory")
            parent = _parent(parent)
    return kinds


def _snapshot(record: Mapping[str, Any]) -> IndexedRecordSnapshot:
    return IndexedRecordSnapshot(
        record_id=str(record["id"]),
        level=int(record["level"]),
        type=(str(record["type"]) if record.get("type") is not None else None),
        abstract=(str(record["abstract"]) if record.get("abstract") is not None else None),
        md5=(str(record["md5"]) if record.get("md5") else None),
        created_at=(str(record["created_at"]) if record.get("created_at") else None),
        updated_at=(str(record["updated_at"]) if record.get("updated_at") else None),
        active_count=(
            int(record["active_count"]) if record.get("active_count") is not None else None
        ),
        name=(str(record["name"]) if record.get("name") is not None else None),
        description=(str(record["description"]) if record.get("description") is not None else None),
        tags=(str(record["tags"]) if record.get("tags") is not None else None),
        search_tags=(
            tuple(str(value) for value in record["search_tags"])
            if record.get("search_tags") is not None
            else None
        ),
    )


async def build_semantic_plan(
    *,
    root_uri: str,
    context_type: str,
    new: Mapping[str, NewEntry],
    target_files: Mapping[str, TargetFile],
    diff_plan: DiffPlan,
    inventory: Mapping[str, Mapping[str, Any]],
    vikingdb: Any,
    ctx: Any,
    vectorize: bool,
    is_code_repo: bool,
    root_preexisting: bool,
    ingest_options: IngestOptions | Mapping[str, Any] | None = None,
    source_metadata: Mapping[str, str] | None = None,
) -> SemanticPlan:
    """Build the smallest self-contained tree needed by semantic execution."""
    root_uri = root_uri.rstrip("/")
    current = _current_tree(new)
    inventory_by_uri: dict[str, list[Mapping[str, Any]]] = {}
    for record in inventory.values():
        inventory_by_uri.setdefault(str(record.get("uri") or ""), []).append(record)

    added = set(diff_plan.added)
    modified = set(diff_plan.modified) | set(diff_plan.repair)
    deleted = set(diff_plan.deleted)
    structural = set(diff_plan.structural)
    changed = added | modified | deleted | structural

    if not root_preexisting:
        retained = set(current)
        candidate_dirs = {path for path, kind in current.items() if kind == "directory"}
    else:
        old_dirs = {path for path, item in target_files.items() if item.is_dir}
        current_dirs = {path for path, kind in current.items() if kind == "directory"}
        indexed_levels_by_rel: dict[str, set[int]] = {}
        for record in inventory.values():
            uri = str(record.get("uri") or "")
            rel_path = (
                ""
                if uri == root_uri
                else uri[len(root_uri) + 1 :]
                if uri.startswith(root_uri + "/")
                else None
            )
            if rel_path is not None:
                indexed_levels_by_rel.setdefault(rel_path, set()).add(int(record.get("level", -1)))
        semantic_repairs = {
            path
            for path, kind in current.items()
            if (
                kind == "file" and not vectorize and 2 not in indexed_levels_by_rel.get(path, set())
            )
            or (kind == "directory" and indexed_levels_by_rel.get(path, set()) & {0, 1} != {0, 1})
        }
        modified.update(semantic_repairs)
        changed.update(semantic_repairs)
        candidate_dirs = {_parent(path) for path in changed} | {
            path for path in semantic_repairs if current.get(path) == "directory"
        }
        deleted_dirs = old_dirs - current_dirs
        deleted.update(deleted_dirs)
        candidate_dirs.update(_parent(path) for path in deleted_dirs)

        # New directories need their own summary and make their parent structurally dirty.
        new_dirs = (current_dirs - old_dirs) - {""}
        added.update(new_dirs)
        pending = list(new_dirs)
        while pending:
            directory = pending.pop()
            candidate_dirs.add(directory)
            parent = _parent(directory)
            candidate_dirs.add(parent)
            if parent and parent in new_dirs:
                pending.append(parent)

        # Keep the semantic graph connected through the resource root. Every
        # candidate directory contributes all direct children below, so adding
        # only the directory ancestors preserves a minimal tree rather than
        # expanding unchanged sibling subtrees.
        candidate_dirs = _with_ancestor_dirs(candidate_dirs)

        retained = set(changed) | set(deleted_dirs) | candidate_dirs
        for path in current:
            if path and _parent(path) in candidate_dirs:
                retained.add(path)

    required_ids: dict[str, Mapping[str, Any]] = {}
    entry_record_ids: dict[str, list[str]] = {}
    for rel_path in sorted(retained):
        state = (
            "deleted"
            if rel_path in deleted and rel_path not in current
            else "added"
            if not root_preexisting or rel_path in added or rel_path in structural
            else "modified"
            if rel_path in modified
            else "unchanged"
        )
        if state in {"added", "deleted"} or rel_path in structural:
            continue
        kind = current.get(rel_path)
        if kind is None:
            continue
        records = inventory_by_uri.get(_join_uri(root_uri, rel_path), [])
        if kind == "file":
            wanted_levels = {2}
        elif rel_path in candidate_dirs:
            # Active directories may be rebuilt at both semantic levels.
            wanted_levels = {0, 1}
        else:
            # An unchanged sibling directory contributes only its L0 abstract
            # when its parent is aggregated; its own overview (L1) is not read.
            wanted_levels = {0}
        for record in records:
            if int(record.get("level", -1)) not in wanted_levels:
                continue
            record_id = str(record.get("id") or "")
            if record_id:
                required_ids[record_id] = {
                    "uri": str(record.get("uri") or ""),
                    "level": int(record.get("level", -1)),
                }
                entry_record_ids.setdefault(rel_path, []).append(record_id)

    hydrated = (
        await vikingdb.hydrate_incremental_records(required_ids, ctx=ctx) if required_ids else {}
    )
    missing_required = set(required_ids) - set(hydrated)
    missing_dependency_ids = {
        record_id
        for rel_path, record_ids in entry_record_ids.items()
        if rel_path not in modified
        for record_id in record_ids
        if record_id in missing_required
    }
    if missing_dependency_ids:
        raise RuntimeError(
            "Semantic plan dependencies disappeared during hydration: "
            + ", ".join(sorted(missing_dependency_ids))
        )

    def _empty_dependency_paths() -> set[str]:
        paths: set[str] = set()
        for rel_path, record_ids in entry_record_ids.items():
            if rel_path in modified:
                continue
            required_level = 2 if current.get(rel_path) == "file" else 0
            for record_id in record_ids:
                record = hydrated.get(record_id)
                if record is None or int(record.get("level", -1)) != required_level:
                    continue
                if not str(record.get("abstract") or "").strip():
                    paths.add(rel_path)
                    break
        return paths

    empty_abstract_paths = _empty_dependency_paths()
    while empty_abstract_paths:
        # A present L0/L2 record is not a usable aggregation dependency when its
        # abstract is empty. Promote only the affected dependency to semantic
        # repair and, for directories, include its direct children.
        modified.update(empty_abstract_paths)
        candidate_dirs.update(
            path for path in empty_abstract_paths if current.get(path) == "directory"
        )
        retained.update(empty_abstract_paths)
        for path in current:
            if path and _parent(path) in candidate_dirs:
                retained.add(path)

        added_required_ids: dict[str, Mapping[str, Any]] = {}
        for rel_path in sorted(retained):
            if rel_path in added or rel_path in deleted:
                continue
            kind = current.get(rel_path)
            if kind is None:
                continue
            wanted_levels = {2} if kind == "file" else {0, 1} if rel_path in candidate_dirs else {0}
            known_ids = set(entry_record_ids.get(rel_path, ()))
            for record in inventory_by_uri.get(_join_uri(root_uri, rel_path), []):
                if int(record.get("level", -1)) not in wanted_levels:
                    continue
                record_id = str(record.get("id") or "")
                if not record_id or record_id in known_ids:
                    continue
                added_required_ids[record_id] = {
                    "uri": str(record.get("uri") or ""),
                    "level": int(record.get("level", -1)),
                }
                entry_record_ids.setdefault(rel_path, []).append(record_id)
                known_ids.add(record_id)
        if added_required_ids:
            hydrated.update(await vikingdb.hydrate_incremental_records(added_required_ids, ctx=ctx))
            missing_added = set(added_required_ids) - set(hydrated)
            if missing_added:
                raise RuntimeError(
                    "Semantic plan dependencies disappeared during hydration: "
                    + ", ".join(sorted(missing_added))
                )
        empty_abstract_paths = _empty_dependency_paths()

    entries: list[SemanticTreeEntry] = []
    for rel_path in sorted(retained, key=lambda value: (value.count("/"), value)):
        exists_now = rel_path in current
        if rel_path in deleted and not exists_now:
            old = target_files.get(rel_path)
            kind = "directory" if old is not None and old.is_dir else "file"
            records = inventory_by_uri.get(_join_uri(root_uri, rel_path), [])
            indexed_records = tuple(
                sorted((_snapshot(record) for record in records), key=lambda item: item.level)
            )
            entries.append(
                SemanticTreeEntry(
                    relative_path=rel_path,
                    kind=kind,
                    state="deleted",
                    indexed_records=indexed_records,
                )
            )
            continue
        if not exists_now:
            continue
        kind = current[rel_path]
        state = (
            "added"
            if not root_preexisting or rel_path in added or rel_path in structural
            else "modified"
            if rel_path in modified
            else "unchanged"
        )
        if rel_path in structural or (state == "added" and root_preexisting):
            indexed_records = tuple(
                sorted(
                    (
                        _snapshot(record)
                        for record in inventory_by_uri.get(_join_uri(root_uri, rel_path), [])
                    ),
                    key=lambda item: item.level,
                )
            )
        else:
            indexed_records = tuple(
                sorted(
                    (
                        _snapshot(hydrated[record_id])
                        for record_id in entry_record_ids.get(rel_path, [])
                        if record_id in hydrated
                    ),
                    key=lambda item: item.level,
                )
            )
        entries.append(
            SemanticTreeEntry(
                relative_path=rel_path,
                kind=kind,
                state=state,
                md5=(new[rel_path].md5 or None if kind == "file" else None),
                indexed_records=indexed_records,
            )
        )

    tombstone_paths = deleted | structural
    orphan_deletes: list[VectorRecordRef] = []
    orphan_ids: set[str] = set()
    for record in inventory.values():
        uri = str(record.get("uri") or "")
        rel_path = (
            ""
            if uri == root_uri
            else uri[len(root_uri) + 1 :]
            if uri.startswith(root_uri + "/")
            else None
        )
        if rel_path is None or rel_path in tombstone_paths:
            continue
        level = int(record.get("level", -1))
        current_kind = current.get(rel_path)
        valid_levels = (
            {2} if current_kind == "file" else {0, 1} if current_kind == "directory" else set()
        )
        if level in valid_levels:
            continue
        record_id = str(record.get("id") or "")
        if not record_id or record_id in orphan_ids:
            continue
        orphan_ids.add(record_id)
        orphan_deletes.append(VectorRecordRef(record_id=record_id, uri=uri, level=level))

    return SemanticPlan(
        root_uri=root_uri,
        context_type=context_type,
        tree=SemanticTreeSnapshot(entries=tuple(entries)),
        orphan_vector_deletes=tuple(
            sorted(orphan_deletes, key=lambda record: (record.uri, record.level, record.record_id))
        ),
        outputs=SemanticOutputs(vectorize=vectorize),
        file_vector_source=(
            FileVectorSource.SUMMARY_WHEN_AVAILABLE if is_code_repo else FileVectorSource.CONTENT
        ),
        ingest_options=IngestOptions.from_value(ingest_options),
        source_metadata=(dict(source_metadata) if source_metadata else None),
    )


async def build_initial_semantic_plan(
    *,
    root_uri: str,
    context_type: str,
    store: Any,
    artifact_ref: Any,
    doc_rel: str,
    md5_by_rel: Mapping[str, str],
    vectorize: bool,
    is_code_repo: bool,
    ingest_options: IngestOptions | Mapping[str, Any] | None = None,
    source_metadata: Mapping[str, str] | None = None,
    read_manifest: Any = None,
) -> SemanticPlan:
    """Build an all-added plan for a newly committed directory resource."""
    if read_manifest is None:
        from openviking.storage.resource_diff import read_new_manifest

        read_manifest_fn = read_new_manifest
    else:
        read_manifest_fn = read_manifest
    new = await read_manifest_fn(store, artifact_ref, doc_rel=doc_rel)
    new = {
        rel_path: NewEntry(md5=str(md5_by_rel.get(rel_path) or entry.md5), is_dir=entry.is_dir)
        for rel_path, entry in new.items()
    }
    return await build_semantic_plan(
        root_uri=root_uri,
        context_type=context_type,
        new=new,
        target_files={},
        diff_plan=DiffPlan(
            added=sorted(new),
            new_files=sorted(path for path, entry in new.items() if not entry.is_dir),
            new_md5s={path: entry.md5 for path, entry in new.items() if entry.md5},
        ),
        inventory={},
        vikingdb=None,
        ctx=None,
        vectorize=vectorize,
        is_code_repo=is_code_repo,
        root_preexisting=False,
        ingest_options=ingest_options,
        source_metadata=source_metadata,
    )


__all__ = ["build_initial_semantic_plan", "build_semantic_plan"]
