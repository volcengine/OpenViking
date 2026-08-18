# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tree sync (diff + mv/rm merge) primitives for VikingFS."""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from openviking.storage.viking_fs._base import LS_ALL_NODES
from openviking_cli.utils.uri import VikingURI

if TYPE_CHECKING:
    from openviking.server.identity import RequestContext

logger = logging.getLogger(__name__)


@dataclass
class SyncDiff:
    """Directory diff result for sync operations."""

    added_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    updated_files: List[str] = field(default_factory=list)
    added_dirs: List[str] = field(default_factory=list)
    deleted_dirs: List[str] = field(default_factory=list)

    def to_changes(self) -> Dict[str, List[str]]:
        return {
            "added": self.added_files + self.added_dirs,
            "modified": self.updated_files,
            "deleted": self.deleted_files + self.deleted_dirs,
        }


class _SyncMixin:
    """Top-down recursive tree diff-and-merge primitive.

    Moves (not copies) visible children of ``root_uri`` into ``target_uri``
    so the target ends up mirroring the source, returning a :class:`SyncDiff`
    describing what changed.  Hidden files (``.``-prefixed) are skipped —
    callers that need to carry over parser sidecars (e.g.
    ``.image_mappings.json``) do so explicitly after the sync.
    """

    async def sync_tree(
        self,
        root_uri: str,
        target_uri: str,
        *,
        ctx: Optional["RequestContext"] = None,
        file_change_status: Optional[Dict[str, bool]] = None,
        lease_ref: Optional[Dict[str, Any]] = None,
        delete_temp_after: bool = False,
        is_changed: Optional[Callable[[str, str, "RequestContext"], "bool"]] = None,
    ) -> SyncDiff:
        """Recursively merge ``root_uri`` into ``target_uri``.

        Hidden entries (names starting with ``.``) are skipped on both sides.
        Name conflicts (file vs. directory at the same name) are resolved by
        deleting the target-side entry before moving the source in place.

        Args:
            root_uri: Source (typically a temp/staging tree). Must exist.
            target_uri: Destination. Created (incl. parents) if missing.
            ctx: Request context for ACL / tenancy checks.
            file_change_status: Optional pre-computed map of source-URI →
                ``True`` (changed) / ``False`` (unchanged). Used as a
                performance hint to skip content comparison for files the
                caller already knows about.
            lease_ref: Optional pathlock lease passed through to every
                ``mv``/``rm``/``mkdir`` so the whole sync is covered by one
                lock.
            delete_temp_after: If ``True``, call ``delete_temp(root_uri)``
                after a successful sync into an existing target (the
                whole-tree mv path already consumes the source).
            is_changed: Optional async predicate ``(root_file, target_file,
                ctx) -> bool`` used to decide if an existing file needs to
                be replaced.  Defaults to a stat-size shortcut followed by
                byte-for-byte content comparison.

        Returns:
            A :class:`SyncDiff` describing the applied changes (URIs are
            the *target* paths, suitable for feeding into incremental
            reindex pipelines).
        """
        if not await self.exists(root_uri, ctx=ctx):
            raise FileNotFoundError(
                f"Sync source no longer exists; refusing to sync into {target_uri}: {root_uri}"
            )

        diff = SyncDiff()

        async def list_children(dir_uri: str) -> Tuple[Dict[str, str], Dict[str, str]]:
            files: Dict[str, str] = {}
            dirs: Dict[str, str] = {}
            entries = await self.ls(
                dir_uri, show_all_hidden=True, node_limit=LS_ALL_NODES, ctx=ctx
            )
            for entry in entries:
                name = entry.get("name", "")
                if not name or name in [".", ".."]:
                    continue
                if name.startswith("."):
                    continue
                item_uri = VikingURI(dir_uri).join(name).uri
                if entry.get("isDir", False):
                    dirs[name] = item_uri
                else:
                    files[name] = item_uri
            return files, dirs

        async def default_is_changed(root_file: str, target_file: str) -> bool:
            try:
                current_stat = await self.stat(root_file, ctx=ctx)
                target_stat = await self.stat(target_file, ctx=ctx)
                current_size = current_stat.get("size") if isinstance(current_stat, dict) else None
                target_size = target_stat.get("size") if isinstance(target_stat, dict) else None
                if current_size is not None and target_size is not None and current_size != target_size:
                    return True
                current_content = await self.read_file(root_file, ctx=ctx)
                target_content = await self.read_file(target_file, ctx=ctx)
                return current_content != target_content
            except Exception:
                return True

        check_changed = is_changed if is_changed is not None else default_is_changed

        async def sync_dir(root_dir: str, target_dir: str) -> None:
            root_files, root_dirs = await list_children(root_dir)
            target_files, target_dirs = await list_children(target_dir)

            file_names = set(root_files.keys()) | set(target_files.keys())
            for name in sorted(file_names):
                root_file = root_files.get(name)
                target_file = target_files.get(name)

                if root_file and name in target_dirs:
                    target_conflict_dir = target_dirs[name]
                    try:
                        await self.rm(
                            target_conflict_dir,
                            recursive=True,
                            ctx=ctx,
                            lease_ref=lease_ref,
                        )
                        diff.deleted_dirs.append(target_conflict_dir)
                        target_dirs.pop(name, None)
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to delete directory for file conflict: {target_conflict_dir}, error={e}"
                        )
                    target_file = None

                if target_file and name in root_dirs and not root_file:
                    try:
                        await self.rm(target_file, ctx=ctx, lease_ref=lease_ref)
                        diff.deleted_files.append(target_file)
                        target_files.pop(name, None)
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to delete file for dir conflict: {target_file}, error={e}"
                        )
                    continue

                if target_file and not root_file:
                    try:
                        await self.rm(target_file, ctx=ctx, lease_ref=lease_ref)
                        diff.deleted_files.append(target_file)
                    except Exception as e:
                        logger.error(f"[SyncDiff] Failed to delete file: {target_file}, error={e}")
                    continue

                if root_file and target_file:
                    changed = False
                    if file_change_status and root_file in file_change_status:
                        changed = file_change_status[root_file]
                    else:
                        try:
                            changed = await check_changed(root_file, target_file)
                        except Exception as e:
                            logger.error(
                                f"[SyncDiff] Failed to compare file content for {root_file}: {e}, treating as unchanged"
                            )
                            changed = False
                    if changed:
                        diff.updated_files.append(target_file)
                        try:
                            await self.rm(target_file, ctx=ctx, lease_ref=lease_ref)
                        except Exception as e:
                            logger.error(
                                f"[SyncDiff] Failed to remove old file before update: {target_file}, error={e}"
                            )
                        try:
                            await self.mv(
                                root_file,
                                target_file,
                                ctx=ctx,
                                lease_ref=lease_ref,
                            )
                        except Exception as e:
                            logger.error(
                                f"[SyncDiff] Failed to move updated file: {root_file} -> {target_file}, error={e}"
                            )
                    continue

                if root_file and not target_file:
                    target_file_uri = VikingURI(target_dir).join(name).uri
                    diff.added_files.append(target_file_uri)
                    try:
                        await self.mv(
                            root_file,
                            target_file_uri,
                            ctx=ctx,
                            lease_ref=lease_ref,
                        )
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to move added file: {root_file} -> {target_file_uri}, error={e}"
                        )

            dir_names = set(root_dirs.keys()) | set(target_dirs.keys())
            for name in sorted(dir_names):
                root_subdir = root_dirs.get(name)
                target_subdir = target_dirs.get(name)

                if root_subdir and name in target_files:
                    target_conflict_file = target_files[name]
                    try:
                        await self.rm(
                            target_conflict_file,
                            ctx=ctx,
                            lease_ref=lease_ref,
                        )
                        diff.deleted_files.append(target_conflict_file)
                        target_files.pop(name, None)
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to delete file for dir conflict: {target_conflict_file}, error={e}"
                        )
                    target_subdir = None

                if target_subdir and not root_subdir:
                    try:
                        await self.rm(
                            target_subdir,
                            recursive=True,
                            ctx=ctx,
                            lease_ref=lease_ref,
                        )
                        diff.deleted_dirs.append(target_subdir)
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to delete directory: {target_subdir}, error={e}"
                        )
                    continue

                if root_subdir and not target_subdir:
                    target_subdir_uri = VikingURI(target_dir).join(name).uri
                    diff.added_dirs.append(target_subdir_uri)
                    try:
                        await self.mv(
                            root_subdir,
                            target_subdir_uri,
                            ctx=ctx,
                            lease_ref=lease_ref,
                        )
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to move added directory: {root_subdir} -> {target_subdir_uri}, error={e}"
                        )
                    continue

                if root_subdir and target_subdir:
                    await sync_dir(root_subdir, target_subdir)

        target_exists = await self.exists(target_uri, ctx=ctx)
        if not target_exists:
            parent_uri = VikingURI(target_uri).parent
            if parent_uri:
                await self.mkdir(
                    parent_uri.uri,
                    exist_ok=True,
                    ctx=ctx,
                    lease_ref=lease_ref,
                )
            diff.added_dirs.append(target_uri)
            await self.mv(root_uri, target_uri, ctx=ctx, lease_ref=lease_ref)
            return diff

        await sync_dir(root_uri, target_uri)
        if delete_temp_after:
            try:
                await self.delete_temp(root_uri, ctx=ctx)
            except Exception as e:
                logger.error(f"[SyncDiff] Failed to delete temp root {root_uri}: {e}")
        return diff
