# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared destructive boundary for the two TTL experiments."""

import json
from datetime import datetime, timezone

from openviking.config.ttl import resolve_ttl_config
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.server.error_mapping import is_not_found_error
from openviking.storage.ttl import expires_at, scope_and_root, deletion_uri

POLICY_LOCK = "/local/__system__/ttl/policy.lock"


async def delete_expired(fs, uri, ctx):
    return await run_to_completion(lambda: _delete_expired(fs, uri, ctx))


async def _delete_expired(fs, uri, ctx):
    uri = deletion_uri(uri)
    if uri is None:
        return False
    target = scope_and_root(uri)
    session = target[0] == "sessions"
    path = fs._uri_to_path(uri, ctx=ctx)
    policy_lease = await fs._async_agfs.pathlock_acquire_exact(POLICY_LOCK)
    lease = None
    try:
        lease = await (
            fs._async_agfs.pathlock_acquire_tree(path)
            if session
            else fs._async_agfs.pathlock_acquire_exact(path)
        )
        config = await resolve_ttl_config(fs, ctx.account_id, fresh=True)
        if config is None or not config.enabled:
            return False
        if config.resolve_uri_policy(uri, target[0]).mode != "days":
            return False
        try:
            stat = await fs._async_agfs.stat(path)
        except Exception as exc:
            if not is_not_found_error(exc):
                raise
            # Clean orphan vectors under the same URI lease.
            await fs.rm(uri, recursive=session, ctx=ctx, lease_ref=lease)
            return True
        if stat.get("isDir") and not session:
            return False
        stamp = stat.get("modTime")
        if session:
            raw = fs._handle_agfs_read(await fs._async_agfs.read(path + "/.meta.json"))
            stamp = json.loads(raw).get("created_at")
        deadline = expires_at(config, uri, stamp)
        if deadline is None or deadline > datetime.now(timezone.utc):
            return False
        await fs.rm(uri, recursive=session, ctx=ctx, lease_ref=lease)
        return True
    finally:
        if lease is not None:
            await fs._async_agfs.pathlock_release(lease)
        await fs._async_agfs.pathlock_release(policy_lease)


async def patch_configuration(fs, manager, patch, *, account_id=None):
    """Only TTL saves serialize with final deletion checks; never scan content."""

    async def save():
        return (
            await manager.patch_cluster(patch)
            if account_id is None
            else await manager.patch_account(account_id, patch)
        )

    if "ttl" not in patch:
        return await save()

    async def protected_save():
        lease = await fs._async_agfs.pathlock_acquire_exact(POLICY_LOCK)
        try:
            return await save()
        finally:
            await fs._async_agfs.pathlock_release(lease)

    return await run_to_completion(protected_save)
