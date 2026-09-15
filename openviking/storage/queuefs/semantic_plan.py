# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Serializable semantic work planned after a resource tree is committed."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Dict, Literal, Mapping

from openviking.storage.resource_rnfv import NON_PORTABLE_VECTOR_RECORD_FIELDS
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.utils import VikingURI

EntryKind = Literal["file", "directory"]
EntryState = Literal["unchanged", "added", "modified", "deleted"]
_ENTRY_KINDS = frozenset({"file", "directory"})
_ENTRY_STATES = frozenset({"unchanged", "added", "modified", "deleted"})


def _validate_relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("relative_path must be a string")
    if value == "":
        return value
    path = PurePosixPath(value)
    if path.is_absolute() or value != str(path) or any(part == ".." for part in path.parts):
        raise ValueError(f"invalid relative path: {value}")
    return value


def _is_within_root(root_uri: str, uri: str) -> bool:
    root = VikingURI(root_uri).uri.rstrip("/")
    candidate = VikingURI(uri).uri.rstrip("/")
    return candidate == root or candidate.startswith(root + "/")


@dataclass(frozen=True, init=False)
class IndexedRecordSnapshot:
    record_id: str
    level: int
    fields: Mapping[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        record_id: str,
        level: int,
        fields: Mapping[str, Any] | None = None,
        **legacy_fields: Any,
    ) -> None:
        payload = dict(fields or {})
        payload.update(
            {key: value for key, value in legacy_fields.items() if value is not None}
        )
        if payload.get("search_tags") is not None:
            payload["search_tags"] = tuple(str(item) for item in payload["search_tags"])
        object.__setattr__(self, "record_id", record_id)
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "fields", payload)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("record_id must not be empty")
        if self.level not in {0, 1, 2}:
            raise ValueError(f"invalid semantic level: {self.level}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IndexedRecordSnapshot":
        values = dict(data)
        known = {"record_id", "level", "fields"}
        legacy_fields = {key: values.pop(key) for key in list(values) if key not in known}
        fields = dict(values.get("fields") or {})
        fields.update({key: value for key, value in legacy_fields.items() if value is not None})
        values["fields"] = fields
        return cls(**values)

    def __getattr__(self, name: str) -> Any:
        # Compatibility for existing DAG/readers while the serialized contract
        # moves from fixed dataclass fields to a dynamic payload.
        if name in self.fields:
            return self.fields[name]
        raise AttributeError(name)

    def portable_fields(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in self.fields.items()
            if key not in NON_PORTABLE_VECTOR_RECORD_FIELDS and not key.startswith("_")
        }


@dataclass(frozen=True)
class SemanticTreeEntry:
    relative_path: str
    kind: EntryKind
    state: EntryState
    md5: str | None = None
    indexed_records: tuple[IndexedRecordSnapshot, ...] = ()

    def __post_init__(self) -> None:
        _validate_relative_path(self.relative_path)
        if self.kind not in _ENTRY_KINDS:
            raise ValueError(f"invalid entry kind: {self.kind}")
        if self.state not in _ENTRY_STATES:
            raise ValueError(f"invalid entry state: {self.state}")
        if self.kind == "directory" and self.md5:
            raise ValueError("directory entry cannot carry md5")
        if self.state == "deleted" and self.md5:
            raise ValueError("deleted entry cannot carry current md5")
        levels = [record.level for record in self.indexed_records]
        if len(levels) != len(set(levels)):
            raise ValueError(f"duplicate indexed record level for {self.relative_path}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticTreeEntry":
        values = dict(data)
        values["indexed_records"] = tuple(
            IndexedRecordSnapshot.from_dict(item)
            for item in values.get("indexed_records", ())
        )
        return cls(**values)


@dataclass(frozen=True)
class SemanticTreeSnapshot:
    entries: tuple[SemanticTreeEntry, ...]

    def __post_init__(self) -> None:
        paths = [entry.relative_path for entry in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate semantic tree entry path")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticTreeSnapshot":
        return cls(
            entries=tuple(
                SemanticTreeEntry.from_dict(item) for item in data.get("entries", ())
            )
        )


@dataclass(frozen=True)
class VectorRecordRef:
    record_id: str
    uri: str
    level: int

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("record_id must not be empty")
        if self.level not in {0, 1, 2}:
            raise ValueError(f"invalid semantic level: {self.level}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VectorRecordRef":
        return cls(**dict(data))


@dataclass(frozen=True)
class PlannedScalarUpdate:
    record_id: str
    uri: str
    level: int
    fields: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("scalar update record_id must not be empty")
        if self.level not in {0, 1, 2}:
            raise ValueError(f"invalid scalar update level: {self.level}")
        if not self.fields:
            raise ValueError("scalar update fields must not be empty")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlannedScalarUpdate":
        values = dict(data)
        values["fields"] = dict(values.get("fields", {}))
        return cls(**values)


@dataclass(frozen=True)
class SemanticOutputs:
    vectorize: bool = True


class FileVectorSource(str, Enum):
    CONTENT = "content"
    SUMMARY_WHEN_AVAILABLE = "summary_when_available"


@dataclass(frozen=True)
class ParentPropagation:
    enabled: bool = True
    use_freshness: bool = True


@dataclass(frozen=True)
class SemanticPlan:
    root_uri: str
    context_type: str
    tree: SemanticTreeSnapshot
    orphan_vector_deletes: tuple[VectorRecordRef, ...] = ()
    scalar_updates: tuple[PlannedScalarUpdate, ...] = ()
    outputs: SemanticOutputs = field(default_factory=SemanticOutputs)
    propagation: ParentPropagation = field(default_factory=ParentPropagation)
    file_vector_source: FileVectorSource = FileVectorSource.CONTENT
    ingest_options: IngestOptions = field(default_factory=IngestOptions)
    source_metadata: Dict[str, str] | None = None

    def __post_init__(self) -> None:
        root_uri = VikingURI(self.root_uri).uri.rstrip("/")
        object.__setattr__(self, "root_uri", root_uri)
        object.__setattr__(
            self,
            "file_vector_source",
            FileVectorSource(self.file_vector_source),
        )
        if self.context_type not in {"resource", "skill", "memory"}:
            raise ValueError(f"invalid context_type: {self.context_type}")
        for record in self.orphan_vector_deletes:
            if not _is_within_root(root_uri, record.uri):
                raise ValueError(f"vector record is outside root: {record.uri}")
        for update in self.scalar_updates:
            if not _is_within_root(root_uri, update.uri):
                raise ValueError(f"scalar update is outside root: {update.uri}")
        live_record_ids = {
            record.record_id
            for entry in self.tree.entries
            if entry.state != "deleted"
            for record in entry.indexed_records
        }
        deleted_record_ids = {
            record.record_id for record in self.orphan_vector_deletes
        } | {
            record.record_id
            for entry in self.tree.entries
            if entry.state == "deleted"
            for record in entry.indexed_records
        }
        conflicts = live_record_ids & deleted_record_ids
        if conflicts:
            raise ValueError(
                f"conflicting vector operation for record ids: {sorted(conflicts)}"
            )
        scalar_record_ids = [update.record_id for update in self.scalar_updates]
        if len(scalar_record_ids) != len(set(scalar_record_ids)):
            raise ValueError("duplicate scalar update record id")
        scalar_delete_conflicts = set(scalar_record_ids) & deleted_record_ids
        if scalar_delete_conflicts:
            raise ValueError(
                f"conflicting scalar/delete operation for record ids: {sorted(scalar_delete_conflicts)}"
            )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["file_vector_source"] = self.file_vector_source.value
        data["ingest_options"] = self.ingest_options.to_dict()
        return data

    def is_noop(self) -> bool:
        return (
            not any(entry.state != "unchanged" for entry in self.tree.entries)
            and not self.orphan_vector_deletes
            and not self.scalar_updates
        )

    def execution_root_uris(self) -> tuple[str, ...]:
        """Return the shallowest directories that contain planned changes."""
        current_dirs = {
            entry.relative_path
            for entry in self.tree.entries
            if entry.kind == "directory" and entry.state != "deleted"
        }
        candidates: set[str] = set()
        for entry in self.tree.entries:
            if entry.state == "unchanged":
                continue
            if entry.kind == "directory" and entry.state == "modified":
                candidate = entry.relative_path
            else:
                parent = str(PurePosixPath(entry.relative_path).parent)
                candidate = "" if parent == "." else parent
            if candidate not in current_dirs and entry.state != "deleted":
                raise ValueError(
                    "semantic plan lacks execution directory "
                    f"{candidate!r} for changed entry {entry.relative_path!r}"
                )
            while candidate not in current_dirs and candidate:
                parent = str(PurePosixPath(candidate).parent)
                candidate = "" if parent == "." else parent
            candidates.add(candidate)
            if entry.kind == "directory" and entry.state == "added":
                candidates.add(entry.relative_path)
        if candidates and "" in current_dirs:
            candidates.add("")

        def is_reachable_descendant(parent: str, path: str) -> bool:
            """Whether plan adjacency can walk from parent down to path."""
            if parent == path or (parent and not path.startswith(parent + "/")):
                return False
            current = path
            while current != parent:
                if current not in current_dirs:
                    return False
                current_parent = str(PurePosixPath(current).parent)
                current = "" if current_parent == "." else current_parent
                if parent and not current:
                    return False
            return True

        # A sparse plan intentionally omits unchanged subtrees. A lexical ancestor
        # only covers a nested candidate when every directory on the path is in
        # the plan; otherwise the DAG adjacency has no edge to that candidate.
        shallowest = sorted(
            path
            for path in candidates
            if not any(
                parent != path and is_reachable_descendant(parent, path)
                for parent in candidates
            )
        )
        return tuple(
            self.root_uri if not path else f"{self.root_uri}/{path}"
            for path in shallowest
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticPlan":
        return cls(
            root_uri=str(data["root_uri"]),
            context_type=str(data["context_type"]),
            tree=SemanticTreeSnapshot.from_dict(data.get("tree", {})),
            orphan_vector_deletes=tuple(
                VectorRecordRef.from_dict(item)
                for item in data.get("orphan_vector_deletes", ())
            ),
            scalar_updates=tuple(
                PlannedScalarUpdate.from_dict(item)
                for item in data.get("scalar_updates", ())
            ),
            outputs=SemanticOutputs(**dict(data.get("outputs", {}))),
            propagation=ParentPropagation(**dict(data.get("propagation", {}))),
            file_vector_source=FileVectorSource(
                data.get("file_vector_source", FileVectorSource.CONTENT.value)
            ),
            ingest_options=IngestOptions.from_value(data.get("ingest_options")),
            source_metadata=(
                {str(k): str(v) for k, v in data["source_metadata"].items()}
                if isinstance(data.get("source_metadata"), Mapping)
                else None
            ),
        )


__all__ = [
    "FileVectorSource",
    "IndexedRecordSnapshot",
    "ParentPropagation",
    "PlannedScalarUpdate",
    "SemanticOutputs",
    "SemanticPlan",
    "SemanticTreeEntry",
    "SemanticTreeSnapshot",
    "VectorRecordRef",
]
