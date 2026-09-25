# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Explicit retention edits for live event and resource files."""

from datetime import datetime, timezone
from uuid import uuid4

from openviking.core.ttl import (
    OBJECT_TYPE_EVENT,
    OBJECT_TYPE_RESOURCE_FILE,
    TTL_FIELD_NAMES,
    compute_expires_at,
    hidden_by_ttl,
    ttl_object_for_uri,
    ttl_scope_for_uri,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.acl import AclAction
from openviking.storage.internal_names import (
    WEBDAV_RESERVED_FILENAMES,
    is_storage_internal_name,
    is_ttl_metadata_name,
)
from openviking.storage.resource_ttl import read_resource_fields, write_resource_fields
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime
from openviking_cli.exceptions import ConflictError, InvalidArgumentError
from openviking_cli.utils.config.ttl_config import DocumentTTL


async def _document_target(fs, uri, *, ctx):
    name = uri.rsplit("/", 1)[-1]
    if (
        name in WEBDAV_RESERVED_FILENAMES
        or is_storage_internal_name(name)
        or is_ttl_metadata_name(name)
    ):
        raise InvalidArgumentError("TTL can only be set on event or resource content files")
    stat = await fs.stat(uri, ctx=ctx)
    if ttl_scope_for_uri(uri) == "resources":
        if stat.get("isDir"):
            raise InvalidArgumentError(
                "resource directories define defaults via resources/config; "
                "resources/ttl requires a file"
            )
        fields = await read_resource_fields(fs, OBJECT_TYPE_RESOURCE_FILE, uri, ctx=ctx)
        return OBJECT_TYPE_RESOURCE_FILE, fields or {}, stat
    if ttl_object_for_uri(uri, is_dir=bool(stat.get("isDir"))) == (OBJECT_TYPE_EVENT, uri):
        memory = MemoryFileUtils.read(await fs.read_file(uri, ctx=ctx), uri=uri)
        return (
            OBJECT_TYPE_EVENT,
            {key: value for key, value in memory.extra_fields.items() if key in TTL_FIELD_NAMES},
            stat,
        )
    raise InvalidArgumentError("uri must identify an event file or resource document")


def _public_fields(uri, fields):
    # Watch fingerprints and other lifecycle bookkeeping are private.
    return {"uri": uri, **{key: value for key, value in fields.items() if key in TTL_FIELD_NAMES}}


async def get_document_ttl(fs, uri: str, *, ctx) -> dict:
    """Read the exact file's retention snapshot once."""
    _, fields, _ = await _document_target(fs, uri, ctx=ctx)
    return _public_fields(uri, fields)


async def update_document_expiry(
    fs, uri: str, expires_at: str | None = None, *, ctx, ttl_relative: int | None = None
) -> dict:
    """Set file retention under its source lock, even when global TTL is off."""
    try:
        policy = DocumentTTL(expires_at=expires_at, ttl_relative=ttl_relative)
        expiry = (
            format_iso8601(parse_iso_datetime(policy.expires_at))
            if policy.expires_at is not None
            else None
        )
        if expiry is not None and hidden_by_ttl(expiry):
            raise ValueError("expires_at must be in the future")
    except (ValueError, TypeError) as exc:
        raise InvalidArgumentError(str(exc)) from exc
    kind, original, _ = await _document_target(fs, uri, ctx=ctx)
    await fs._ensure_access(uri, ctx, action=AclAction.WRITE)
    lease = await fs._async_agfs.pathlock_acquire_exact(fs._uri_to_path(uri, ctx=ctx))
    try:
        live_kind, fields, stat = await _document_target(fs, uri, ctx=ctx)
        if (live_kind, fields.get("ttl_generation")) != (kind, original.get("ttl_generation")):
            raise ConflictError("document changed while updating its expiry; reload and retry")
        # Retention edits do not count as content updates. Preserve the saved
        # content timestamp; unmanaged files start from the storage modification time.
        if fields.get("received_at"):
            updated = parse_iso_datetime(fields["received_at"])
        else:
            mtime = fs._ls_entry_mtime(stat)
            if mtime is None:
                raise InvalidArgumentError("document content update time is unavailable")
            updated = datetime.fromtimestamp(mtime, timezone.utc)
        if policy.ttl_relative is not None:
            expiry = format_iso8601(compute_expires_at(updated, policy.ttl_relative))
        fields.update(
            ttl_days=policy.ttl_relative,
            received_at=format_iso8601(updated),
            expires_at=expiry,
            ttl_generation=fields.get("ttl_generation") or str(uuid4()),
        )
        if kind == OBJECT_TYPE_EVENT:
            memory = MemoryFileUtils.read(await fs.read_file(uri, ctx=ctx), uri=uri)
            memory.extra_fields.update(fields)
            await fs.write_file(uri, MemoryFileUtils.write(memory), ctx=ctx, lease_ref=lease)
        else:
            await write_resource_fields(fs, kind, uri, fields, ctx=ctx, lease_ref=lease)
    finally:
        await fs._async_agfs.pathlock_release(lease)
    # A shorter relative duration may make the file due immediately.
    return _public_fields(uri, fields)
