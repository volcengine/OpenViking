# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared writeback for semantic sidecar files."""

from typing import Any, Callable, Optional

from openviking.core.namespace import uri_parts
from openviking.server.identity import RequestContext
from openviking.storage.transaction import NO_LOCK, LockLease
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def structural_directory_semantics(dir_uri: str) -> Optional[tuple[str, str]]:
    """Return stable L1/L0 semantics for structural ownership directories."""
    parts = uri_parts(dir_uri)
    peer_id = None
    if len(parts) == 4 and parts[0] == "user" and parts[2] == "peers":
        peer_id = parts[3]
    elif len(parts) == 3 and parts[:2] == ["user", "peers"]:
        peer_id = parts[2]

    if peer_id is None:
        return None

    abstract = (
        f"Private knowledge and memory scoped to interaction peer {peer_id}; "
        "this directory represents the peer, not any single child item."
    )
    overview = (
        f"# Peer {peer_id}\n\n"
        f"This directory contains private long-term context associated with interaction "
        f"peer `{peer_id}`, including peer-scoped memories and resources. Its meaning is "
        "defined by peer ownership rather than by the first uploaded or extracted item."
    )
    return overview, abstract


async def write_semantic_sidecars(
    *,
    viking_fs: Any,
    dir_uri: str,
    overview: str,
    abstract: str,
    ctx: Optional[RequestContext],
    is_stale: Callable[[], bool],
    lock: LockLease = NO_LOCK,
    log_prefix: str = "[Semantic]",
) -> bool:
    stable_semantics = structural_directory_semantics(dir_uri)
    if stable_semantics is not None:
        overview, abstract = stable_semantics

    if is_stale():
        logger.info("%s Skipping stale semantic write for %s", log_prefix, dir_uri)
        return False

    try:
        from openviking.storage.transaction import (
            LockContext,
            get_lock_manager,
        )

        lock_manager = get_lock_manager()
    except Exception:
        await _write_sidecars(viking_fs, dir_uri, overview, abstract, ctx, lock.handle)
        return True

    lock_paths = [
        viking_fs._uri_to_path(f"{dir_uri}/.overview.md", ctx=ctx),
        viking_fs._uri_to_path(f"{dir_uri}/.abstract.md", ctx=ctx),
    ]
    async with LockContext(lock_manager, lock_paths, lock_mode="exact", handle=lock.handle):
        if is_stale():
            logger.info("%s Skipping stale semantic write for %s", log_prefix, dir_uri)
            return False
        await _write_sidecars(viking_fs, dir_uri, overview, abstract, ctx, lock.handle)
        return True


async def _write_sidecars(
    viking_fs: Any,
    dir_uri: str,
    overview: str,
    abstract: str,
    ctx: Optional[RequestContext],
    lock_handle: Any = None,
) -> None:
    # TODO: This must be optimized once pathlock is pushed down into ragfs.
    await viking_fs.write_file(
        f"{dir_uri}/.overview.md",
        overview,
        ctx=ctx,
        lock_handle=lock_handle,
    )
    await viking_fs.write_file(
        f"{dir_uri}/.abstract.md",
        abstract,
        ctx=ctx,
        lock_handle=lock_handle,
    )
