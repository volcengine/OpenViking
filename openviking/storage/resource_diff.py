# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read the R / N / F / V snapshots used by resource update planning.

This module gathers the typed R/N/F/V inputs used by the canonical resource
update plan.

- ``R`` normalized request intent — target, processing mode, and scalar fields
  such as tags that this request explicitly wants to mutate.

- ``N`` new-artifact manifest — the files a parser produced, read from the
  parse output store. Canonical add-resource prepares it once before planning:
  image references are rewritten to their final URI and their final-byte md5 is
  retained in the resulting inventory.
- ``F`` target file tree — one unbounded ``tree`` call. Any permission-denied
  subtree (or a truncated scan) marks the snapshot incomplete so the planner
  refuses deletions instead of treating unreadable files as removed.
- ``V`` target vectors — lightweight L0/L1/L2 records under the target URI.

The canonical ``add_resources`` path resolves these snapshots into a
``ResourceDiffResult`` before building a ``ContextUpdatePlan``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Tuple

from openviking.concurrency import bounded_map
from openviking.storage.internal_names import is_storage_internal_name
from openviking.storage.resource_rnfv import (
    CONTROL_BASENAMES,
    FormalEntry,
    FormalTreeSnapshot,
    NewArtifactSnapshot,
    NewEntry,
    RequestIntent,
    RNFVSnapshot,
    VectorIndexSnapshot,
    VectorRecordSnapshot,
    canonical_vector_records_by_level,
)
from openviking.utils.log_correlation import log_correlation

logger = logging.getLogger(__name__)


class ContentState(str, Enum):
    """Relationship between the new artifact (N) and formal tree (F).

    F is the source of truth for whether content currently exists.  Vector
    records are deliberately not part of this axis; their presence is expressed
    independently by :class:`IndexState`.
    """

    # Neither N nor F contains this path.
    ABSENT = "absent"
    # N and F contain the same file bytes, or the same directory node.
    UNCHANGED = "unchanged"
    # N contains a path that has no formal file and no prior vector records.
    ADDED = "added"
    # N and F both contain a file, but their bytes differ.
    MODIFIED = "modified"
    # F contains a path omitted from N.
    DELETED = "deleted"
    # N contains a path absent from F while V still has records for that path.
    RESTORE = "restore"
    # N and F contain the path with different node kinds (file versus directory).
    REPLACE_KIND = "replace_kind"


class IndexState(str, Enum):
    """Health of V for the node kind selected by N/F.

    A file is valid only with L2.  A directory is valid only with both L0 and
    L1.  This axis is independent from content change, so an unchanged file can
    still require index repair.
    """

    # No formal node and no vector record exist for the path.
    ABSENT = "absent"
    # All and only the levels expected for the current node kind are present.
    COMPLETE = "complete"
    # None of the levels expected for the current node kind are present.
    MISSING = "missing"
    # Some expected levels are present, but the complete expected set is not.
    PARTIAL = "partial"
    # Expected levels exist but are derived from content known to be out of date.
    STALE = "stale"
    # V belongs to content removed by the current update.
    OBSOLETE = "obsolete"
    # V has records while neither N nor F has a corresponding content node.
    ORPHAN = "orphan"
    # V contains a level that is invalid for the current file/directory kind.
    LEVEL_CONFLICT = "level_conflict"


@dataclass(frozen=True)
class ResourceDiffEntry:
    """One path's independent content and index facts after RNFV resolution.

    ``content_state`` determines formal-tree mutation. ``index_state`` determines
    vector repair or cleanup. ``md5`` is the final new-file fingerprint when the
    path is a file; directories intentionally never carry an aggregate MD5.
    """

    relative_path: str
    content_state: ContentState
    index_state: IndexState
    old_kind: str | None = None
    new_kind: str | None = None
    md5: str | None = None
    level_states: Mapping[int, IndexState] = field(default_factory=dict)
    level_md5s: Mapping[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceDiffResult:
    entries: Mapping[str, ResourceDiffEntry]


@dataclass(frozen=True)
class ArtifactInventory:
    """Prepared N snapshot plus paths relative to its parse artifact.

    ``entries`` are rooted at the final resource URI; ``artifact_paths`` point
    to the same files under the parse artifact. The latter keeps diff body
    fallbacks independent from the parser's optional document wrapper.
    """

    entries: Mapping[str, NewEntry]
    artifact_paths: Mapping[str, str]
    rewritten_paths: frozenset[str] = frozenset()


def count_tree_entry_kinds(
    entries: Mapping[str, NewEntry | FormalEntry],
) -> tuple[int, int, bool]:
    """Return business file/dir counts and whether a logical root is present.

    Directory plans add ``relative_path=""`` as a traversal-only root. Keeping
    it separate prevents logs from presenting that synthetic node as a file or
    directory stored below the target. A flat resource uses the same empty path
    with ``is_dir=False`` and is therefore counted as one real file.
    """
    logical_root = bool((root := entries.get("")) is not None and root.is_dir)
    files = sum(not entry.is_dir for entry in entries.values())
    directories = sum(entry.is_dir for path, entry in entries.items() if path)
    return files, directories, logical_root


def _index_state(
    records: tuple[VectorRecordSnapshot, ...],
    *,
    kind: str | None,
    content_state: ContentState,
    md5: str | None,
) -> IndexState:
    """Classify V with strict precedence.

    Content removed by this update makes its V record obsolete. A vector with
    neither new nor formal content is an orphan. For an existing node, invalid
    levels win over missing or stale checks; then missing expected levels, then
    content/fingerprint staleness, and finally complete.
    """
    if content_state is ContentState.DELETED:
        return IndexState.OBSOLETE if records else IndexState.ABSENT
    if kind is None:
        return IndexState.ORPHAN if records else IndexState.ABSENT
    valid_levels = {2} if kind == "file" else {0, 1}
    levels = {record.level for record in records}
    if levels - valid_levels:
        return IndexState.LEVEL_CONFLICT
    if not levels:
        return IndexState.MISSING
    if levels != valid_levels:
        return IndexState.PARTIAL
    if content_state == ContentState.MODIFIED:
        return IndexState.STALE
    if kind == "file" and md5:
        record_md5 = next(
            (str(record.fields.get("md5") or "") for record in records if record.level == 2),
            "",
        )
        if record_md5 and record_md5 != md5:
            return IndexState.STALE
    return IndexState.COMPLETE


async def resolve_resource_diff(
    snapshot: RNFVSnapshot,
    *,
    store: Any,
    artifact_ref: Any,
    target: Any,
    artifact_paths: Mapping[str, str] | None = None,
    concurrency: int | None = None,
) -> ResourceDiffResult:
    """Resolve R/N/F/V into final states, including bounded body fallbacks."""
    snapshot.validate_for_planning()
    if (
        not snapshot.new.complete
        or not snapshot.formal.complete
        or (snapshot.request.vectorize and not snapshot.vectors.complete)
    ):
        raise ValueError("cannot resolve an incomplete RNFV snapshot")

    new = snapshot.new.entries
    formal = snapshot.formal.entries
    vector_records = snapshot.vectors.records_by_id if snapshot.request.vectorize else {}
    records_by_rel: dict[str, list[VectorRecordSnapshot]] = {}
    for record in vector_records.values():
        records_by_rel.setdefault(record.relative_path, []).append(record)

    states: dict[str, tuple[ContentState, str | None]] = {}
    compare_paths: list[str] = []
    hash_paths: list[str] = []
    md5_fast_path_count = 0
    keys = set(new) | set(formal) | set(records_by_rel)
    for rel_path in sorted(keys):
        new_entry = new.get(rel_path)
        old_entry = formal.get(rel_path)
        if new_entry is not None and old_entry is not None:
            new_kind = "directory" if new_entry.is_dir else "file"
            old_kind = "directory" if old_entry.is_dir else "file"
            if new_kind != old_kind:
                states[rel_path] = (ContentState.REPLACE_KIND, new_entry.md5 or None)
            elif new_entry.is_dir:
                states[rel_path] = (ContentState.UNCHANGED, None)
            else:
                canonical_records, _ = canonical_vector_records_by_level(
                    records_by_rel.get(rel_path, ())
                )
                l2_record = canonical_records.get(2)
                l2_md5 = str(l2_record.fields.get("md5") or "") if l2_record else ""
                if new_entry.md5 and l2_md5:
                    md5_fast_path_count += 1
                    state = (
                        ContentState.UNCHANGED if new_entry.md5 == l2_md5 else ContentState.MODIFIED
                    )
                    states[rel_path] = (state, new_entry.md5)
                else:
                    compare_paths.append(rel_path)
        elif new_entry is not None:
            state = ContentState.RESTORE if records_by_rel.get(rel_path) else ContentState.ADDED
            states[rel_path] = (state, new_entry.md5 or None)
            if not new_entry.is_dir and not new_entry.md5:
                hash_paths.append(rel_path)
        elif old_entry is not None:
            states[rel_path] = (ContentState.DELETED, None)
        else:
            states[rel_path] = (ContentState.ABSENT, None)

    async def compare(rel_path: str) -> tuple[str, ContentState, str]:
        artifact_path = (artifact_paths or {}).get(rel_path, rel_path)
        new_bytes, old_bytes = await asyncio.gather(
            store.read_bytes(artifact_ref, artifact_path),
            target.read_file(rel_path),
        )
        from openviking.utils.content_hash import content_md5

        return (
            rel_path,
            ContentState.UNCHANGED if new_bytes == old_bytes else ContentState.MODIFIED,
            content_md5(new_bytes),
        )

    async def hash_new(rel_path: str) -> tuple[str, str]:
        artifact_path = (artifact_paths or {}).get(rel_path, rel_path)
        new_bytes = await store.read_bytes(artifact_ref, artifact_path)
        from openviking.utils.content_hash import content_md5

        return rel_path, content_md5(new_bytes)

    if concurrency is None:
        from openviking_cli.utils.config import get_openviking_config

        concurrency = int(
            get_openviking_config().queue_workers.add_resource.file_operation_concurrency
        )

    for rel_path, state, md5 in await bounded_map(
        compare_paths, compare, concurrency=max(1, concurrency)
    ):
        states[rel_path] = (state, md5)
    for rel_path, md5 in await bounded_map(hash_paths, hash_new, concurrency=max(1, concurrency)):
        state, _ = states[rel_path]
        states[rel_path] = (state, md5)

    result: dict[str, ResourceDiffEntry] = {}
    for rel_path in sorted(keys):
        state, md5 = states[rel_path]
        new_entry = new.get(rel_path)
        old_entry = formal.get(rel_path)
        new_kind = ("directory" if new_entry.is_dir else "file") if new_entry is not None else None
        old_kind = ("directory" if old_entry.is_dir else "file") if old_entry is not None else None
        current_kind = (
            new_kind if state not in {ContentState.DELETED, ContentState.ABSENT} else None
        )
        records = tuple(records_by_rel.get(rel_path, ()))
        canonical_records, _ = canonical_vector_records_by_level(records)
        result[rel_path] = ResourceDiffEntry(
            relative_path=rel_path,
            content_state=state,
            index_state=_index_state(
                tuple(canonical_records.values()),
                kind=current_kind,
                content_state=state,
                md5=md5,
            ),
            old_kind=old_kind,
            new_kind=new_kind,
            md5=md5,
        )
    resolved = ResourceDiffResult(entries=result)
    n_files, n_dirs, n_has_root = count_tree_entry_kinds(new)
    f_files, f_dirs, f_has_root = count_tree_entry_kinds(formal)
    file_entries = [
        entry
        for path, entry in resolved.entries.items()
        if path and (entry.new_kind or entry.old_kind) == "file"
    ]
    directory_entries = [
        entry
        for path, entry in resolved.entries.items()
        if path and (entry.new_kind or entry.old_kind) == "directory"
    ]
    root_entry = resolved.entries.get("")
    file_content_counts = Counter(entry.content_state.value for entry in file_entries)
    directory_content_counts = Counter(entry.content_state.value for entry in directory_entries)
    file_index_counts = Counter(entry.index_state.value for entry in file_entries)
    directory_index_counts = Counter(entry.index_state.value for entry in directory_entries)
    logger.info(
        "[ResourceDiffResult] %s target=%s n_files=%d n_dirs=%d f_files=%d f_dirs=%d "
        "logical_root=%s v_records=%d root_state=%s root_index=%s "
        "md5_fast_path=%d body_compared=%d new_files_hashed=%d "
        "states={file:%s,dir:%s} index={file:%s,dir:%s}",
        log_correlation(),
        snapshot.request.target_uri,
        n_files,
        n_dirs,
        f_files,
        f_dirs,
        str(n_has_root or f_has_root).lower(),
        len(vector_records),
        root_entry.content_state.value if root_entry is not None else "none",
        root_entry.index_state.value if root_entry is not None else "none",
        md5_fast_path_count,
        len(compare_paths),
        len(hash_paths),
        dict(file_content_counts),
        dict(directory_content_counts),
        dict(file_index_counts),
        dict(directory_index_counts),
    )
    return resolved


def _is_excluded_rel_path(rel_path: str) -> bool:
    """Control sidecars and storage-internal entries are never business files."""
    if not rel_path:
        return True
    for segment in rel_path.split("/"):
        if is_storage_internal_name(segment) or segment in CONTROL_BASENAMES:
            return True
    return False


async def read_target_file_snapshot(
    viking_fs: Any,
    target_uri: str,
    *,
    ctx: Any,
    root_is_file: bool = False,
) -> Tuple[Dict[str, FormalEntry], bool]:
    """Return ``(rel_path -> FormalEntry, complete)`` for the formal tree.

    ``complete`` is False when any entry is permission-denied, because a subtree
    we cannot see must not be interpreted as absent (which would drive deletion).
    """
    if root_is_file:
        stat = await viking_fs.stat(target_uri, ctx=ctx, skip_count=True)
        return {"": FormalEntry(is_dir=bool(stat.get("isDir")))}, True

    entries = await viking_fs.tree(
        target_uri,
        output="original",
        show_all_hidden=True,
        node_limit=None,
        level_limit=None,
        ctx=ctx,
    )
    files: Dict[str, FormalEntry] = {}
    complete = True
    for entry in entries:
        if entry.get("access") == "denied":
            complete = False
            continue
        rel_path = str(entry.get("rel_path") or "").strip("/")
        if _is_excluded_rel_path(rel_path):
            continue
        files[rel_path] = FormalEntry(is_dir=bool(entry.get("isDir")))
    return files, complete


async def prepare_artifact_inventory(
    store: Any,
    ref: Any,
    *,
    doc_rel: str = "",
    target_root_uri: str | None = None,
    root_is_file: bool = False,
) -> ArtifactInventory:
    """Prepare an artifact once and return its final-byte N snapshot.

    The one recursive walk supplies the file inventory consumed by RNFV and,
    when a final target URI is known, rewrites mapped markdown image references
    in place. Rewritten markdown immediately receives an updated manifest MD5,
    so the next diff compares the exact bytes that would be uploaded.
    """
    from openviking.parse.image_rewrite import IMAGE_MAPPINGS_FILENAME, _rewrite_content
    from openviking.parse.output import read_artifact_manifest, write_artifact_manifest
    from openviking.utils.content_hash import content_md5

    base = doc_rel.strip("/")
    prefix = f"{base}/" if base else ""
    md5_by_artifact_rel = await read_artifact_manifest(store, ref)
    entries: Dict[str, NewEntry] = {}
    artifact_paths: Dict[str, str] = {}
    rewritten_paths: set[str] = set()

    def target_relative(artifact_rel: str) -> str:
        if base and artifact_rel.startswith(prefix):
            return artifact_rel[len(prefix) :]
        return artifact_rel

    if root_is_file:
        return ArtifactInventory(
            entries={"": NewEntry(md5=md5_by_artifact_rel.get(base, ""), is_dir=False)},
            artifact_paths={"": base},
        )

    async def walk(
        directory: str,
        mapping_dir: str = "",
        inherited_mappings: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        directory_entries = await store.list(ref, directory)
        current_mappings = inherited_mappings or {}
        current_mapping_dir = mapping_dir
        mapping_entry = next(
            (entry for entry in directory_entries if entry.name == IMAGE_MAPPINGS_FILENAME),
            None,
        )
        if mapping_entry is not None:
            try:
                loaded = json.loads(
                    (await store.read_bytes(ref, mapping_entry.rel_path)).decode("utf-8")
                )
            except Exception as exc:
                logger.warning(
                    "[ArtifactInventory] Ignoring unreadable image mapping sidecar %s: %s",
                    mapping_entry.rel_path,
                    exc,
                )
                loaded = None
            if not isinstance(loaded, dict):
                if loaded is not None:
                    logger.warning(
                        "[ArtifactInventory] Ignoring invalid image mapping sidecar %s",
                        mapping_entry.rel_path,
                    )
            else:
                current_mappings = loaded
                current_mapping_dir = directory

        available_files = {
            entry.name
            for entry in directory_entries
            if not entry.is_dir and not entry.name.startswith(".")
        }
        for entry in directory_entries:
            if _is_excluded_rel_path(entry.rel_path):
                continue
            relative_path = target_relative(entry.rel_path)
            if entry.is_dir:
                if relative_path:
                    entries[relative_path] = NewEntry(is_dir=True)
                await walk(entry.rel_path, current_mapping_dir, current_mappings)
                continue

            if not relative_path:
                continue
            md5 = md5_by_artifact_rel.get(entry.rel_path, "")
            if target_root_uri and entry.name.lower().endswith((".md", ".markdown")):
                mapping_key = (
                    entry.rel_path[len(current_mapping_dir) + 1 :]
                    if current_mapping_dir and entry.rel_path.startswith(current_mapping_dir + "/")
                    else entry.rel_path
                )
                path_mappings = current_mappings.get(mapping_key)
                if isinstance(path_mappings, dict) and path_mappings:
                    content = (await store.read_bytes(ref, entry.rel_path)).decode("utf-8")
                    target_uri = f"{target_root_uri.rstrip('/')}/{relative_path}"
                    rewritten, count = _rewrite_content(
                        content,
                        target_uri.rsplit("/", 1)[0],
                        available_files,
                        {str(key): str(value) for key, value in path_mappings.items()},
                    )
                    if count:
                        final_bytes = rewritten.encode("utf-8")
                        await store.write_bytes(ref, entry.rel_path, final_bytes)
                        md5 = content_md5(final_bytes)
                        md5_by_artifact_rel[entry.rel_path] = md5
                        rewritten_paths.add(relative_path)
            entries[relative_path] = NewEntry(md5=md5, is_dir=False)
            artifact_paths[relative_path] = entry.rel_path

    await walk(base)
    if rewritten_paths:
        await write_artifact_manifest(store, ref, md5_by_artifact_rel)
    return ArtifactInventory(
        entries=entries,
        artifact_paths=artifact_paths,
        rewritten_paths=frozenset(rewritten_paths),
    )


async def build_rnfv_snapshot(
    *,
    viking_fs: Any,
    vikingdb: Any,
    store: Any,
    artifact_ref: Any,
    target_uri: str,
    ctx: Any,
    doc_rel: str = "",
    request_intent: RequestIntent | None = None,
    root_is_file: bool = False,
    target_preexisting: bool = True,
    artifact_inventory: ArtifactInventory | None = None,
) -> RNFVSnapshot:
    """Read the complete R/N/F/V inputs without deriving an executable plan."""
    request = request_intent or RequestIntent(
        target_uri=target_uri, processing_mode="semantic_and_vectors"
    )

    async def read_artifact() -> ArtifactInventory:
        return artifact_inventory or await prepare_artifact_inventory(
            store, artifact_ref, doc_rel=doc_rel, root_is_file=root_is_file
        )

    async def read_formal() -> tuple[Dict[str, FormalEntry], bool]:
        if not target_preexisting:
            return {}, True
        return await read_target_file_snapshot(
            viking_fs, target_uri, ctx=ctx, root_is_file=root_is_file
        )

    projection = request.required_vector_fields()
    if request.vectorize and (root_is_file or request.processing_mode == "vectors_only"):
        projection = projection | {"abstract"}

    tasks = [
        asyncio.create_task(read_artifact()),
        asyncio.create_task(read_formal()),
    ]
    if request.vectorize:
        tasks.append(
            asyncio.create_task(
                _read_incremental_vector_inventory(
                    vikingdb, target_uri=target_uri, ctx=ctx, projection=projection
                )
            )
        )
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    artifact, formal = results[:2]
    inventory = results[2] if request.vectorize else {}
    target_files, files_complete = formal
    base = target_uri.rstrip("/")
    prefix = base + "/"
    vector_records: Dict[str, VectorRecordSnapshot] = {}
    for record_id, record in inventory.items():
        level = int(record.get("level", -1))
        uri = str(record.get("uri") or "")
        rel = "" if uri == base else uri[len(prefix) :] if uri.startswith(prefix) else None
        if rel is None:
            raise RuntimeError(f"Vector inventory returned an out-of-scope URI: {uri}")
        fields = {
            field: record[field] for field in projection - {"id", "uri", "level"} if field in record
        }
        vector_records[record_id] = VectorRecordSnapshot(
            record_id=record_id,
            uri=uri,
            relative_path=rel,
            level=level,
            fields=fields,
        )
    return RNFVSnapshot(
        request=request,
        new=NewArtifactSnapshot(entries=artifact.entries),
        formal=FormalTreeSnapshot(entries=target_files, complete=files_complete),
        vectors=VectorIndexSnapshot(
            records_by_id=vector_records,
            projected_fields=projection,
        ),
    )


async def _read_incremental_vector_inventory(
    vikingdb: Any,
    *,
    target_uri: str,
    ctx: Any,
    projection: frozenset[str],
) -> Dict[str, Dict[str, Any]]:
    """Read the lightweight V snapshot used to build RNFV."""
    return await vikingdb.get_incremental_inventory_under_uri(
        target_uri, ctx=ctx, output_fields=sorted(projection)
    )


__all__ = [
    "ArtifactInventory",
    "ContentState",
    "IndexState",
    "ResourceDiffEntry",
    "ResourceDiffResult",
    "build_rnfv_snapshot",
    "count_tree_entry_kinds",
    "prepare_artifact_inventory",
    "read_target_file_snapshot",
]
