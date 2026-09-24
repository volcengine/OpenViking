# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Build and execute explicit content, semantic and index actions."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping

from openviking.concurrency import bounded_map
from openviking.storage.index_action import FieldPatch, IndexAction
from openviking.storage.resource_diff import ContentState, IndexState
from openviking.storage.resource_rnfv import (
    NON_PORTABLE_VECTOR_RECORD_FIELDS,
    FormalEntry,
    FormalTreeSnapshot,
    NewArtifactSnapshot,
    NewEntry,
    RequestIntent,
    RNFVSnapshot,
    VectorRecordSnapshot,
    canonical_vector_records_by_level,
)
from openviking.storage.vector_ids import vector_record_id
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.log_correlation import log_correlation
from openviking_cli.utils import VikingURI

logger = logging.getLogger(__name__)


class SemanticAction(str, Enum):
    """Per-node semantic work executed by the minimal semantic tree."""

    # Consume a hydrated existing abstract; do not call a semantic model.
    REUSE = "reuse"
    # Generate a file abstract from the current formal file contents.
    GENERATE = "generate"
    # Generate directory L0/L1 bodies from direct child abstracts.
    AGGREGATE = "aggregate"


class FileVectorSource(str, Enum):
    """Input selected for a file's embedding request."""

    # Embed the current file body using the configured text-source policy.
    CONTENT = "content"
    # Prefer the generated file summary when one is available (code repositories).
    SUMMARY_WHEN_AVAILABLE = "summary_when_available"


@dataclass(frozen=True)
class ParentPropagation:
    enabled: bool = True


@dataclass(frozen=True)
class FileRefreshIntent:
    """Refresh a flat file and its parent after synchronous content commit."""

    file_uri: str
    md5: str | None = None

    def __post_init__(self) -> None:
        if not self.file_uri.startswith("viking://"):
            raise ValueError("file refresh requires a Viking URI")


def _validate_relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("relative_path must be a string")
    if not value:
        return value
    path = PurePosixPath(value)
    if path.is_absolute() or value != str(path) or any(part == ".." for part in path.parts):
        raise ValueError(f"invalid relative path: {value}")
    return value


def _validate_index_fields(fields: Mapping[str, Any]) -> None:
    if set(fields) & {"id", "uri", "level", "vector", "sparse_vector"}:
        raise ValueError("index fields contain identity or vector payload")


@dataclass(frozen=True)
class IndexSlot:
    """Planned operation for one semantic node and one vector level.

    Existing record identity and portable scalar fields travel with the slot so a
    later embedding worker can update the real stored record rather than derive a
    replacement ID locally. ``DELETE`` and pure ``UPDATE_FIELDS`` actions never
    belong here because they do not depend on semantic output.
    """

    level: int
    record_id: str
    existing_fields: Mapping[str, Any] | None = None
    action: IndexAction = IndexAction.NONE
    upsert_fields: Mapping[str, Any] = field(default_factory=dict)
    field_patch: FieldPatch | None = None
    fallback_to_patch: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", IndexAction(self.action))
        if self.level not in {0, 1, 2} or not self.record_id:
            raise ValueError("index slot requires a level and record ID")
        if self.action is IndexAction.DELETE:
            raise ValueError("delete belongs to direct index actions")
        if self.action is IndexAction.UPDATE_FIELDS:
            raise ValueError("semantic index slots only supports none, upsert, or merge")
        _validate_index_fields(self.existing_fields or {})
        _validate_index_fields(self.upsert_fields)
        if self.action is IndexAction.NONE and (self.upsert_fields or self.field_patch):
            raise ValueError("none index slot cannot carry index mutations")
        if self.fallback_to_patch and self.field_patch is None:
            raise ValueError("fallback_to_patch requires a field patch")
        if self.action is IndexAction.MERGE and self.upsert_fields:
            raise ValueError("merge index slot uses field_patch, not upsert_fields")

    @property
    def abstract(self) -> str:
        return str((self.existing_fields or {}).get("abstract") or "")

    def scalar_override(self) -> dict[str, Any]:
        existing = {
            key: value
            for key, value in (self.existing_fields or {}).items()
            if key not in NON_PORTABLE_VECTOR_RECORD_FIELDS and not key.startswith("_")
        }
        return {
            **({} if self.action is IndexAction.MERGE else existing),
            **dict(self.upsert_fields),
            "_record_id": self.record_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IndexSlot":
        values = dict(data)
        if any(
            name in values
            for name in ("fields", "update_fields", "field_modes", "fallback_update_fields")
        ):
            legacy_fields = dict(values.pop("fields", {}) or {})
            legacy_update = dict(values.pop("update_fields", {}) or {})
            legacy_modes = dict(values.pop("field_modes", {}) or {})
            action = IndexAction(values.get("action", IndexAction.NONE))
            values.setdefault("upsert_fields", {} if action is IndexAction.MERGE else legacy_fields)
            patch_values = legacy_update or (legacy_fields if action is IndexAction.MERGE else {})
            values.setdefault(
                "field_patch",
                FieldPatch(patch_values, legacy_modes) if patch_values else None,
            )
            legacy_fallback = bool(values.pop("fallback_update_fields", False))
            # Older planners marked every modified semantic node for fallback,
            # even when there was no scalar patch to apply. Normalize that
            # inert state while decoding already queued plans.
            values["fallback_to_patch"] = legacy_fallback and bool(patch_values)
        values.setdefault("upsert_fields", {})
        if isinstance(values.get("field_patch"), Mapping):
            values["field_patch"] = FieldPatch.from_dict(values["field_patch"])
        return cls(**values)


@dataclass(frozen=True)
class SemanticTreeEntry:
    """A retained node in the serializable minimal semantic-tree closure.

    ``REUSE`` entries provide existing child abstracts needed by an ancestor but
    do not run work themselves. Active file entries ``GENERATE`` and active
    directory entries ``AGGREGATE``.
    """

    relative_path: str
    kind: str
    content_state: ContentState
    semantic_action: SemanticAction
    md5: str | None = None
    index_slots: tuple[IndexSlot, ...] = ()
    membership_changed: bool = False
    repair: bool = False

    def __post_init__(self) -> None:
        _validate_relative_path(self.relative_path)
        object.__setattr__(self, "content_state", ContentState(self.content_state))
        object.__setattr__(self, "semantic_action", SemanticAction(self.semantic_action))
        if self.kind not in {"file", "directory"}:
            raise ValueError("invalid semantic entry kind")
        if self.kind == "file" and self.semantic_action is SemanticAction.AGGREGATE:
            raise ValueError("file semantic action must be generate or reuse")
        if self.kind == "directory" and self.semantic_action is SemanticAction.GENERATE:
            raise ValueError("directory semantic action must be aggregate or reuse")
        if self.kind == "directory" and self.md5:
            raise ValueError("directory entry cannot carry md5")
        levels = [slot.level for slot in self.index_slots]
        if len(levels) != len(set(levels)):
            raise ValueError(f"duplicate index slot level for {self.relative_path}")
        if self.semantic_action is SemanticAction.REUSE:
            if any(slot.action is not IndexAction.NONE for slot in self.index_slots):
                raise ValueError("reuse semantic entry cannot contain index mutations")
            slot = self.slot(2 if self.kind == "file" else 0)
            if slot is None or not slot.abstract.strip():
                raise ValueError("reuse requires an existing abstract")

    def slot(self, level: int) -> IndexSlot | None:
        return next((slot for slot in self.index_slots if slot.level == level), None)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticTreeEntry":
        values = dict(data)
        values["index_slots"] = tuple(
            IndexSlot.from_dict(item) for item in values.get("index_slots", ())
        )
        return cls(**values)


@dataclass(frozen=True)
class SemanticTreeSnapshot:
    """Compact tree retained after diff closure and vector hydration."""

    entries: tuple[SemanticTreeEntry, ...]

    def __post_init__(self) -> None:
        paths = [entry.relative_path for entry in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate semantic tree entry path")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticTreeSnapshot":
        return cls(
            entries=tuple(SemanticTreeEntry.from_dict(item) for item in data.get("entries", ()))
        )


@dataclass(frozen=True)
class SemanticPlan:
    """Asynchronous semantic/derived-index portion of one context update.

    The plan contains only final resource URIs and hydrated non-vector facts. It
    must never require a parser artifact, local file path, or another RNFV scan
    after it has crossed the durable semantic queue boundary.
    """

    root_uri: str
    context_type: str
    tree: SemanticTreeSnapshot
    vectorize: bool = True
    propagation: ParentPropagation = field(default_factory=ParentPropagation)
    file_vector_source: FileVectorSource = FileVectorSource.CONTENT
    ingest_options: IngestOptions = field(default_factory=IngestOptions)
    source_metadata: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_uri", VikingURI(self.root_uri).uri.rstrip("/"))
        object.__setattr__(self, "file_vector_source", FileVectorSource(self.file_vector_source))
        object.__setattr__(self, "ingest_options", IngestOptions.from_value(self.ingest_options))
        if self.context_type not in {"resource", "skill", "memory"}:
            raise ValueError(f"invalid context_type: {self.context_type}")
        entries_by_path = {entry.relative_path: entry for entry in self.tree.entries}
        if entries_by_path and "" not in entries_by_path:
            raise ValueError("semantic plan must contain the resource root")
        for entry in entries_by_path.values():
            if not entry.relative_path:
                continue
            parent = _parent(entry.relative_path)
            if parent not in entries_by_path:
                raise ValueError(
                    f"semantic plan lacks parent {parent!r} for {entry.relative_path!r}"
                )
            current = parent
            while True:
                if current not in entries_by_path:
                    raise ValueError(
                        f"semantic plan lacks ancestor {current!r} for {entry.relative_path!r}"
                    )
                ancestor_entry = entries_by_path[current]
                if (
                    entry.semantic_action is not SemanticAction.REUSE
                    and ancestor_entry.semantic_action is not SemanticAction.AGGREGATE
                ):
                    raise ValueError(
                        f"semantic plan ancestor {current!r} must aggregate active descendant "
                        f"{entry.relative_path!r}"
                    )
                if not current:
                    break
                current = _parent(current)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["file_vector_source"] = self.file_vector_source.value
        data["ingest_options"] = self.ingest_options.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticPlan":
        return cls(
            root_uri=str(data["root_uri"]),
            context_type=str(data["context_type"]),
            tree=SemanticTreeSnapshot.from_dict(data.get("tree", {})),
            vectorize=bool(data.get("vectorize", True)),
            propagation=ParentPropagation(**dict(data.get("propagation", {}))),
            file_vector_source=FileVectorSource(
                data.get("file_vector_source", FileVectorSource.CONTENT.value)
            ),
            ingest_options=IngestOptions.from_value(data.get("ingest_options")),
            source_metadata=data.get("source_metadata"),
        )

    def execution_root_uris(self) -> tuple[str, ...]:
        directories = {
            entry.relative_path
            for entry in self.tree.entries
            if entry.kind == "directory" and entry.semantic_action is SemanticAction.AGGREGATE
        }
        roots = sorted(
            path
            for path in directories
            if not any(
                parent != path
                and (not parent or path.startswith(parent + "/"))
                and parent in directories
                for parent in directories
            )
        )
        if not roots and any(
            entry.semantic_action is not SemanticAction.REUSE for entry in self.tree.entries
        ):
            roots = [""]
        return tuple(self.root_uri if not path else f"{self.root_uri}/{path}" for path in roots)


class ContentTreeOperation(str, Enum):
    """Synchronous formal-tree mutation performed before queue handoff."""

    # Write a new file or ensure a planned directory exists.
    UPSERT = "upsert"
    # Remove an existing file or subtree.
    DELETE = "delete"
    # Delete the old kind before creating the different new kind.
    REPLACE_KIND = "replace_kind"


@dataclass(frozen=True)
class ContentTreeAction:
    """One synchronous mutation from a parse artifact into the formal tree.

    File writes carry the final artifact-relative source and MD5. Directory
    actions only establish topology; they do not invent a directory fingerprint.
    """

    operation: ContentTreeOperation
    relative_path: str
    old_kind: str | None = None
    new_kind: str | None = None
    artifact_path: str | None = None
    md5: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", ContentTreeOperation(self.operation))
        _validate_relative_path(self.relative_path)
        if self.artifact_path is not None:
            _validate_relative_path(self.artifact_path)
        if self.operation is not ContentTreeOperation.DELETE and self.new_kind == "file":
            if self.artifact_path is None or not self.md5:
                raise ValueError("file write requires artifact_path and md5")
        if self.operation is ContentTreeOperation.REPLACE_KIND and (
            not self.old_kind or not self.new_kind or self.old_kind == self.new_kind
        ):
            raise ValueError("replacement requires different old and new kinds")


@dataclass(frozen=True)
class DirectIndexAction:
    """Direct vector operation independent of semantic model output.

    Deletes remove exact inventory IDs. ``UPDATE_FIELDS`` mutates only approved
    scalars. Direct upserts are restricted to file L2 vectors in vectors-only
    processing, where no semantic node produces the embedding request.
    """

    action: IndexAction
    uri: str
    level: int
    record_id: str
    upsert_fields: Mapping[str, Any] = field(default_factory=dict)
    field_patch: FieldPatch | None = None
    md5: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", IndexAction(self.action))
        if self.action is IndexAction.NONE or self.level not in {0, 1, 2}:
            raise ValueError("invalid direct index action")
        if not self.record_id:
            raise ValueError("direct index action requires a record ID")
        _validate_index_fields(self.upsert_fields)
        if self.action is IndexAction.DELETE and (self.upsert_fields or self.field_patch):
            raise ValueError("delete cannot carry index fields")
        if self.action is IndexAction.UPDATE_FIELDS and (
            self.field_patch is None or not self.field_patch.values or self.upsert_fields
        ):
            raise ValueError("field update requires only a non-empty field patch")
        if self.action is IndexAction.UPSERT and self.field_patch is not None:
            raise ValueError("upsert uses resolved upsert_fields, not a field patch")
        if self.action is IndexAction.MERGE and self.upsert_fields:
            raise ValueError("merge uses field_patch, not upsert_fields")
        if self.action in {IndexAction.DELETE, IndexAction.UPDATE_FIELDS} and self.md5 is not None:
            raise ValueError(f"{self.action.value} cannot carry a vectorization md5")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DirectIndexAction":
        values = dict(data)
        if any(
            name in values
            for name in ("fields", "field_modes", "initial_fields", "search_tag_mode")
        ):
            legacy_fields = dict(values.pop("fields", {}) or {})
            legacy_modes = dict(values.pop("field_modes", {}) or {})
            legacy_seed = dict(values.pop("initial_fields", {}) or {})
            values.pop("search_tag_mode", None)
            action = IndexAction(values.get("action"))
            values.setdefault(
                "upsert_fields", legacy_fields if action is IndexAction.UPSERT else {}
            )
            values.setdefault(
                "field_patch",
                (
                    FieldPatch(legacy_fields, legacy_modes, legacy_seed)
                    if action in {IndexAction.MERGE, IndexAction.UPDATE_FIELDS} and legacy_fields
                    else None
                ),
            )
        values.setdefault("upsert_fields", {})
        if isinstance(values.get("field_patch"), Mapping):
            values["field_patch"] = FieldPatch.from_dict(values["field_patch"])
        return cls(**values)


@dataclass(frozen=True)
class ContextUpdatePlan:
    """Complete execution contract for one resource-tree update.

    Content actions run synchronously under the resource lock. The remaining
    semantic plan and direct index actions are durable queue work. A no-op plan
    therefore means no formal-tree mutation, semantic generation, or vector
    mutation is required.
    """

    root_uri: str
    context_type: str
    content_tree_actions: tuple[ContentTreeAction, ...] = ()
    semantic_plan: SemanticPlan | None = None
    direct_index_actions: tuple[DirectIndexAction, ...] = ()
    file_refresh: FileRefreshIntent | None = None

    def __post_init__(self) -> None:
        root = self.root_uri.rstrip("/")
        paths = [action.relative_path for action in self.content_tree_actions]
        if len(paths) != len(set(paths)):
            raise ValueError("conflicting content actions")
        record_ids: set[str] = set()
        for action in self.direct_index_actions:
            if action.uri != root and not action.uri.startswith(root + "/"):
                raise ValueError("index action outside root")
            if action.record_id in record_ids:
                raise ValueError("conflicting index actions")
            record_ids.add(action.record_id)
        if self.semantic_plan is not None:
            if (
                self.semantic_plan.root_uri != root
                or self.semantic_plan.context_type != self.context_type
            ):
                raise ValueError("semantic plan identity mismatch")
            for entry in self.semantic_plan.tree.entries:
                for slot in entry.index_slots:
                    if slot.action is IndexAction.NONE:
                        continue
                    if slot.record_id in record_ids:
                        raise ValueError("conflicting index actions")
                    record_ids.add(slot.record_id)
        if self.file_refresh is not None and self.file_refresh.file_uri != root:
            raise ValueError("file refresh must target the plan root")

    def is_noop(self) -> bool:
        return (
            not self.content_tree_actions
            and self.semantic_plan is None
            and not self.direct_index_actions
            and self.file_refresh is None
        )

    def after_content_commit(self) -> "ContextUpdatePlan":
        """Drop synchronous content actions before the async handoff."""
        if not self.content_tree_actions:
            return self
        return ContextUpdatePlan(
            root_uri=self.root_uri,
            context_type=self.context_type,
            semantic_plan=self.semantic_plan,
            direct_index_actions=self.direct_index_actions,
            file_refresh=self.file_refresh,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.semantic_plan is not None:
            data["semantic_plan"] = self.semantic_plan.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContextUpdatePlan":
        return cls(
            root_uri=str(data["root_uri"]),
            context_type=str(data["context_type"]),
            content_tree_actions=tuple(
                ContentTreeAction(**item) for item in data.get("content_tree_actions", ())
            ),
            semantic_plan=(
                SemanticPlan.from_dict(data["semantic_plan"]) if data.get("semantic_plan") else None
            ),
            direct_index_actions=tuple(
                DirectIndexAction.from_dict(item) for item in data.get("direct_index_actions", ())
            ),
            file_refresh=(
                FileRefreshIntent(**data["file_refresh"]) if data.get("file_refresh") else None
            ),
        )


def _uri(root_uri: str, relative_path: str) -> str:
    return root_uri if not relative_path else f"{root_uri}/{relative_path}"


def _parent(relative_path: str) -> str:
    value = str(PurePosixPath(relative_path).parent)
    return "" if value == "." else value


def _records_by_path(
    records: Mapping[str, VectorRecordSnapshot],
) -> tuple[
    dict[str, dict[int, VectorRecordSnapshot]],
    tuple[VectorRecordSnapshot, ...],
]:
    result: dict[str, dict[int, VectorRecordSnapshot]] = {}
    duplicates: list[VectorRecordSnapshot] = []
    records_by_relative_path: dict[str, list[VectorRecordSnapshot]] = {}
    for record in records.values():
        records_by_relative_path.setdefault(record.relative_path, []).append(record)
    for relative_path, path_records in records_by_relative_path.items():
        levels, path_duplicates = canonical_vector_records_by_level(path_records)
        result[relative_path] = levels
        duplicates.extend(path_duplicates)
    return result, tuple(duplicates)


def _portable_existing_fields(record: VectorRecordSnapshot | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        key: value
        for key, value in record.fields.items()
        if key not in {"id", "uri", "level", "vector", "sparse_vector", "content"}
        and value is not None
    }


def _field_patch(request: RequestIntent, record: VectorRecordSnapshot | None) -> FieldPatch | None:
    existing = dict(record.fields) if record is not None else {}
    values: dict[str, Any] = {}
    modes: dict[str, str] = {}
    for intent in request.scalar_intents:
        if record is not None and record.level not in intent.target_levels:
            continue
        if intent.field != "search_tags":
            continue
        candidate = FieldPatch({intent.field: intent.value}, {intent.field: intent.mode})
        desired = candidate.resolve(existing).get(intent.field)
        current = FieldPatch(
            {intent.field: existing.get(intent.field)},
            {intent.field: "replace"},
        ).resolve({})[intent.field]
        if record is None or current != desired:
            values[intent.field] = intent.value
            modes[intent.field] = intent.mode
    return FieldPatch(values, modes) if values else None


def _semantic_closure(
    diff: Any,
    new_kinds: Mapping[str, str],
    *,
    repair_indexes: bool = True,
) -> tuple[set[str], set[str], set[str]]:
    """Return active nodes, membership-changing parents, and retained closure.

    Active content/index repair nodes and all their ancestors must aggregate.
    Direct children of every active directory are retained as reusable inputs, so
    aggregation sees the same sibling set without traversing unchanged subtrees.
    """
    active: set[str] = set()
    membership_changed: set[str] = set()

    def add_ancestors(path: str) -> None:
        parent = _parent(path)
        while True:
            active.add(parent)
            if not parent:
                return
            parent = _parent(parent)

    for path, entry in diff.entries.items():
        content_state = ContentState(entry.content_state)
        index_state = IndexState(entry.index_state)
        content_requires_semantics = content_state in {
            ContentState.ADDED,
            ContentState.MODIFIED,
            ContentState.REPLACE_KIND,
        } or (content_state is ContentState.RESTORE and index_state is not IndexState.COMPLETE)
        if (
            content_requires_semantics
            or (
                repair_indexes
                and index_state
                in {
                    IndexState.MISSING,
                    IndexState.PARTIAL,
                    IndexState.LEVEL_CONFLICT,
                }
                or (repair_indexes and index_state is IndexState.STALE and bool(entry.level_states))
            )
        ) and entry.new_kind in {"file", "directory"}:
            active.add(path)
            add_ancestors(path)
        if content_state in {
            ContentState.ADDED,
            ContentState.DELETED,
            ContentState.RESTORE,
            ContentState.REPLACE_KIND,
        }:
            membership_changed.add(_parent(path))
            add_ancestors(path)

    retained = set(active)
    for path in new_kinds:
        if path and _parent(path) in active:
            retained.add(path)
    return active, membership_changed, retained


def _promote_missing_semantic_inputs(
    *,
    active: set[str],
    retained: set[str],
    new_kinds: Mapping[str, str],
    records_by_path: Mapping[str, Mapping[int, VectorRecordSnapshot]],
) -> None:
    """Promote retained inputs that cannot provide an abstract to a parent.

    An L2 record can be vector-complete after a vectors-only ingest while still
    lacking the file abstract required by its parent's first aggregation.  The
    same applies to a retained directory without an L0 abstract.  Semantic
    closure therefore tracks reusable abstracts independently from index MD5.
    """
    while True:
        promoted = {
            path
            for path in retained - active
            if new_kinds.get(path) in {"file", "directory"}
            and (
                (
                    record := records_by_path.get(path, {}).get(
                        2 if new_kinds.get(path) == "file" else 0
                    )
                )
                is None
                or not str(record.fields.get("abstract") or "").strip()
            )
        }
        if not promoted:
            return
        active.update(promoted)
        for path in new_kinds:
            if path and _parent(path) in promoted:
                retained.add(path)


async def hydrate_context_plan_records(
    *,
    diff: Any,
    new_kinds: Mapping[str, str],
    inventory: Mapping[str, VectorRecordSnapshot],
    vikingdb: Any,
    ctx: Any,
    request: RequestIntent | None = None,
    repair_indexes: bool = True,
) -> tuple[Mapping[str, VectorRecordSnapshot], tuple[set[str], set[str], set[str]]]:
    request = request or RequestIntent("", "semantic_and_vectors")
    if not request.vectorize:
        inventory = {}
    active, membership_changed, retained = _semantic_closure(
        diff, new_kinds, repair_indexes=repair_indexes
    )
    result = dict(inventory)
    summary_hydrated_ids: set[str] = set()

    def required_summary_ids() -> dict[str, Mapping[str, Any]]:
        required: dict[str, Mapping[str, Any]] = {}
        for record_id, record in inventory.items():
            if record.relative_path not in retained:
                continue
            kind = new_kinds.get(record.relative_path)
            wanted = {2} if kind == "file" else {0, 1} if record.relative_path in active else {0}
            if (
                record.level in wanted
                and record_id not in summary_hydrated_ids
                and not str(record.fields.get("abstract") or "").strip()
            ):
                required[record_id] = {"uri": record.uri, "level": record.level}
        return required

    def merge_hydrated(payloads: Mapping[str, Mapping[str, Any]]) -> None:
        for record_id, payload in payloads.items():
            current = result[record_id]
            fields = {
                key: value
                for key, value in payload.items()
                if key not in {"id", "uri", "level", "vector", "sparse_vector", "content"}
                and value is not None
            }
            result[record_id] = VectorRecordSnapshot(
                current.record_id,
                current.uri,
                current.relative_path,
                current.level,
                {**current.fields, **fields},
            )

    while True:
        required = required_summary_ids()
        hydrated = (
            await vikingdb.hydrate_incremental_records(
                required, ctx=ctx, output_fields={"abstract"}
            )
            if required
            else {}
        )
        # Inventory and hydration are separate reads. A missing hydration result
        # has no reusable abstract, so the dependency is promoted below. Keep
        # the inventory identity: an existing record must never be replaced by a
        # locally recomputed ID merely because its second read raced or failed.
        merge_hydrated(hydrated)
        summary_hydrated_ids.update(required)

        by_path, _ = _records_by_path(result)
        promoted = {
            path
            for path in retained - active
            if (
                (record := by_path.get(path, {}).get(2 if new_kinds.get(path) == "file" else 0))
                is None
                or not str(record.fields.get("abstract") or "").strip()
            )
        }
        if not promoted:
            break
        active.update(promoted)
        for path in new_kinds:
            if path and _parent(path) in active:
                retained.add(path)

    if request.vectorize:
        required_scalars: dict[str, Mapping[str, Any]] = {}
        for record_id, record in result.items():
            kind = new_kinds.get(record.relative_path)
            if record.relative_path not in active:
                continue
            if request.processing_mode == "vectors_only":
                if kind != "file" or record.level != 2:
                    continue
            elif record.level not in ({2} if kind == "file" else {0, 1}):
                continue
            required_scalars[record_id] = {"uri": record.uri, "level": record.level}
        if required_scalars:
            merge_hydrated(await vikingdb.hydrate_incremental_records(required_scalars, ctx=ctx))

    return result, (active, membership_changed, retained)


def build_context_update_plan(
    *,
    root_uri: str,
    context_type: str,
    request: RequestIntent,
    diff: Any,
    new_kinds: Mapping[str, str],
    artifact_paths: Mapping[str, str],
    records: Mapping[str, VectorRecordSnapshot],
    is_code_repo: bool,
    account_id: str,
    ingest_options: IngestOptions | Mapping[str, Any] | None = None,
    source_metadata: Mapping[str, str] | None = None,
    closure: tuple[set[str], set[str], set[str]] | None = None,
) -> ContextUpdatePlan:
    """Compile resolved RNFV facts into synchronous and asynchronous actions.

    Content mutations happen before queue handoff. Semantic-dependent index work
    becomes ``IndexSlot`` data; pure deletes and scalar-only changes become direct
    embedding-queue actions. Existing vector record IDs are preserved when an
    existing same-level record is overwritten. Portable existing scalars may be
    carried for normal updates; derived content, vectors, and ACL fields always
    come from the current execution.
    """
    root_uri = root_uri.rstrip("/")
    records_by_path, duplicate_records = (
        _records_by_path(records) if request.vectorize else ({}, ())
    )
    active, membership_changed, retained = closure or _semantic_closure(
        diff,
        new_kinds,
        repair_indexes=request.vectorize and request.processing_mode != "vectors_only",
    )
    semantic_enabled = request.processing_mode != "vectors_only"
    if not semantic_enabled:
        active, membership_changed, retained = set(), set(), set()

    content_actions: list[ContentTreeAction] = []
    direct_actions: list[DirectIndexAction] = [
        DirectIndexAction(IndexAction.DELETE, record.uri, record.level, record.record_id)
        for record in duplicate_records
    ]
    for path, entry in sorted(diff.entries.items()):
        state = ContentState(entry.content_state)
        kind = entry.new_kind
        if state in {ContentState.ADDED, ContentState.RESTORE, ContentState.MODIFIED}:
            content_actions.append(
                ContentTreeAction(
                    ContentTreeOperation.UPSERT,
                    path,
                    entry.old_kind,
                    kind,
                    artifact_paths.get(path),
                    entry.md5,
                )
            )
        elif state is ContentState.DELETED:
            content_actions.append(
                ContentTreeAction(ContentTreeOperation.DELETE, path, old_kind=entry.old_kind)
            )
        elif state is ContentState.REPLACE_KIND:
            content_actions.append(
                ContentTreeAction(
                    ContentTreeOperation.REPLACE_KIND,
                    path,
                    entry.old_kind,
                    kind,
                    artifact_paths.get(path),
                    entry.md5,
                )
            )

        existing = records_by_path.get(path, {})
        valid_levels = {2} if kind == "file" else {0, 1} if kind == "directory" else set()
        for level, record in existing.items():
            if state in {ContentState.DELETED, ContentState.ABSENT} or level not in valid_levels:
                direct_actions.append(
                    DirectIndexAction(IndexAction.DELETE, record.uri, level, record.record_id)
                )
        if (
            not semantic_enabled
            and request.vectorize
            and kind == "file"
            and (
                state
                in {
                    ContentState.ADDED,
                    ContentState.MODIFIED,
                    ContentState.REPLACE_KIND,
                }
                or existing.get(2) is None
                or IndexState(entry.index_state) is IndexState.STALE
            )
        ):
            record = existing.get(2)
            field_patch = _field_patch(request, record) if request.vectorize else None
            index_action = (
                IndexAction.MERGE
                if (
                    record is None
                    and entry.old_kind == kind
                    and IndexState(entry.index_state) in {IndexState.MISSING, IndexState.PARTIAL}
                )
                else IndexAction.UPSERT
            )
            direct_actions.append(
                DirectIndexAction(
                    index_action,
                    _uri(root_uri, path),
                    2,
                    record.record_id
                    if record is not None
                    else vector_record_id(account_id, _uri(root_uri, path), 2),
                    upsert_fields=(
                        {
                            **(_portable_existing_fields(record) or {}),
                            **(
                                field_patch.resolve(record.fields if record else {})
                                if field_patch
                                else {}
                            ),
                        }
                        if index_action is IndexAction.UPSERT
                        else {}
                    ),
                    field_patch=field_patch if index_action is IndexAction.MERGE else None,
                    md5=entry.md5,
                )
            )

    scheduled_direct_ids = {action.record_id for action in direct_actions}
    for path, levels in records_by_path.items():
        for record in levels.values():
            field_patch = _field_patch(request, record) if request.vectorize else None
            patch_values = dict(field_patch.values) if field_patch is not None else {}
            diff_entry = diff.entries.get(path)
            if (
                record.level == 2
                and diff_entry is not None
                and ContentState(diff_entry.content_state) is ContentState.UNCHANGED
                and diff_entry.md5
                and not str(record.fields.get("md5") or "")
            ):
                patch_values["md5"] = diff_entry.md5
                field_patch = FieldPatch(
                    patch_values,
                    field_patch.modes if field_patch is not None else {},
                )
            if (
                field_patch is not None
                and record.record_id not in scheduled_direct_ids
                and (path not in active or not request.vectorize)
            ):
                seed = {
                    "uri": record.uri,
                    "account_id": account_id,
                    "level": record.level,
                    **(_portable_existing_fields(record) or {}),
                    **field_patch.resolve(record.fields),
                }
                direct_actions.append(
                    DirectIndexAction(
                        IndexAction.UPDATE_FIELDS,
                        record.uri,
                        record.level,
                        record.record_id,
                        field_patch=field_patch.with_seed(seed),
                    )
                )
                scheduled_direct_ids.add(record.record_id)

    if semantic_enabled:
        _promote_missing_semantic_inputs(
            active=active,
            retained=retained,
            new_kinds=new_kinds,
            records_by_path=records_by_path,
        )

    semantic_entries: list[SemanticTreeEntry] = []
    for path in sorted(retained, key=lambda value: (value.count("/"), value)):
        kind = new_kinds.get(path)
        if kind not in {"file", "directory"}:
            continue
        diff_entry = diff.entries.get(path)
        state = ContentState(diff_entry.content_state) if diff_entry else ContentState.UNCHANGED
        action = (
            SemanticAction.GENERATE
            if kind == "file" and path in active
            else SemanticAction.AGGREGATE
            if kind == "directory" and path in active
            else SemanticAction.REUSE
        )
        slots: list[IndexSlot] = []
        for level in (2,) if kind == "file" else (0, 1):
            record = records_by_path.get(path, {}).get(level)
            if action is SemanticAction.REUSE:
                if level != (2 if kind == "file" else 0):
                    continue
                if record is None or not str(record.fields.get("abstract") or "").strip():
                    raise ValueError(f"semantic closure lacks reusable abstract for {path}")
                slots.append(IndexSlot(level, record.record_id, _portable_existing_fields(record)))
                continue
            record_id = (
                record.record_id
                if record is not None
                else vector_record_id(account_id, _uri(root_uri, path), level)
            )
            field_patch = _field_patch(request, record) if request.vectorize else None
            index_action = (
                IndexAction.MERGE
                if (
                    record is None
                    and diff_entry
                    and diff_entry.old_kind == kind
                    and IndexState(diff_entry.index_state)
                    in {IndexState.MISSING, IndexState.PARTIAL}
                )
                else IndexAction.UPSERT
            )
            if diff_entry and diff_entry.level_states:
                index_action = IndexAction.MERGE
            slots.append(
                IndexSlot(
                    level,
                    record_id,
                    _portable_existing_fields(record),
                    action=(index_action if request.vectorize else IndexAction.NONE),
                    upsert_fields=(
                        field_patch.resolve(record.fields if record else {})
                        if index_action is IndexAction.UPSERT and field_patch is not None
                        else {}
                    ),
                    field_patch=field_patch,
                    # Keep the original patch mode even for a resolved UPSERT.
                    # If semantic output is unchanged, the executor falls back
                    # to UPDATE_FIELDS and must still interpret append against
                    # the execution-time exact-get record. The normal UPSERT
                    # path deliberately does not forward these modes.
                    fallback_to_patch=field_patch is not None,
                )
            )
        semantic_entries.append(
            SemanticTreeEntry(
                path,
                kind,
                state,
                action,
                md5=diff_entry.md5 if diff_entry else None,
                index_slots=tuple(slots),
                membership_changed=path in membership_changed,
                repair=bool(
                    request.vectorize
                    and diff_entry
                    and IndexState(diff_entry.index_state)
                    in {
                        IndexState.MISSING,
                        IndexState.PARTIAL,
                        IndexState.LEVEL_CONFLICT,
                    }
                    or (
                        request.vectorize
                        and diff_entry
                        and IndexState(diff_entry.index_state) is IndexState.STALE
                        and bool(diff_entry.level_states)
                    )
                ),
            )
        )

    semantic_plan = (
        SemanticPlan(
            root_uri,
            context_type,
            SemanticTreeSnapshot(tuple(semantic_entries)),
            vectorize=request.vectorize,
            file_vector_source=(
                FileVectorSource.SUMMARY_WHEN_AVAILABLE
                if is_code_repo
                else FileVectorSource.CONTENT
            ),
            ingest_options=(
                IngestOptions.from_value(ingest_options) if request.vectorize else IngestOptions()
            ),
            source_metadata=source_metadata,
        )
        if semantic_entries
        else None
    )
    return ContextUpdatePlan(
        root_uri,
        context_type,
        tuple(content_actions),
        semantic_plan,
        tuple(direct_actions),
    )


def build_rfv_context_update_plan(
    *,
    snapshot: Any,
    context_type: str,
    account_id: str,
    source_metadata: Mapping[str, str] | None = None,
) -> tuple[Any, ContextUpdatePlan]:
    """Compile an R/F/V maintenance snapshot without inventing content writes."""
    from openviking.storage.resource_rfv import resolve_rfv_state

    diff = resolve_rfv_state(snapshot)
    if snapshot.request.processing_mode != "vectors_only":
        root_entry = snapshot.formal.entries.get("")
        if root_entry is not None and not root_entry.is_dir:
            vectors_request = RequestIntent(
                target_uri=snapshot.request.target_uri,
                processing_mode="vectors_only",
                vectorize=snapshot.request.vectorize,
                scalar_intents=snapshot.request.scalar_intents,
                force=snapshot.request.force,
            )
            vector_snapshot = type(snapshot)(
                vectors_request,
                snapshot.formal,
                snapshot.vectors,
                snapshot.source_contents,
                snapshot.source_raw_contents,
                snapshot.source_metadata,
            )
            diff, vector_plan = build_rfv_context_update_plan(
                snapshot=vector_snapshot,
                context_type=context_type,
                account_id=account_id,
            )
            root_diff = diff.entries[""]
            needs_refresh = snapshot.request.force or IndexState(root_diff.index_state) in {
                IndexState.MISSING,
                IndexState.PARTIAL,
                IndexState.STALE,
                IndexState.LEVEL_CONFLICT,
            }
            return diff, ContextUpdatePlan(
                root_uri=snapshot.request.target_uri,
                context_type=context_type,
                direct_index_actions=tuple(
                    action
                    for action in vector_plan.direct_index_actions
                    if action.action in {IndexAction.DELETE, IndexAction.UPDATE_FIELDS}
                ),
                file_refresh=FileRefreshIntent(
                    snapshot.request.target_uri,
                    diff.entries[""].md5,
                )
                if needs_refresh
                else None,
            )
        plan = build_context_update_plan(
            root_uri=snapshot.request.target_uri,
            context_type=context_type,
            request=snapshot.request,
            diff=diff,
            new_kinds={
                path: "directory" if entry.is_dir else "file"
                for path, entry in snapshot.formal.entries.items()
            },
            artifact_paths={},
            records=snapshot.vectors.records_by_id,
            is_code_repo=False,
            account_id=account_id,
            source_metadata=source_metadata or snapshot.source_metadata,
        )
        if plan.content_tree_actions:
            raise ValueError("RFV maintenance plan must not mutate formal content")
        return diff, plan
    records_by_path, duplicate_records = _records_by_path(snapshot.vectors.records_by_id)
    direct_actions: list[DirectIndexAction] = [
        DirectIndexAction(IndexAction.DELETE, record.uri, record.level, record.record_id)
        for record in duplicate_records
    ]

    for path, entry in sorted(diff.entries.items()):
        existing = records_by_path.get(path, {})
        if entry.new_kind is None and IndexState(entry.index_state) is IndexState.ORPHAN:
            direct_actions.extend(
                DirectIndexAction(IndexAction.DELETE, record.uri, level, record.record_id)
                for level, record in sorted(existing.items())
            )
            continue

        expected_levels = {2} if entry.new_kind == "file" else {0, 1}
        for level, record in sorted(existing.items()):
            if level not in expected_levels or (
                snapshot.request.processing_mode == "vectors_only"
                and IndexState(entry.level_states.get(level, IndexState.ABSENT))
                is IndexState.ORPHAN
            ):
                direct_actions.append(
                    DirectIndexAction(IndexAction.DELETE, record.uri, level, record.record_id)
                )

        for level in sorted(expected_levels):
            record = existing.get(level)
            level_state = IndexState(entry.level_states.get(level, IndexState.MISSING))
            field_patch = _field_patch(snapshot.request, record)
            if level_state in {IndexState.MISSING, IndexState.STALE}:
                if level not in entry.level_md5s:
                    continue
                uri = _uri(snapshot.request.target_uri, path)
                direct_actions.append(
                    DirectIndexAction(
                        IndexAction.MERGE,
                        uri,
                        level,
                        (
                            record.record_id
                            if record is not None
                            else vector_record_id(account_id, uri, level)
                        ),
                        field_patch=field_patch,
                        md5=entry.level_md5s.get(level),
                    )
                )
            elif field_patch is not None and record is not None:
                direct_actions.append(
                    DirectIndexAction(
                        IndexAction.UPDATE_FIELDS,
                        record.uri,
                        level,
                        record.record_id,
                        field_patch=field_patch.with_seed(
                            {
                                "uri": record.uri,
                                "account_id": account_id,
                                "level": level,
                                **(_portable_existing_fields(record) or {}),
                                **field_patch.resolve(record.fields),
                            }
                        ),
                    )
                )

    return diff, ContextUpdatePlan(
        snapshot.request.target_uri,
        context_type,
        direct_index_actions=tuple(direct_actions),
    )


def _with_directory_root(snapshot: RNFVSnapshot, *, root_preexisting: bool) -> RNFVSnapshot:
    """Add the logical directory root omitted by artifact/tree walks."""
    new_entries = {"": NewEntry(is_dir=True), **snapshot.new.entries}
    formal_entries = (
        {"": FormalEntry(is_dir=True), **snapshot.formal.entries}
        if root_preexisting
        else dict(snapshot.formal.entries)
    )
    return RNFVSnapshot(
        request=snapshot.request,
        new=NewArtifactSnapshot(new_entries, complete=snapshot.new.complete),
        formal=FormalTreeSnapshot(formal_entries, complete=snapshot.formal.complete),
        vectors=snapshot.vectors,
    )


async def build_context_update_plan_from_snapshot(
    *,
    snapshot: RNFVSnapshot,
    store: Any,
    artifact_ref: Any,
    target: Any,
    vikingdb: Any,
    context_type: str,
    is_code_repo: bool,
    account_id: str,
    ctx: Any,
    root_preexisting: bool,
    artifact_paths: Mapping[str, str] | None = None,
    ingest_options: Any = None,
    source_metadata: Mapping[str, str] | None = None,
    root_is_file: bool = False,
) -> tuple[Any, ContextUpdatePlan]:
    """Resolve RNFV facts, hydrate the minimal closure, and build one plan."""
    from openviking.storage.resource_diff import resolve_resource_diff

    if not root_is_file:
        snapshot = _with_directory_root(snapshot, root_preexisting=root_preexisting)
    paths = dict(artifact_paths or {})
    diff = await resolve_resource_diff(
        snapshot,
        store=store,
        artifact_ref=artifact_ref,
        target=target,
        artifact_paths=paths,
    )
    new_kinds = {
        path: "directory" if entry.is_dir else "file"
        for path, entry in snapshot.new.entries.items()
    }
    records, closure = await hydrate_context_plan_records(
        diff=diff,
        new_kinds=new_kinds,
        inventory=snapshot.vectors.records_by_id,
        vikingdb=vikingdb,
        ctx=ctx,
        request=snapshot.request,
        repair_indexes=(
            snapshot.request.vectorize and snapshot.request.processing_mode != "vectors_only"
        ),
    )
    plan = build_context_update_plan(
        root_uri=snapshot.request.target_uri,
        context_type=context_type,
        request=snapshot.request,
        diff=diff,
        new_kinds=new_kinds,
        artifact_paths=paths,
        records=records,
        is_code_repo=is_code_repo,
        account_id=account_id,
        ingest_options=ingest_options,
        source_metadata=source_metadata,
        closure=closure,
    )
    if not root_is_file:
        return diff, plan

    root_entry = diff.entries[""]
    needs_refresh = ContentState(root_entry.content_state) in {
        ContentState.ADDED,
        ContentState.RESTORE,
        ContentState.MODIFIED,
        ContentState.REPLACE_KIND,
    } or (
        snapshot.request.vectorize
        and IndexState(root_entry.index_state)
        in {IndexState.MISSING, IndexState.PARTIAL, IndexState.STALE, IndexState.LEVEL_CONFLICT}
    )
    return diff, ContextUpdatePlan(
        root_uri=plan.root_uri,
        context_type=plan.context_type,
        content_tree_actions=plan.content_tree_actions,
        direct_index_actions=plan.direct_index_actions,
        file_refresh=(
            FileRefreshIntent(snapshot.request.target_uri, root_entry.md5)
            if needs_refresh and snapshot.request.processing_mode != "vectors_only"
            else None
        ),
    )


async def execute_content_tree_actions(
    actions: tuple[ContentTreeAction, ...],
    *,
    store: Any,
    artifact_ref: Any,
    target: Any,
    concurrency: int | None = None,
) -> None:
    """Commit planned content mutations before any asynchronous work."""

    async def run_action(action: ContentTreeAction, operation: Any) -> None:
        try:
            await operation
        except Exception:
            logger.exception(
                "[ContentTreeActionFailed] %s operation=%s relative_path=%s "
                "old_kind=%s new_kind=%s",
                log_correlation(),
                action.operation.value,
                action.relative_path,
                action.old_kind or "-",
                action.new_kind or "-",
            )
            raise

    destructive = [
        action
        for action in actions
        if action.operation in {ContentTreeOperation.DELETE, ContentTreeOperation.REPLACE_KIND}
    ]
    for action in sorted(
        destructive, key=lambda item: (-item.relative_path.count("/"), item.relative_path)
    ):
        await run_action(
            action,
            target.delete_path(action.relative_path, is_dir=action.old_kind == "directory"),
        )
    for action in sorted(
        (action for action in actions if action.new_kind == "directory"),
        key=lambda item: (item.relative_path.count("/"), item.relative_path),
    ):
        await run_action(action, target.mkdir(action.relative_path))
    file_actions = [action for action in actions if action.new_kind == "file"]
    if not file_actions:
        return
    if concurrency is None:
        from openviking_cli.utils.config import get_openviking_config

        concurrency = int(
            get_openviking_config().queue_workers.add_resource.file_operation_concurrency
        )

    async def write(action: ContentTreeAction) -> None:
        async def read_and_write() -> None:
            data = await store.read_bytes(artifact_ref, action.artifact_path)
            await target.write_file(action.relative_path, data)

        await run_action(action, read_and_write())

    await bounded_map(file_actions, write, concurrency=max(1, concurrency))


__all__ = [
    "ContentState",
    "ContentTreeAction",
    "ContentTreeOperation",
    "ContextUpdatePlan",
    "FileVectorSource",
    "FileRefreshIntent",
    "DirectIndexAction",
    "IndexAction",
    "IndexSlot",
    "IndexState",
    "ParentPropagation",
    "SemanticAction",
    "SemanticPlan",
    "SemanticTreeEntry",
    "SemanticTreeSnapshot",
    "build_context_update_plan",
    "build_context_update_plan_from_snapshot",
    "build_rfv_context_update_plan",
    "execute_content_tree_actions",
    "hydrate_context_plan_records",
]
