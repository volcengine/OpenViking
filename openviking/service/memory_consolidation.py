"""Deterministic, read-only planning for existing-memory consolidation.

This module intentionally does not read from VikingFS or apply any mutations.
Callers must enumerate one explicit user/type scope and pass the resulting
files in.  Keeping the planner pure makes the first consolidation slice safe
to preview and straightforward to protect with a revision check later.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

from pydantic import BaseModel, Field

from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.utils.memory_file_utils import (
    MemoryFileUtils,
    memory_version_from_fields,
)

EXACT_DUPLICATE_CONSOLIDATOR_VERSION = "exact-normalized-v1"


class ConsolidationSource(BaseModel):
    """One memory file already enumerated inside an explicit scope."""

    uri: str
    raw_content: str


class ExactDuplicateMember(BaseModel):
    """Stable evidence needed to identify and revision-check a candidate."""

    uri: str
    version: int = Field(ge=1)
    content_sha256: str


class ExactDuplicateGroup(BaseModel):
    """One deterministic exact/normalized duplicate group."""

    candidate_id: str
    canonical: ExactDuplicateMember
    duplicates: list[ExactDuplicateMember]
    normalized_sha256: str
    reason: str = "normalized content is identical"


class ExactDuplicateDryRunPlan(BaseModel):
    """A read-only consolidation preview for one user and memory type."""

    schema_version: str = "memory_consolidation_dry_run_plan_v1"
    consolidator_version: str = EXACT_DUPLICATE_CONSOLIDATOR_VERSION
    scope_uri: str
    memory_type: str
    revision: str
    scanned_files: int = Field(ge=0)
    groups: list[ExactDuplicateGroup]


def _normalized_content(content: str) -> str:
    """Normalize only representation noise, never prose or Markdown structure."""

    content = unicodedata.normalize("NFC", content.replace("\r\n", "\n").replace("\r", "\n"))
    return "\n".join(line.rstrip() for line in content.split("\n")).strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity_payload(raw_content: str, *, uri: str) -> tuple[str, str, int]:
    """Return a conservative identity digest plus persisted revision evidence.

    The persisted version is excluded from identity because it protects plan
    freshness rather than describing memory meaning.  All other metadata,
    including links and backlinks, remains in the digest so a dry-run never
    proposes collapsing memories whose relationships differ.
    """

    memory_file = MemoryFileUtils.read(raw_content, uri=uri)
    metadata = memory_file.model_dump(
        mode="json",
        exclude={"uri", "content"},
    )
    extra_fields = dict(metadata.get("extra_fields") or {})
    version = memory_version_from_fields(extra_fields)
    extra_fields.pop("version", None)
    metadata["extra_fields"] = extra_fields
    identity = {
        "content": _normalized_content(memory_file.content),
        "metadata": metadata,
    }
    return (
        _sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"))),
        _sha256(raw_content),
        version,
    )


def _validate_scope(scope_uri: str, memory_type: str) -> str:
    normalized_scope = scope_uri.rstrip("/")
    parts = [part for part in normalized_scope.split("/") if part]
    if (
        not memory_type
        or MemoryUpdater.memory_type_from_uri(normalized_scope) != memory_type
        or len(parts) < 2
        or parts[-2:] != ["memories", memory_type]
    ):
        raise ValueError("scope_uri must identify exactly one memories/<memory_type> directory")
    return normalized_scope


def build_exact_duplicate_dry_run_plan(
    *,
    scope_uri: str,
    memory_type: str,
    sources: list[ConsolidationSource],
) -> ExactDuplicateDryRunPlan:
    """Build a deterministic preview without reading or mutating storage.

    The canonical member is the lexicographically smallest URI.  Candidates
    are grouped only when their persisted content is identical after Unicode,
    newline, and trailing-whitespace normalization.  Links and other Markdown
    remain part of the fingerprint, so structurally different memories are
    never collapsed by this conservative first slice.
    """

    normalized_scope = _validate_scope(scope_uri, memory_type)
    members_by_hash: dict[str, list[ExactDuplicateMember]] = {}
    seen_uris: set[str] = set()

    for source in sorted(sources, key=lambda item: item.uri):
        if source.uri in seen_uris:
            raise ValueError(f"duplicate source URI: {source.uri}")
        seen_uris.add(source.uri)

        if not source.uri.startswith(f"{normalized_scope}/"):
            raise ValueError(f"source is outside consolidation scope: {source.uri}")
        if MemoryUpdater.memory_type_from_uri(source.uri) != memory_type:
            raise ValueError(f"source has a different memory type: {source.uri}")

        memory_file = MemoryFileUtils.read(source.raw_content, uri=source.uri)
        if memory_file.memory_type and memory_file.memory_type != memory_type:
            raise ValueError(f"source metadata has a different memory type: {source.uri}")

        normalized_sha256, content_sha256, version = _identity_payload(
            source.raw_content,
            uri=source.uri,
        )
        member = ExactDuplicateMember(
            uri=source.uri,
            version=version,
            content_sha256=content_sha256,
        )
        members_by_hash.setdefault(normalized_sha256, []).append(member)

    groups: list[ExactDuplicateGroup] = []
    for normalized_sha256, members in sorted(members_by_hash.items()):
        members.sort(key=lambda item: item.uri)
        if len(members) < 2:
            continue
        candidate_payload = {
            "consolidator_version": EXACT_DUPLICATE_CONSOLIDATOR_VERSION,
            "normalized_sha256": normalized_sha256,
            "uris": [member.uri for member in members],
        }
        groups.append(
            ExactDuplicateGroup(
                candidate_id=(
                    "exact:"
                    + _sha256(json.dumps(candidate_payload, sort_keys=True, separators=(",", ":")))
                ),
                canonical=members[0],
                duplicates=members[1:],
                normalized_sha256=normalized_sha256,
            )
        )
    groups.sort(key=lambda group: group.canonical.uri)

    revision_payload = {
        "schema_version": "memory_consolidation_dry_run_plan_v1",
        "consolidator_version": EXACT_DUPLICATE_CONSOLIDATOR_VERSION,
        "scope_uri": normalized_scope,
        "memory_type": memory_type,
        "sources": [
            {
                "uri": member.uri,
                "version": member.version,
                "content_sha256": member.content_sha256,
            }
            for members in members_by_hash.values()
            for member in members
        ],
    }
    revision_payload["sources"].sort(key=lambda item: item["uri"])
    revision = _sha256(json.dumps(revision_payload, sort_keys=True, separators=(",", ":")))

    return ExactDuplicateDryRunPlan(
        scope_uri=normalized_scope,
        memory_type=memory_type,
        revision=revision,
        scanned_files=len(sources),
        groups=groups,
    )
