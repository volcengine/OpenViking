# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource file metadata adapter for the common TTL lifecycle.

Directory policies provide defaults, but directories are not lifecycle owners.
Each source file owns an independent sidecar so its relative deadline follows
that file's latest content update and cleanup never removes a live sibling.
Generated summaries never serve as lifecycle metadata.
"""

from __future__ import annotations

import json
from typing import Mapping

from openviking.concurrency import bounded_map
from openviking.config.ttl import resolve_ttl_config
from openviking.core.namespace import classify_uri
from openviking.core.ttl import (
    OBJECT_TYPE_RESOURCE_FILE,
    apply_ttl_fields,
    freeze_ttl_fields,
    hidden_by_ttl,
    ttl_enabled,
    ttl_metadata_uri,
    ttl_scope_for_uri,
)
from openviking.pyagfs.exceptions import AGFSNotADirectoryError
from openviking.server.error_mapping import is_storage_not_found
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime
from openviking_cli.exceptions import ConflictError, InvalidArgumentError, NotFoundError


def resource_ttl_targets(uri: str):
    """Yield the exact resource file owner, never an ancestor directory."""
    if ttl_scope_for_uri(uri) != "resources":
        return
    shape = classify_uri(uri)
    parts = shape.parts
    root_depth = (shape.content_index or 0) + 1
    if len(parts) <= root_depth:
        return
    yield OBJECT_TYPE_RESOURCE_FILE, uri.rstrip("/")


async def read_resource_fields(fs, object_type: str, uri: str, *, ctx):
    path = fs._uri_to_path(ttl_metadata_uri(object_type, uri), ctx=ctx)
    try:
        # Never cache absence: imports/restores can publish metadata on another worker.
        await fs._async_agfs.stat(path, bypass_cache=True)
        raw = fs._handle_agfs_read(await fs._async_agfs.read(path))
    except (NotADirectoryError, AGFSNotADirectoryError):
        # The candidate owner may be a flat file, which has no child sidecar.
        return None
    except Exception as exc:
        if is_storage_not_found(exc):
            return None
        raise
    fields = json.loads(raw)
    if not isinstance(fields, dict) or not fields.get("ttl_generation"):
        raise ValueError(f"Invalid resource TTL metadata for {uri}")
    fields["expires_at"] = format_iso8601(parse_iso_datetime(fields["expires_at"]))
    return fields


async def resource_ttl_fields(fs, uri: str, *, ctx) -> dict:
    """Return the exact file's lifecycle fields."""
    if not ttl_enabled() and not await fs.ttl_registry.account_may_have_records(ctx.account_id):
        return {}
    for object_type, owner in resource_ttl_targets(uri):
        fields = await read_resource_fields(fs, object_type, owner, ctx=ctx)
        return dict(fields) if fields is not None else {}
    return {}


async def resource_ttl_visible(fs, uri: str, *, ctx, require_source=False) -> bool:
    for object_type, owner in resource_ttl_targets(uri):
        fields = await read_resource_fields(fs, object_type, owner, ctx=ctx)
        if fields is not None:
            if hidden_by_ttl(fields.get("expires_at")):
                return False
        else:
            # Partial strict deletion may remove metadata before all bytes/index rows.
            record = await fs.ttl_registry.get(ctx.account_id, owner)
            if record is not None and hidden_by_ttl(record.expires_at):
                return False
    if require_source:
        try:
            await fs._async_agfs.stat(fs._uri_to_path(uri, ctx=ctx), bypass_cache=True)
        except Exception as exc:
            if is_storage_not_found(exc):
                return False
            raise
    return True


async def prepare_resource_ttl(
    fs,
    uri: str,
    *,
    is_dir: bool,
    existing: bool,
    ctx,
    lease_ref,
    resource_ttl=None,
    received_at=None,
    content_md5=None,
) -> dict:
    """Create or renew one resource file's independent TTL snapshot.

    A directory only supplies configuration defaults and therefore receives no
    metadata or cleanup registration. Existing relative snapshots keep their
    original duration and renew from ``received_at``; explicit absolute
    deadlines remain unchanged. Legacy files with no snapshot do not become
    managed merely because a directory policy changed.
    """
    if ttl_scope_for_uri(uri) != "resources" or is_dir:
        return {}
    object_type = OBJECT_TYPE_RESOURCE_FILE
    fields = await read_resource_fields(fs, object_type, uri, ctx=ctx)
    pending = await fs.ttl_registry.get(ctx.account_id, uri)
    expired_snapshot = (fields is not None and hidden_by_ttl(fields.get("expires_at"))) or (
        fields is None and pending is not None and hidden_by_ttl(pending.expires_at)
    )
    if expired_snapshot and pending is not None:
        # Physical cleanup is still in flight. Never let a writer race the old
        # generation; once cleanup removes the registry entry, a changed Watch
        # source may deliberately establish a new incarnation.
        raise NotFoundError(uri, "resource")
    if fields is not None and not expired_snapshot:
        if existing:
            renewed = apply_ttl_fields(uri, {}, existing_fields=fields, received_at=received_at)
            if content_md5:
                renewed["content_md5"] = content_md5
            elif fields.get("content_md5"):
                renewed["content_md5"] = fields["content_md5"]
            if renewed != fields:
                await write_resource_fields(
                    fs, object_type, uri, renewed, ctx=ctx, lease_ref=lease_ref
                )
                return renewed
        return fields
    if pending is not None and not expired_snapshot:
        if pending.object_type != object_type:
            raise ConflictError("resource TTL write is pending for a different object type")
        # A crash between registry publication and metadata publication must
        # not replace the pending generation with an unmanaged new object.
        fields = {"expires_at": pending.expires_at, "ttl_generation": pending.generation}
        await write_resource_fields(fs, object_type, uri, fields, ctx=ctx, lease_ref=lease_ref)
        return fields
    config = await resolve_ttl_config(fs, ctx.account_id) if not existing else None
    fields = (
        None
        if existing
        else freeze_ttl_fields(
            uri,
            received_at=received_at,
            resource_ttl=resource_ttl,
            config=config,
        )
    )
    if fields is None:
        if expired_snapshot:
            # A direct create or a Watch-observed content change is a new
            # incarnation. If policy is now disabled, remove the tombstone so
            # the newly written file is visible and unmanaged.
            await fs._remove_resource_file_metadata(uri, ctx=ctx, lease_ref=lease_ref)
        return {}
    if content_md5:
        fields["content_md5"] = content_md5
    await write_resource_fields(fs, object_type, uri, fields, ctx=ctx, lease_ref=lease_ref)
    return fields


async def unchanged_expired_resource_paths(
    fs,
    root_uri: str,
    file_md5s: Mapping[str, str],
    *,
    ctx,
    concurrency: int = 64,
) -> set[str]:
    """Return Watch artifact files that match durable expiry tombstones.

    A directory Watch must keep updating live files, but polling an unchanged
    external source must not recreate a file that TTL already removed. Cleanup
    retains only a hidden sidecar containing the last successful content MD5;
    changed source bytes intentionally create a new file incarnation.
    """

    async def inspect(item: tuple[str, str]) -> str | None:
        rel_path, expected_md5 = item
        uri = f"{root_uri.rstrip('/')}/{rel_path}" if rel_path else root_uri.rstrip("/")
        fields = await read_resource_fields(fs, OBJECT_TYPE_RESOURCE_FILE, uri, ctx=ctx)
        if (
            fields is not None
            and hidden_by_ttl(fields.get("expires_at"))
            and fields.get("content_md5") == expected_md5
        ):
            return rel_path
        return None

    matches = await bounded_map(
        file_md5s.items(), inspect, concurrency=max(1, min(concurrency, len(file_md5s) or 1))
    )
    return {path for path in matches if path is not None}


async def write_resource_fields(fs, object_type, uri, fields, *, ctx, lease_ref):
    metadata_uri = ttl_metadata_uri(object_type, uri)
    path = fs._uri_to_path(metadata_uri, ctx=ctx)
    metadata_lease = await fs._async_agfs.pathlock_acquire_exact(path, owner_lease_ref=lease_ref)
    try:
        await fs.write_file(metadata_uri, json.dumps(fields), ctx=ctx, lease_ref=metadata_lease)
    finally:
        await fs._async_agfs.pathlock_release(metadata_lease)


async def update_resource_expiry(
    fs, uri: str, expires_at: str | None = None, *, ctx, ttl_relative: int | None = None
) -> dict:
    from openviking.storage.document_ttl import update_document_expiry

    if ttl_scope_for_uri(uri) != "resources":
        raise InvalidArgumentError("uri must identify a resource")
    return await update_document_expiry(fs, uri, expires_at, ctx=ctx, ttl_relative=ttl_relative)
