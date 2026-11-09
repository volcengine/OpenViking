# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for transferring vector records when content is copied or moved."""

from __future__ import annotations

from typing import Any, Literal

from openviking.core.namespace import (
    context_type_for_uri,
    owner_fields_for_uri,
    owner_space_for_uri,
)
from openviking.server.identity import RequestContext
from openviking.storage.abstract_overview import (
    rewrite_abstract_overview_for_transfer,
    rewrite_viking_uri_references,
)
from openviking.storage.acl import ACL_CONTEXT_FIELDS
from openviking.storage.expr import And, Contains, Eq, Or, PathScope
from openviking.storage.vector_ids import vector_record_id
from openviking_cli.utils.uri import VikingURI


def _normalize_uri(uri: str) -> str:
    return VikingURI(uri).uri.rstrip("/")


def _vector_record_id(account_id: str, uri: str, level: Any) -> str:
    return vector_record_id(account_id, uri, level)


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
    payload = {
        key: value
        for key, value in record.items()
        if key != "_score" and key not in ACL_CONTEXT_FIELDS
    }
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
