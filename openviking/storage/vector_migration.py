# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for copying and deleting vector records during namespace migration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from openviking.core.namespace import context_type_for_uri, owner_fields_for_uri
from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Contains, Eq, Or, PathScope
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
    "owner_space",
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
    return VikingURI.normalize(uri).rstrip("/")


def _seed_uri_for_id(uri: str, level: Any) -> str:
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        level_int = 2

    if level_int == 0:
        return uri if uri.endswith("/.abstract.md") else f"{uri}/.abstract.md"
    if level_int == 1:
        return uri if uri.endswith("/.overview.md") else f"{uri}/.overview.md"
    return uri


def _vector_record_id(account_id: str, uri: str, level: Any) -> str:
    seed_uri = _seed_uri_for_id(uri, level)
    return hashlib.md5(f"{account_id}:{seed_uri}".encode("utf-8")).hexdigest()


def _has_vector_payload(record: dict[str, Any]) -> bool:
    for field_name in _VECTOR_PAYLOAD_FIELDS:
        value = record.get(field_name)
        if isinstance(value, (list, dict)) and value:
            return True
    return False


def _uri_in_scope(uri: str, scope_uri: str, *, recursive: bool) -> bool:
    return (
        uri == scope_uri
        or uri.startswith(scope_uri + "#")
        or (recursive and uri.startswith(scope_uri + "/"))
    )


def _rewrite_uri(uri: str, source_uri: str, target_uri: str) -> str:
    if uri == source_uri:
        return target_uri
    if uri.startswith(source_uri + "/") or uri.startswith(source_uri + "#"):
        return target_uri + uri[len(source_uri) :]
    return uri


async def _records_in_scope(
    vector_store: Any,
    *,
    account_id: str,
    uri: str,
    recursive: bool,
) -> list[dict[str, Any]]:
    filters = [Eq("uri", uri), Contains("uri", uri + "#")]
    if recursive:
        filters.append(PathScope("uri", uri, depth=-1))

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
        and _uri_in_scope(record["uri"], uri, recursive=recursive)
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

    source_uri = _normalize_uri(source_uri)
    target_uri = _normalize_uri(target_uri)
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

        rewritten_uri = _rewrite_uri(source_record_uri, source_uri, target_uri)
        owner_fields = owner_fields_for_uri(
            rewritten_uri,
            user=ctx.user,
            account_id=account_id,
        )
        level = record.get("level", 2)
        payload = {
            key: value
            for key, value in record.items()
            if key not in {"id", "uri", "account_id", "owner_user_id", "owner_space", "_score"}
            and value is not None
        }
        payload.update(
            {
                "id": _vector_record_id(account_id, rewritten_uri, level),
                "uri": rewritten_uri,
                "account_id": account_id,
                "owner_user_id": owner_fields.get("owner_user_id"),
                "owner_space": owner_fields.get("owner_user_id") or "",
                "context_type": context_type_for_uri(rewritten_uri),
                "created_at": timestamp,
                "updated_at": timestamp,
                "active_count": 0,
            }
        )
        try:
            await vector_store.upsert(payload, ctx=ctx)
            result.copied += 1
        except Exception as exc:
            result.failed += 1
            result.warnings.append(
                f"Failed to copy vector {source_record_uri} to {rewritten_uri}: {exc}"
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

    uri = _normalize_uri(uri)
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
