# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read the R / N / F / V snapshots that feed the incremental :mod:`DiffPlan`.

This module is the IO-facing counterpart to ``viking_fs._diff_plan`` (which is
pure). It gathers four snapshots into the typed data the planner expects:

- ``R`` normalized request intent — target, processing mode, and scalar fields
  such as tags that this request explicitly wants to mutate.

- ``N`` new-artifact manifest — the files a parser produced, read from the
  parse output store. md5 is filled later at the final-bytes upload site, so it
  is left empty here rather than reading bytes back just to fingerprint them.
- ``F`` target file tree — one unbounded ``tree`` call. Any permission-denied
  subtree (or a truncated scan) marks the snapshot incomplete so the planner
  refuses deletions instead of treating unreadable files as removed.
- ``V`` target vectors — md5 + abstract per file URI via the vector backend's
  fully-paginated diff reader.

Assembling the plan then delegates to :func:`build_diff_plan`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from openviking.core.namespace import uri_parts
from openviking.storage.internal_names import STORAGE_INTERNAL_ENTRY_NAMES
from openviking.storage.resource_rnfv import (
    FormalTreeSnapshot,
    NewArtifactSnapshot,
    RequestIntent,
    RNFVSnapshot,
    VectorIndexSnapshot,
    VectorRecordSnapshot,
)
from openviking.storage.viking_fs._diff_plan import (
    CONTROL_BASENAMES,
    DiffPlan,
    NewEntry,
    TargetFile,
    TargetVector,
    apply_request_scalar_intents,
    build_diff_plan,
)
from openviking_cli.utils import VikingURI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResourceDiffSnapshot:
    rnfv: RNFVSnapshot
    plan: DiffPlan

    @property
    def new(self) -> Mapping[str, NewEntry]:
        return self.rnfv.new.entries

    @property
    def target_files(self) -> Mapping[str, TargetFile]:
        return self.rnfv.formal.entries

    @property
    def vector_inventory(self) -> Dict[str, Dict[str, Any]]:
        return {
            record_id: {
                "id": record.record_id,
                "uri": record.uri,
                "level": record.level,
                **dict(record.fields),
            }
            for record_id, record in self.rnfv.vectors.records_by_id.items()
        }


def _is_excluded_rel_path(rel_path: str) -> bool:
    """Control sidecars and storage-internal entries are never business files."""
    if not rel_path:
        return True
    for segment in rel_path.split("/"):
        if segment in STORAGE_INTERNAL_ENTRY_NAMES or segment in CONTROL_BASENAMES:
            return True
    return False


async def read_target_file_snapshot(
    viking_fs: Any,
    target_uri: str,
    *,
    ctx: Any,
    root_is_file: bool = False,
) -> Tuple[Dict[str, TargetFile], bool]:
    """Return ``(rel_path -> TargetFile, complete)`` for the target tree.

    ``complete`` is False when any entry is permission-denied, because a subtree
    we cannot see must not be interpreted as absent (which would drive deletion).
    """
    if root_is_file:
        stat = await viking_fs.stat(target_uri, ctx=ctx, skip_count=True)
        return {"": TargetFile(is_dir=bool(stat.get("isDir")))}, True

    entries = await viking_fs.tree(
        target_uri,
        output="original",
        show_all_hidden=True,
        node_limit=None,
        level_limit=None,
        ctx=ctx,
    )
    files: Dict[str, TargetFile] = {}
    complete = True
    for entry in entries:
        if entry.get("access") == "denied":
            complete = False
            continue
        rel_path = str(entry.get("rel_path") or "").strip("/")
        if _is_excluded_rel_path(rel_path):
            continue
        files[rel_path] = TargetFile(is_dir=bool(entry.get("isDir")))
    return files, complete


async def read_target_vector_snapshot(
    vikingdb: Any,
    *,
    target_uri: str,
    ctx: Any,
    root_is_file: bool = False,
) -> Dict[str, TargetVector]:
    """Return every L2 vector below ``target_uri``, keyed by relative path."""
    base = target_uri.rstrip("/")
    records = (
        await vikingdb.get_l2_diff_records_by_uris([base], ctx=ctx)
        if root_is_file
        else await vikingdb.get_l2_diff_records_under_uri(base, ctx=ctx)
    )
    vectors: Dict[str, TargetVector] = {}
    prefix = base + "/"
    for uri, record in records.items():
        if root_is_file and uri == base:
            vectors[""] = TargetVector(
                md5=str(record.get("md5") or ""),
                abstract=str(record.get("abstract") or ""),
            )
            continue
        canonical_record_uri = VikingURI.build(*uri_parts(uri))
        if uri != canonical_record_uri:
            raise RuntimeError(f"Vector scan returned non-canonical L2 URI: {uri}")
        if uri == base:
            rel = ""
        elif uri.startswith(prefix):
            rel = uri[len(prefix) :]
        else:
            continue
        vectors[rel] = TargetVector(
            md5=str(record.get("md5") or ""),
            abstract=str(record.get("abstract") or ""),
        )
    return vectors


async def read_new_manifest(
    store: Any, ref: Any, *, doc_rel: str = "", root_is_file: bool = False
) -> Dict[str, NewEntry]:
    """Walk the parse output store and return ``rel_path -> NewEntry``.

    md5 is populated from the artifact manifest (``.artifact_manifest.json``)
    written at upload time, so the diff can compare fingerprints without
    re-reading files. A missing/unreadable manifest leaves md5 empty and the diff
    falls back to comparing file bytes.

    ``doc_rel`` (e.g. ``repository``) is stripped from every path so the manifest
    keys line up with the target resource tree, which has no such wrapper.
    """
    from openviking.parse.output import read_artifact_manifest

    manifest: Dict[str, NewEntry] = {}
    base = doc_rel.strip("/")
    prefix = f"{base}/" if base else ""

    # md5 sidecar is keyed by artifact-relative path (pre-strip); read once. A
    # missing manifest yields empty md5 so the diff compares bytes instead of
    # assuming equality.
    md5_by_artifact_rel = await read_artifact_manifest(store, ref)

    if root_is_file:
        return {"": NewEntry(md5=md5_by_artifact_rel.get(base, ""), is_dir=False)}

    async def _walk(rel: str) -> None:
        for entry in await store.list(ref, rel):
            if _is_excluded_rel_path(entry.rel_path):
                continue
            if entry.is_dir:
                key = entry.rel_path[len(prefix) :] if prefix else entry.rel_path
                if key:
                    manifest[key] = NewEntry(is_dir=True)
                await _walk(entry.rel_path)
                continue
            key = entry.rel_path[len(prefix) :] if prefix else entry.rel_path
            if key:
                manifest[key] = NewEntry(
                    md5=md5_by_artifact_rel.get(entry.rel_path, ""), is_dir=False
                )

    await _walk(base)
    return manifest


async def build_resource_diff_plan(
    *,
    viking_fs: Any,
    vikingdb: Any,
    store: Any,
    artifact_ref: Any,
    target_uri: str,
    ctx: Any,
    doc_rel: str = "",
    root_is_file: bool = False,
) -> DiffPlan:
    """Read all three snapshots and assemble the incremental plan.

    md5 on the N side is not yet populated (see :func:`read_new_manifest`); the
    planner therefore routes intersection files through ``needs_body_compare``
    until the apply stage supplies fingerprints at write time.

    ``doc_rel`` is the artifact-relative path of the document root (e.g.
    ``repository``); it is stripped from the manifest so the new-tree keys align
    with the target file/vector snapshots.
    """
    new = await read_new_manifest(store, artifact_ref, doc_rel=doc_rel, root_is_file=root_is_file)
    target_files, files_complete = await read_target_file_snapshot(
        viking_fs, target_uri, ctx=ctx, root_is_file=root_is_file
    )
    target_vectors = await read_target_vector_snapshot(
        vikingdb,
        target_uri=target_uri,
        ctx=ctx,
        root_is_file=root_is_file,
    )
    plan = build_diff_plan(
        new=new,
        target_files=target_files,
        target_vectors=target_vectors,
        target_files_complete=files_complete,
        target_vectors_complete=True,
    )
    _log_diff_diagnostics(
        target_uri=target_uri,
        new=new,
        target_files=target_files,
        target_vectors=target_vectors,
        plan=plan,
        target_files_complete=files_complete,
    )
    return plan


async def build_resource_diff_snapshot(
    *,
    viking_fs: Any,
    vikingdb: Any,
    store: Any,
    artifact_ref: Any,
    target_uri: str,
    ctx: Any,
    doc_rel: str = "",
    require_vectors: bool = True,
    request_intent: RequestIntent | None = None,
    root_is_file: bool = False,
) -> ResourceDiffSnapshot:
    """Read a complete R/N/F/V snapshot and build its DiffPlan."""
    request = request_intent or RequestIntent(
        target_uri=target_uri, processing_mode="semantic_and_vectors"
    )
    new = await read_new_manifest(
        store, artifact_ref, doc_rel=doc_rel, root_is_file=root_is_file
    )
    target_files, files_complete = await read_target_file_snapshot(
        viking_fs, target_uri, ctx=ctx, root_is_file=root_is_file
    )
    projection = request.required_vector_fields()
    if root_is_file or request.processing_mode == "vectors_only":
        projection = projection | {"abstract"}
    inventory = await vikingdb.get_incremental_inventory_under_uri(
        target_uri, ctx=ctx, output_fields=sorted(projection)
    )
    base = target_uri.rstrip("/")
    prefix = base + "/"
    vector_records: Dict[str, VectorRecordSnapshot] = {}
    record_ids_by_key: Dict[tuple[str, int], list[str]] = {}
    target_vectors: Dict[str, TargetVector] = {}
    for record_id, record in inventory.items():
        level = int(record.get("level", -1))
        uri = str(record.get("uri") or "")
        rel = "" if uri == base else uri[len(prefix) :] if uri.startswith(prefix) else None
        if rel is None:
            raise RuntimeError(f"Vector inventory returned an out-of-scope URI: {uri}")
        fields = {
            field: record[field]
            for field in projection - {"id", "uri", "level"}
            if field in record
        }
        vector_records[record_id] = VectorRecordSnapshot(
            record_id=record_id,
            uri=uri,
            relative_path=rel,
            level=level,
            fields=fields,
        )
        record_ids_by_key.setdefault((rel, level), []).append(record_id)
        if level == 2:
            target_vectors[rel] = TargetVector(md5=str(record.get("md5") or ""))
    rnfv = RNFVSnapshot(
        request=request,
        new=NewArtifactSnapshot(entries=new),
        formal=FormalTreeSnapshot(entries=target_files, complete=files_complete),
        vectors=VectorIndexSnapshot(
            records_by_id=vector_records,
            record_ids_by_key={
                key: tuple(record_ids) for key, record_ids in record_ids_by_key.items()
            },
            projected_fields=projection,
        ),
    )
    if not require_vectors:
        for rel_path, target_file in target_files.items():
            if not target_file.is_dir:
                target_vectors.setdefault(rel_path, TargetVector())
        plan = build_diff_plan(
            new=new,
            target_files=target_files,
            target_vectors=target_vectors,
            target_files_complete=files_complete,
            target_vectors_complete=True,
        )
        # Disabling vector creation suppresses missing-index repair, but an
        # explicit R scalar mutation still applies to records that already exist.
        apply_request_scalar_intents(rnfv, plan)
    else:
        plan = build_diff_plan(rnfv)
    _log_diff_diagnostics(
        target_uri=target_uri,
        new=new,
        target_files=target_files,
        target_vectors=target_vectors,
        plan=plan,
        target_files_complete=files_complete,
    )
    return ResourceDiffSnapshot(
        rnfv=rnfv,
        plan=plan,
    )


def _log_diff_diagnostics(
    *,
    target_uri: str,
    new: Dict[str, NewEntry],
    target_files: Dict[str, TargetFile],
    target_vectors: Dict[str, TargetVector],
    plan: DiffPlan,
    target_files_complete: bool,
) -> None:
    """Log the MD5 decision buckets behind an incremental diff."""
    md5_equal = 0
    md5_mismatch = 0
    md5_missing = 0
    vector_missing = 0
    mismatch_samples: list[str] = []
    missing_md5_samples: list[str] = []

    for rel_path, new_entry in new.items():
        if new_entry.is_dir or rel_path not in target_files:
            continue
        target_vector = target_vectors.get(rel_path)
        if target_vector is None:
            vector_missing += 1
            continue
        if new_entry.md5 and target_vector.md5:
            if new_entry.md5 == target_vector.md5:
                md5_equal += 1
            else:
                md5_mismatch += 1
                if len(mismatch_samples) < 5:
                    mismatch_samples.append(
                        f"{rel_path}(source={new_entry.md5},target={target_vector.md5})"
                    )
        else:
            md5_missing += 1
            if len(missing_md5_samples) < 5:
                missing_md5_samples.append(
                    f"{rel_path}(source={bool(new_entry.md5)},target={bool(target_vector.md5)})"
                )

    logger.info(
        "[IncrementalDiff] target=%s N_files=%d F_files=%d V_vectors=%d "
        "plan_added=%d plan_modified=%d plan_unchanged=%d plan_repair=%d "
        "plan_needs_body_compare=%d plan_deleted=%d plan_orphan_vectors=%d "
        "plan_scalar_updates=%d "
        "md5_equal=%d md5_mismatch=%d md5_missing=%d vector_missing=%d "
        "target_files_complete=%s",
        target_uri,
        sum(1 for entry in new.values() if not entry.is_dir),
        sum(1 for entry in target_files.values() if not entry.is_dir),
        len(target_vectors),
        len(plan.added),
        len(plan.modified),
        len(plan.unchanged),
        len(plan.repair),
        len(plan.needs_body_compare),
        len(plan.deleted),
        len(plan.orphan_vectors),
        len(plan.scalar_updates),
        md5_equal,
        md5_mismatch,
        md5_missing,
        vector_missing,
        target_files_complete,
    )
    if mismatch_samples:
        logger.info(
            "[IncrementalDiff] md5_mismatch_samples target=%s samples=%s",
            target_uri,
            mismatch_samples,
        )
    if missing_md5_samples:
        logger.info(
            "[IncrementalDiff] md5_missing_samples target=%s samples=%s",
            target_uri,
            missing_md5_samples,
        )


__all__ = [
    "build_resource_diff_plan",
    "build_resource_diff_snapshot",
    "ResourceDiffSnapshot",
    "read_new_manifest",
    "read_target_file_snapshot",
    "read_target_vector_snapshot",
]
