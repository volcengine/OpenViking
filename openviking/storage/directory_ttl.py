# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Directory lifetimes and short write admission locks."""

import json
from contextlib import asynccontextmanager

from openviking.config.ttl import resolve_ttl_config
from openviking.core.ttl import (
    OBJECT_TYPE_EVENT,
    OBJECT_TYPE_SESSION,
    hidden_by_ttl,
    initial_ttl_fields,
    ttl_metadata_uri,
    ttl_object_for_uri,
)
from openviking.server.error_mapping import is_storage_not_found
from openviking.storage.abstract_overview import is_abstract_overview_uri
from openviking.storage.internal_names import is_storage_internal_name, is_ttl_metadata_name
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.exceptions import NotFoundError


async def read_directory_fields(fs, uri, *, ctx):
    target = ttl_object_for_uri(uri)
    if target is None:
        return {}
    kind, root = target
    path = fs._uri_to_path(ttl_metadata_uri(kind, root), ctx=ctx)
    try:
        await fs._async_agfs.stat(path, bypass_cache=True)
        fields = json.loads(fs._handle_agfs_read(await fs._async_agfs.read(path)))
    except Exception as exc:
        if not is_storage_not_found(exc):
            raise
        if kind != OBJECT_TYPE_EVENT:
            return {}
        # Read the previous sidecar during upgrade without changing its deadline.
        try:
            fields = json.loads(
                fs._handle_agfs_read(
                    await fs._async_agfs.read(fs._uri_to_path(root + "/.ttl.json", ctx=ctx))
                )
            )
        except Exception as legacy_error:
            if is_storage_not_found(legacy_error):
                return {}
            raise
    if not isinstance(fields, dict):
        raise ValueError(f"Invalid TTL metadata: {root}")
    return fields


async def write_directory_fields(fs, root, fields, *, ctx, lease_ref):
    kind, owner = ttl_object_for_uri(root)
    await fs.write_file(
        ttl_metadata_uri(kind, owner), json.dumps(fields), ctx=ctx, lease_ref=lease_ref
    )


def is_ttl_content(uri):
    name = uri.rsplit("/", 1)[-1]
    return not (
        is_abstract_overview_uri(uri)
        or is_storage_internal_name(name)
        or is_ttl_metadata_name(name)
        or name in {".meta.json", ".done", ".pending.json", ".failed.json"}
    )


async def _has_content(fs, path):
    for entry in await fs._ls_entries(path):
        name = entry.get("name", "")
        if not name or name in {".", ".."}:
            continue
        child = path.rstrip("/") + "/" + name
        if entry.get("isDir"):
            if await _has_content(fs, child):
                return True
        elif is_ttl_content(child):
            return True
    return False


@asynccontextmanager
async def content_update(fs, uri, content, *, ctx, lease_ref=None, allow_empty_directory=False):
    """Register a new bucket before publishing content; roll back failed writes."""
    target = ttl_object_for_uri(uri)
    if target is None:
        yield lease_ref
        return
    kind, root = target
    if uri.rstrip("/") in {ttl_metadata_uri(kind, root), root + "/.ttl.json"}:
        yield lease_ref
        return
    content_file = is_ttl_content(uri)
    # Every writer takes a body lock before admission. Once admitted, ordinary
    # body I/O runs in parallel; cleanup checks these outstanding file leases
    # while holding the metadata lock, including not-yet-materialized files.
    body_lease = await fs._async_agfs.pathlock_acquire_exact(
        fs._uri_to_path(uri, ctx=ctx), owner_lease_ref=lease_ref, timeout_secs=30.0
    )
    lease = None

    async def release_admission():
        nonlocal lease
        await fs._async_agfs.pathlock_release(lease)
        lease = None

    try:
        lease = await fs._async_agfs.pathlock_acquire_exact(
            fs._uri_to_path(ttl_metadata_uri(kind, root), ctx=ctx),
            owner_lease_ref=body_lease,
            timeout_secs=30.0,
        )
        previous = await read_directory_fields(fs, root, ctx=ctx)
        if hidden_by_ttl(previous.get("expires_at")):
            raise NotFoundError(root, "directory")
        if kind == OBJECT_TYPE_SESSION:
            if not previous and not (uri == root + "/messages.jsonl" and not content):
                # Legacy sessions may have messages without metadata. A deleted
                # session has neither, so delayed archive writes stop here.
                try:
                    await fs._async_agfs.stat(
                        fs._uri_to_path(root + "/messages.jsonl", ctx=ctx), bypass_cache=True
                    )
                except Exception as exc:
                    if is_storage_not_found(exc):
                        await fs._remove_empty_lock_directory(fs._uri_to_path(root, ctx=ctx))
                        raise NotFoundError(root, "session") from exc
                    raise
            await release_admission()
            yield body_lease
            return
        if not content_file:
            # Derived summaries never initialize a new event bucket. Checking
            # under the metadata lock also prevents a late summary resurrecting it.
            if not previous and not allow_empty_directory:
                if not await _has_content(fs, fs._uri_to_path(root, ctx=ctx)):
                    await fs._remove_empty_lock_directory(fs._uri_to_path(root, ctx=ctx))
                    raise NotFoundError(root, "directory")
            await release_admission()
            yield body_lease
            return
        # Content writes do not renew events. Explicit root policy application
        # updates existing lifetimes separately. The file locks above already
        # ensure the parent exists; only a bucket without metadata needs a scan.
        if previous or await _has_content(fs, fs._uri_to_path(root, ctx=ctx)):
            await release_admission()
            yield body_lease
            return
        desired = initial_ttl_fields(
            root, config=await resolve_ttl_config(fs, ctx.account_id, fresh=True)
        ) or {"received_at": get_current_timestamp()}
        await write_directory_fields(fs, root, desired, ctx=ctx, lease_ref=lease)
        try:
            yield body_lease
        except BaseException:
            await write_directory_fields(fs, root, previous, ctx=ctx, lease_ref=lease)
            raise
    finally:
        if lease is not None:
            await fs._async_agfs.pathlock_release(lease)
        await fs._async_agfs.pathlock_release(body_lease)


@asynccontextmanager
async def directory_write(fs, uri, content, *, ctx, lease_ref=None, allow_empty_directory=False):
    """Admit a file write against both its source commit and target directory."""
    from openviking.session.commit_lifetime import commit_write

    async with commit_write(fs, ctx, lease_ref) as source_lease:
        file_lease = None
        try:
            if source_lease is not lease_ref:
                # The source Session lease does not cover the target file.
                file_lease = await fs._async_agfs.pathlock_acquire_exact(
                    fs._uri_to_path(uri, ctx=ctx), owner_lease_ref=source_lease, timeout_secs=30.0
                )
            async with content_update(
                fs,
                uri,
                content,
                ctx=ctx,
                lease_ref=file_lease or source_lease,
                allow_empty_directory=allow_empty_directory,
            ) as lease:
                yield lease
        finally:
            if file_lease is not None:
                await fs._async_agfs.pathlock_release(file_lease)
