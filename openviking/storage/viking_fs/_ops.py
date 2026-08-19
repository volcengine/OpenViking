# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Core filesystem operations mixin for VikingFS."""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from openviking.core.namespace import (
    may_include_hidden_actor_peers,
)
from openviking.pyagfs.exceptions import (
    AGFSClientError,
    AGFSDirectoryNotEmptyError,
    AGFSHTTPError,
)
from openviking.server.error_mapping import is_not_found_error, map_exception
from openviking.server.identity import RequestContext
from openviking.storage.expr import PathScope
from openviking.storage.internal_names import STORAGE_INTERNAL_ENTRY_NAMES
from openviking.storage.viking_fs._base import (
    _ABSTRACT_WORKER_COUNT,
    LS_ALL_NODES,
    _ensure_non_empty_search_query,
    _is_directory_not_empty_error,
    logger,
)
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
)
from openviking_cli.utils.uri import VikingURI


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
        """Read file"""
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
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
        self._ensure_mutable_access(uri, ctx)
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
        self._ensure_mutable_access(uri, ctx)
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
    ) -> Dict[str, Any]:
        """Delete file/directory + recursively update vector index.

        This method is idempotent: deleting a non-existent file succeeds
        after cleaning up any orphan index records.

        Acquires a path lock, deletes VectorDB records, then FS files.
        Raises ResourceBusyError when the target is locked by an ongoing
        operation (e.g. semantic processing).

        Returns:
            Dict with 'estimated_deleted_count' indicating the estimated number
            of nodes deleted from vector index.
        """
        from openviking.storage.errors import LockAcquisitionError, ResourceBusyError

        self._ensure_delete_access(uri, ctx)
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
            # Path does not exist: clean up any orphan index records and return
            uris_to_delete = await self._collect_uris(path, recursive, ctx=ctx)
            uris_to_delete.append(target_uri)
            real_ctx = self._ctx_or_default(ctx)
            estimated_count = await _estimate_deleted_count(path, real_ctx)
            await self._delete_from_vector_store(uris_to_delete, ctx=ctx)
            logger.info(f"[VikingFS] rm target not found, cleaned orphan index: {uri}")
            return {"estimated_deleted_count": estimated_count}

        if is_dir:
            if not recursive:
                raise FailedPreconditionError(
                    f"Cannot remove directory without --recursive: {uri}",
                    details={"resource": uri, "expected_flag": "recursive"},
                )
            lock_method = self._async_agfs.pathlock_acquire_tree
        else:
            lock_method = self._async_agfs.pathlock_acquire_exact

        lease = lease_ref
        if lease is None:
            try:
                lease = await lock_method(path)
            except LockAcquisitionError:
                raise ResourceBusyError(f"Resource is being processed: {uri}", uri=uri)

        try:
            uris_to_delete = await self._collect_uris(path, recursive, ctx=ctx) if is_dir else []
            uris_to_delete.append(target_uri)
            real_ctx = self._ctx_or_default(ctx)
            estimated_count = await _estimate_deleted_count(path, real_ctx)
            await self._delete_from_vector_store(uris_to_delete, ctx=ctx)
            try:
                result = await self._async_agfs.rm(
                    path,
                    recursive=recursive,
                    fs_ctx=self._pathlock_fs_ctx(ctx, lease),
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
            if lease_ref is None:
                await self._async_agfs.pathlock_release(lease)

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

        self._ensure_mutable_access(old_uri, ctx)
        # mv is implemented as copy + recursive rm of the source (see the
        # ``rm(old_path, recursive=is_dir)`` below), so the source must also clear
        # the delete guard. Without this, a protected account root such as
        # ``viking://`` — which rm() rejects up front (#2873) — could still be
        # destroyed via mv, since the write guard alone permits the bare root.
        self._ensure_delete_access(old_uri, ctx)
        self._ensure_mutable_access(new_uri, ctx)
        old_path = self._uri_to_path(old_uri, ctx=ctx)
        new_path = self._uri_to_path(new_uri, ctx=ctx)
        target_uri = self._path_to_uri(old_path, ctx=ctx)

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
                [
                    {"path": old_path, "kind": "tree"},
                    {"path": new_path, "kind": "exact"},
                ],
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

        try:
            uris_to_move = (
                await self._collect_uris(old_path, recursive=True, ctx=ctx) if is_dir else []
            )
            uris_to_move.append(target_uri)

            # Check if it's temp directory (files already encrypted)
            is_temp = old_uri.startswith("viking://temp/")

            # Copy source to destination. Source must stay intact until vector updates succeed.
            try:
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
            except Exception as e:
                if "not found" in str(e).lower():
                    try:
                        await self._delete_from_vector_store(uris_to_move, ctx=ctx)
                    except Exception:
                        # Orphan cleanup is best effort here; preserve the copy error.
                        pass
                    else:
                        logger.info(
                            f"[VikingFS] mv source not found, cleaned orphan index: {old_uri}"
                        )
                raise

            # Update VectorDB URIs (on failure, clean up the copy)
            try:
                await self._update_vector_store_uris(uris_to_move, old_uri, new_uri, ctx=ctx)
            except Exception:
                try:
                    if is_dir:
                        cleanup_lease = await self._async_agfs.pathlock_acquire_tree(
                            new_path,
                            owner_lease_ref=lease,
                        )
                        try:
                            await self._async_agfs.rm(
                                new_path,
                                recursive=True,
                                fs_ctx=self._pathlock_fs_ctx(ctx, cleanup_lease),
                            )
                        finally:
                            await self._async_agfs.pathlock_release(cleanup_lease)
                    else:
                        await self._async_agfs.rm(
                            new_path,
                            fs_ctx=self._pathlock_fs_ctx(ctx, lease),
                        )
                except Exception:
                    pass
                raise

            # Delete source
            await self._async_agfs.rm(
                old_path,
                recursive=is_dir,
                fs_ctx=self._pathlock_fs_ctx(ctx, lease),
            )
            return {}
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
    ) -> None:
        """Copy source to destination for mv without deleting source."""
        if is_temp:
            if is_dir:
                await self._copy_temp_dir_with_exact_locks(
                    old_path,
                    new_path,
                    ctx=ctx,
                    lease_ref=lease_ref,
                )
            else:
                await self._async_agfs.cp(
                    old_path,
                    new_path,
                    recursive=False,
                    fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref),
                )
            return

        if is_dir:
            await self._copy_dir_through_vikingfs(old_uri, new_uri, ctx=ctx, lease_ref=lease_ref)
        else:
            await self._copy_file_through_vikingfs(old_uri, new_uri, ctx=ctx, lease_ref=lease_ref)

    async def _copy_temp_dir_with_exact_locks(
        self,
        old_path: str,
        new_path: str,
        ctx: Optional[RequestContext],
        lease_ref: Dict[str, Any] | None,
    ) -> None:
        """Copy an encrypted temp directory while locking every destination entry.

        Args:
            old_path: Source backend directory path.
            new_path: Destination backend directory path.
            ctx: Request context used for filesystem operations.
            lease_ref: Exact lease covering the current destination directory.

        Returns:
            None.
        """
        if lease_ref is None:
            raise ValueError("temp directory copy requires a pathlock lease")
        fs_ctx = self._pathlock_fs_ctx(ctx, lease_ref)
        await self._async_agfs.mkdir(new_path, fs_ctx=fs_ctx)
        entries = await self._async_agfs.ls(old_path, fs_ctx=fs_ctx)
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in (".", ".."):
                continue
            old_child = f"{old_path.rstrip('/')}/{name}"
            new_child = f"{new_path.rstrip('/')}/{name}"
            child_lease = await self._async_agfs.pathlock_acquire_exact(
                new_child,
                owner_lease_ref=lease_ref,
            )
            try:
                if entry.get("isDir", False):
                    await self._copy_temp_dir_with_exact_locks(
                        old_child,
                        new_child,
                        ctx=ctx,
                        lease_ref=child_lease,
                    )
                else:
                    await self._async_agfs.cp(
                        old_child,
                        new_child,
                        recursive=False,
                        fs_ctx=self._pathlock_fs_ctx(ctx, child_lease),
                    )
            finally:
                await self._async_agfs.pathlock_release(child_lease)

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

    async def stat(
        self, uri: str, ctx: Optional[RequestContext] = None, skip_count: bool = False
    ) -> Dict[str, Any]:
        """
        File/directory information.

        example: {'name': 'resources', 'size': 128, 'mode': 2147484141, 'modTime': '2026-02-10T21:26:02.934376379+08:00', 'isDir': True, 'isLocked': False, 'count': 42, 'meta': {'Name': 'localfs', 'Type': 'local', 'Content': {'local_path': '...'}}}

        Extra field:
            isLocked (bool): Whether the path is currently held by a path lock
                (either the path itself or any ancestor directory). Returns
                False when the pathlock system is not enabled or the lookup
                fails.
            count (int): For directories, the number of nodes in the vector index
                under this directory (including subdirectories). For files, this
                field is not included.

        Args:
            uri: Viking URI
            ctx: Request context
            skip_count: If True, skip the vector_store.count() call for directories.
                Use this when the count field is not needed (e.g. in grep) to avoid
                an extra VikingDB API call.
        """
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
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
            result["isLocked"] = await self._is_path_locked_async(path)
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
        """Check if a URI exists.

        Args:
            uri: Viking URI
            ctx: Request context

        Returns:
            bool: True if the URI exists, False otherwise
        """
        try:
            await self.stat(uri, ctx=ctx)
            return True
        except Exception:
            return False

    async def glob(
        self,
        pattern: str,
        uri: str = "viking://",
        node_limit: Optional[int] = None,
        ctx: Optional[RequestContext] = None,
    ) -> Dict:
        """File pattern matching, supports **/*.md recursive."""
        _ensure_non_empty_search_query(pattern)
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
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

            for entry in page.get("entries", []):
                if node_limit is not None and node_limit > 0 and len(matches) >= node_limit:
                    return {"matches": matches, "count": len(matches)}
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
                matches.append(entry_uri)

            if node_limit is not None and node_limit > 0 and len(matches) >= node_limit:
                return {"matches": matches, "count": len(matches)}
            continuation_token = page.get("next_token")
            if not continuation_token:
                break
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

        output="original"
        [{'name': '.abstract.md', 'size': 100, 'mode': 420, 'modTime': '2026-02-11T16:52:16.256334192+08:00', 'isDir': False, 'rel_path': '.abstract.md', 'uri': 'viking://resources...'}]

        output="agent"
        [{'uri': 'viking://resources...', 'size': 100, 'isDir': False, 'modTime': '2026-02-11T08:52:16.256Z', 'rel_path': '.abstract.md', 'abstract': "..."}]
        """
        self._ensure_access(uri, ctx)
        if output == "original":
            return await self._tree_original(uri, show_all_hidden, node_limit, level_limit, ctx=ctx)
        elif output == "agent":
            return await self._tree_agent(
                uri, abs_limit, show_all_hidden, node_limit, level_limit, ctx=ctx
            )
        else:
            raise ValueError(f"Invalid output format: {output}")

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
            new_entry = dict(entry.get("extra", {}))
            new_entry.update({
                "name": info["name"],
                "size": info["size"],
                "mode": info["mode"],
                "modTime": info["modTime"],
                "isDir": info["isDir"],
                "rel_path": entry["rel_path"],
                "uri": entry_uri,
            })
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
            result.append({
                "uri": entry_uri,
                "size": 0 if is_dir else info["size"],
                "isDir": is_dir,
                "modTime": format_iso8601(parse_iso_datetime(info["modTime"])),
                "rel_path": entry["rel_path"],
            })

        await self._batch_fetch_abstracts(result, abs_limit, ctx=ctx)

        return result

    # ========== Vector Sync Helper Methods ==========

    async def _collect_uris(
        self, path: str, recursive: bool, ctx: Optional[RequestContext] = None
    ) -> List[str]:
        """Recursively collect all URIs (for rm/mv), including directories."""
        uris = []

        async def _collect(p: str):
            try:
                entries = await self._ls_entries(p, ctx=ctx)
            except Exception as exc:
                if is_not_found_error(exc):
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
    ) -> None:
        """Write file directly. Encryption lock handled internally by EncryptionWrappedFS."""
        self._ensure_mutable_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        await self._ensure_parent_dirs(path, ctx=ctx, lease_ref=lease_ref)

        if isinstance(content, str):
            content = content.encode("utf-8")

        await self._async_agfs.write(path, content, fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref))

    async def read_file(
        self,
        uri: str,
        offset: int = 0,
        limit: int = -1,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read single file, optionally sliced by line range.

        Args:
            uri: Viking URI
            offset: Starting line number (0-indexed). Default 0.
            limit: Number of lines to read. -1 means read to end. Default -1.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
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
        except Exception:
            raise NotFoundError(uri, "file")

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
        """Read single binary file."""
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
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
        except Exception:
            raise NotFoundError(uri, "file")

    async def write_file_bytes(
        self,
        uri: str,
        content: bytes,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Write single binary file. Encryption lock handled internally by EncryptionWrappedFS."""
        self._ensure_mutable_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        await self._ensure_parent_dirs(path, ctx=ctx, lease_ref=lease_ref)

        await self._async_agfs.write(path, content, fs_ctx=self._pathlock_fs_ctx(ctx, lease_ref))

    async def append_file(
        self,
        uri: str,
        content: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Append content to file while holding one exact pathlock lease."""
        self._ensure_mutable_access(uri, ctx)
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

        output="original"
        [{'name': '.abstract.md', 'size': 100, 'mode': 420, 'modTime': '2026-02-11T16:52:16.256334192+08:00', 'isDir': False, 'meta': {'Name': 'localfs', 'Type': 'local', 'Content': None}, 'uri': 'viking://resources/.abstract.md'}]

        output="agent"
        [{'name': '.abstract.md', 'size': 100, 'modTime': '2026-02-11T08:52:16.256Z', 'isDir': False, 'uri': 'viking://resources/.abstract.md', 'abstract': "..."}]
        """
        self._ensure_access(uri, ctx)
        if sort_by not in {None, "name", "mtime"}:
            raise ValueError("sort_by must be 'name' or 'mtime'")
        if sort_order not in {"asc", "desc"}:
            raise ValueError("sort_order must be 'asc' or 'desc'")
        if output == "original":
            return await self._ls_original(
                uri,
                show_all_hidden,
                node_limit,
                sort_by=sort_by,
                sort_order=sort_order,
                ctx=ctx,
            )
        elif output == "agent":
            return await self._ls_agent(
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
        real_ctx = self._ctx_or_default(ctx)
        entry_items = await self._list_read_path_items(uri, ctx=ctx)
        entry_items = self._sort_ls_entry_items(entry_items, sort_by, sort_order)
        # basic info
        fallback_time = datetime.now(timezone.utc)
        all_entries = []
        for entry, entry_uri in entry_items:
            if len(all_entries) >= node_limit:
                break
            name = entry.get("name", "")
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
            if not self._is_accessible(new_entry["uri"], real_ctx):
                continue
            if is_dir:
                all_entries.append(new_entry)
            elif not name.startswith("."):
                all_entries.append(new_entry)
            elif show_all_hidden:
                all_entries.append(new_entry)
        await self._batch_fetch_abstracts(all_entries, abs_limit, ctx=ctx)
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
        real_ctx = self._ctx_or_default(ctx)
        try:
            entry_items = await self._list_read_path_items(uri, ctx=ctx)
            entry_items = self._sort_ls_entry_items(entry_items, sort_by, sort_order)
            # AGFS returns read-only structure, need to create new dict
            all_entries = []
            for entry, entry_uri in entry_items:
                if len(all_entries) >= node_limit:
                    break
                name = entry.get("name", "")
                new_entry = dict(entry)  # Copy original data
                new_entry["uri"] = entry_uri
                if not self._is_accessible(new_entry["uri"], real_ctx):
                    continue
                if entry.get("isDir"):
                    all_entries.append(new_entry)
                elif not name.startswith("."):
                    all_entries.append(new_entry)
                elif show_all_hidden:
                    all_entries.append(new_entry)
            return all_entries
        except Exception:
            raise NotFoundError(uri, "directory")

    async def move_file(
        self,
        from_uri: str,
        to_uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Move file."""
        self._ensure_mutable_access(from_uri, ctx)
        self._ensure_mutable_access(to_uri, ctx)
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
        self._ensure_access(temp_uri, ctx)
        self._ensure_mutable_access(target_uri, ctx)
        src_path = self._uri_to_path(temp_uri, ctx=ctx)
        dst_path = self._uri_to_path(target_uri, ctx=ctx)
        fs_ctx = self._pathlock_fs_ctx(ctx, lease_ref)
        await self._ensure_parent_dirs(dst_path, ctx=ctx, lease_ref=lease_ref)
        await self._async_agfs.cp(
            src_path,
            dst_path,
            recursive=True,
            fs_ctx=fs_ctx or {"account_id": self._ctx_or_default(ctx).account_id},
        )

    async def delete_temp(
        self,
        temp_uri: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Dict[str, Any] | None = None,
    ) -> None:
        """Delete temp directory and its contents."""
        self._ensure_mutable_access(temp_uri, ctx)
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
