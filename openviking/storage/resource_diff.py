# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read the N / F / V snapshots that feed the incremental :mod:`DiffPlan`.

This module is the IO-facing counterpart to ``viking_fs._diff_plan`` (which is
pure). It gathers three snapshots into the plain data the planner expects:

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

import json
from typing import Any, Dict, Tuple

from openviking.core.namespace import uri_parts
from openviking.storage.internal_names import STORAGE_INTERNAL_ENTRY_NAMES
from openviking.storage.viking_fs._diff_plan import (
    DiffPlan,
    NewEntry,
    TargetFile,
    TargetVector,
    build_diff_plan,
)
from openviking_cli.utils import VikingURI

_CONTROL_BASENAMES = frozenset({".abstract.md", ".overview.md"})


def _is_excluded_rel_path(rel_path: str) -> bool:
    """Control sidecars and storage-internal entries are never business files."""
    if not rel_path:
        return True
    for segment in rel_path.split("/"):
        if segment in STORAGE_INTERNAL_ENTRY_NAMES or segment in _CONTROL_BASENAMES:
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
    from openviking.parse.parsers.upload_utils import ARTIFACT_MANIFEST_NAME

    manifest: Dict[str, NewEntry] = {}
    base = doc_rel.strip("/")
    prefix = f"{base}/" if base else ""

    # md5 sidecar is keyed by artifact-relative path (pre-strip); read once.
    md5_by_artifact_rel: Dict[str, str] = {}
    try:
        raw = await store.read_bytes(ref, ARTIFACT_MANIFEST_NAME)
        loaded = json.loads(raw.decode("utf-8"))
        if isinstance(loaded, dict):
            md5_by_artifact_rel = {str(k): str(v) for k, v in loaded.items()}
    except Exception:
        # No manifest (legacy/agfs artifacts) or unreadable: fall back to empty
        # md5 so the diff compares bytes instead of assuming equality.
        md5_by_artifact_rel = {}

    if root_is_file:
        return {"": NewEntry(md5=md5_by_artifact_rel.get(base, ""), is_dir=False)}

    async def _walk(rel: str) -> None:
        for entry in await store.list(ref, rel):
            if _is_excluded_rel_path(entry.rel_path):
                continue
            if entry.is_dir:
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
    return build_diff_plan(
        new=new,
        target_files=target_files,
        target_vectors=target_vectors,
        target_files_complete=files_complete,
        target_vectors_complete=True,
    )


__all__ = [
    "build_resource_diff_plan",
    "read_new_manifest",
    "read_target_file_snapshot",
    "read_target_vector_snapshot",
]
