# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Item declarations backed by the collection-owned Catalog engine."""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from openviking.storage.acl import AclAction
from openviking_cli.exceptions import InvalidArgumentError, NotInitializedError, OpenVikingError

ITEM_ATTRS_OMITTED = object()


def validate_item_attrs(item_attrs: Any) -> dict[str, str]:
    """Validate the public flat string map without exposing engine sentinels."""
    if not isinstance(item_attrs, dict):
        raise InvalidArgumentError("item_attrs must be a flat string-to-string object")
    normalized: dict[str, str] = {}
    for key, value in item_attrs.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise InvalidArgumentError("item_attrs must be a flat string-to-string object")
        if not key or "=" in key or "\0" in key or "\0" in value:
            raise InvalidArgumentError(
                "item_attrs keys must be non-empty strings without '=' or NUL"
            )
        normalized[key] = value
    return normalized


def validate_item_declaration(
    as_item: bool, item_attrs: Any = ITEM_ATTRS_OMITTED
) -> dict[str, str] | None:
    if type(as_item) is not bool:
        raise InvalidArgumentError("as_item must be a boolean")
    if item_attrs is ITEM_ATTRS_OMITTED:
        return None
    if not as_item:
        raise InvalidArgumentError("item_attrs may only be provided when as_item=true")
    return validate_item_attrs(item_attrs)


def encode_item_attrs(item_attrs: Mapping[str, str]) -> list[str]:
    """Convert the OV map into the engine's existing list<string> representation."""
    return [f"{key}={item_attrs[key]}" for key in sorted(item_attrs)]


def decode_item_attrs(item_attrs: Any) -> dict[str, str]:
    """Decode engine values and keep malformed internal placeholders out of OV."""
    decoded: dict[str, str] = {}
    if not isinstance(item_attrs, list):
        return decoded
    for entry in item_attrs:
        if not isinstance(entry, str) or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        if key:
            decoded[key] = value
    return decoded


async def declare_item(
    fs,
    uri: str,
    ctx,
    *,
    as_item: bool,
    item_attrs: Any = ITEM_ATTRS_OMITTED,
    is_dir: bool,
) -> dict[str, Any] | None:
    """Atomically create or replay an Item declaration at its actual root."""
    normalized_attrs = validate_item_declaration(as_item, item_attrs)
    if not as_item:
        return None

    from openviking.core.namespace_policy import is_system_uri

    if is_system_uri(uri):
        raise InvalidArgumentError("Item declarations are not supported for the system namespace")
    await fs._ensure_access(uri, ctx, action=AclAction.WRITE)
    storage = fs._get_vector_store()
    if storage is None:
        raise NotInitializedError("Catalog storage")

    try:
        await storage.catalog_execute(
            {
                "op": "upsert_nodes",
                "nodes": [{"uri": uri, "node_type": "dir" if is_dir else "file"}],
            },
            ctx=ctx,
        )
        request: dict[str, Any] = {
            "op": "declare_item",
            "uri": uri,
            # The native operation keeps an existing value and uses this only
            # for the first declaration under its collection lock.
            "item_uuid": str(uuid.uuid4()),
        }
        if normalized_attrs is not None:
            request["item_attrs"] = encode_item_attrs(normalized_attrs)
        result = await storage.catalog_execute(request, ctx=ctx)
        return {
            "item_uuid": result["item_uuid"],
            "uri": result["uri"],
            "item_attrs": decode_item_attrs(result.get("item_attrs")),
        }
    except OpenVikingError as exc:
        exc.details = {**exc.details, "uri": uri, "stage": "catalog_declaration"}
        raise
    except Exception as exc:
        raise OpenVikingError(
            f"Catalog declaration failed for {uri}: {exc}",
            code="INTERNAL",
            details={"uri": uri, "stage": "catalog_declaration"},
        ) from exc


async def delete_catalog_subtree(fs, uri: str, ctx) -> None:
    from openviking.core.namespace_policy import is_system_uri
    from openviking_cli.exceptions import UnimplementedError

    if is_system_uri(uri):
        return
    storage = fs._get_vector_store()
    execute = getattr(storage, "catalog_execute", None)
    if execute is None:
        return
    try:
        await execute({"op": "delete_subtree", "uri": uri}, ctx=ctx)
    except UnimplementedError:
        return


async def move_catalog_subtree(fs, from_uri: str, to_uri: str, ctx) -> dict[str, Any] | None:
    """Move Item declarations and return an opaque token for exact rollback."""
    from openviking.core.namespace_policy import is_system_uri
    from openviking_cli.exceptions import UnimplementedError

    if is_system_uri(from_uri) or is_system_uri(to_uri):
        return None
    storage = fs._get_vector_store()
    execute = getattr(storage, "catalog_execute", None)
    if execute is None:
        return None
    try:
        result = await execute(
            {"op": "move_subtree", "from_uri": from_uri, "to_uri": to_uri},
            ctx=ctx,
        )
        if not result.get("moved_nodes", 0):
            return None
        undo = result.get("undo")
        if not isinstance(undo, dict):
            raise RuntimeError("Catalog move did not return rollback state")
        return undo
    except UnimplementedError:
        return None


async def restore_catalog_move(fs, undo: dict[str, Any], ctx) -> None:
    """Restore both source and pre-existing target metadata after a failed move."""
    storage = fs._get_vector_store()
    execute = getattr(storage, "catalog_execute", None)
    if execute is None:
        raise RuntimeError("Catalog rollback is unavailable")
    await execute({"op": "restore_move", "undo": undo}, ctx=ctx)
