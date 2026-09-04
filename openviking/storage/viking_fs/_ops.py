# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Core filesystem operations mixin for VikingFS."""

import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from openviking.core.context import ContextLevel
from openviking.core.namespace import (
    is_hidden_by_actor_peer_view,
    may_include_hidden_actor_peers,
    uri_parts,
)
from openviking.pyagfs.exceptions import (
    AGFSClientError,
    AGFSDirectoryNotEmptyError,
    AGFSHTTPError,
)
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.error_mapping import is_not_found_error, map_exception
from openviking.server.identity import RequestContext, Role
from openviking.storage.abstract_overview import (
    ABSTRACT_OVERVIEW_FILENAMES,
    rewrite_abstract_overview_for_transfer,
)
from openviking.storage.acl import AclAction, is_acl_uri
from openviking.storage.expr import PathScope
from openviking.storage.internal_names import STORAGE_INTERNAL_ENTRY_NAMES
from openviking.storage.vector_ids import is_vector_record_id, vector_record_id
from openviking.storage.viking_fs._base import (
    _ABSTRACT_WORKER_COUNT,
    LS_ALL_NODES,
    _ensure_non_empty_search_query,
    _is_directory_not_empty_error,
    logger,
)
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.utils.uri import VikingURI


def _glob_match_uri(entry_uri: str, is_dir: Optional[bool]) -> str:
    """Mark directory matches with a trailing slash.

    `glob` returns a flat list of uri strings, so the trailing slash is the only
    way a caller can tell a directory match from a file match. Matches the
    convention `normalize_dir_uri` and the tree renderer already use.
    """
    if not is_dir or entry_uri.endswith("/"):
        return entry_uri
    return f"{entry_uri}/"


class TransferRollbackError(RuntimeError):
    """A filesystem transfer failed and left a compensation failure to repair."""

    def __init__(self, message: str, *, phase: str, residual_uri: str):
        super().__init__(message)
        self.phase = phase
        self.residual_uri = residual_uri


class _OpsMixin:
    """Core filesystem operations (read/write/mkdir/rm/mv/stat/glob/tree/ls/temp)."""

    # ========== AGFS Basic Commands ==========

    async def read(
        self,
        uri: str,
        offset: int = 0,
        size: int = -1,
        ctx: Optional[RequestContext] = None,
    ) -> bytes:
        """Read file. Accepts a Viking URI or a 32-char hex vector record id."""
        real_ctx = self._ctx_or_default(ctx)
        uri = await self.resolve_uri(uri, real_ctx)
        await self._ensure_access(uri, ctx)
        primary_path = self._uri_to_path(uri, ctx=ctx)

        # Decryption + offset/size slicing now happen inside the ragfs encryption layer
        # (when configured); the plaintext stack reads bytes directly. Either way, pass the
        # offset/size through and let the Rust layer return the requested slice.
        last_not_found: Optional[Exception] = None
        for path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, path, primary_path, real_ctx):
                continue
            try:
                result = await self._async_agfs.read(path, offset, size)
                break
            except Exception as exc:
                if is_not_found_error(exc):
                    last_not_found = exc
                    continue
                raise
        else:
            raise NotFoundError(uri, "file") from last_not_found
        if isinstance(result, bytes):
            raw = result
        elif result is not None and hasattr(result, "content"):
            raw = result.content
        else:
            raw = b""

        return raw

    async def write(
        self,
        uri: str,
        data: Union[bytes, str],
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Write file"""
        await self._ensure_access(uri, ctx, action=AclAction.WRITE)
        path = self._uri_to_path(uri, ctx=ctx)
        if isinstance(data, str):
            data = data.encode("utf-8")

        # Encryption (when configured) happens inside the ragfs layer keyed by account_id.
        return await self._async_agfs.write(path, data)

    async def mkdir(
        self,
        uri: str,
        mode: str = "755",
        exist_ok: bool = False,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Create directory."""
        await self._ensure_access(uri, ctx, action=AclAction.WRITE)
        path = self._uri_to_path(uri, ctx=ctx)
        # Always ensure parent directories exist before creating this directory
        await self._ensure_parent_dirs(path, ctx=ctx, lease_ref=lease_ref)
        try:
            await self._async_agfs.mkdir(path, fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref))
        except Exception as exc:
            message = str(exc).lower()
            already_exists = "exist" in message or "already" in message
            if exist_ok and already_exists:
                return
            raise

    async def rm(
        self,
        uri: str,
        recursive: bool = False,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
        auto_pathlock: bool = True,
    ) -> Dict[str, Any]:
        """Delete file/directory + recursively update vector index.

        This method is idempotent: deleting a non-existent file succeeds
        after cleaning up any orphan index records.

        Acquires a path lock, deletes VectorDB records, then FS files.
        Raises ResourceBusyError when the target is locked by an ongoing
        operation (e.g. semantic processing).

        When ``auto_pathlock`` is False and no outer ``lease_ref`` is supplied,
        the VikingFS-level tree/exact lease is skipped and the underlying AGFS
        delete runs with automatic pathlock disabled. Callers must guarantee the
        target is not concurrently mutated (e.g. best-effort shared upload
        cleanup that only deletes already-expired directories).

        Returns:
            Dict with 'estimated_deleted_count' indicating the estimated number
            of nodes deleted from vector index.
        """
        from openviking.storage.errors import LockAcquisitionError, ResourceBusyError

        guard_ctx = replace(self._ctx_or_default(ctx), bypass_acl=True)
        await self._ensure_access(uri, guard_ctx, action=AclAction.MANAGE)
        await self._ensure_access(uri, ctx, action=AclAction.WRITE)
        path = self._uri_to_path(uri, ctx=ctx)
        target_uri = self._path_to_uri(path, ctx=ctx)

        async def _estimate_deleted_count(target_path: str, real_ctx: RequestContext) -> int:
            """Estimate number of nodes to be deleted using vector index."""
            vector_store = self._get_vector_store()
            if not vector_store:
                return 0
            try:
                target_canonical_uri = self._path_to_uri(target_path, ctx=real_ctx)
                filter_expr = PathScope("uri", target_canonical_uri, depth=-1)
                return await vector_store.count(filter=filter_expr, ctx=real_ctx)
            except Exception as e:
                logger.warning(f"[VikingFS] Failed to count nodes before delete: {e}")
                return 0

        # Check existence and determine lock strategy
        try:
            stat = await self._async_agfs.stat(path)
            is_dir = stat.get("isDir", False) if isinstance(stat, dict) else False
        except Exception as exc:
            if not is_not_found_error(exc):
                mapped = map_exception(exc, resource=uri)
                if mapped is not None:
                    raise mapped from exc
                raise
            if recursive:
                await self._ensure_access(target_uri, ctx, action=AclAction.MANAGE)
            # Path does not exist: clean up any orphan index records and return
            uris_to_delete = await self._collect_uris(path, recursive, ctx=ctx)
            uris_to_delete.append(target_uri)
            real_ctx = self._ctx_or_default(ctx)
            estimated_count = await _estimate_deleted_count(path, real_ctx)
            await self._delete_from_vector_store(uris_to_delete, ctx=ctx)
            logger.info(f"[VikingFS] rm target not found, cleaned orphan index: {uri}")
            return {"estimated_deleted_count": estimated_count}

        if is_dir:
            await self._ensure_access(target_uri, ctx, action=AclAction.MANAGE)
            if not recursive:
                raise FailedPreconditionError(
                    f"Cannot remove directory without --recursive: {uri}",
                    details={"resource": uri, "expected_flag": "recursive"},
                )
            lock_method = self._async_agfs.pathlock_acquire_tree
        else:
            recursive = False
            lock_method = self._async_agfs.pathlock_acquire_exact

        # When an outer lease is supplied we always honor it. Otherwise callers
        # can opt out of the VikingFS-level lease via auto_pathlock=False, in
        # which case the AGFS delete also runs with automatic pathlock disabled.
        skip_lock = lease_ref is None and not auto_pathlock
        lease = lease_ref
        if lease is None and not skip_lock:
            try:
                lease = await lock_method(path)
            except LockAcquisitionError:
                raise ResourceBusyError(f"Resource is being processed: {uri}", uri=uri)

        try:
            uris_to_delete = (
                await self._collect_uris(
                    path,
                    recursive,
                    ctx=ctx,
                    strict=is_dir and self._acl_enabled(ctx),
                )
                if is_dir
                else []
            )
            uris_to_delete.append(target_uri)
            if is_dir:
                await self._ensure_access_many(uris_to_delete, ctx, action=AclAction.MANAGE)
            real_ctx = self._ctx_or_default(ctx)
            estimated_count = await _estimate_deleted_count(path, real_ctx)
            await self._delete_from_vector_store(uris_to_delete, ctx=ctx)
            try:
                result = await self._async_agfs.rm(
                    path,
                    recursive=recursive,
                    fs_ctx=self._pathlock_fs_ctx(ctx, lease),
                    auto_pathlock=auto_pathlock,
                )
            except AGFSDirectoryNotEmptyError:
                raise FailedPreconditionError(
                    f"Directory not empty: {uri}. Use recursive=True to delete non-empty directories."
                )
            except RuntimeError as e:
                # Fallback for older versions without typed exceptions
                if _is_directory_not_empty_error(str(e)):
                    raise FailedPreconditionError(
                        f"Directory not empty: {uri}. Use recursive=True to delete non-empty directories."
                    )
                raise
            # Add estimated_deleted_count to the result
            if isinstance(result, dict):
                result["estimated_deleted_count"] = estimated_count
            else:
                result = {"estimated_deleted_count": estimated_count}
            return result
        finally:
            if lease_ref is None and lease is not None:
                await self._async_agfs.pathlock_release(lease)

    async def remove_files(
        self,
        uri: str,
        recursive: bool = False,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
        auto_pathlock: bool = True,
    ) -> Dict[str, Any]:
        """Delete a file/directory from AGFS storage only, skipping vector-index cleanup.

        Unlike :meth:`rm`, this never touches the vector store. Use it for URIs
        that carry no vector-index records — e.g. raw temporary uploads under
        ``viking://upload`` — so cleanup avoids pointless ``delete_by_filter``
        round-trips. Despite the plural name it deletes a single URI (a file or,
        with ``recursive=True``, a directory subtree). URI safety and account
        isolation are still enforced by ``_uri_to_path``; this method performs no
        ACL checks, so callers must scope the URI themselves.

        ``auto_pathlock`` / ``lease_ref`` are forwarded to AGFS exactly like the
        underlying delete in :meth:`rm`. Callers that pass ``auto_pathlock=False``
        must guarantee the target is not concurrently mutated (best-effort
        cleanup of already-expired, uniquely-named upload directories).
        """
        path = self._uri_to_path(uri, ctx=ctx)
        return await self._async_agfs.rm(
            path,
            recursive=recursive,
            fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref),
            auto_pathlock=auto_pathlock,
        )

    async def cp(
        self,
        old_uri: str,
        new_uri: str,
        recursive: bool = False,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Copy a file or directory together with its vector records."""
        await self._ensure_copy_source_access(old_uri, recursive=recursive, ctx=ctx)
        await self._ensure_access(new_uri, ctx, action=AclAction.WRITE)
        old_scope = old_uri.rstrip("/")
        new_scope = new_uri.rstrip("/")
        if old_scope == new_scope:
            raise InvalidArgumentError("cp source and target must be different")
        if new_scope.startswith(old_scope + "/"):
            raise InvalidArgumentError("cp target cannot be inside the source subtree")

        old_path = self._uri_to_path(old_uri, ctx=ctx)
        new_path = self._uri_to_path(new_uri, ctx=ctx)
        try:
            stat = await self._async_agfs.stat(old_path)
        except Exception as exc:
            if is_not_found_error(exc):
                raise FileNotFoundError(f"cp source not found: {old_uri}") from exc
            mapped = map_exception(exc, resource=old_uri)
            if mapped is not None:
                raise mapped from exc
            raise
        is_dir = stat.get("isDir", False) if isinstance(stat, dict) else False
        if is_dir and not recursive:
            raise FailedPreconditionError(
                f"Cannot copy directory without --recursive: {old_uri}",
                details={"resource": old_uri, "expected_flag": "recursive"},
            )
        if not is_dir and new_uri.rstrip("/") != new_uri:
            raise InvalidArgumentError(
                f"cp destination for a file must include the target file name: {new_uri}"
            )

        await self._ensure_transfer_parent_directory(new_path, new_uri, operation="cp")
        await self._ensure_transfer_target_missing(new_path, new_uri)
        lock_requests = (
            self._directory_transfer_lock_requests(old_path, new_path)
            if is_dir
            else [
                {"path": old_path, "kind": "exact"},
                {"path": new_path, "kind": "exact"},
            ]
        )
        lease = await self._async_agfs.pathlock_acquire_batch(
            lock_requests,
            owner_lease_ref=lease_ref,
        )
        operation_id = uuid.uuid4().hex
        try:
            await self._ensure_transfer_target_missing(new_path, new_uri)
            try:
                files_created = await self._copy_agfs_entry(
                    old_path,
                    new_path,
                    old_uri=old_uri,
                    new_uri=new_uri,
                    is_dir=is_dir,
                    ctx=ctx,
                    lease_ref=lease,
                )
                vector_result = await self._copy_vector_store_uris(
                    old_uri,
                    new_uri,
                    recursive=is_dir,
                    ctx=ctx,
                )
            except Exception as transfer_error:
                try:
                    await self._cleanup_transfer_target(
                        new_path,
                        is_dir=is_dir,
                        ctx=ctx,
                        lease_ref=lease,
                    )
                except Exception as rollback_error:
                    if not is_not_found_error(rollback_error):
                        raise TransferRollbackError(
                            f"cp failed and target cleanup failed for {new_uri}: {rollback_error}",
                            phase="target_cleanup",
                            residual_uri=new_uri,
                        ) from transfer_error
                raise
            result: Dict[str, Any] = {
                "operation_id": operation_id,
                "operation": "copy",
                "from": old_uri,
                "to": new_uri,
                "recursive": is_dir,
                "phase": "completed",
                "files_created": files_created,
            }
            if vector_result is not None:
                result["vectors"] = {
                    "scanned": vector_result.scanned,
                    "written": vector_result.written,
                    "deleted": vector_result.deleted,
                    "restored": vector_result.restored,
                    "batches": vector_result.batches,
                }
            logger.info(
                "Filesystem transfer completed: operation_id=%s operation=copy "
                "object_type=%s recursive=%s result=success",
                operation_id,
                "directory" if is_dir else "file",
                is_dir,
            )
            return result
        finally:
            await self._async_agfs.pathlock_release(lease)

    async def _ensure_transfer_target_missing(self, path: str, uri: str) -> None:
        try:
            await self._async_agfs.stat(path)
        except Exception as exc:
            if is_not_found_error(exc):
                return
            mapped = map_exception(exc, resource=uri)
            if mapped is not None:
                raise mapped from exc
            raise
        raise ConflictError(f"transfer target already exists: {uri}", resource=uri)

    async def _ensure_copy_source_access(
        self,
        uri: str,
        *,
        recursive: bool,
        ctx: Optional[RequestContext],
    ) -> None:
        """Require a copy source to stay inside the caller's visible data view."""
        await self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
        canonical_uri = uri
        if is_watch_task_control_uri(canonical_uri):
            raise PermissionDeniedError(
                "Copying watch-task control state is not allowed",
                resource=canonical_uri,
            )
        if recursive and (
            is_hidden_by_actor_peer_view(canonical_uri, real_ctx)
            or may_include_hidden_actor_peers(canonical_uri, real_ctx)
        ):
            raise PermissionDeniedError(
                "Copy source may include hidden peer data",
                resource=canonical_uri,
            )
        if real_ctx.role != Role.ROOT and uri_parts(canonical_uri) in (
            [],
            ["user"],
            ["resources"],
            ["temp"],
        ):
            raise PermissionDeniedError(
                "Copying a namespace container root requires root access",
                resource=canonical_uri,
            )

    async def _ensure_transfer_parent_directory(
        self, path: str, uri: str, *, operation: str
    ) -> None:
        parent_path = self._transfer_parent_path(path)
        try:
            parent_stat = await self._async_agfs.stat(parent_path)
        except Exception as exc:
            if is_not_found_error(exc):
                parent_uri = VikingURI(uri).parent
                raise NotFoundError(
                    parent_uri.uri if parent_uri is not None else "",
                    "directory",
                ) from exc
            mapped = map_exception(exc, resource=uri)
            if mapped is not None:
                raise mapped from exc
            raise
        if not isinstance(parent_stat, dict) or not parent_stat.get("isDir", False):
            raise InvalidArgumentError(f"{operation} target parent is not a directory: {uri}")

    @staticmethod
    def _transfer_parent_path(path: str) -> str:
        return path.rstrip("/").rsplit("/", 1)[0] or "/"

    @classmethod
    def _directory_transfer_lock_requests(
        cls, old_path: str, new_path: str
    ) -> List[Dict[str, str]]:
        """Lock stable parents so directory deletion and recreation stay covered."""

        parents: List[str] = []
        for parent in (
            cls._transfer_parent_path(old_path),
            cls._transfer_parent_path(new_path),
        ):
            if any(cls._tree_lock_covers(existing, parent) for existing in parents):
                continue
            parents = [
                existing for existing in parents if not cls._tree_lock_covers(parent, existing)
            ]
            parents.append(parent)
        return [{"path": parent, "kind": "tree"} for parent in parents]

    @staticmethod
    def _tree_lock_covers(ancestor: str, path: str) -> bool:
        normalized_ancestor = ancestor.rstrip("/") or "/"
        normalized_path = path.rstrip("/") or "/"
        if normalized_ancestor == "/":
            return True
        return normalized_path == normalized_ancestor or normalized_path.startswith(
            f"{normalized_ancestor}/"
        )

    async def _copy_agfs_entry(
        self,
        old_path: str,
        new_path: str,
        *,
        old_uri: str,
        new_uri: str,
        is_dir: bool,
        ctx: Optional[RequestContext],
        lease_ref: Dict[str, Any],
    ) -> int:
        if is_dir:
            return await self._copy_directory_under_parent_locks(
                old_path,
                new_path,
                old_uri=old_uri,
                new_uri=new_uri,
                ctx=ctx,
                lease_ref=lease_ref,
            )

        await self._async_agfs.cp(
            old_path,
            new_path,
            recursive=False,
            fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref),
        )
        return 1

    async def _cleanup_transfer_target(
        self,
        path: str,
        *,
        is_dir: bool,
        ctx: Optional[RequestContext],
        lease_ref: Dict[str, Any],
    ) -> None:
        await self._async_agfs.rm(
            path,
            recursive=is_dir,
            fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref),
        )

    async def mv(
        self,
        old_uri: str,
        new_uri: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Move file/directory while extending an optional outer pathlock lease.

        Implemented as cp + rm to avoid lock files being carried by FS mv.
        On VectorDB update failure the copy is cleaned up so the source stays intact.
        """

        acl_manager = self.acl_manager
        acl_enabled = self._acl_enabled(ctx)
        guard_ctx = replace(self._ctx_or_default(ctx), bypass_acl=True)
        await self._ensure_access(old_uri, guard_ctx, action=AclAction.MANAGE)
        await self._ensure_access(old_uri, ctx, action=AclAction.WRITE)
        await self._ensure_access(new_uri, ctx, action=AclAction.WRITE)
        old_scope = old_uri.rstrip("/")
        new_scope = new_uri.rstrip("/")
        if old_scope == new_scope:
            raise InvalidArgumentError("mv source and target must be different")
        if new_scope.startswith(old_scope + "/"):
            raise InvalidArgumentError("mv target cannot be inside the source subtree")
        old_path = self._uri_to_path(old_uri, ctx=ctx)
        new_path = self._uri_to_path(new_uri, ctx=ctx)
        source_vector_uri = self._path_to_uri(old_path, ctx=ctx)
        target_uri = source_vector_uri
        new_acl_scope = acl_enabled and is_acl_uri(new_uri)

        # Verify source exists and determine type before locking.
        try:
            stat = await self._async_agfs.stat(old_path)
            is_dir = stat.get("isDir", False) if isinstance(stat, dict) else False
        except Exception as exc:
            if not is_not_found_error(exc):
                mapped = map_exception(exc, resource=old_uri)
                if mapped is not None:
                    raise mapped from exc
                raise
            raise FileNotFoundError(f"mv source not found: {old_uri}") from exc

        if is_dir:
            await self._ensure_access(old_uri, ctx, action=AclAction.MANAGE)
        await self._ensure_transfer_parent_directory(new_path, new_uri, operation="mv")
        await self._ensure_transfer_target_missing(new_path, new_uri)

        if not is_dir:
            if new_uri.rstrip("/") != new_uri:
                raise InvalidArgumentError(
                    f"mv destination for a file must include the target file name: {new_uri}",
                    details={"from_uri": old_uri, "to_uri": new_uri},
                )
            try:
                destination_stat = await self._async_agfs.stat(new_path)
            except Exception as exc:
                if not is_not_found_error(exc):
                    mapped = map_exception(exc, resource=new_uri)
                    if mapped is not None:
                        raise mapped from exc
                    raise
            else:
                if isinstance(destination_stat, dict) and destination_stat.get("isDir", False):
                    raise InvalidArgumentError(
                        f"mv destination for a file must include the target file name: {new_uri}",
                        details={"from_uri": old_uri, "to_uri": new_uri},
                    )

        if is_dir:
            lease = await self._async_agfs.pathlock_acquire_batch(
                self._directory_transfer_lock_requests(old_path, new_path),
                owner_lease_ref=lease_ref,
            )
        else:
            lease = await self._async_agfs.pathlock_acquire_batch(
                [
                    {"path": old_path, "kind": "exact"},
                    {"path": new_path, "kind": "exact"},
                ],
                owner_lease_ref=lease_ref,
            )

        operation_id = uuid.uuid4().hex
        try:
            uris_to_move = (
                await self._collect_uris(
                    old_path,
                    recursive=True,
                    ctx=ctx,
                    strict=is_dir and acl_enabled,
                )
                if is_dir
                else []
            )
            uris_to_move.append(target_uri)
            if is_dir:
                await self._ensure_access_many(uris_to_move, ctx, action=AclAction.MANAGE)
            await self._ensure_transfer_target_missing(new_path, new_uri)

            # Check if it's temp directory (files already encrypted)
            is_temp = old_uri.startswith("viking://temp/")

            # Copy source to destination. Source must stay intact until vector updates succeed.
            try:
                files_created = (
                    await self._copy_for_mv(
                        old_uri=old_uri,
                        new_uri=new_uri,
                        old_path=old_path,
                        new_path=new_path,
                        is_dir=is_dir,
                        is_temp=is_temp,
                        ctx=ctx,
                        lease_ref=lease,
                    )
                    or 0
                )
            except Exception as transfer_error:
                try:
                    await self._cleanup_transfer_target(
                        new_path,
                        is_dir=is_dir,
                        ctx=ctx,
                        lease_ref=lease,
                    )
                except Exception as rollback_error:
                    if not is_not_found_error(rollback_error):
                        raise TransferRollbackError(
                            f"mv AGFS copy failed and target cleanup failed for "
                            f"{new_uri}: {rollback_error}",
                            phase="target_cleanup",
                            residual_uri=new_uri,
                        ) from transfer_error
                if is_not_found_error(transfer_error):
                    try:
                        await self._delete_from_vector_store(uris_to_move, ctx=ctx)
                    except Exception as vector_cleanup_error:
                        raise TransferRollbackError(
                            f"mv source disappeared and orphan vector cleanup failed for "
                            f"{old_uri}: {vector_cleanup_error}",
                            phase="source_vector_cleanup",
                            residual_uri=old_uri,
                        ) from transfer_error
                    else:
                        logger.info(
                            f"[VikingFS] mv source not found, cleaned orphan index: {old_uri}"
                        )
                raise

            # Update VectorDB URIs (on failure, clean up the copy)
            vector_transfer_completed = False
            try:
                vector_result = await self._update_vector_store_uris(
                    old_uri,
                    new_uri,
                    recursive=is_dir,
                    ctx=ctx,
                )
                vector_transfer_completed = True
                if acl_manager is not None and new_acl_scope:
                    await acl_manager.refresh_context_subtree(
                        new_uri,
                        self._ctx_or_default(ctx),
                    )
            except Exception as transfer_error:
                if vector_transfer_completed:
                    try:
                        await self._update_vector_store_uris(
                            new_uri,
                            old_uri,
                            recursive=is_dir,
                            ctx=ctx,
                        )
                    except Exception as rollback_error:
                        raise TransferRollbackError(
                            f"mv post-vector step failed and vector restore was incomplete for "
                            f"{old_uri} -> {new_uri}: {rollback_error}",
                            phase="vector_restore",
                            residual_uri=new_uri,
                        ) from transfer_error
                try:
                    await self._cleanup_transfer_target(
                        new_path,
                        is_dir=is_dir,
                        ctx=ctx,
                        lease_ref=lease,
                    )
                except Exception as rollback_error:
                    raise TransferRollbackError(
                        f"mv vector transfer failed and target cleanup failed for "
                        f"{new_uri}: {rollback_error}",
                        phase="target_cleanup",
                        residual_uri=new_uri,
                    ) from transfer_error
                raise

            # Delete source
            try:
                await self._async_agfs.rm(
                    old_path,
                    recursive=is_dir,
                    fs_ctx=self._pathlock_fs_ctx(ctx, lease),
                )
            except Exception as delete_error:
                try:
                    await self._cleanup_transfer_target(
                        old_path,
                        is_dir=is_dir,
                        ctx=ctx,
                        lease_ref=lease,
                    )
                    await self._copy_agfs_entry(
                        new_path,
                        old_path,
                        old_uri=new_uri,
                        new_uri=old_uri,
                        is_dir=is_dir,
                        ctx=ctx,
                        lease_ref=lease,
                    )
                    await self._update_vector_store_uris(
                        new_uri,
                        old_uri,
                        recursive=is_dir,
                        ctx=ctx,
                    )
                    await self._cleanup_transfer_target(
                        new_path,
                        is_dir=is_dir,
                        ctx=ctx,
                        lease_ref=lease,
                    )
                except Exception as rollback_error:
                    raise TransferRollbackError(
                        f"mv source deletion failed and rollback was incomplete for "
                        f"{old_uri} -> {new_uri}: {rollback_error}",
                        phase="source_restore",
                        residual_uri=old_uri,
                    ) from delete_error
                raise
            result: Dict[str, Any] = {
                "operation_id": operation_id,
                "operation": "move",
                "from": old_uri,
                "to": new_uri,
                "recursive": is_dir,
                "phase": "completed",
                "files_created": files_created,
                "files_deleted": files_created,
            }
            if vector_result is not None:
                result["vectors"] = {
                    "scanned": vector_result.scanned,
                    "written": vector_result.written,
                    "deleted": vector_result.deleted,
                    "restored": vector_result.restored,
                    "batches": vector_result.batches,
                }
            logger.info(
                "Filesystem transfer completed: operation_id=%s operation=move "
                "object_type=%s recursive=%s result=success",
                operation_id,
                "directory" if is_dir else "file",
                is_dir,
            )
            return result
        finally:
            await self._async_agfs.pathlock_release(lease)

    async def _copy_for_mv(
        self,
        old_uri: str,
        new_uri: str,
        old_path: str,
        new_path: str,
        is_dir: bool,
        is_temp: bool,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> int:
        """Copy source to destination for mv without deleting source."""
        del is_temp
        if lease_ref is None:
            raise ValueError("mv copy requires a pathlock lease")
        return await self._copy_agfs_entry(
            old_path,
            new_path,
            old_uri=old_uri,
            new_uri=new_uri,
            is_dir=is_dir,
            ctx=ctx,
            lease_ref=lease_ref,
        )

    async def _copy_directory_under_parent_locks(
        self,
        old_path: str,
        new_path: str,
        old_uri: str,
        new_uri: str,
        ctx: Optional[RequestContext],
        lease_ref: Dict[str, Any] | None,
        transfer_source_uri: str | None = None,
        transfer_target_uri: str | None = None,
    ) -> int:
        """Copy a directory under the operation's stable parent Tree leases.

        Args:
            old_path: Source backend directory path.
            new_path: Destination backend directory path.
            ctx: Request context used for filesystem operations.
            lease_ref: Batch lease covering the source and destination parents.

        Returns:
            Number of created directories and files.
        """
        if lease_ref is None:
            raise ValueError("directory copy requires a pathlock lease")
        transfer_source_uri = transfer_source_uri or old_uri
        transfer_target_uri = transfer_target_uri or new_uri
        fs_ctx = self._pathlock_fs_ctx(ctx, lease_ref)
        await self._async_agfs.mkdir(new_path, fs_ctx=fs_ctx)
        copied = 1
        entries = await self._async_agfs.ls(old_path, fs_ctx=fs_ctx)
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in (".", ".."):
                continue
            old_child = f"{old_path.rstrip('/')}/{name}"
            new_child = f"{new_path.rstrip('/')}/{name}"
            old_child_uri = f"{old_uri.rstrip('/')}/{name}"
            new_child_uri = f"{new_uri.rstrip('/')}/{name}"
            await self._ensure_copy_source_access(
                old_child_uri,
                recursive=bool(entry.get("isDir", False)),
                ctx=ctx,
            )
            if entry.get("isDir", False):
                copied += await self._copy_directory_under_parent_locks(
                    old_child,
                    new_child,
                    old_uri=old_child_uri,
                    new_uri=new_child_uri,
                    ctx=ctx,
                    lease_ref=lease_ref,
                    transfer_source_uri=transfer_source_uri,
                    transfer_target_uri=transfer_target_uri,
                )
            else:
                if name in ABSTRACT_OVERVIEW_FILENAMES:
                    raw = await self._async_agfs.cat(old_child, fs_ctx=fs_ctx)
                    level = (
                        ContextLevel.ABSTRACT if name == ".abstract.md" else ContextLevel.OVERVIEW
                    )
                    rewritten = rewrite_abstract_overview_for_transfer(
                        raw,
                        level=level,
                        source_dir_uri=old_uri,
                        target_dir_uri=new_uri,
                        source_scope_uri=transfer_source_uri,
                        target_scope_uri=transfer_target_uri,
                    )
                    await self._async_agfs.write(
                        new_child,
                        rewritten.encode("utf-8"),
                        fs_ctx=fs_ctx,
                    )
                else:
                    await self._async_agfs.cp(
                        old_child,
                        new_child,
                        recursive=False,
                        fs_ctx=fs_ctx,
                    )
                copied += 1
        return copied

    async def _copy_dir_through_vikingfs(
        self,
        old_uri: str,
        new_uri: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Recursively copy a directory through VikingFS read/write hooks."""
        await self.mkdir(new_uri, exist_ok=True, ctx=ctx, lease_ref=lease_ref)

        entries = await self.ls(old_uri, show_all_hidden=True, node_limit=LS_ALL_NODES, ctx=ctx)
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in (".", ".."):
                continue
            old_child_uri = f"{old_uri.rstrip('/')}/{name}"
            new_child_uri = f"{new_uri.rstrip('/')}/{name}"
            if entry.get("isDir"):
                await self._copy_dir_through_vikingfs(
                    old_child_uri,
                    new_child_uri,
                    ctx=ctx,
                    lease_ref=lease_ref,
                )
            else:
                await self._copy_file_through_vikingfs(
                    old_child_uri,
                    new_child_uri,
                    ctx=ctx,
                    lease_ref=lease_ref,
                )

    async def _copy_file_through_vikingfs(
        self,
        from_uri: str,
        to_uri: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Copy one file through VikingFS read/write hooks without deleting source."""
        content_bytes = await self.read_file_bytes(from_uri, ctx=ctx)
        if lease_ref is None:
            await self.write_file_bytes(to_uri, content_bytes, ctx=ctx)
            return

        child_path = self._uri_to_path(to_uri, ctx=ctx)
        child_lease = await self._async_agfs.pathlock_acquire_exact(
            child_path,
            owner_lease_ref=lease_ref,
        )
        try:
            await self.write_file_bytes(to_uri, content_bytes, ctx=ctx, lease_ref=child_lease)
        finally:
            await self._async_agfs.pathlock_release(child_lease)

    async def resolve_uri(self, uri_or_id: str, ctx: RequestContext) -> str:
        """If ``uri_or_id`` is a 32-char hex vector record id, look it up in the
        vector store and return the corresponding URI. Otherwise return it as-is.
        Account scoping is enforced by the vector store's get() post-filter.
        """
        if not is_vector_record_id(uri_or_id):
            return uri_or_id
        missing_reason = "The data may not have been indexed yet or may have been deleted"
        vector_store = self._get_vector_store()
        if vector_store is None:
            raise NotFoundError(uri_or_id, "file", reason=missing_reason)
        records = await vector_store.get([uri_or_id], ctx=ctx)
        if not records:
            raise NotFoundError(uri_or_id, "file", reason=missing_reason)
        resolved = records[0].get("uri")
        if not resolved or not isinstance(resolved, str):
            raise NotFoundError(uri_or_id, "file", reason=missing_reason)
        return resolved

    async def stat(
        self, uri: str, ctx: Optional[RequestContext] = None, skip_count: bool = False
    ) -> Dict[str, Any]:
        """
        File/directory information.

        example: {'name': 'resources', 'size': 128, 'mode': 2147484141, 'modTime': '2026-02-10T21:26:02.934376379+08:00', 'isDir': True, 'isLocked': False, 'count': 42, 'meta': {'Name': 'localfs', 'Type': 'local', 'Content': {'local_path': '...'}}}

        Extra fields:
            isLocked (bool): Whether the path is currently held by a path lock
                (either the path itself or any ancestor directory). Returns
                False when the pathlock system is not enabled or the lookup
                fails.
            id (str): For files (non-directories), the deterministic VikingDB
                vector record primary key (level 2), computed as
                ``md5(f"{account_id}:{uri}")``. This matches the ID used in the
                vector collection so callers can cross-reference without an
                extra lookup. Not present for directories (which may have
                multiple records across L0/L1/L2 levels).
            count (int): For directories, the number of nodes in the vector index
                under this directory (including subdirectories). For files, this
                field is not included.

        Args:
            uri: Viking URI, or a 32-char hex vector record id (resolves to URI via vector store)
            ctx: Request context
            skip_count: If True, skip the vector_store.count() call for directories.
                Use this when the count field is not needed (e.g. in grep) to avoid
                an extra VikingDB API call.
        """
        real_ctx = self._ctx_or_default(ctx)
        uri = await self.resolve_uri(uri, real_ctx)
        await self._ensure_access(uri, ctx)
        primary_path = self._uri_to_path(uri, ctx=ctx)
        path = primary_path
        last_not_found: Optional[Exception] = None
        for candidate_path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, candidate_path, primary_path, real_ctx):
                continue
            try:
                result = await self._async_agfs.stat(candidate_path)
                path = candidate_path
                break
            except Exception as exc:
                if is_not_found_error(exc):
                    last_not_found = exc
                    continue
                raise
        else:
            if self._is_session_root_uri(uri):
                now = datetime.now(timezone.utc).isoformat()
                return {
                    "name": "session",
                    "size": 0,
                    "mode": 0o755,
                    "modTime": now,
                    "isDir": True,
                    "isLocked": False,
                }
            raise NotFoundError(uri, "file") from last_not_found
        if isinstance(result, dict):
            result["uri"] = uri
            result["isLocked"] = await self._is_path_locked_async(path)
            # Add deterministic vector record id for files (level 2).
            # This matches the ID used in VikingDB so callers can cross-reference
            # vector records without an extra lookup.
            if not result.get("isDir", False):
                result["id"] = vector_record_id(real_ctx.account_id, uri, level=2)
            # Add count for directories if vector store available
            if not skip_count and result.get("isDir", False):
                try:
                    vector_store = self._get_vector_store()
                    if vector_store:
                        if not may_include_hidden_actor_peers(uri, real_ctx):
                            filter_expr = PathScope("uri", uri, depth=-1)
                            result["count"] = await vector_store.count(
                                filter=filter_expr,
                                ctx=real_ctx,
                            )
                except Exception as e:
                    logger.warning(f"[VikingFS] Failed to count nodes for directory stat: {e}")
        return result

    async def exists(self, uri: str, ctx: Optional[RequestContext] = None) -> bool:
        """Check whether a URI is physically present in the caller's namespace.

        Resource ACLs control access to content, not namespace occupancy.  In
        particular, auto-naming must not treat an occupied but unreadable URI
        as available.  Namespace isolation still applies to private user,
        actor-peer, upload, and internal paths.
        """
        real_ctx = self._ctx_or_default(ctx)
        self._safe_uri_parts(uri)
        if not self._is_accessible(uri, real_ctx):
            return False

        primary_path = self._uri_to_path(uri, ctx=ctx)
        for candidate_path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, candidate_path, primary_path, real_ctx):
                continue
            if await self._agfs_path_exists(candidate_path):
                return True
        return self._is_session_root_uri(uri)

    async def glob(
        self,
        pattern: str,
        uri: str = "viking://",
        node_limit: Optional[int] = None,
        ctx: Optional[RequestContext] = None,
        extra_fields: Optional[List[str]] = None,
    ) -> Dict:
        """File pattern matching, supports **/*.md recursive.

        When extra_fields is None (default), returns URI strings.
        When extra_fields is a list (possibly empty), returns entry dicts; entries in the list
        request additional augmentation (locked, id, count). An empty list still returns dicts
        (with name/uri/size/mode/mtime/isDir populated from stat) for CLI table rendering.
        """
        _ensure_non_empty_search_query(pattern)
        await self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
        return_entries = extra_fields is not None
        aug_fields = list(extra_fields) if extra_fields else []
        primary_path = self._uri_to_path(uri, ctx=ctx)
        path: Optional[str] = None
        for candidate_path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, candidate_path, primary_path, real_ctx):
                continue
            if await self._agfs_path_exists(candidate_path):
                path = candidate_path
                break
        if path is None:
            if self._is_session_root_uri(uri):
                return {"matches": [], "count": 0}
            raise NotFoundError(uri, "directory")

        page_size = self._glob_page_size(node_limit)
        continuation_token: Optional[str] = None
        matches = []
        while True:
            page = await self._async_agfs.glob_directory(
                path,
                pattern,
                show_hidden=False,
                page_size=page_size,
                level_limit=None,
                continuation_token=continuation_token,
            )

            # ACL lookups and metadata reads keep the bare URI. Only flat string
            # results need a trailing slash to identify directory matches.
            page_matches: List[tuple[str, str, Dict[str, Any]]] = []
            for entry in page.get("entries", []):
                if not self._is_path_entry_visible(
                    entry["path"],
                    entry.get("name") or entry["path"].rsplit("/", 1)[-1],
                    path,
                    real_ctx,
                ):
                    continue
                if not await self._read_path_visible(uri, entry["path"], primary_path, real_ctx):
                    continue
                entry_uri = self._alias_uri_for_path(
                    request_uri=uri,
                    base_path=path,
                    entry_path=entry["path"],
                    ctx=ctx,
                )
                match_uri = _glob_match_uri(entry_uri, entry.get("is_dir"))
                page_matches.append((entry_uri, match_uri, entry))

            access = await self._can_access_many(
                [entry_uri for entry_uri, _, _ in page_matches], real_ctx
            )
            for entry_uri, match_uri, entry in page_matches:
                if not access.get(entry_uri, False):
                    continue
                if return_entries:
                    try:
                        entry_stat = await self.stat(entry_uri, ctx=ctx, skip_count=True)
                    except NotFoundError:
                        name = entry.get("name") or entry["path"].rsplit("/", 1)[-1]
                        entry_stat = {
                            "uri": entry_uri,
                            "name": name,
                            "isDir": bool(entry.get("is_dir", False)),
                        }
                    entry_stat.setdefault("uri", entry_uri)
                    matches.append(entry_stat)
                else:
                    matches.append(match_uri)
                if node_limit is not None and node_limit > 0 and len(matches) >= node_limit:
                    if return_entries:
                        await self._augment_entries_extra_fields(matches, aug_fields, ctx=ctx)
                    return {"matches": matches, "count": len(matches)}

            if node_limit is not None and node_limit > 0 and len(matches) >= node_limit:
                if return_entries:
                    await self._augment_entries_extra_fields(matches, aug_fields, ctx=ctx)
                return {"matches": matches, "count": len(matches)}
            continuation_token = page.get("next_token")
            if not continuation_token:
                break
        if return_entries:
            await self._augment_entries_extra_fields(matches, aug_fields, ctx=ctx)
        return {"matches": matches, "count": len(matches)}

    async def _batch_fetch_abstracts(
        self,
        entries: List[Dict[str, Any]],
        abs_limit: int,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Batch fetch abstracts for entries using a fixed-size worker pool.

        Non-directory entries receive an empty abstract immediately.
        Directory entries are processed concurrently via a worker pool,
        using _read_abstract_for_known_dir to skip redundant stat() calls.

        Args:
            entries: List of entries to fetch abstracts for
            abs_limit: Maximum length for abstract truncation
        """
        dir_jobs = []
        for index, entry in enumerate(entries):
            if not entry.get("isDir", False):
                entry["abstract"] = ""
                continue
            dir_jobs.append((index, entry))

        if not dir_jobs:
            return

        worker_count = min(_ABSTRACT_WORKER_COUNT, len(dir_jobs))

        cursor = 0
        cursor_lock = asyncio.Lock()
        results: Dict[int, str] = {}

        async def worker() -> None:
            nonlocal cursor
            while True:
                async with cursor_lock:
                    if cursor >= len(dir_jobs):
                        return
                    index, entry = dir_jobs[cursor]
                    cursor += 1

                try:
                    abstract = await self._read_abstract_for_known_dir(entry["uri"], ctx=ctx)
                except Exception:
                    abstract = "[.abstract.md is not ready]"

                results[index] = abstract

        await asyncio.gather(*(worker() for _ in range(worker_count)))

        for index, abstract in results.items():
            if len(abstract) > abs_limit:
                abstract = abstract[: abs_limit - 3] + "..."
            entries[index]["abstract"] = abstract

    async def tree(
        self,
        uri: str = "viking://",
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: Optional[int] = 1000,
        level_limit: Optional[int] = 3,
        ctx: Optional[RequestContext] = None,
        extra_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recursively list all contents (includes rel_path).

        Args:
            uri: Viking URI
            output: str = "original" or "agent"
            abs_limit: int = 256 (for agent output abstract truncation)
            show_all_hidden: bool = False (list all hidden files, like -a)
            node_limit: int | None = 1000 (maximum number of nodes to list, None means unlimited)
            level_limit: int | None = 3 (maximum depth level to traverse, None means unlimited)
            extra_fields: optional list of extra fields to include: "locked", "id", "count"

        output="original"
        [{'name': '.abstract.md', 'size': 100, 'mode': 420, 'modTime': '2026-02-11T16:52:16.256334192+08:00', 'isDir': False, 'rel_path': '.abstract.md', 'uri': 'viking://resources...'}]

        output="agent"
        [{'uri': 'viking://resources...', 'size': 100, 'isDir': False, 'modTime': '2026-02-11T08:52:16.256Z', 'rel_path': '.abstract.md', 'abstract': "..."}]
        """
        await self._ensure_access(uri, ctx)
        extra_fields = extra_fields or []
        if output == "original":
            entries = await self._tree_original(
                uri, show_all_hidden, node_limit, level_limit, ctx=ctx
            )
        elif output == "agent":
            entries = await self._tree_agent(
                uri, abs_limit, show_all_hidden, node_limit, level_limit, ctx=ctx
            )
        else:
            raise ValueError(f"Invalid output format: {output}")
        if extra_fields and output == "original":
            await self._augment_entries_extra_fields(entries, extra_fields, ctx=ctx)
        return entries

    async def _tree_original(
        self,
        uri: str,
        show_all_hidden: bool = False,
        node_limit: Optional[int] = 1000,
        level_limit: Optional[int] = 3,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Recursively list all contents (original format)."""
        result = []
        async for entry, entry_uri in self._iter_visible_tree_entries(
            uri,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
            level_limit=level_limit,
            ctx=ctx,
        ):
            info = entry["info"]
            if entry.get("access") == "denied":
                result.append(
                    {
                        "name": info["name"],
                        "isDir": info["isDir"],
                        "rel_path": entry["rel_path"],
                        "uri": entry_uri,
                        "access": "denied",
                    }
                )
                continue
            new_entry = dict(entry.get("extra", {}))
            new_entry.update(
                {
                    "name": info["name"],
                    "size": info["size"],
                    "mode": info["mode"],
                    "modTime": info["modTime"],
                    "isDir": info["isDir"],
                    "rel_path": entry["rel_path"],
                    "uri": entry_uri,
                }
            )
            result.append(new_entry)
        return result

    async def _tree_agent(
        self,
        uri: str,
        abs_limit: int,
        show_all_hidden: bool = False,
        node_limit: Optional[int] = 1000,
        level_limit: Optional[int] = 3,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Recursively list all contents (agent format with abstracts)."""
        result = []

        async for entry, entry_uri in self._iter_visible_tree_entries(
            uri,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
            level_limit=level_limit,
            ctx=ctx,
        ):
            info = entry["info"]
            is_dir = info["isDir"]
            if entry.get("access") == "denied":
                result.append(
                    {
                        "uri": entry_uri,
                        "isDir": is_dir,
                        "rel_path": entry["rel_path"],
                        "access": "denied",
                    }
                )
                continue
            result.append(
                {
                    "uri": entry_uri,
                    "size": 0 if is_dir else info["size"],
                    "isDir": is_dir,
                    "modTime": format_iso8601(parse_iso_datetime(info["modTime"])),
                    "rel_path": entry["rel_path"],
                }
            )

        await self._batch_fetch_abstracts(
            [entry for entry in result if entry.get("access") != "denied"],
            abs_limit,
            ctx=ctx,
        )

        return result

    # ========== Vector Sync Helper Methods ==========

    async def _collect_uris(
        self,
        path: str,
        recursive: bool,
        ctx: Optional[RequestContext] = None,
        *,
        strict: bool = False,
    ) -> List[str]:
        """Recursively collect all URIs (for rm/mv), including directories."""
        uris = []

        async def _collect(p: str):
            try:
                entries = await self._ls_entries(p, ctx=ctx)
            except Exception as exc:
                if is_not_found_error(exc) and not strict:
                    return
                raise

            for entry in entries:
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                full_path = f"{p}/{name}".replace("//", "/")
                if entry.get("isDir"):
                    uris.append(self._path_to_uri(full_path, ctx=ctx))
                    if recursive:
                        await _collect(full_path)
                else:
                    uris.append(self._path_to_uri(full_path, ctx=ctx))

        await _collect(path)
        return uris

    # ========== Parent Directory Creation ==========

    async def _ensure_parent_dirs(
        self,
        path: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Recursively create all parent directories."""
        try:
            await self._async_agfs.ensure_parent_dirs(
                path,
                fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref),
            )
        except Exception as e:
            logger.debug(f"Failed to ensure parent directories for {path}: {e}")
            parent = path.rstrip("/").rsplit("/", 1)[0]
            await self._mkdir_path_with_parents(parent, ctx=ctx, lease_ref=lease_ref)

    async def _mkdir_path_with_parents(
        self,
        dir_path: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Create a directory path segment-by-segment using the same fs context."""
        parts = [part for part in dir_path.strip("/").split("/") if part]
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                await self._async_agfs.mkdir(
                    current,
                    fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref),
                )
            except Exception as e:
                message = str(e).lower()
                if "exist" in message or "already" in message:
                    continue
                logger.debug(f"Failed to create parent directory {current}: {e}")

    # ========== Batch Read (backward compatible) ==========

    async def read_batch(
        self, uris: List[str], level: str = "l0", ctx: Optional[RequestContext] = None
    ) -> Dict[str, str]:
        """Batch read content from multiple URIs."""
        results = {}
        for uri in uris:
            try:
                content = ""
                if level == "l0":
                    content = await self.abstract(uri, ctx=ctx)
                elif level == "l1":
                    content = await self.overview(uri, ctx=ctx)
                results[uri] = content
            except Exception:
                pass
        return results

    # ========== Other Preserved Methods ==========

    async def write_file(
        self,
        uri: str,
        content: Union[str, bytes],
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
        auto_pathlock: bool = True,
    ) -> None:
        """Write file directly. Encryption lock handled internally by EncryptionWrappedFS.

        When ``auto_pathlock`` is False the underlying AGFS write runs with
        automatic pathlock disabled. Only safe for URIs that are never written
        concurrently (e.g. unique-per-request shared upload directories).
        """
        await self._ensure_access(uri, ctx, action=AclAction.WRITE)
        path = self._uri_to_path(uri, ctx=ctx)
        await self._ensure_parent_dirs(path, ctx=ctx, lease_ref=lease_ref)

        if isinstance(content, str):
            content = content.encode("utf-8")

        await self._async_agfs.write(
            path,
            content,
            fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref),
            auto_pathlock=auto_pathlock,
        )

    async def read_file(
        self,
        uri: str,
        offset: int = 0,
        limit: int = -1,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read single file, optionally sliced by line range.

        Args:
            uri: Viking URI, or a 32-char hex vector record id (resolves to URI via vector store)
            offset: Starting line number (0-indexed). Default 0.
            limit: Number of lines to read. -1 means read to end. Default -1.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        real_ctx = self._ctx_or_default(ctx)
        uri = await self.resolve_uri(uri, real_ctx)
        await self._ensure_access(uri, ctx)
        primary_path = self._uri_to_path(uri, ctx=ctx)
        # Verify the file exists before reading, because AGFS read returns
        # empty bytes for non-existent files instead of raising an error.
        last_not_found: Optional[Exception] = None
        for path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, path, primary_path, real_ctx):
                continue
            try:
                stat = await self._async_agfs.stat(path)
                break
            except Exception as exc:
                if is_not_found_error(exc):
                    last_not_found = exc
                    continue
                raise
        else:
            raise NotFoundError(uri, "file") from last_not_found
        if isinstance(stat, dict) and stat.get("isDir", False):
            raise InvalidArgumentError(
                f"Directory URI is not readable as a file: {uri}. "
                "List it first, then read a file URI.",
                details={"resource": uri, "expected": "file", "actual": "directory"},
            )
        try:
            content = await self._async_agfs.read(path)
            if isinstance(content, bytes):
                raw = content
            elif content is not None and hasattr(content, "content"):
                raw = content.content
            else:
                raw = b""

            text = self._decode_bytes(raw)
        except Exception as exc:
            if is_not_found_error(exc):
                raise NotFoundError(uri, "file") from exc
            raise

        if offset == 0 and limit == -1:
            return text
        lines = text.splitlines(keepends=True)
        sliced = lines[offset:] if limit == -1 else lines[offset : offset + limit]
        return "".join(sliced)

    async def read_file_bytes(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> bytes:
        """Read single binary file. Accepts a Viking URI or a 32-char hex vector record id."""
        real_ctx = self._ctx_or_default(ctx)
        uri = await self.resolve_uri(uri, real_ctx)
        await self._ensure_access(uri, ctx)
        primary_path = self._uri_to_path(uri, ctx=ctx)
        last_not_found: Optional[Exception] = None
        for path in self._read_paths(uri, ctx=ctx):
            if not await self._read_path_visible(uri, path, primary_path, real_ctx):
                continue
            try:
                stat = await self._async_agfs.stat(path)
                break
            except Exception as exc:
                if is_not_found_error(exc):
                    last_not_found = exc
                    continue
                raise
        else:
            raise NotFoundError(uri, "file") from last_not_found
        if isinstance(stat, dict) and stat.get("isDir", False):
            raise InvalidArgumentError(
                f"Cannot read directory as file: {uri}",
                details={"resource": uri, "expected": "file", "actual": "directory"},
            )
        try:
            raw = self._handle_agfs_read(await self._async_agfs.read(path))
            return raw
        except Exception as exc:
            if is_not_found_error(exc):
                raise NotFoundError(uri, "file") from exc
            raise

    async def write_file_bytes(
        self,
        uri: str,
        content: bytes,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
        auto_pathlock: bool = True,
    ) -> None:
        """Write single binary file. Encryption lock handled internally by EncryptionWrappedFS.

        When ``auto_pathlock`` is False the underlying AGFS write runs with
        automatic pathlock disabled. Only safe for URIs that are never written
        concurrently (e.g. unique-per-request shared upload directories).
        """
        await self._ensure_access(uri, ctx, action=AclAction.WRITE)
        path = self._uri_to_path(uri, ctx=ctx)
        await self._ensure_parent_dirs(path, ctx=ctx, lease_ref=lease_ref)

        await self._async_agfs.write(
            path,
            content,
            fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref),
            auto_pathlock=auto_pathlock,
        )

    async def append_file(
        self,
        uri: str,
        content: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Append content to file while holding one exact pathlock lease."""
        await self._ensure_access(uri, ctx, action=AclAction.WRITE)
        path = self._uri_to_path(uri, ctx=ctx)

        owned_lease = None
        try:
            await self._ensure_parent_dirs(path, ctx=ctx, lease_ref=lease_ref)
            lease = lease_ref
            if lease is None:
                lease = await self._async_agfs.pathlock_acquire_exact(path)
                owned_lease = lease
            fs_ctx = self._pathlock_fs_ctx(ctx, lease)

            # Read old content and rewrite the whole file to avoid lost updates.
            existing = ""
            try:
                existing_bytes = self._handle_agfs_read(
                    await self._async_agfs.read(path, fs_ctx=fs_ctx)
                )
                existing = self._decode_bytes(existing_bytes)
            except FileNotFoundError:
                pass
            except AGFSHTTPError as e:
                if e.status_code != 404:
                    raise
            except AGFSClientError:
                raise

            final_content = (existing + content).encode("utf-8")
            await self._async_agfs.write(
                path,
                final_content,
                fs_ctx=fs_ctx,
            )

        except Exception as e:
            logger.error(f"[VikingFS] Failed to append to file {uri}: {e}")
            raise IOError(f"Failed to append to file {uri}: {e}")
        finally:
            if owned_lease is not None:
                await self._async_agfs.pathlock_release(owned_lease)

    async def ls(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        ctx: Optional[RequestContext] = None,
        extra_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        List directory contents (URI version).

        Args:
            uri: Viking URI
            output: str = "original"
            abs_limit: int = 256
            show_all_hidden: bool = False (list all hidden files, like -a)
            node_limit: int = 1000 (maximum number of nodes to list)
            sort_by: Optional sort field, "name" or "mtime"
            sort_order: Sort direction, "asc" or "desc"
            extra_fields: optional list of extra fields to include: "locked", "id", "count"

        output="original"
        [{'name': '.abstract.md', 'size': 100, 'mode': 420, 'modTime': '2026-02-11T16:52:16.256334192+08:00', 'isDir': False, 'meta': {'Name': 'localfs', 'Type': 'local', 'Content': None}, 'uri': 'viking://resources/.abstract.md'}]

        output="agent"
        [{'name': '.abstract.md', 'size': 100, 'modTime': '2026-02-11T08:52:16.256Z', 'isDir': False, 'uri': 'viking://resources/.abstract.md', 'abstract': "..."}]
        """
        await self._ensure_access(uri, ctx)
        extra_fields = extra_fields or []
        if sort_by not in {None, "name", "mtime"}:
            raise ValueError("sort_by must be 'name' or 'mtime'")
        if sort_order not in {"asc", "desc"}:
            raise ValueError("sort_order must be 'asc' or 'desc'")
        if output == "original":
            entries = await self._ls_original(
                uri,
                show_all_hidden,
                node_limit,
                sort_by=sort_by,
                sort_order=sort_order,
                ctx=ctx,
            )
        elif output == "agent":
            entries = await self._ls_agent(
                uri,
                abs_limit,
                show_all_hidden,
                node_limit,
                sort_by=sort_by,
                sort_order=sort_order,
                ctx=ctx,
            )
        else:
            raise ValueError(f"Invalid output format: {output}")
        if extra_fields and output == "original":
            await self._augment_entries_extra_fields(entries, extra_fields, ctx=ctx)
        return entries

    @staticmethod
    def _ls_entry_mtime(entry: Dict[str, Any]) -> Optional[float]:
        raw_time = entry.get("modTime")
        if isinstance(raw_time, (int, float)):
            return float(raw_time)
        if isinstance(raw_time, str) and raw_time:
            try:
                return parse_iso_datetime(raw_time).timestamp()
            except (TypeError, ValueError, OverflowError):
                return None

        legacy_time = entry.get("mtime")
        if isinstance(legacy_time, (int, float)):
            return float(legacy_time)
        return None

    @classmethod
    def _sort_ls_entry_items(
        cls,
        entry_items: List[tuple[Dict[str, Any], str]],
        sort_by: Optional[str],
        sort_order: str,
    ) -> List[tuple[Dict[str, Any], str]]:
        if sort_by is None:
            return entry_items

        descending = sort_order == "desc"
        directories = [item for item in entry_items if item[0].get("isDir", False)]
        files = [item for item in entry_items if not item[0].get("isDir", False)]

        if sort_by == "name":

            def name_key(item: tuple[Dict[str, Any], str]) -> tuple[str, str]:
                name = str(item[0].get("name", ""))
                return name.lower(), name

            directories.sort(key=name_key, reverse=descending)
            files.sort(key=name_key, reverse=descending)
            return directories + files

        def sort_by_mtime(
            items: List[tuple[Dict[str, Any], str]],
        ) -> List[tuple[Dict[str, Any], str]]:
            timestamped = []
            missing = []
            for item in items:
                timestamp = cls._ls_entry_mtime(item[0])
                if timestamp is None:
                    missing.append(item)
                else:
                    timestamped.append((timestamp, item))
            timestamped.sort(
                key=lambda pair: pair[0],
                reverse=descending,
            )
            return [item for _, item in timestamped] + missing

        return sort_by_mtime(directories) + sort_by_mtime(files)

    async def _ls_agent(
        self,
        uri: str,
        abs_limit: int,
        show_all_hidden: bool,
        node_limit: int = 1000,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """List directory contents (URI version)."""
        entry_items = await self._ls_browsable_items(uri, ctx=ctx)
        entry_items = self._sort_ls_entry_items(entry_items, sort_by, sort_order)
        # basic info
        fallback_time = datetime.now(timezone.utc)
        all_entries = []
        for entry, entry_uri in entry_items:
            name = entry.get("name", "")
            if entry.get("access") == "denied":
                if entry.get("isDir") or not name.startswith(".") or show_all_hidden:
                    all_entries.append(
                        {
                            "name": name,
                            "uri": entry_uri,
                            "isDir": bool(entry.get("isDir", False)),
                            "access": "denied",
                        }
                    )
                continue
            raw_time = entry.get("modTime", "")
            parsed_time = fallback_time
            if isinstance(raw_time, (int, float)):
                parsed_time = datetime.fromtimestamp(raw_time, tz=timezone.utc)
            elif raw_time:
                if len(raw_time) > 26 and "+" in raw_time:
                    parts = raw_time.split("+")
                    raw_time = parts[0][:26] + "+" + parts[1]
                parsed_time = parse_iso_datetime(raw_time)
            elif isinstance(entry.get("mtime"), (int, float)):
                parsed_time = datetime.fromtimestamp(entry["mtime"], tz=timezone.utc)
            is_dir = entry.get("isDir", False)
            new_entry = {
                "uri": entry_uri,
                "size": 0 if is_dir else entry.get("size", 0),
                "isDir": is_dir,
                "modTime": format_iso8601(parsed_time),
            }
            if is_dir:
                all_entries.append(new_entry)
            elif not name.startswith("."):
                all_entries.append(new_entry)
            elif show_all_hidden:
                all_entries.append(new_entry)
        all_entries = all_entries[:node_limit]
        await self._batch_fetch_abstracts(
            [entry for entry in all_entries if entry.get("access") != "denied"],
            abs_limit,
            ctx=ctx,
        )
        return all_entries

    async def _ls_original(
        self,
        uri: str,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """List directory contents (URI version)."""
        entry_items = await self._ls_browsable_items(uri, ctx=ctx)
        entry_items = self._sort_ls_entry_items(entry_items, sort_by, sort_order)
        # AGFS returns read-only structure, need to create new dict
        all_entries = []
        for entry, entry_uri in entry_items:
            name = entry.get("name", "")
            if entry.get("access") == "denied":
                new_entry = {
                    "name": name,
                    "isDir": bool(entry.get("isDir", False)),
                    "uri": entry_uri,
                    "access": "denied",
                }
            else:
                new_entry = dict(entry)
                new_entry["uri"] = entry_uri
            if entry.get("isDir"):
                all_entries.append(new_entry)
            elif not name.startswith("."):
                all_entries.append(new_entry)
            elif show_all_hidden:
                all_entries.append(new_entry)
        return all_entries[:node_limit]

    async def _ls_browsable_items(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> List[tuple[Dict[str, Any], str]]:
        """Return list entries according to namespace-enumeration semantics."""
        entry_items = await self._list_read_path_items(uri, ctx=ctx)
        access = await self._can_access_many([entry_uri for _, entry_uri in entry_items], ctx)
        expose_resource_names = self._acl_enabled(ctx) and is_acl_uri(uri)

        browsable = []
        for entry, entry_uri in entry_items:
            if access.get(entry_uri, False):
                browsable.append((entry, entry_uri))
            elif expose_resource_names:
                browsable.append(
                    (
                        {
                            "name": entry.get("name", ""),
                            "isDir": bool(entry.get("isDir", False)),
                            "access": "denied",
                        },
                        entry_uri,
                    )
                )
        return browsable

    async def _augment_entries_extra_fields(
        self,
        entries: List[Dict[str, Any]],
        extra_fields: List[str],
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Augment entries in-place with extra fields (locked, id, count)."""
        real_ctx = self._ctx_or_default(ctx)
        need_locked = "locked" in extra_fields
        need_id = "id" in extra_fields
        need_count = "count" in extra_fields
        vector_store = self._get_vector_store() if need_count else None

        lock_paths: List[tuple[int, str]] = []
        for i, entry in enumerate(entries):
            # ACL directory enumeration may expose only a name/type placeholder.
            # Do not enrich denied entries with metadata from inaccessible paths.
            if entry.get("access") == "denied":
                continue
            entry_uri = entry.get("uri", "")
            is_dir = entry.get("isDir", False)
            if need_locked:
                path = self._try_uri_to_path(entry_uri, ctx=ctx)
                if path is not None:
                    lock_paths.append((i, path))
            if need_id and not is_dir:
                entry["id"] = vector_record_id(real_ctx.account_id, entry_uri, level=2)
            if need_count and is_dir and vector_store and entry_uri:
                try:
                    if not may_include_hidden_actor_peers(entry_uri, real_ctx):
                        filter_expr = PathScope("uri", entry_uri, depth=-1)
                        entry["count"] = await vector_store.count(
                            filter=filter_expr,
                            ctx=real_ctx,
                        )
                except Exception as e:
                    logger.warning(f"[VikingFS] Failed to count nodes for {entry_uri}: {e}")

        if need_locked and lock_paths:
            for i, path in lock_paths:
                try:
                    entries[i]["isLocked"] = await self._is_path_locked_async(path)
                except Exception:
                    entries[i]["isLocked"] = False

    def _try_uri_to_path(self, uri: str, ctx: Optional[RequestContext] = None) -> Optional[str]:
        """Best-effort URI to path conversion; returns None on failure."""
        try:
            return self._uri_to_path(uri, ctx=ctx)
        except Exception:
            return None

    async def move_file(
        self,
        from_uri: str,
        to_uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Move file."""
        await self._ensure_access(from_uri, ctx, action=AclAction.WRITE)
        await self._ensure_access(to_uri, ctx, action=AclAction.WRITE)
        from_path = self._uri_to_path(from_uri, ctx=ctx)

        await self._copy_file_through_vikingfs(from_uri, to_uri, ctx=ctx)
        await self._async_agfs.rm(from_path)

    # ========== Temp File Operations (backward compatible) ==========

    def create_temp_uri(self, ctx: Optional[RequestContext] = None) -> str:
        """Create a temp directory URI.

        - explicit ctx or bound request context -> user-scoped temp URI
        - no explicit/bound context -> legacy temp URI shape for backward compatibility
        """
        real_ctx = ctx if ctx is not None else self._bound_ctx.get()
        if real_ctx is None:
            return VikingURI.create_temp_uri()
        return VikingURI.create_temp_uri(space=real_ctx.user.user_space_name())

    async def persist_temp_tree(
        self,
        temp_uri: str,
        target_uri: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Persist an already-encrypted temp tree without rewriting file bytes."""
        await self._ensure_access(temp_uri, ctx)
        await self._ensure_access(target_uri, ctx, action=AclAction.WRITE)
        src_path = self._uri_to_path(temp_uri, ctx=ctx)
        dst_path = self._uri_to_path(target_uri, ctx=ctx)
        fs_ctx = self._pathlock_fs_ctx(ctx, lease_ref)
        await self._ensure_parent_dirs(dst_path, ctx=ctx, lease_ref=lease_ref)
        await self._async_agfs.cp(
            src_path,
            dst_path,
            recursive=True,
            fs_ctx=fs_ctx or {"account_id": self._ctx_or_default(ctx).account_id},
            allow_same_mount_fast_path=True,
        )

    async def delete_temp(
        self,
        temp_uri: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Delete temp directory and its contents."""
        await self._ensure_access(temp_uri, ctx, action=AclAction.MANAGE)
        path = self._uri_to_path(temp_uri, ctx=ctx)
        fs_ctx = self._pathlock_fs_ctx(ctx, lease_ref)
        try:
            await self._async_agfs.rm(path, recursive=True, fs_ctx=fs_ctx)
        except Exception as e:
            logger.warning(f"[VikingFS] Failed to delete temp {temp_uri}: {e}")

    async def _ls_entries(
        self, path: str, ctx: Optional[RequestContext] = None
    ) -> List[Dict[str, Any]]:
        """List directory entries, filtering out internal directories.

        At account root (/local/{account}), uses LISTABLE_SCOPES whitelist.
        At other levels, uses the shared storage internal-name blacklist.
        """
        entries = await self._async_agfs.ls(path)
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) == 2 and parts[0] == "local":
            return [e for e in entries if e.get("name") in VikingURI.LISTABLE_SCOPES]
        return [e for e in entries if e.get("name") not in STORAGE_INTERNAL_ENTRY_NAMES]
