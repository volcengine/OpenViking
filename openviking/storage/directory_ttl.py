# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""One immutable lifetime per event date bucket; sessions own their renewal."""

import hashlib
import json
from contextlib import asynccontextmanager
from functools import wraps

from openviking.config.ttl import resolve_ttl_config
from openviking.core.ttl import (
    OBJECT_TYPE_EVENT,
    OBJECT_TYPE_SESSION,
    freeze_ttl_fields,
    hidden_by_ttl,
    ttl_metadata_uri,
    ttl_object_for_uri,
)
from openviking.server.error_mapping import is_storage_not_found
from openviking.storage.abstract_overview import is_abstract_overview_uri
from openviking.storage.internal_names import is_storage_internal_name, is_ttl_metadata_name
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
    pending = fields.pop("_ttl_pending", None)
    if pending:
        try:
            raw = fs._handle_agfs_read(
                await fs._async_agfs.read(fs._uri_to_path(pending["uri"], ctx=ctx))
            )
        except Exception as exc:
            if not is_storage_not_found(exc):
                raise
        else:
            if hashlib.sha256(raw).hexdigest() == pending["sha256"]:
                return pending["fields"]
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
async def content_update(fs, uri, content, *, ctx, lease_ref=None):
    """Initialize a new bucket only after its first successful content write.

    A durable intent lets readers and cleanup recover the write if metadata
    finalization is interrupted. Failed writes retain the old deadline.
    """
    target = ttl_object_for_uri(uri)
    if target is None:
        yield lease_ref
        return
    kind, root = target
    if uri.rstrip("/") in {ttl_metadata_uri(kind, root), root + "/.ttl.json"}:
        yield lease_ref
        return
    content_file = is_ttl_content(uri)
    # Serialize the shared metadata, while an exact body lock fences directory
    # cleanup. This also composes with callers already holding a file lease:
    # upgrading two sibling file locks to tree locks would deadlock.
    lease = await fs._async_agfs.pathlock_acquire_batch(
        [
            {"path": fs._uri_to_path(uri, ctx=ctx), "kind": "exact"},
            {"path": fs._uri_to_path(ttl_metadata_uri(kind, root), ctx=ctx), "kind": "exact"},
        ],
        owner_lease_ref=lease_ref,
        timeout_secs=30.0,
    )
    try:
        previous = await read_directory_fields(fs, root, ctx=ctx)
        if not previous:
            record = await fs.ttl_registry.get(ctx.account_id, root)
            if record:
                previous = {"expires_at": record.expires_at}
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
            yield lease
            return
        if not content_file:
            # Derived summaries never initialize a new event bucket. Checking
            # under the metadata lock also prevents a late summary resurrecting it.
            if not previous:
                if not await _has_content(fs, fs._uri_to_path(root, ctx=ctx)):
                    await fs._remove_empty_lock_directory(fs._uri_to_path(root, ctx=ctx))
                    raise NotFoundError(root, "directory")
            yield lease
            return
        existed = True
        try:
            await fs._async_agfs.stat(fs._uri_to_path(root, ctx=ctx), bypass_cache=True)
        except Exception as exc:
            if not is_storage_not_found(exc):
                raise
            existed = False
        if existed and not previous:
            existed = await _has_content(fs, fs._uri_to_path(root, ctx=ctx))
        # A managed event bucket never renews. An existing unmanaged bucket
        # remains unmanaged even when its root policy has since changed.
        if previous.get("expires_at") or existed:
            yield lease
            return
        desired = {
            **previous,
            **(freeze_ttl_fields(root, config=await resolve_ttl_config(fs, ctx.account_id)) or {}),
        }
        if not desired.get("expires_at"):
            yield lease
            return
        try:
            old = fs._handle_agfs_read(await fs._async_agfs.read(fs._uri_to_path(uri, ctx=ctx)))
        except Exception as exc:
            if not is_storage_not_found(exc):
                raise
            old = None
        raw = content.encode("utf-8") if isinstance(content, str) else content
        if old == raw:
            yield lease
            return
        journal = {
            **(previous or desired),
            "_ttl_pending": {
                "uri": uri,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "fields": desired,
            },
        }
        await write_directory_fields(fs, root, journal, ctx=ctx, lease_ref=lease)
        try:
            yield lease
        except BaseException:
            await write_directory_fields(fs, root, previous, ctx=ctx, lease_ref=lease)
            raise
        await write_directory_fields(fs, root, desired, ctx=ctx, lease_ref=lease)
    finally:
        await fs._async_agfs.pathlock_release(lease)


def directory_content_write(method):
    """Share the lifecycle boundary across text and binary file writes."""

    @wraps(method)
    async def wrapped(self, uri, content, ctx=None, lease_ref=None, auto_pathlock=True):
        from openviking.storage.acl import AclAction

        await self._ensure_access(uri, ctx, action=AclAction.WRITE)
        async with content_update(
            self, uri, content, ctx=self._ctx_or_default(ctx), lease_ref=lease_ref
        ) as lease:
            return await method(
                self, uri, content, ctx=ctx, lease_ref=lease, auto_pathlock=auto_pathlock
            )

    return wrapped
