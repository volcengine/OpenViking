# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pure, IO-free incremental diff planning.

Given three snapshots — the new-artifact manifest (N), the target file tree (F),
and the target vector records (V) — classify every business file into the P4
decision table. This module makes decisions only; the apply stage in ``_sync``
performs the actual uploads/deletes.

Keeping this free of storage calls means the decision table (see design §7.2) is
implemented exactly once and can be unit-tested exhaustively. All three callers
(sync, resource_processor, reindex) build the same plan rather than re-deriving
comparison logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping

# Control sidecars / metadata that are derived outputs, never business files.
# They must not enter the business diff or they would be classified as deletions.
_CONTROL_BASENAMES = frozenset({".abstract.md", ".overview.md"})


@dataclass(frozen=True)
class NewEntry:
    """One entry in the new-artifact manifest (N)."""

    md5: str = ""
    is_dir: bool = False


@dataclass(frozen=True)
class TargetFile:
    """One entry in the target file tree (F)."""

    is_dir: bool = False


@dataclass(frozen=True)
class TargetVector:
    """One L2 vector record in the target (V)."""

    md5: str = ""
    abstract: str = ""


@dataclass
class DiffPlan:
    """Classification of business files for incremental application.

    ``needs_body_compare`` holds intersection files whose equality could not be
    decided by md5 (either side missing a fingerprint); the apply stage reads
    both bodies for these before deciding unchanged vs modified.
    """

    added: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    repair: List[str] = field(default_factory=list)
    orphan_vectors: List[str] = field(default_factory=list)
    structural: List[str] = field(default_factory=list)
    needs_body_compare: List[str] = field(default_factory=list)
    new_files: List[str] = field(default_factory=list)
    new_md5s: Mapping[str, str] = field(default_factory=dict)
    file_abstracts: Mapping[str, str] = field(default_factory=dict)


def _is_control_path(rel_path: str) -> bool:
    return rel_path.rsplit("/", 1)[-1] in _CONTROL_BASENAMES


def build_diff_plan(
    *,
    new: Mapping[str, NewEntry],
    target_files: Mapping[str, TargetFile],
    target_vectors: Mapping[str, TargetVector],
    target_files_complete: bool = True,
    target_vectors_complete: bool = True,
) -> DiffPlan:
    """Classify business files across the N / F / V snapshots.

    A deletion or orphan-vector cleanup is only sound when the corresponding
    target snapshot is complete: a file we merely failed to read is not
    "removed". When a deletion/orphan would be emitted from an incomplete
    snapshot, this raises ``ValueError`` rather than risk data loss. Pure
    additions never depend on completeness.
    """
    plan = DiffPlan()
    plan.new_files = sorted(key for key, entry in new.items() if not entry.is_dir)
    plan.new_md5s = {
        key: entry.md5 for key, entry in new.items() if not entry.is_dir and entry.md5
    }
    plan.file_abstracts = {
        key: vector.abstract
        for key, vector in target_vectors.items()
        if key in new and vector.abstract
    }

    # Business-file key universe, excluding control sidecars.
    keys = {
        key
        for key in (*new.keys(), *target_files.keys(), *target_vectors.keys())
        if not _is_control_path(key)
    }

    for key in sorted(keys):
        n = new.get(key)
        f = target_files.get(key)
        v = target_vectors.get(key)

        # Type conflict: a path that is a dir on one side and a file on the other
        # cannot be a content overwrite; it is a structural replacement.
        n_is_dir = n.is_dir if n is not None else None
        f_is_dir = f.is_dir if f is not None else None
        if n is not None and f is not None and n_is_dir != f_is_dir:
            plan.structural.append(key)
            if not n.is_dir:
                plan.added.append(key)
            continue

        # Directories carry no file-level md5; their children are classified on
        # their own keys, so a directory-only entry needs no content decision.
        if (n is not None and n.is_dir) or (f is not None and f.is_dir):
            continue

        if n is not None and f is not None and v is not None:
            # N∩F∩V: prefer md5; fall back to body compare when either is unknown.
            if n.md5 and v.md5:
                if n.md5 == v.md5:
                    plan.unchanged.append(key)
                else:
                    plan.modified.append(key)
            else:
                plan.needs_body_compare.append(key)
        elif n is not None and f is not None and v is None:
            # File exists but no index: repair (re-index correct content).
            plan.repair.append(key)
        elif n is not None and f is None:
            # New tree has it, target file missing: add. A stale vector for the
            # same path is a leftover to clean before the add is indexed.
            plan.added.append(key)
            if v is not None:
                plan.orphan_vectors.append(key)
        elif n is None and f is not None:
            # Removed from the new tree: delete the target file (and its index).
            plan.deleted.append(key)
        elif n is None and f is None and v is not None:
            # Index with no file and not in the new tree: orphan vector.
            plan.orphan_vectors.append(key)

    if plan.deleted and not target_files_complete:
        raise ValueError(
            "refusing to plan deletions from an incomplete target file snapshot"
        )
    if plan.orphan_vectors and not target_vectors_complete:
        raise ValueError(
            "refusing to plan orphan-vector deletions from an incomplete target vector snapshot"
        )
    return plan


def resolve_body_compare(plan: DiffPlan, equal_keys: Mapping[str, bool]) -> None:
    """Fold body-comparison results back into unchanged/modified in place.

    ``equal_keys`` maps each ``needs_body_compare`` key to whether the two bodies
    were byte-equal. The apply stage performs the reads; this keeps the merge
    rule in one place.
    """
    for key in plan.needs_body_compare:
        if equal_keys.get(key):
            plan.unchanged.append(key)
        else:
            plan.modified.append(key)
    plan.needs_body_compare = []
