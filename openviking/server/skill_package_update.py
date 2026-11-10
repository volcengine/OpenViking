# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Skill update transfers that never remove the directory holding the package lock."""

from typing import Any, Dict

from openviking.server.identity import RequestContext
from openviking.storage.internal_names import (
    MULTIWRITE_EXACT_LOCK_FILE_PREFIX,
    MULTIWRITE_INTERNAL_FILE_NAMES,
)
from openviking.storage.viking_fs import VikingFS


async def transfer_skill_package(
    viking_fs: VikingFS,
    source_uri: str,
    target_uri: str,
    *,
    ctx: RequestContext,
    lease_ref: Dict[str, Any],
) -> None:
    """Copy files and move existing vectors, leaving both locked roots intact.

    The caller owns both tree locks and decides when the source can be cleared.
    Do not use cp/mv here: their failure cleanup can delete the target root,
    which during rollback is the directory holding the live package lock.
    """
    source_uris = await viking_fs._prepare_transfer_entries(
        source_uri, target_uri, is_dir=True, move=True, ctx=ctx
    )
    await viking_fs._copy_directory_under_tree_locks(
        viking_fs._uri_to_path(source_uri, ctx=ctx),
        viking_fs._uri_to_path(target_uri, ctx=ctx),
        old_uri=source_uri,
        new_uri=target_uri,
        ctx=ctx,
        lease_ref=lease_ref,
    )
    # Move preserves the existing vector payloads and compensates on failure.
    # Keep the source files available even if index transfer/restoration fails.
    await viking_fs._update_vector_store_uris(
        source_uri, target_uri, recursive=True, ctx=ctx, source_uris=source_uris
    )


async def clear_skill_package_contents(
    viking_fs: VikingFS,
    root_uri: str,
    *,
    ctx: RequestContext,
    lease_ref: Dict[str, Any],
) -> None:
    """Remove package entries and root vectors, keeping the root and its lock."""
    root_path = viking_fs._uri_to_path(root_uri, ctx=ctx)
    # Storage listing includes user-hidden files that the public Skill listing
    # omits. Runtime control entries must remain owned by the storage layer.
    entries = await viking_fs._async_agfs.ls(
        root_path, fs_ctx=viking_fs._pathlock_fs_ctx(ctx, lease_ref)
    )
    for entry in entries:
        name = entry.get("name", "")
        if (
            not name
            or name in {".", ".."}
            or name in MULTIWRITE_INTERNAL_FILE_NAMES
            or name.startswith(MULTIWRITE_EXACT_LOCK_FILE_PREFIX)
        ):
            continue
        await viking_fs.rm(
            f"{root_uri}/{name}",
            recursive=bool(entry.get("isDir", False)),
            ctx=ctx,
            lease_ref=lease_ref,
        )
    await viking_fs._delete_from_vector_store([root_uri], ctx=ctx)
