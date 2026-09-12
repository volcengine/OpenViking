# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Apply a :class:`DiffPlan` against a target, uploading only what changed.

This is the backend-agnostic executor that turns a classified plan into target
writes/deletes:

- ``unchanged`` files are left untouched — the whole point of the diff.
- ``added`` / ``modified`` files are read from the parse output store and written
  to the target; md5 is computed from those bytes at the upload point (no extra
  read) so it can feed the subsequent embedding.
- ``needs_body_compare`` files (md5 unknown on some side) read both bodies once
  and resolve to unchanged or an upload.
- ``deleted`` / ``orphan_vectors`` are removed; ``structural`` (file<->dir flip)
  deletes the stale node before the replacement is written.

The ``target`` and ``store`` are duck-typed so AGFS (viking temp -> target cp)
and local (local dir -> AGFS target upload) reuse the same logic. The caller is
responsible for the completeness gate before invoking this (a plan built from an
incomplete snapshot never carries deletions — see build_diff_plan).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from openviking.storage.viking_fs._diff_plan import DiffPlan
from openviking.utils.content_hash import content_md5


@dataclass
class ApplyResult:
    """What the executor actually performed, for reporting and md5 hand-off."""

    uploaded: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    orphan_vectors: List[str] = field(default_factory=list)
    structural: List[str] = field(default_factory=list)
    repair: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    abstracts_by_rel: Dict[str, str] = field(default_factory=dict)
    # rel_path -> md5 of the bytes just uploaded; handed to the embedding stage
    # so the next diff can skip by fingerprint.
    md5_by_rel: Dict[str, str] = field(default_factory=dict)


async def _upload(rel_path: str, *, store, artifact_ref, target, result: ApplyResult) -> None:
    data = await store.read_bytes(artifact_ref, rel_path)
    # The target may normalize bytes on write (e.g. encoding). md5 must reflect
    # the FINAL stored bytes, so hash what write_file reports it stored.
    written = await target.write_file(rel_path, data)
    final_bytes = written if written is not None else data
    result.uploaded.append(rel_path)
    result.md5_by_rel[rel_path] = content_md5(final_bytes)


async def apply_diff_plan(
    plan: DiffPlan,
    *,
    store: Any,
    artifact_ref: Any,
    target: Any,
) -> ApplyResult:
    """Execute ``plan`` against ``target``, uploading only changed files.

    Structural replacements are removed first so a file<->dir flip never leaves
    stale content under the same URI. Deletions come from a plan that already
    passed the completeness gate, so an unreadable file is never deleted here.
    Any target write/delete failure propagates; the caller marks the task failed
    rather than reporting partial success as done.
    """
    result = ApplyResult(
        added=list(plan.added),
        modified=list(plan.modified),
        repair=list(plan.repair),
        files=list(plan.new_files),
        md5_by_rel=dict(plan.new_md5s),
        abstracts_by_rel=dict(plan.file_abstracts),
    )

    # Structural replacements: delete the stale node up front. The replacement is
    # written by its added/modified classification below.
    for rel_path in plan.structural:
        await target.delete_file(rel_path)
        await target.delete_vector(rel_path)
        result.structural.append(rel_path)

    for rel_path in [*plan.added, *plan.modified]:
        await _upload(rel_path, store=store, artifact_ref=artifact_ref, target=target, result=result)

    for rel_path in plan.needs_body_compare:
        new_bytes = await store.read_bytes(artifact_ref, rel_path)
        try:
            old_bytes = await target.read_file(rel_path)
        except Exception:
            old_bytes = None
        if old_bytes is not None and old_bytes == new_bytes:
            result.unchanged.append(rel_path)
            continue
        written = await target.write_file(rel_path, new_bytes)
        final_bytes = written if written is not None else new_bytes
        result.uploaded.append(rel_path)
        result.modified.append(rel_path)
        result.md5_by_rel[rel_path] = content_md5(final_bytes)

    result.unchanged.extend(plan.unchanged)

    for rel_path in plan.deleted:
        await target.delete_file(rel_path)
        await target.delete_vector(rel_path)
        result.deleted.append(rel_path)

    for rel_path in plan.orphan_vectors:
        await target.delete_vector(rel_path)
        result.orphan_vectors.append(rel_path)

    return result


async def apply_full_artifact_upload(
    *,
    store: Any,
    artifact_ref: Any,
    doc_rel: str,
    target: Any,
) -> ApplyResult:
    """Upload every file under ``doc_rel`` in the artifact to the target.

    Initial import is "the plan is all added": there is no existing target to
    diff against, so every business file below the document root is uploaded.
    Paths are made target-relative by stripping the ``doc_rel`` prefix (e.g. the
    ``repository`` wrapper), and md5 is computed from the final stored bytes at
    the upload point, exactly like the incremental path.
    """
    result = ApplyResult()
    base = doc_rel.strip("/")
    prefix = f"{base}/" if base else ""

    async def _walk(rel: str) -> None:
        for entry in await store.list(artifact_ref, rel):
            if entry.is_dir:
                await _walk(entry.rel_path)
                continue
            target_rel = entry.rel_path[len(prefix):] if prefix else entry.rel_path
            data = await store.read_bytes(artifact_ref, entry.rel_path)
            written = await target.write_file(target_rel, data)
            final_bytes = written if written is not None else data
            result.uploaded.append(target_rel)
            result.files.append(target_rel)
            result.md5_by_rel[target_rel] = content_md5(final_bytes)

    await _walk(base)
    result.files.sort()
    return result


__all__ = ["ApplyResult", "apply_diff_plan", "apply_full_artifact_upload"]
