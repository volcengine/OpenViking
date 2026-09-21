# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Typed R/N/F/V inputs for resource update planning.

R is normalized request intent, N is the parser artifact snapshot, F is the
formal resource tree, and V is the vector-index snapshot.  A validated snapshot
contains every fact needed by the update planner; the planner must not read the
request or either storage backend again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from openviking.utils.ingest_options import IngestOptions

BASE_VECTOR_PROJECTION = frozenset({"id", "uri", "level", "md5"})
NON_PORTABLE_VECTOR_RECORD_FIELDS = frozenset(
    {
        # Identity and tenancy are derived from the current URI/request.
        "id",
        "uri",
        "level",
        "account_id",
        "owner_user_id",
        # Model/content outputs belong to the current execution.
        "vector",
        "sparse_vector",
        "content",
        "updated_at",
        "md5",
        "abstract",
        "type",
        "name",
        # ACL is re-materialized from the current resource hierarchy.
        "acl_mode",
        "acl_direct_grants",
        "acl_inherited_grants",
    }
)
_SCALAR_MODES = frozenset({"replace", "append"})
_SCALAR_FIELDS = frozenset({"search_tags"})

# Parser and formal-tree entries use the same compact shape. They are defined
# beside RNFV rather than a legacy planner so every update path shares them.
CONTROL_BASENAMES = frozenset(
    {".abstract.md", ".overview.md", ".image_mappings.json", ".artifact_manifest.json"}
)


@dataclass(frozen=True)
class NewEntry:
    """One parser-artifact path: final file MD5 or a topology-only directory."""

    md5: str = ""
    is_dir: bool = False


@dataclass(frozen=True)
class FormalEntry:
    """One formal-tree path; F records topology, not historical file bytes."""

    is_dir: bool = False


@dataclass(frozen=True)
class ScalarIntent:
    """One normalized request-side scalar mutation."""

    field: str
    mode: str
    value: Any
    target_levels: frozenset[int] = frozenset({0, 1, 2})

    def __post_init__(self) -> None:
        if self.field not in _SCALAR_FIELDS:
            raise ValueError(f"unsupported resource scalar intent: {self.field}")
        if self.mode not in _SCALAR_MODES:
            raise ValueError(f"unsupported scalar update mode: {self.mode}")
        if not self.target_levels or not self.target_levels <= {0, 1, 2}:
            raise ValueError(f"invalid scalar target levels: {sorted(self.target_levels)}")

    def required_existing_fields(self) -> frozenset[str]:
        # Reading the old value is useful for both modes: append needs it to
        # calculate the result, while replace needs it to detect an effective
        # no-op instead of issuing a redundant update.
        return frozenset({self.field})


@dataclass(frozen=True)
class RequestIntent:
    """Normalized request intent (R), independent of HTTP/queue payload shape."""

    target_uri: str
    processing_mode: str
    vectorize: bool = True
    scalar_intents: tuple[ScalarIntent, ...] = ()

    @classmethod
    def from_ingest_options(
        cls,
        *,
        target_uri: str,
        processing_mode: Any,
        ingest_options: IngestOptions | Mapping[str, Any] | None,
        vectorize: bool = True,
    ) -> "RequestIntent":
        options = IngestOptions.from_value(ingest_options)
        scalar_intents: tuple[ScalarIntent, ...] = ()
        if options.search_tags is not None:
            mode = getattr(processing_mode, "value", processing_mode)
            scalar_intents = (
                ScalarIntent(
                    field="search_tags",
                    mode=options.search_tag_mode,
                    value=tuple(options.search_tags),
                    target_levels=(
                        frozenset({2}) if str(mode) == "vectors_only" else frozenset({0, 1, 2})
                    ),
                ),
            )
        else:
            mode = getattr(processing_mode, "value", processing_mode)
        return cls(
            target_uri=target_uri.rstrip("/"),
            processing_mode=str(mode),
            vectorize=bool(vectorize),
            scalar_intents=scalar_intents,
        )

    def required_vector_fields(self) -> frozenset[str]:
        if not self.vectorize:
            return frozenset()
        fields = set(BASE_VECTOR_PROJECTION)
        for intent in self.scalar_intents:
            fields.update(intent.required_existing_fields())
        return frozenset(fields)


@dataclass(frozen=True)
class NewArtifactSnapshot:
    entries: Mapping[str, Any]
    complete: bool = True


@dataclass(frozen=True)
class FormalTreeSnapshot:
    entries: Mapping[str, Any]
    complete: bool = True


@dataclass(frozen=True)
class VectorRecordSnapshot:
    """One V inventory record with actual backend identity and hydrated scalars."""

    record_id: str
    uri: str
    relative_path: str
    level: int
    fields: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("vector snapshot record_id must not be empty")
        if self.level not in {0, 1, 2}:
            raise ValueError(f"invalid vector snapshot level: {self.level}")


def canonical_vector_records_by_level(
    records: Iterable[VectorRecordSnapshot],
) -> tuple[dict[int, VectorRecordSnapshot], tuple[VectorRecordSnapshot, ...]]:
    """Choose the lowest record ID per level and return the duplicates."""
    canonical: dict[int, VectorRecordSnapshot] = {}
    duplicates: list[VectorRecordSnapshot] = []
    for record in records:
        current = canonical.get(record.level)
        if current is None:
            canonical[record.level] = record
        elif record.record_id < current.record_id:
            duplicates.append(current)
            canonical[record.level] = record
        else:
            duplicates.append(record)
    return canonical, tuple(duplicates)


@dataclass(frozen=True)
class VectorIndexSnapshot:
    """Complete target-prefix V inventory and the projection used to read it."""

    records_by_id: Mapping[str, VectorRecordSnapshot]
    projected_fields: frozenset[str]
    complete: bool = True


@dataclass(frozen=True)
class RNFVSnapshot:
    """Immutable R/N/F/V facts consumed by diff resolution and plan compilation.

    R carries only explicit request intent, N/F decide content topology, and V
    supplies indexed levels, record identities, and selectively hydrated scalars.
    No later planner step may silently reread those sources.
    """

    request: RequestIntent
    new: NewArtifactSnapshot
    formal: FormalTreeSnapshot
    vectors: VectorIndexSnapshot

    def validate_for_planning(self) -> None:
        if not self.request.vectorize:
            return
        required = self.request.required_vector_fields()
        missing = required - self.vectors.projected_fields
        if missing:
            raise ValueError(
                "RNFV vector projection misses required fields: " + ", ".join(sorted(missing))
            )
        root = self.request.target_uri.rstrip("/")
        for record in self.vectors.records_by_id.values():
            if record.uri != root and not record.uri.startswith(root + "/"):
                raise ValueError(f"RNFV vector record is outside target: {record.uri}")


__all__ = [
    "BASE_VECTOR_PROJECTION",
    "CONTROL_BASENAMES",
    "FormalTreeSnapshot",
    "NewArtifactSnapshot",
    "NewEntry",
    "NON_PORTABLE_VECTOR_RECORD_FIELDS",
    "RequestIntent",
    "RNFVSnapshot",
    "ScalarIntent",
    "FormalEntry",
    "VectorIndexSnapshot",
    "VectorRecordSnapshot",
    "canonical_vector_records_by_level",
]
