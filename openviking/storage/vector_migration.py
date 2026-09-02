# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for copying and deleting vector records during namespace migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from openviking.core.namespace import (
    context_type_for_uri,
    owner_fields_for_uri,
    owner_space_for_uri,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage.abstract_overview import (
    rewrite_abstract_overview_for_transfer,
    rewrite_viking_uri_references,
)
from openviking.storage.expr import And, Contains, Eq, Or, PathScope
from openviking.storage.vector_ids import vector_record_id
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.uri import VikingURI

VECTOR_MIGRATION_OUTPUT_FIELDS = [
    "id",
    "uri",
    "type",
    "context_type",
    "vector",
    "sparse_vector",
    "created_at",
    "updated_at",
    "active_count",
    "level",
    "name",
    "description",
    "tags",
    "abstract",
    "account_id",
    "owner_user_id",
]

_VECTOR_PAYLOAD_FIELDS = ("vector", "sparse_vector")
_MAX_VECTOR_RECORDS_PER_SCOPE = 100_000


@dataclass
class VectorMigrationResult:
    copied: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: "VectorMigrationResult") -> None:
        self.copied += other.copied
        self.deleted += other.deleted
        self.skipped += other.skipped
        self.failed += other.failed
        self.warnings.extend(other.warnings)


def _root_ctx(account_id: str) -> RequestContext:
    return RequestContext(user=UserIdentifier(account_id, "default"), role=Role.ROOT)


def _normalize_uri(uri: str) -> str:
    return VikingURI(uri).uri.rstrip("/")


def _vector_record_id(account_id: str, uri: str, level: Any) -> str:
    return vector_record_id(account_id, uri, level)


def _has_vector_payload(record: dict[str, Any]) -> bool:
    for field_name in _VECTOR_PAYLOAD_FIELDS:
        value = record.get(field_name)
        if isinstance(value, (list, dict)) and value:
            return True
    return False


def uri_in_transfer_scope(uri: str, scope_uri: str, *, recursive: bool) -> bool:
    """Return whether *uri* belongs to a file or recursive directory transfer."""
    scope_uri = _normalize_uri(scope_uri)
    return (
        uri == scope_uri
        or uri.startswith(scope_uri + "#")
        or (recursive and uri.startswith(scope_uri + "/"))
    )


def rewrite_transfer_uri(uri: str, source_uri: str, target_uri: str) -> str:
    """Rewrite a URI from one transfer scope to another without prefix leakage."""
    source_uri = _normalize_uri(source_uri)
    target_uri = _normalize_uri(target_uri)
    if uri == source_uri:
        return target_uri
    if uri.startswith(source_uri + "/") or uri.startswith(source_uri + "#"):
        return target_uri + uri[len(source_uri) :]
    return uri


def rewrite_vector_record(
    record: dict[str, Any],
    *,
    source_uri: str,
    target_uri: str,
    ctx: RequestContext,
    mode: Literal["copy", "move"],
    timestamp: Any,
) -> dict[str, Any]:
    """Build a target vector record while retaining its existing vector payload."""
    if mode not in {"copy", "move"}:
        raise ValueError(f"Unsupported vector transfer mode: {mode}")
    record_uri = record.get("uri")
    if not isinstance(record_uri, str):
        raise ValueError("Vector record is missing a string URI")

    rewritten_uri = rewrite_transfer_uri(record_uri, source_uri, target_uri)
    payload = {key: value for key, value in record.items() if key != "_score"}
    owner_fields = owner_fields_for_uri(rewritten_uri)
    payload.update(
        {
            "id": _vector_record_id(ctx.account_id, rewritten_uri, record.get("level", 2)),
            "uri": rewritten_uri,
            "account_id": ctx.account_id,
            "owner_user_id": owner_fields.get("owner_user_id"),
            "owner_space": owner_space_for_uri(rewritten_uri),
            "context_type": context_type_for_uri(rewritten_uri),
        }
    )
    if mode == "copy":
        payload.update(
            {
                "created_at": timestamp,
                "updated_at": timestamp,
                "active_count": 0,
            }
        )
    try:
        level = int(record.get("level", 2))
    except (TypeError, ValueError):
        level = 2
    if level in {0, 1}:
        abstract = payload.get("abstract")
        if isinstance(abstract, str):
            payload["abstract"] = rewrite_viking_uri_references(
                abstract,
                source_uri,
                target_uri,
            )
        content = payload.get("content")
        if isinstance(content, (str, bytes)):
            payload["content"] = rewrite_abstract_overview_for_transfer(
                content,
                level=level,
                source_dir_uri=record_uri,
                target_dir_uri=rewritten_uri,
                source_scope_uri=source_uri,
                target_scope_uri=target_uri,
            )
    return payload


async def _records_in_scope(
    vector_store: Any,
    *,
    account_id: str,
    uri: str,
    recursive: bool,
) -> list[dict[str, Any]]:
    filters = [Eq("uri", uri)]
    if recursive:
        filters.append(PathScope("uri", uri, depth=-1))
    if getattr(vector_store, "mode", None) == "volcengine":
        parent = VikingURI(uri).parent
        if parent is not None and parent.uri != "viking://":
            filters.append(PathScope("uri", parent.uri, depth=1))
    else:
        filters.append(Contains("uri", uri + "#"))

    ctx = _root_ctx(account_id)
    records = await vector_store.filter(
        filter=And([Eq("account_id", account_id), Or(filters)]),
        limit=_MAX_VECTOR_RECORDS_PER_SCOPE,
        output_fields=VECTOR_MIGRATION_OUTPUT_FIELDS,
        ctx=ctx,
    )
    return [
        record
        for record in records
        if isinstance(record.get("uri"), str)
        and uri_in_transfer_scope(record["uri"], uri, recursive=recursive)
    ]


async def copy_vector_records(
    vector_store: Any,
    *,
    account_id: str,
    source_uri: str,
    target_uri: str,
    recursive: bool,
) -> VectorMigrationResult:
    """Copy vector records from one URI scope to another without re-embedding."""
    result = VectorMigrationResult()
    if (
        not vector_store
        or not hasattr(vector_store, "filter")
        or not hasattr(vector_store, "upsert")
    ):
        result.warnings.append(f"Skipped vector copy for {source_uri}: vector store is unavailable")
        return result

    source_uri = source_uri.rstrip("/")
    target_uri = target_uri.rstrip("/")
    ctx = _root_ctx(account_id)
    try:
        records = await _records_in_scope(
            vector_store,
            account_id=account_id,
            uri=source_uri,
            recursive=recursive,
        )
    except Exception as exc:
        result.failed += 1
        result.warnings.append(f"Failed to read vectors for {source_uri}: {exc}")
        return result

    if len(records) >= _MAX_VECTOR_RECORDS_PER_SCOPE:
        result.warnings.append(
            f"Vector copy for {source_uri} reached the per-scope record limit "
            f"({_MAX_VECTOR_RECORDS_PER_SCOPE}); run reindex if records are missing"
        )

    timestamp = get_current_timestamp()
    for record in records:
        source_record_uri = record["uri"]
        if not _has_vector_payload(record):
            result.skipped += 1
            continue

        payload = rewrite_vector_record(
            record,
            source_uri=source_uri,
            target_uri=target_uri,
            ctx=ctx,
            mode="copy",
            timestamp=timestamp,
        )
        try:
            await vector_store.upsert(payload, ctx=ctx)
            result.copied += 1
        except Exception as exc:
            result.failed += 1
            result.warnings.append(
                f"Failed to copy vector {source_record_uri} to {payload['uri']}: {exc}"
            )
    return result


async def delete_vector_records(
    vector_store: Any,
    *,
    account_id: str,
    uri: str,
    recursive: bool = True,
) -> VectorMigrationResult:
    """Delete vector records for a legacy URI scope."""
    result = VectorMigrationResult()
    if (
        not vector_store
        or not hasattr(vector_store, "filter")
        or not hasattr(vector_store, "delete")
    ):
        result.warnings.append(f"Skipped vector cleanup for {uri}: vector store is unavailable")
        return result

    uri = uri.rstrip("/")
    ctx = _root_ctx(account_id)
    try:
        records = await _records_in_scope(
            vector_store,
            account_id=account_id,
            uri=uri,
            recursive=recursive,
        )
    except Exception as exc:
        result.failed += 1
        result.warnings.append(f"Failed to read vectors for cleanup {uri}: {exc}")
        return result

    ids = sorted({str(record["id"]) for record in records if record.get("id")})
    if not ids:
        return result
    try:
        result.deleted = int(await vector_store.delete(ids, ctx=ctx) or 0)
    except Exception as exc:
        result.failed += len(ids)
        result.warnings.append(f"Failed to delete vectors for {uri}: {exc}")
    return result
