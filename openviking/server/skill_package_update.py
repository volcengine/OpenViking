# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Clear an updated Skill without removing the directory holding its package lock."""

from typing import Any, Dict

from openviking.server.identity import RequestContext
from openviking.server.skill_source_metadata import SOURCE_METADATA_FILENAME
from openviking.storage.internal_names import (
    MULTIWRITE_EXACT_LOCK_FILE_PREFIX,
    MULTIWRITE_INTERNAL_FILE_NAMES,
)
from openviking.storage.viking_fs import VikingFS


async def clear_skill_package_contents(
    viking_fs: VikingFS,
    root_uri: str,
    *,
    ctx: RequestContext,
    lease_ref: Dict[str, Any],
) -> None:
    """Clear old assets and vectors, keeping definition/source files for update retries."""
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
            # Replace these in place so failed writes can be retried without restoring old files.
            or name in {"SKILL.md", SOURCE_METADATA_FILENAME}
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
    await viking_fs._delete_from_vector_store([root_uri, f"{root_uri}/SKILL.md"], ctx=ctx)
