# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exclusive radar directories, shared by admission and background execution."""

import hashlib
import json
from contextlib import AsyncExitStack, asynccontextmanager

from openviking.resource.watch_types import WatchAuthority
from openviking.server.error_mapping import is_not_found_error
from openviking.storage.errors import LockAcquisitionError
from openviking_cli.exceptions import ConflictError

OWNER_FILENAME = ".radar-owner.json"


@asynccontextmanager
async def radar_admission_guard(locks, account_id):
    """Serialize creation and target projection changes within one account."""
    try:
        async with locks.tree(
            account_id, "viking://temp/.pr-radar-admission", timeout=30.0,
        ):
            yield
    except LockAcquisitionError as exc:
        raise ConflictError("pr_radar admission is busy; retry the operation.") from exc


def radar_identity(task, ctx):
    # Lazy import: the runner also uses this guard.
    from openviking.resource.pr_radar_runner import PrRadarRunner, radar_config_or_none

    config = radar_config_or_none(task.processor_kwargs)
    if config is None or not config.enabled:
        return None
    repo = PrRadarRunner._resolve_repo(task.path)
    if repo is None:
        return None
    target = PrRadarRunner._target_uri(
        config, repo, str(getattr(task, "user_id", None) or PrRadarRunner._owner_user_id(task, ctx)),
        task.to_uri,
    )
    return target, {"watch_id": task.task_id, "repo": repo.casefold()}


@asynccontextmanager
async def radar_directory_guard(
    fs, locks, store, task, ctx, *, claim=False, watches=None, lease_ref=None,
):
    """Serialize check + Watch insertion / ownership publication across workers.

    The Watch row reserves a fresh directory at admission; no files are changed
    until execution or target transfer. The immutable owner file keeps history bound to
    its Watch even after deletion. The lock lives outside the report tree so
    ingestion's existing Tree lease cannot conflict with this lock.
    """
    identity = radar_identity(task, ctx)
    if identity is None:
        yield
        return
    target, expected = identity
    target_path = fs._uri_to_path(target, ctx=ctx)
    lock_key = hashlib.sha256(target_path.encode()).hexdigest()
    lock_uri = f"viking://temp/.pr-radar-ownership/{lock_key}"

    def conflict():
        return ConflictError(
            "pr_radar directory is already owned by another Watch or has "
            "unverified history; choose a fresh radar target directory.",
            resource=target,
        )

    try:
        async with locks.tree(ctx.account_id, lock_uri, timeout=30.0):
            if store is not None:
                if watches is None:
                    watches = await store.list_tasks(
                        WatchAuthority(ctx.account_id, ctx.user.user_id, "root"),
                        scope_account_id=ctx.account_id,
                    )
                found = False
                for other in watches:
                    other_identity = radar_identity(other, ctx)
                    if other_identity is None:
                        continue
                    other_target, _ = other_identity
                    if fs._uri_to_path(other_target, ctx=ctx) != target_path:
                        continue
                    if other.task_id != task.task_id:
                        raise conflict()
                    found = True
                # A deleted / withdrawn Watch must not create an owner file.
                if claim and not found:
                    raise conflict()
            owner_uri = f"{target}/{OWNER_FILENAME}"
            try:
                raw = await fs.read_file(owner_uri, ctx=ctx)
            except Exception as exc:
                if not is_not_found_error(exc):
                    raise
                raw = None
            if raw is not None:
                try:
                    owner = json.loads(raw)
                except (ValueError, TypeError):
                    raise conflict() from None
                if owner != expected:
                    raise conflict()
            else:
                # Legacy files cannot establish which Watch produced a cursor
                # or queue. Fail closed instead of adopting contaminated state.
                if store is not None:
                    from openviking.resource.github_radar.models import (
                        LATEST_REPORT_NAME,
                        REPORTS_DIRNAME,
                        SIGNALS_FILENAME,
                        STATE_FILENAME,
                    )

                    for filename in (STATE_FILENAME, SIGNALS_FILENAME, LATEST_REPORT_NAME):
                        try:
                            await fs.read_file(f"{target}/{filename}", ctx=ctx)
                        except Exception as exc:
                            if not is_not_found_error(exc):
                                raise
                        else:
                            raise conflict()
                    try:
                        # Directory existence is enough: listing can hide
                        # archives behind child ACLs or listing limits.
                        await fs.stat(f"{target}/{REPORTS_DIRNAME}", ctx=ctx, skip_count=True)
                    except Exception as exc:
                        if not is_not_found_error(exc):
                            raise
                    else:
                        raise conflict()
                if claim:
                    async with AsyncExitStack() as stack:
                        options = {}
                        if lease_ref is not None:
                            # The derived radar directory may lie inside or
                            # outside the transfer. Borrow the same owner and
                            # obtain a lease covering this write in either case.
                            owner_lease = await stack.enter_async_context(locks.tree(
                                ctx.account_id, target, timeout=30.0,
                                owner_lease_ref=lease_ref,
                            ))
                            options["lease_ref"] = owner_lease
                        await fs.write_file(
                            owner_uri, json.dumps(expected, sort_keys=True), ctx=ctx,
                            **options,
                        )
            yield
    except LockAcquisitionError as exc:
        raise ConflictError(
            "pr_radar directory ownership is being checked; retry creation or execution.",
            resource=target,
        ) from exc
