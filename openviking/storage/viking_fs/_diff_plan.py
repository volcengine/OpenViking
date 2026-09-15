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
from typing import TYPE_CHECKING, Any, List, Mapping, Optional

from openviking.utils.tags import merge_search_tags, normalize_search_tags

if TYPE_CHECKING:
    from openviking.storage.resource_rnfv import RNFVSnapshot

# Control sidecars / metadata that are derived outputs, never business files.
# They must not enter the business diff or they would be classified as deletions.
# Single source of truth: resource_diff and resource_diff_apply import this set so
# adding a new sidecar name only touches one place.
CONTROL_BASENAMES = frozenset(
    {".abstract.md", ".overview.md", ".image_mappings.json", ".artifact_manifest.json"}
)


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


@dataclass(frozen=True)
class ScalarUpdate:
    """One fully-resolved vector scalar update produced from R and V."""

    record_id: str
    uri: str
    relative_path: str
    level: int
    fields: Mapping[str, Any]


@dataclass
class DiffPlan:
    """Classification of business files for incremental application.

    ``needs_body_compare`` holds intersection files whose equality could not be
    decided by md5 (either side missing a fingerprint); the apply stage reads
    both bodies for these before deciding unchanged vs modified.
    """

    added: List[str] = field(default_factory=list)
    added_dirs: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    deleted_dirs: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    repair: List[str] = field(default_factory=list)
    orphan_vectors: List[str] = field(default_factory=list)
    structural: List[str] = field(default_factory=list)
    needs_body_compare: List[str] = field(default_factory=list)
    new_files: List[str] = field(default_factory=list)
    new_md5s: Mapping[str, str] = field(default_factory=dict)
    file_abstracts: Mapping[str, str] = field(default_factory=dict)
    scalar_updates: List[ScalarUpdate] = field(default_factory=list)
    # Final request-owned values keyed by an existing vector record ID. A record
    # that is rebuilt should carry these values in its upsert rather than rely on
    # a second scalar-only operation.
    scalar_overrides: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def is_noop(self) -> bool:
        """Whether neither content/index nor request-scalar work remains."""
        return not any(
            (
                self.added,
                self.added_dirs,
                self.modified,
                self.deleted,
                self.deleted_dirs,
                self.repair,
                self.orphan_vectors,
                self.structural,
                self.needs_body_compare,
                self.scalar_updates,
            )
        )


def _is_control_path(rel_path: str) -> bool:
    return rel_path.rsplit("/", 1)[-1] in CONTROL_BASENAMES


def build_diff_plan(
    snapshot: Optional["RNFVSnapshot"] = None,
    *,
    new: Optional[Mapping[str, NewEntry]] = None,
    target_files: Optional[Mapping[str, TargetFile]] = None,
    target_vectors: Optional[Mapping[str, TargetVector]] = None,
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
    if snapshot is not None:
        snapshot.validate_for_planning()
        new = snapshot.new.entries
        target_files = snapshot.formal.entries
        target_files_complete = snapshot.formal.complete
        target_vectors_complete = snapshot.vectors.complete
        target_vectors = {
            record.relative_path: TargetVector(
                md5=str(record.fields.get("md5") or ""),
                abstract=str(record.fields.get("abstract") or ""),
            )
            for record in snapshot.vectors.records_by_id.values()
            if record.level == 2
        }
    if new is None or target_files is None or target_vectors is None:
        raise TypeError("build_diff_plan requires an RNFV snapshot or explicit N/F/V mappings")

    plan = DiffPlan()
    plan.new_files = sorted(key for key, entry in new.items() if not entry.is_dir)
    plan.new_md5s = {key: entry.md5 for key, entry in new.items() if not entry.is_dir and entry.md5}
    plan.file_abstracts = {
        key: vector.abstract
        for key, vector in target_vectors.items()
        if key in new and vector.abstract
    }

    # New/file snapshots exclude generated control files. Keep every L2 URI in
    # the vector snapshot so malformed or stale records are cleaned as orphans.
    keys = {key for key in (*new.keys(), *target_files.keys()) if not _is_control_path(key)}
    keys.update(target_vectors)

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
            else:
                plan.added_dirs.append(key)
            continue

        # Directories carry no legitimate L2 record. If one exists at the same
        # URI, clean that vector while children are classified on their own keys.
        if (n is not None and n.is_dir) or (f is not None and f.is_dir):
            if n is not None and n.is_dir and f is None:
                plan.added_dirs.append(key)
            if n is None and f is not None and f.is_dir:
                plan.deleted_dirs.append(key)
            if v is not None:
                plan.orphan_vectors.append(key)
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

    if (plan.deleted or plan.deleted_dirs) and not target_files_complete:
        raise ValueError("refusing to plan deletions from an incomplete target file snapshot")
    if plan.orphan_vectors and not target_files_complete:
        raise ValueError(
            "refusing to plan orphan-vector deletions from an incomplete target file snapshot"
        )
    if plan.orphan_vectors and not target_vectors_complete:
        raise ValueError(
            "refusing to plan orphan-vector deletions from an incomplete target vector snapshot"
        )
    if snapshot is not None:
        apply_request_scalar_intents(snapshot, plan)
    return plan


def apply_request_scalar_intents(snapshot: "RNFVSnapshot", plan: DiffPlan) -> None:
    """Resolve R scalar intent against V into concrete, id-addressed actions."""
    plan.scalar_updates = []
    plan.scalar_overrides = {}
    if not snapshot.request.scalar_intents:
        return

    current_kinds: dict[str, str] = {"": "directory"}
    for rel_path, entry in snapshot.new.entries.items():
        current_kinds[rel_path] = "directory" if entry.is_dir else "file"
    deleted_or_replaced = (
        set(plan.deleted)
        | set(plan.deleted_dirs)
        | set(plan.structural)
        | set(plan.orphan_vectors)
    )
    updates: list[ScalarUpdate] = []
    overrides: dict[str, Mapping[str, Any]] = {}

    for record in snapshot.vectors.records_by_id.values():
        kind = current_kinds.get(record.relative_path)
        valid_levels = {2} if kind == "file" else {0, 1} if kind == "directory" else set()
        if (
            record.relative_path in deleted_or_replaced
            or record.level not in valid_levels
        ):
            continue
        changed_fields: dict[str, Any] = {}
        for intent in snapshot.request.scalar_intents:
            if record.level not in intent.target_levels:
                continue
            if intent.field == "search_tags":
                existing = normalize_search_tags(
                    record.fields.get("search_tags"), discard_invalid=True
                )
                incoming = normalize_search_tags(intent.value, discard_invalid=True)
                desired = (
                    merge_search_tags(existing, incoming)
                    if intent.mode == "append"
                    else incoming
                )
                if sorted(existing) != sorted(desired):
                    changed_fields[intent.field] = desired
        if not changed_fields:
            continue
        overrides[record.record_id] = changed_fields
        # Keep the scalar action even when content is expected to rebuild this
        # record. Execution removes it only after the full upsert is confirmed
        # enqueued; unsupported/cancelled vectorization can then still apply R.
        updates.append(
            ScalarUpdate(
                record_id=record.record_id,
                uri=record.uri,
                relative_path=record.relative_path,
                level=record.level,
                fields=changed_fields,
            )
        )

    plan.scalar_updates = updates
    plan.scalar_overrides = overrides


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
