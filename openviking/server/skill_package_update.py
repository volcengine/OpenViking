# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Remove obsolete Skill assets while retaining ACL-bearing records for replacements."""

from pathlib import PurePosixPath
from typing import Any, Dict

from openviking.server.identity import RequestContext
from openviking.server.skill_source_metadata import SOURCE_METADATA_FILENAME
from openviking.storage.acl import AclAction
from openviking.storage.internal_names import is_storage_internal_name
from openviking.storage.viking_fs import VikingFS
from openviking.utils.skill_processor import SkillProcessingPreparation


async def remove_obsolete_skill_files(
    viking_fs: VikingFS,
    root_uri: str,
    preparation: SkillProcessingPreparation,
    *,
    ctx: RequestContext,
    lease_ref: Dict[str, Any],
) -> None:
    """Overwrite retained paths in place so indexing preserves their direct ACLs."""
    retained_files = {"SKILL.md", SOURCE_METADATA_FILENAME}
    retained_files.update(
        path.relative_to(preparation.base_path).as_posix() if preparation.base_path else path.name
        for path in preparation.auxiliary_files
    )
    retained_dirs = {
        parent.as_posix() for path in retained_files for parent in PurePosixPath(path).parents
    }
    # Storage listing includes user-hidden files that the public Skill listing
    # omits. Runtime control entries must remain owned by the storage layer.
    pending = [""]
    while pending:
        relative_dir = pending.pop()
        directory_uri = f"{root_uri}/{relative_dir}".rstrip("/")
        entries = await viking_fs._async_agfs.ls(
            viking_fs._uri_to_path(directory_uri, ctx=ctx),
            fs_ctx=viking_fs._pathlock_fs_ctx(ctx, lease_ref),
        )
        for entry in entries:
            name = entry.get("name", "")
            if (
                not name
                or name in {".", "..", ".abstract.md", ".overview.md"}
                or is_storage_internal_name(name)
            ):
                continue
            relative_path = f"{relative_dir}/{name}".lstrip("/")
            is_dir = bool(entry.get("isDir", False))
            if is_dir and relative_path in retained_dirs:
                pending.append(relative_path)
            elif not is_dir and relative_path in retained_files:
                continue
            else:
                obsolete_uri = f"{root_uri}/{relative_path}"
                await viking_fs._ensure_access(obsolete_uri, ctx, action=AclAction.MANAGE)
                await viking_fs.rm(
                    obsolete_uri,
                    recursive=is_dir,
                    ctx=ctx,
                    lease_ref=lease_ref,
                )
