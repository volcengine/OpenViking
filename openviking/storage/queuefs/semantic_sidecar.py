# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared writeback for semantic sidecar files."""

from typing import Any, Callable, Optional

from openviking.server.identity import RequestContext
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


async def write_semantic_sidecars(
    *,
    viking_fs: Any,
    dir_uri: str,
    overview: str,
    abstract: str,
    ctx: Optional[RequestContext],
    is_stale: Callable[[], bool],
    lifecycle_lock_handle_id: str = "",
    log_prefix: str = "[Semantic]",
) -> bool:
    if is_stale():
        logger.info("%s Skipping stale semantic write for %s", log_prefix, dir_uri)
        return False

    try:
        from openviking.storage.transaction import LockContext, get_lock_manager

        lock_manager = get_lock_manager()
    except Exception:
        await _write_sidecars(viking_fs, dir_uri, overview, abstract, ctx)
        return True

    handle = None
    owns_handle = False
    if lifecycle_lock_handle_id:
        handle = lock_manager.get_handle(lifecycle_lock_handle_id)
    if handle is None:
        handle = lock_manager.create_handle()
        owns_handle = True

    lock_paths = [
        viking_fs._uri_to_path(f"{dir_uri}/.overview.md", ctx=ctx),
        viking_fs._uri_to_path(f"{dir_uri}/.abstract.md", ctx=ctx),
    ]
    try:
        async with LockContext(lock_manager, lock_paths, lock_mode="exact", handle=handle):
            if is_stale():
                logger.info("%s Skipping stale semantic write for %s", log_prefix, dir_uri)
                return False
            await _write_sidecars(viking_fs, dir_uri, overview, abstract, ctx)
            return True
    finally:
        if owns_handle:
            await lock_manager.release(handle)


async def _write_sidecars(
    viking_fs: Any,
    dir_uri: str,
    overview: str,
    abstract: str,
    ctx: Optional[RequestContext],
) -> None:
    await viking_fs.write_file(f"{dir_uri}/.overview.md", overview, ctx=ctx)
    await viking_fs.write_file(f"{dir_uri}/.abstract.md", abstract, ctx=ctx)
