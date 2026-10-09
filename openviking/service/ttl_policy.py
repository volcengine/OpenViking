# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Apply root policies through the configuration request, including retries."""

from __future__ import annotations

import asyncio
from typing import Any

from openviking.config.ttl import resolve_ttl_config
from openviking.core.ttl import (
    OBJECT_TYPE_SESSION,
    hidden_by_ttl,
    policy_ttl_fields,
    ttl_metadata_uri,
    ttl_object_for_uri,
    ttl_scope_for_uri,
)
from openviking.server.error_mapping import is_storage_not_found
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.directory_ttl import (
    _has_content,
    read_directory_fields,
    write_directory_fields,
)
from openviking.storage.internal_names import is_storage_internal_name
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import FailedPreconditionError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

_CONCURRENCY = 8
# Serializes configuration application, not ordinary content writes. A storage
# lease also orders patches handled by different server processes.
_CONFIG_LOCK = "/local/__system__/ttl/policy.lock"


async def _directories(fs, path):
    # Offset pages can skip live siblings when cleanup removes earlier entries.
    # Keep this level's names while applying changes, not full entry metadata.
    try:
        entries = await fs._async_agfs.ls(path, sort_by="name")
    except Exception as exc:
        if is_storage_not_found(exc):
            return
        raise
    names = []
    for entry in entries:
        name = str(entry.get("name") or "")
        if path.rstrip("/") == "/local" and name == "_system":
            continue
        if entry.get("isDir") and name not in {"", ".", "..", "__system__"}:
            if not is_storage_internal_name(name):
                names.append(name)
    del entries
    for name in names:
        yield name


async def _config(fs, account_id):
    return await resolve_ttl_config(fs, account_id, fresh=True) or get_openviking_config().ttl


def _affected(root, scope, config, previous, patch):
    """Keep explicit sibling overrides out of a global/type-only change."""
    if patch is None:
        return True
    if previous is not None and previous.resolve_uri_policy(
        root, scope
    ) != config.resolve_uri_policy(root, scope):
        return True
    directories = patch.get("directories", {})
    if directories is None or root in {key.rstrip("/") for key in directories}:
        return True
    root_policy = config.directories.get(root)
    if root_policy is not None and root_policy.mode != "inherit":
        return False
    if scope in patch:
        return True
    if getattr(config, scope).mode != "inherit":
        return False
    return "global" in patch or "global_default" in patch


async def _roots(fs, ctx):
    async for user in _directories(fs, fs._uri_to_path("viking://user", ctx=ctx)):
        prefix = f"viking://user/{user}"
        yield prefix + "/sessions"
        yield prefix + "/memories/events"
        async for peer in _directories(fs, fs._uri_to_path(prefix + "/peers", ctx=ctx)):
            yield prefix + f"/peers/{peer}/memories/events"


async def _owners(fs, root, ctx):
    if root.endswith("/sessions"):
        async for name in _directories(fs, fs._uri_to_path(root, ctx=ctx)):
            uri = root + "/" + name
            if ttl_object_for_uri(uri):
                yield uri
        return
    async for year in _directories(fs, fs._uri_to_path(root, ctx=ctx)):
        year_uri = root + "/" + year
        async for month in _directories(fs, fs._uri_to_path(year_uri, ctx=ctx)):
            month_uri = year_uri + "/" + month
            async for day in _directories(fs, fs._uri_to_path(month_uri, ctx=ctx)):
                uri = month_uri + "/" + day
                if ttl_object_for_uri(uri):
                    yield uri


async def _apply_owner(fs, uri, ctx, config):
    kind, _ = ttl_object_for_uri(uri)
    path = fs._uri_to_path(uri, ctx=ctx)
    # Lock creation can materialize parent directories. Check existence first
    # and recheck metadata inside the lock before changing an existing owner.
    await fs._async_agfs.stat(path, bypass_cache=True)
    before = await read_directory_fields(fs, uri, ctx=ctx)
    requests = [{"path": fs._uri_to_path(ttl_metadata_uri(kind, uri), ctx=ctx), "kind": "exact"}]
    if kind == OBJECT_TYPE_SESSION:
        requests.append({"path": path, "kind": "exact"})
    lease = await fs._async_agfs.pathlock_acquire_batch(requests, timeout_secs=30.0)
    try:
        fields = await read_directory_fields(fs, uri, ctx=ctx)
        if fields.get("expires_at"):
            await fs.ttl_registry.mark_account(ctx.account_id)
        if before and not fields and not await _has_content(fs, path):
            await fs._remove_empty_lock_directory(path)
            return "deleted"
        if hidden_by_ttl(fields.get("expires_at")):
            return "expired"
        policy = config.resolve_uri_policy(uri, ttl_scope_for_uri(uri))
        desired = dict(fields)
        desired.pop("ttl_days", None)
        if kind == OBJECT_TYPE_SESSION:
            desired.pop("received_at", None)
        content_time = None
        if policy.mode in {"disabled", "inherit"}:
            if not fields:
                return "unmanaged"
        elif policy.mode == "absolute":
            if not fields and not await _has_content(fs, path):
                return "empty"
        else:
            content_time = fields.get(
                "created_at" if kind == OBJECT_TYPE_SESSION else "received_at"
            )
            if not content_time:
                if not await _has_content(fs, path):
                    return "empty"
                raise ValueError("directory has no reliable original content timestamp")
            content_time = parse_iso_datetime(content_time)
        desired.update(policy_ttl_fields(policy, content_time))
        if desired != fields:
            await write_directory_fields(fs, uri, desired, ctx=ctx, lease_ref=lease)
        return "updated"
    finally:
        await fs._async_agfs.pathlock_release(lease)


async def apply_account_ttl(fs, account_id, *, previous=None, patch=None, config=None):
    """Apply current effective policies with bounded storage work and errors."""
    ctx = RequestContext(user=UserIdentifier(account_id, "__system__"), role=Role.ROOT)
    result: dict[str, Any] = {"updated": 0, "skipped": 0, "failed": 0, "failures": []}

    def failed(uri, exc):
        result["failed"] += 1
        if len(result["failures"]) < 100:
            result["failures"].append({"account_id": account_id, "uri": uri, "reason": str(exc)})

    async def apply(uri):
        try:
            status = await _apply_owner(fs, uri, ctx, config)
        except Exception as exc:
            if is_storage_not_found(exc):
                result["skipped"] += 1
            else:
                failed(uri, exc)
        else:
            result["updated" if status == "updated" else "skipped"] += 1

    batch = []
    try:
        config = config or await _config(fs, account_id)
        async for root in _roots(fs, ctx):
            scope = ttl_scope_for_uri(root)
            if not _affected(root, scope, config, previous, patch):
                continue
            try:
                async for uri in _owners(fs, root, ctx):
                    batch.append(uri)
                    if len(batch) == _CONCURRENCY:
                        await asyncio.gather(*(apply(uri) for uri in batch))
                        batch.clear()
            except Exception as exc:
                # Discovery cannot count unseen owners; report its root once.
                failed(root, exc)
    except Exception as exc:
        # Includes fresh config reads and account/user/peer discovery failures.
        failed("viking://user", exc)
    if batch:
        await asyncio.gather(*(apply(uri) for uri in batch))
    return result


async def patch_ttl_configuration(fs, manager, patch, *, account_id=None):
    """Save and apply one explicit TTL patch before reporting API success.

    Ordinary config notifications are best effort and omit unchanged values.
    This awaited path deliberately also handles retries of an identical patch.
    """

    async def update():
        if account_id is None:
            return await manager.patch_cluster(patch)
        return await manager.patch_account(account_id, patch)

    if "ttl" not in patch:
        return await update()

    async def apply():
        lease = await fs._async_agfs.pathlock_acquire_exact(_CONFIG_LOCK, timeout_secs=30.0)
        try:
            accounts = (
                [account_id]
                if account_id is not None
                else [name async for name in _directories(fs, "/local")]
            )
            previous = {name: await _config(fs, name) for name in accounts}
            event = await update()
            failures = []
            failed_count = 0
            for name in accounts:
                result = await apply_account_ttl(
                    fs, name, previous=previous[name], patch=patch["ttl"]
                )
                failed_count += result["failed"]
                failures.extend(result["failures"][: max(0, 100 - len(failures))])
            if failed_count:
                raise FailedPreconditionError(
                    "TTL configuration was saved, but some directories could not be updated. Retry the same configuration to complete application.",
                    details={"failed_count": failed_count, "failures": failures},
                )
            return event
        finally:
            await fs._async_agfs.pathlock_release(lease)

    return await run_to_completion(apply)


async def apply_startup_ttl(fs):
    """Apply enabled startup policies and resume any interrupted application."""
    lease = await fs._async_agfs.pathlock_acquire_exact(_CONFIG_LOCK, timeout_secs=30.0)
    try:
        async for account_id in _directories(fs, "/local"):
            config = await _config(fs, account_id)
            if not config.enabled:
                continue
            result = await apply_account_ttl(fs, account_id, config=config)
            if result["failed"]:
                get_logger(__name__).error(
                    "TTL startup application incomplete for %s: %s", account_id, result
                )
    finally:
        await fs._async_agfs.pathlock_release(lease)
