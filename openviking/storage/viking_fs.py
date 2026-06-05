# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
VikingFS: OpenViking file system abstraction layer

Encapsulates the AGFS binding client, providing file operation interface based on Viking URI.
Responsibilities:
- URI conversion (viking:// <-> /local/)
- L0/L1 reading (.abstract.md, .overview.md)
- Relation management (.relations.json)
- Semantic search (vector retrieval + rerank)
- Vector sync (sync vector store on rm/mv)
"""

import asyncio
import contextvars
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from openviking.core.namespace import canonicalize_uri
from openviking.core.namespace import (
    is_accessible as namespace_is_accessible,
)
from openviking.core.retrieval_targets import resolve_retrieval_targets
from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs.exceptions import (
    AGFSClientError,
    AGFSDirectoryNotEmptyError,
    AGFSHTTPError,
    AGFSNotSupportedError,
)
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.error_mapping import is_not_found_error, map_exception
from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import PathScope
from openviking.telemetry import get_current_telemetry
from openviking.utils.time_utils import format_simplified, get_current_timestamp, parse_iso_datetime
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

if TYPE_CHECKING:
    from openviking.storage.transaction.lock_handle import LockHandle
    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
    from openviking_cli.utils.config import RerankConfig, RetrievalConfig

logger = get_logger(__name__)


def _ensure_non_empty_search_query(query: str) -> None:
    if not query.strip():
        raise InvalidArgumentError("Search query must not be empty.")


def _is_directory_not_empty_error(message: str) -> bool:
    """Check if an error message indicates a directory not empty error.

    Handles multiple possible error message formats from different backends.
    """
    msg = message.lower()
    return any(
        pattern in msg
        for pattern in [
            "directory not empty",
            "dir not empty",
            "directory is not empty",
        ]
    )


def _get_cpu_count() -> int:
    """Return the number of CPUs available to this process.

    Tries process_cpu_count (Python 3.13+, cgroup-aware),
    falls back to sched_getaffinity (Linux),
    then os.cpu_count (may report host CPUs in containers).
    """
    if hasattr(os, "process_cpu_count"):
        return os.process_cpu_count() or 1
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, NotImplementedError):
        return os.cpu_count() or 1


def _get_abstract_worker_count() -> int:
    default = max(4, min(12, min(32, _get_cpu_count() + 4) // 2))
    env_val = os.getenv("OPENVIKING_FILE_OPS_CONCURRENCY")
    if env_val is not None:
        try:
            return max(1, int(env_val))
        except ValueError:
            pass
    return max(1, default)


_ABSTRACT_WORKER_COUNT = _get_abstract_worker_count()


# ========== Dataclass ==========


@dataclass
class RelationEntry:
    """Relation table entry."""

    id: str
    uris: List[str]
    reason: str = ""
    created_at: str = field(default_factory=get_current_timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "uris": self.uris,
            "reason": self.reason,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "RelationEntry":
        return RelationEntry(**data)


# ========== Singleton Pattern ==========

_instance: Optional["VikingFS"] = None


def init_viking_fs(
    agfs: Any,
    query_embedder: Optional[Any] = None,
    rerank_config: Optional["RerankConfig"] = None,
    vector_store: Optional["VikingVectorIndexBackend"] = None,
    retrieval_config: Optional["RetrievalConfig"] = None,
    timeout: int = 10,
    enable_recorder: bool = False,
    encryptor: Optional[Any] = None,
) -> "VikingFS":
    """Initialize VikingFS singleton.

    Args:
        agfs: Pre-initialized AGFS client (HTTP or Binding)
        agfs_config: AGFS configuration object for backend settings
        query_embedder: Embedder instance
        rerank_config: Rerank configuration
        retrieval_config: Retrieval ranking configuration
        vector_store: Vector store instance
        enable_recorder: Whether to enable IO recording
        encryptor: FileEncryptor instance for encryption/decryption
    """
    global _instance

    _instance = VikingFS(
        agfs=agfs,
        query_embedder=query_embedder,
        rerank_config=rerank_config,
        vector_store=vector_store,
        retrieval_config=retrieval_config,
        encryptor=encryptor,
    )

    if enable_recorder:
        _enable_viking_fs_recorder(_instance)

    return _instance


def _enable_viking_fs_recorder(viking_fs: "VikingFS") -> None:
    """
    Enable recorder for a VikingFS instance.

    This wraps the VikingFS instance with recording capabilities.
    Called automatically when enable_recorder=True in init_viking_fs.

    Args:
        viking_fs: VikingFS instance to enable recording for
    """
    from openviking.eval.recorder import RecordingVikingFS, get_recorder

    recorder = get_recorder()
    if not recorder.enabled:
        from openviking.eval.recorder import init_recorder

        init_recorder(enabled=True)

    global _instance
    _instance = RecordingVikingFS(viking_fs)
    logger.info("[VikingFS] IO Recorder enabled")


def enable_viking_fs_recorder() -> None:
    """
    Enable recorder for the global VikingFS singleton.

    This function wraps the existing VikingFS's AGFS client with recording.
    Must be called after init_viking_fs().
    """
    global _instance
    if _instance is None:
        raise RuntimeError("VikingFS not initialized. Call init_viking_fs() first.")
    _enable_viking_fs_recorder(_instance)


def get_viking_fs() -> "VikingFS":
    """Get VikingFS singleton."""
    if _instance is None:
        raise RuntimeError("VikingFS not initialized. Call init_viking_fs() first.")
    return _instance


# ========== VikingFS Main Class ==========


class VikingFS:
    """RAGFS-based OpenViking file system.

    APIs are divided into two categories:
    - RAGFS basic commands (direct forwarding): read, ls, write, mkdir, rm, mv, grep, stat
    - VikingFS specific capabilities: abstract, overview, find, search, relations, link, unlink

    Uses Rust binding mode: Use RAGFSBindingClient to directly use RAGFS implementation
    """

    def __init__(
        self,
        agfs: Any,
        query_embedder: Optional[Any] = None,
        rerank_config: Optional["RerankConfig"] = None,
        vector_store: Optional["VikingVectorIndexBackend"] = None,
        retrieval_config: Optional["RetrievalConfig"] = None,
        timeout: int = 10,
        encryptor: Optional[Any] = None,
    ):
        self.agfs = agfs
        self._async_agfs = AsyncAGFSClient(agfs)
        self.query_embedder = query_embedder
        self.rerank_config = rerank_config
        self.vector_store = vector_store
        self.retrieval_config = retrieval_config
        self._encryptor = encryptor
        self._bound_ctx: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar(
            "vikingfs_bound_ctx", default=None
        )

    @staticmethod
    def _default_ctx() -> RequestContext:
        return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    def _ctx_or_default(self, ctx: Optional[RequestContext]) -> RequestContext:
        if ctx is not None:
            return ctx
        bound = self._bound_ctx.get()
        return bound or self._default_ctx()

    @contextmanager
    def bind_request_context(self, ctx: RequestContext):
        """Temporarily bind ctx for legacy internal call paths without explicit ctx param."""
        token = self._bound_ctx.set(ctx)
        try:
            yield
        finally:
            self._bound_ctx.reset(token)

    @staticmethod
    def _normalize_uri(uri: str) -> str:
        """Normalize short-format URIs to the canonical viking:// form."""
        if uri.startswith("viking://"):
            return uri
        return VikingURI.normalize(uri)

    @classmethod
    def _normalized_uri_parts(cls, uri: str) -> tuple[str, List[str]]:
        """Normalize a URI and reject ambiguous or platform-specific path traversal forms."""
        normalized = cls._normalize_uri(uri)
        parts = [p for p in normalized[len("viking://") :].strip("/").split("/") if p]

        for part in parts:
            if part in {".", ".."}:
                raise PermissionDeniedError(
                    f"Unsafe URI traversal segment '{part}' in {normalized}",
                    resource=normalized,
                )
            if "\\" in part:
                raise PermissionDeniedError(
                    f"Unsafe URI path separator '\\\\' in component '{part}' of {normalized}",
                    resource=normalized,
                )
            if len(part) >= 2 and part[1] == ":" and part[0].isalpha():
                raise PermissionDeniedError(
                    f"Unsafe URI drive-prefixed component '{part}' in {normalized}",
                    resource=normalized,
                )

        return normalized, parts

    def _ensure_access(self, uri: str, ctx: Optional[RequestContext]) -> None:
        real_ctx = self._ctx_or_default(ctx)
        normalized_uri, _ = self._normalized_uri_parts(uri)
        if not self._is_accessible(normalized_uri, real_ctx):
            raise PermissionDeniedError(f"Access denied for {uri}", resource=normalized_uri)

    def _ensure_mutable_access(self, uri: str, ctx: Optional[RequestContext]) -> None:
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
        normalized_uri, _ = self._normalized_uri_parts(uri)
        if real_ctx.role != Role.ROOT and normalized_uri.rstrip("/") == "viking://temp":
            raise PermissionDeniedError(
                "Temp root is read-only for non-root users",
                resource=normalized_uri,
            )

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
        path = self._uri_to_path(uri, ctx=ctx)

        # Decryption + offset/size slicing now happen inside the ragfs encryption layer
        # (when configured); the plaintext stack reads bytes directly. Either way, pass the
        # offset/size through and let the Rust layer return the requested slice.
        result = await self._async_agfs.read(path, offset, size)
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
    ) -> None:
        """Create directory."""
        self._ensure_mutable_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        # Always ensure parent directories exist before creating this directory
        await self._ensure_parent_dirs(path, ctx=ctx)
        try:
            await self._async_agfs.mkdir(path)
        except Exception as exc:
            message = str(exc).lower()
            already_exists = "exist" in message or "already" in message
            if exist_ok and already_exists:
                return

    async def rm(
        self,
        uri: str,
        recursive: bool = False,
        ctx: Optional[RequestContext] = None,
        lock_handle: Optional["LockHandle"] = None,
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
        from openviking.storage.transaction import LockContext, get_lock_manager

        self._ensure_mutable_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        target_uri = self._path_to_uri(path, ctx=ctx)

        async def _estimate_deleted_count(target_path: str, real_ctx: RequestContext) -> int:
            """Estimate number of nodes to be deleted using vector index."""
            vector_store = self._get_vector_store()
            if not vector_store:
                return 0
            try:
                target_canonical_uri = canonicalize_uri(
                    self._path_to_uri(target_path, ctx=real_ctx), real_ctx
                )
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
            lock_paths = [path]
            lock_mode = "tree"
        else:
            lock_paths = [path]
            lock_mode = "exact"

        try:
            async with LockContext(
                get_lock_manager(),
                lock_paths,
                lock_mode=lock_mode,
                handle=lock_handle,
            ):
                uris_to_delete = await self._collect_uris(path, recursive, ctx=ctx)
                uris_to_delete.append(target_uri)
                real_ctx = self._ctx_or_default(ctx)
                estimated_count = await _estimate_deleted_count(path, real_ctx)
                await self._delete_from_vector_store(uris_to_delete, ctx=ctx)
                try:
                    result = await self._async_agfs.rm(path, recursive=recursive)
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
        except LockAcquisitionError:
            raise ResourceBusyError(f"Resource is being processed: {uri}", uri=uri)

    async def mv(
        self,
        old_uri: str,
        new_uri: str,
        ctx: Optional[RequestContext] = None,
        lock_handle: Optional["LockHandle"] = None,
    ) -> Dict[str, Any]:
        """Move file/directory + recursively update vector index.

        Implemented as cp + rm to avoid lock files being carried by FS mv.
        On VectorDB update failure the copy is cleaned up so the source stays intact.
        """
        from openviking.storage.transaction import LockContext, get_lock_manager

        self._ensure_mutable_access(old_uri, ctx)
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

        lock_context = (
            LockContext(
                get_lock_manager(),
                [old_path],
                lock_mode="mv",
                mv_dst_path=new_path,
                src_is_dir=True,
                handle=lock_handle,
            )
            if is_dir
            else LockContext(
                get_lock_manager(),
                [old_path, new_path],
                lock_mode="exact",
                handle=lock_handle,
            )
        )

        async with lock_context:
            uris_to_move = await self._collect_uris(old_path, recursive=True, ctx=ctx)
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
                )
            except Exception as e:
                if "not found" in str(e).lower():
                    await self._delete_from_vector_store(uris_to_move, ctx=ctx)
                    logger.info(f"[VikingFS] mv source not found, cleaned orphan index: {old_uri}")
                raise

            # Remove carried lock file from the copy (directory only)
            if is_dir:
                carried_lock = new_path.rstrip("/") + "/.path.ovlock"
                try:
                    await self._async_agfs.rm(carried_lock)
                except Exception:
                    pass

            # Update VectorDB URIs (on failure, clean up the copy)
            try:
                await self._update_vector_store_uris(uris_to_move, old_uri, new_uri, ctx=ctx)
            except Exception:
                try:
                    if is_dir:
                        await self._async_agfs.rm(new_path, recursive=True)
                    else:
                        await self._async_agfs.rm(new_path)
                except Exception:
                    pass
                raise

            # Delete source
            await self._async_agfs.rm(old_path, recursive=is_dir)
            return {}

    async def _copy_for_mv(
        self,
        old_uri: str,
        new_uri: str,
        old_path: str,
        new_path: str,
        is_dir: bool,
        is_temp: bool,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Copy source to destination for mv without deleting source."""
        if is_temp:
            await self._async_agfs.cp(old_path, new_path, recursive=is_dir)
            return

        if is_dir:
            await self._copy_dir_through_vikingfs(old_uri, new_uri, ctx=ctx)
        else:
            await self._copy_file_through_vikingfs(old_uri, new_uri, ctx=ctx)

    async def _copy_dir_through_vikingfs(
        self,
        old_uri: str,
        new_uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Recursively copy a directory through VikingFS read/write hooks."""
        await self.mkdir(new_uri, exist_ok=True, ctx=ctx)

        entries = await self.ls(old_uri, show_all_hidden=True, ctx=ctx)
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in (".", ".."):
                continue
            old_child_uri = f"{old_uri.rstrip('/')}/{name}"
            new_child_uri = f"{new_uri.rstrip('/')}/{name}"
            if entry.get("isDir"):
                await self._copy_dir_through_vikingfs(old_child_uri, new_child_uri, ctx=ctx)
            else:
                await self._copy_file_through_vikingfs(old_child_uri, new_child_uri, ctx=ctx)

    async def _copy_file_through_vikingfs(
        self,
        from_uri: str,
        to_uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Copy one file through VikingFS read/write hooks without deleting source."""
        content_bytes = await self.read_file_bytes(from_uri, ctx=ctx)
        await self.write_file_bytes(to_uri, content_bytes, ctx=ctx)

    async def grep(
        self,
        uri: str,
        pattern: str,
        exclude_uri: Optional[str] = None,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        level_limit: int = 5,
        ctx: Optional[RequestContext] = None,
    ) -> Dict:
        """Content search by pattern or keywords.

        The ragfs layer greps transparently over encrypted and plaintext files
        (it decrypts via account_id when an encryption layer is configured).

        Args:
            uri: Viking URI
            pattern: Regular expression pattern to search for
            exclude_uri: Optional URI prefix to exclude from search
            case_insensitive: Whether to perform case-insensitive matching
            node_limit: Maximum number of results to return
            level_limit: Maximum depth level to traverse (default: 5)
            ctx: Request context

        Returns:
            Dict with matches, count, match_count, files_scanned
        """
        self._ensure_access(uri, ctx)
        await self.stat(uri, ctx=ctx)

        return await self._grep_with_agfs(
            uri=uri,
            pattern=pattern,
            exclude_uri=exclude_uri,
            case_insensitive=case_insensitive,
            node_limit=node_limit,
            level_limit=level_limit,
            ctx=ctx,
        )

    async def _grep_with_agfs(
        self,
        uri: str,
        pattern: str,
        exclude_uri: Optional[str] = None,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        level_limit: int = 5,
        ctx: Optional[RequestContext] = None,
    ) -> Dict:
        """Grep using agfs native implementation.

        This is the optimized path for non-encrypted files.
        Uses agfs.grep() which performs matching on the server side.

        Prefer pushing filters down to agfs backend:
        - exclude_uri -> exclude_path
        - level_limit -> level_limit

        Args:
            uri: Viking URI
            pattern: Regular expression pattern to search for
            exclude_uri: Optional URI prefix to exclude from search
            case_insensitive: Whether to perform case-insensitive matching
            node_limit: Maximum number of results to return
            level_limit: Maximum depth level to traverse
            ctx: Request context

        Returns:
            Dict with matches, count, match_count, files_scanned
        """
        path = self._uri_to_path(uri, ctx=ctx)

        excluded_path = None
        if exclude_uri:
            normalized_excluded_uri = self._normalize_uri(exclude_uri).rstrip("/")
            self._ensure_access(normalized_excluded_uri, ctx)
            excluded_path = self._uri_to_path(normalized_excluded_uri, ctx=ctx)

        try:
            result = await self._async_agfs.grep(
                path=path,
                pattern=pattern,
                recursive=True,
                case_insensitive=case_insensitive,
                stream=False,
                node_limit=node_limit,
                exclude_path=excluded_path,
                level_limit=level_limit,
            )
        except (AttributeError, AGFSNotSupportedError, NotImplementedError):
            # Capability missing: let the outer caller fall back to the VikingFS implementation.
            logger.warning("agfs grep unavailable, falling back to VikingFS implementation")
            raise

        matches = result.get("matches", [])
        results = []
        files_scanned_set = set()

        for match in matches:
            match_file = match.get("file", "")
            if not match_file:
                continue

            agfs_file_path = self._resolve_grep_match_agfs_path(path, match_file)

            file_uri = self._path_to_uri(agfs_file_path, ctx=ctx)

            files_scanned_set.add(file_uri)

            results.append(
                {
                    "line": match.get("line", match.get("line_number", 0)),
                    "uri": file_uri,
                    "content": match.get("content", ""),
                }
            )

            if node_limit and len(results) >= node_limit:
                break

        # Prefer backend-provided scanned file count if available; otherwise fall back to
        # counting files that produced at least one match (best-effort).
        backend_files_scanned = result.get("files_scanned")
        if isinstance(backend_files_scanned, int) and backend_files_scanned >= 0:
            files_scanned = backend_files_scanned
        else:
            files_scanned = len(files_scanned_set)

        return {
            "matches": results,
            "count": len(results),
            "match_count": len(results),
            "files_scanned": files_scanned,
        }

    def _resolve_grep_match_agfs_path(self, base_path: str, match_file: str) -> str:
        """Resolve a grep match path (relative to query root) into a full AGFS path."""
        if match_file == ".":
            return base_path
        return f"{base_path.rstrip('/')}/{match_file.lstrip('/')}"

    def _calculate_grep_match_depth(self, match_file: str) -> int:
        """Calculate relative depth from a grep result path relative to the query root."""
        if not match_file or match_file == ".":
            return 0
        return len([part for part in match_file.split("/") if part])

    async def stat(self, uri: str, ctx: Optional[RequestContext] = None) -> Dict[str, Any]:
        """
        File/directory information.

        example: {'name': 'resources', 'size': 128, 'mode': 2147484141, 'modTime': '2026-02-10T21:26:02.934376379+08:00', 'isDir': True, 'isLocked': False, 'count': 42, 'meta': {'Name': 'localfs', 'Type': 'local', 'Content': {'local_path': '...'}}}

        Extra field:
            isLocked (bool): Whether the path is currently held by a path lock
                (either the path itself or any ancestor directory). Returns
                False when the LockManager is not initialized or the lookup
                fails.
            count (int): For directories, the number of nodes in the vector index
                under this directory (including subdirectories). For files, this
                field is not included.
        """
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        result = await self._async_agfs.stat(path)
        if isinstance(result, dict):
            result["isLocked"] = await self._is_path_locked_async(path)
            # Add count for directories if vector store available
            if result.get("isDir", False):
                try:
                    vector_store = self._get_vector_store()
                    if vector_store:
                        real_ctx = self._ctx_or_default(ctx)
                        target_canonical_uri = canonicalize_uri(
                            self._path_to_uri(path, ctx=real_ctx), real_ctx
                        )
                        filter_expr = PathScope("uri", target_canonical_uri, depth=-1)
                        result["count"] = await vector_store.count(filter=filter_expr, ctx=real_ctx)
                except Exception as e:
                    logger.warning(f"[VikingFS] Failed to count nodes for directory stat: {e}")
        return result

    async def _is_path_locked_async(self, path: str) -> bool:
        """Best-effort async path-lock lookup; returns False when LockManager is absent."""
        try:
            from openviking.storage.transaction import get_lock_manager

            return await get_lock_manager().is_path_locked_async(path)
        except Exception:
            return False

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
        entries = await self.tree(uri, node_limit=1000000, level_limit=None, ctx=ctx)
        base_uri = uri.rstrip("/")
        matches = []
        for entry in entries:
            rel_path = entry.get("rel_path", "")
            if PurePath(rel_path).match(pattern):
                matches.append(f"{base_uri}/{rel_path}")
        # Now apply node limit to the filtered matches
        if node_limit is not None and node_limit > 0:
            matches = matches[:node_limit]
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
        [{'uri': 'viking://resources...', 'size': 100, 'isDir': False, 'modTime': '2026-02-11 16:52:16', 'rel_path': '.abstract.md', 'abstract': "..."}]
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
        now = datetime.now(timezone.utc)

        async for entry, entry_uri in self._iter_visible_tree_entries(
            uri,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
            level_limit=level_limit,
            ctx=ctx,
        ):
            info = entry["info"]
            is_dir = info["isDir"]
            result.append(
                {
                    "uri": entry_uri,
                    "size": 0 if is_dir else info["size"],
                    "isDir": is_dir,
                    "modTime": format_simplified(parse_iso_datetime(info["modTime"]), now),
                    "rel_path": entry["rel_path"],
                }
            )

        await self._batch_fetch_abstracts(result, abs_limit, ctx=ctx)

        return result

    # ========== VikingFS Specific Capabilities ==========

    async def _read_abstract_file(
        self,
        path: str,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read and decrypt/decode .abstract.md from a known directory path.

        Does NOT perform stat or isDir check -- caller is responsible for
        ensuring the path points to a directory.
        """
        file_path = f"{path}/.abstract.md"
        try:
            content_bytes = self._handle_agfs_read(await self._async_agfs.read(file_path))
        except Exception as exc:
            if not is_not_found_error(exc):
                mapped = map_exception(exc, resource=uri)
                if mapped is not None:
                    raise mapped from exc
                raise
            return f"# {uri} [Directory abstract is not ready]"

        return self._decode_bytes(content_bytes)

    async def _read_abstract_for_known_dir(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read .abstract.md for a directory that is already known to be a directory.

        Bypasses stat() and isDir check. Caller (i.e. _batch_fetch_abstracts)
        must guarantee that the URI points to a directory.
        """
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        return await self._read_abstract_file(path, uri, ctx=ctx)

    async def abstract(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read directory's L0 summary (.abstract.md).

        If the caller points to a file, its parent directory is used instead so
        the endpoint remains usable for both file and directory URIs.
        """
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        try:
            info = await self._async_agfs.stat(path)
        except Exception as exc:
            mapped = map_exception(exc, resource=uri)
            if mapped is not None:
                raise mapped from exc
            raise
        if not info.get("isDir", info.get("is_dir")):
            parent_path = path.rsplit("/", 1)[0] or "/"
            parent_uri = self._path_to_uri(parent_path, ctx=ctx)
            logger.info(
                "content/abstract: %s is a file, falling back to parent directory %s",
                uri,
                parent_uri,
            )
            return await self.abstract(parent_uri, ctx=ctx)
        return await self._read_abstract_file(path, uri, ctx=ctx)

    async def overview(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read directory's L1 overview (.overview.md).

        If the caller points to a file, its parent directory is used instead so
        the endpoint remains usable for both file and directory URIs.
        """
        self._ensure_access(uri, ctx=ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        try:
            info = await self._async_agfs.stat(path)
        except Exception as exc:
            mapped = map_exception(exc, resource=uri)
            if mapped is not None:
                raise mapped from exc
            raise
        if not info.get("isDir", info.get("is_dir")):
            parent_path = path.rsplit("/", 1)[0] or "/"
            parent_uri = self._path_to_uri(parent_path, ctx=ctx)
            logger.info(
                "content/overview: %s is a file, falling back to parent directory %s",
                uri,
                parent_uri,
            )
            return await self.overview(parent_uri, ctx=ctx)
        file_path = f"{path}/.overview.md"
        try:
            content_bytes = self._handle_agfs_read(await self._async_agfs.read(file_path))
        except Exception as exc:
            if not is_not_found_error(exc):
                mapped = map_exception(exc, resource=uri)
                if mapped is not None:
                    raise mapped from exc
                raise
            # Fallback to default if .overview.md doesn't exist
            return f"# {uri}\n\n[Directory overview is not ready]"

        return self._decode_bytes(content_bytes)

    async def relations(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Get relation list.

        Returns: [{"uri": "...", "reason": "..."}, ...]
        """
        self._ensure_access(uri, ctx)
        entries = await self.get_relation_table(uri, ctx=ctx)
        result = []
        for entry in entries:
            for u in entry.uris:
                if self._is_accessible(u, self._ctx_or_default(ctx)):
                    result.append({"uri": u, "reason": entry.reason})
        return result

    async def find(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        ctx: Optional[RequestContext] = None,
        level: Optional[List[int]] = None,
        peer_id: Optional[str] = None,
    ):
        """Semantic search.

        Args:
            query: Search query
            target_uri: Target directory URI(s), supports str or List[str]
            limit: Return count
            score_threshold: Score threshold
            filter: Metadata filter

        Returns:
            FindResult
        """
        _ensure_non_empty_search_query(query)
        telemetry = get_current_telemetry()
        from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
        from openviking_cli.retrieve import (
            ContextType,
            FindResult,
            TypedQuery,
        )

        real_ctx = self._ctx_or_default(ctx)
        retrieval_targets = resolve_retrieval_targets(target_uri, real_ctx, peer_id)

        for target_dir in retrieval_targets.target_directories:
            self._ensure_access(target_dir, ctx)

        storage = self._get_vector_store()
        if not storage:
            raise RuntimeError("Vector store not initialized. Call OpenViking.initialize() first.")

        embedder = self._get_embedder()
        if not embedder:
            raise RuntimeError("Embedder not configured.")

        retriever = HierarchicalRetriever(
            storage=storage,
            embedder=embedder,
            rerank_config=self.rerank_config,
            retrieval_config=self.retrieval_config,
        )

        typed_query = TypedQuery(
            query=query,
            context_type=None,
            intent="",
            target_directories=retrieval_targets.target_directories,
        )

        logger.debug(
            "[VikingFS.find] Calling retriever.retrieve with "
            f"ctx.account_id={real_ctx.account_id}, ctx.user={real_ctx.user}"
        )

        result = await retriever.retrieve(
            typed_query,
            ctx=real_ctx,
            limit=limit,
            score_threshold=score_threshold,
            scope_dsl=filter,
            level=level,
        )

        # Convert QueryResult to FindResult
        memories, resources, skills = [], [], []
        for ctx in result.matched_contexts:
            if ctx.context_type == ContextType.MEMORY:
                memories.append(ctx)
            elif ctx.context_type == ContextType.RESOURCE:
                resources.append(ctx)
            elif ctx.context_type == ContextType.SKILL:
                skills.append(ctx)

        find_result = FindResult(
            memories=memories,
            resources=resources,
            skills=skills,
        )
        telemetry.set("vector.returned", find_result.total)
        return find_result

    async def search(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        session_info: Optional[Dict] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        ctx: Optional[RequestContext] = None,
        level: Optional[List[int]] = None,
        peer_id: Optional[str] = None,
    ):
        """Complex search with session context.

        Args:
            query: Search query
            target_uri: Target directory URI(s), supports str or List[str]
            session_info: Session information
            limit: Return count
            filter: Metadata filter

        Returns:
            FindResult
        """
        _ensure_non_empty_search_query(query)
        telemetry = get_current_telemetry()
        from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
        from openviking.retrieve.intent_analyzer import IntentAnalyzer
        from openviking_cli.retrieve import (
            ContextType,
            FindResult,
            QueryPlan,
            TypedQuery,
        )

        real_ctx = self._ctx_or_default(ctx)
        retrieval_targets = resolve_retrieval_targets(target_uri, real_ctx, peer_id)
        primary_target_uri = retrieval_targets.first_explicit_directory

        session_summary = (
            str(session_info.get("latest_archive_overview") or "") if session_info else ""
        )
        current_messages = session_info.get("current_messages") if session_info else None

        query_plan: Optional[QueryPlan] = None
        for target_dir in retrieval_targets.target_directories:
            self._ensure_access(target_dir, ctx)

        # When target_uri exists, read its abstract as optional query-planning context.
        target_abstract = ""
        if primary_target_uri:
            try:
                target_abstract = await self.abstract(primary_target_uri, ctx=ctx)
            except Exception:
                target_abstract = ""

        # With session context: intent analysis
        if session_summary or current_messages:
            analyzer = IntentAnalyzer(max_recent_messages=5)
            query_plan = await analyzer.analyze(
                compression_summary=session_summary or "",
                messages=current_messages or [],
                current_message=query,
                target_abstract=target_abstract,
            )
            typed_queries = query_plan.queries
            for tq in typed_queries:
                tq.target_directories = retrieval_targets.target_directories
        else:
            # No session context: create query directly
            typed_queries = [
                TypedQuery(
                    query=query,
                    context_type=None,
                    intent="",
                    priority=1,
                    target_directories=retrieval_targets.target_directories,
                )
            ]
        telemetry.set("search.typed_queries_count", len(typed_queries))

        # Concurrent execution
        storage = self._get_vector_store()
        embedder = self._get_embedder()
        retriever = HierarchicalRetriever(
            storage=storage,
            embedder=embedder,
            rerank_config=self.rerank_config,
            retrieval_config=self.retrieval_config,
        )

        async def _execute(tq: TypedQuery):
            real_ctx = self._ctx_or_default(ctx)
            logger.debug(
                "[VikingFS.search._execute] Calling retriever.retrieve with "
                f"ctx.account_id={real_ctx.account_id}, ctx.user={real_ctx.user}"
            )
            return await retriever.retrieve(
                tq,
                ctx=real_ctx,
                limit=limit,
                score_threshold=score_threshold,
                scope_dsl=filter,
                level=level,
            )

        query_results = await asyncio.gather(*[_execute(tq) for tq in typed_queries])

        # Aggregate results to FindResult
        memories, resources, skills = [], [], []
        for result in query_results:
            for ctx in result.matched_contexts:
                if ctx.context_type == ContextType.MEMORY:
                    memories.append(ctx)
                elif ctx.context_type == ContextType.RESOURCE:
                    resources.append(ctx)
                elif ctx.context_type == ContextType.SKILL:
                    skills.append(ctx)

        find_result = FindResult(
            memories=memories,
            resources=resources,
            skills=skills,
            query_plan=query_plan,
            query_results=query_results,
        )
        telemetry.set("vector.returned", find_result.total)
        return find_result

    # ========== Relation Management ==========

    async def link(
        self,
        from_uri: str,
        uris: Union[str, List[str]],
        reason: str = "",
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Create relation (maintained in .relations.json)."""
        if isinstance(uris, str):
            uris = [uris]
        self._ensure_access(from_uri, ctx)
        for uri in uris:
            self._ensure_access(uri, ctx)

        from_path = self._uri_to_path(from_uri, ctx=ctx)

        entries = await self._read_relation_table(from_path, ctx=ctx)
        existing_ids = {e.id for e in entries}

        link_id = next(f"link_{i}" for i in range(1, 10000) if f"link_{i}" not in existing_ids)

        entries.append(RelationEntry(id=link_id, uris=uris, reason=reason))

        await self._write_relation_table(from_path, entries, ctx=ctx)
        logger.debug(f"[VikingFS] Created link: {from_uri} -> {uris}")

    async def unlink(
        self,
        from_uri: str,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Delete relation."""
        self._ensure_access(from_uri, ctx)
        self._ensure_access(uri, ctx)
        from_path = self._uri_to_path(from_uri, ctx=ctx)

        try:
            entries = await self._read_relation_table(from_path, ctx=ctx)

            entry_to_modify = None
            for entry in entries:
                if uri in entry.uris:
                    entry_to_modify = entry
                    break

            if not entry_to_modify:
                logger.debug(f"[VikingFS] URI not found in relations: {uri}")
                return

            entry_to_modify.uris.remove(uri)

            if not entry_to_modify.uris:
                entries.remove(entry_to_modify)
                logger.debug(f"[VikingFS] Removed empty entry: {entry_to_modify.id}")

            await self._write_relation_table(from_path, entries, ctx=ctx)
            logger.debug(f"[VikingFS] Removed link: {from_uri} -> {uri}")

        except Exception as e:
            logger.error(f"[VikingFS] Failed to unlink {from_uri} -> {uri}: {e}")
            raise IOError(f"Failed to unlink: {e}")

    async def get_relation_table(
        self, uri: str, ctx: Optional[RequestContext] = None
    ) -> List[RelationEntry]:
        """Get relation table."""
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        return await self._read_relation_table(path, ctx=ctx)

    # ========== Tree Traversal (Refactored) ==========

    def _is_name_visible_at_path(self, name: str, parent_path: str) -> bool:
        """Check if name would appear in _ls_entries(parent_path).

        At account root (/local/{account}), uses LISTABLE_SCOPES whitelist.
        At other levels, uses _INTERNAL_NAMES blacklist.
        """
        parts = [p for p in parent_path.strip("/").split("/") if p]
        if len(parts) == 2 and parts[0] == "local":
            return name in VikingURI.LISTABLE_SCOPES
        return name not in self._INTERNAL_NAMES

    def _ancestor_is_filtered(self, entry_path: str, base_path: str) -> bool:
        """Check if any ancestor directory of entry_path would be filtered by _ls_entries.

        Walks from base_path (exclusive) to entry's parent directory (exclusive),
        checking each component against _is_name_visible_at_path.
        """
        base_parts = [p for p in base_path.strip("/").split("/") if p]
        entry_parts = [p for p in entry_path.strip("/").split("/") if p]

        for i in range(len(base_parts), len(entry_parts) - 1):
            name = entry_parts[i]
            parent_parts = entry_parts[:i]
            parent_path = "/" + "/".join(parent_parts) if parent_parts else "/"
            if not self._is_name_visible_at_path(name, parent_path):
                return True
        return False

    def _is_tree_entry_visible(
        self, entry: Dict[str, Any], base_path: str, ctx: RequestContext
    ) -> bool:
        """Check visibility for a single TreeEntry returned by Rust tree_directory.

        Applies three layers of filtering:
        1. Ancestor chain — if any ancestor directory would be filtered by _ls_entries,
           all descendants are invisible.
        2. Self — the entry's own name must pass _ls_entries at its parent level.
        3. ACL — the entry must be accessible by the requesting context.
        """
        entry_path = entry["path"]

        if self._ancestor_is_filtered(entry_path, base_path):
            return False

        entry_parts = [p for p in entry_path.strip("/").split("/") if p]
        if entry_parts:
            name = entry_parts[-1]
            parent_parts = entry_parts[:-1]
            parent_path = "/" + "/".join(parent_parts) if parent_parts else "/"
            if not self._is_name_visible_at_path(name, parent_path):
                return False

        uri = self._path_to_uri(entry_path, ctx=ctx)
        if not self._is_accessible(uri, ctx):
            return False

        return True

    # Over-fetch multiplier for bounded tree traversal. When a node_limit is
    # set, we push down node_limit * this factor as the raw-node limit to Rust,
    # leaving headroom for ACL/internal-name filtering before re-fetching.
    _TREE_OVERFETCH_FACTOR = 4

    async def _iter_visible_tree_entries(
        self,
        uri: str,
        show_all_hidden: bool = False,
        node_limit: Optional[int] = None,
        level_limit: Optional[int] = None,
        ctx: Optional[RequestContext] = None,
    ):
        """Shared generator: fetch raw TreeEntry list from Rust, yield (entry, uri) tuples.

        node_limit counts ACL-visible entries (see design §6.5), so the user's
        node_limit cannot be pushed directly to Rust — doing so would truncate
        before filtering and drop entries that should be visible.

        To keep memory bounded without changing that semantic, we push down an
        *amplified* raw-node limit (node_limit * _TREE_OVERFETCH_FACTOR). If ACL
        filtering leaves fewer than node_limit visible entries while Rust still
        returned a full page (i.e. more raw nodes may exist), we double the raw
        limit and re-fetch. Because Rust truncates a deterministic sorted prefix,
        this yields exactly the same result as an unbounded fetch, while avoiding
        materializing the entire prefix in the common case.

        When node_limit is None (full-tree callers), no limit is pushed down.
        level_limit IS always passed to Rust.
        """
        path = self._uri_to_path(uri, ctx=ctx)
        real_ctx = self._ctx_or_default(ctx)

        if node_limit is None:
            raw_limit: Optional[int] = None
        else:
            raw_limit = max(node_limit * self._TREE_OVERFETCH_FACTOR, node_limit)

        while True:
            raw_entries = await self._async_agfs.tree_directory(
                path,
                show_hidden=show_all_hidden,
                node_limit=raw_limit,
                level_limit=level_limit,
            )

            visible: List[tuple] = []
            for entry in raw_entries:
                if node_limit is not None and len(visible) >= node_limit:
                    break
                if not self._is_tree_entry_visible(entry, path, real_ctx):
                    continue
                entry_uri = self._path_to_uri(entry["path"], ctx=ctx)
                visible.append((entry, entry_uri))

            # If we still lack enough visible entries but Rust returned a full
            # page (raw_limit reached), more raw nodes may exist — re-fetch with
            # a doubled limit. Otherwise Rust is exhausted and we yield as-is.
            need_more = (
                node_limit is not None
                and len(visible) < node_limit
                and raw_limit is not None
                and len(raw_entries) >= raw_limit
            )
            if need_more:
                raw_limit *= 2
                continue

            for item in visible:
                yield item
            return

    # ========== URI Conversion ==========

    # Maximum bytes for a single filename component (filesystem limit is typically 255)
    _MAX_FILENAME_BYTES = 255

    @staticmethod
    def _shorten_component(component: str, max_bytes: int = 255) -> str:
        """Shorten a path component if its UTF-8 encoding exceeds max_bytes."""
        if len(component.encode("utf-8")) <= max_bytes:
            return component
        hash_suffix = hashlib.sha256(component.encode("utf-8")).hexdigest()[:8]
        # Trim to fit within max_bytes after adding hash suffix
        prefix = component
        target = max_bytes - len(f"_{hash_suffix}".encode("utf-8"))
        while len(prefix.encode("utf-8")) > target and prefix:
            prefix = prefix[:-1]
        return f"{prefix}_{hash_suffix}"

    _USER_STRUCTURE_DIRS = {"memories"}
    _AGENT_STRUCTURE_DIRS = {"memories", "skills", "instructions", "workspaces"}

    def _uri_to_path(self, uri: str, ctx: Optional[RequestContext] = None) -> str:
        """Map virtual URI to account-isolated AGFS path.

        Pure prefix replacement: viking://{remainder} -> /local/{account_id}/{remainder}.
        No implicit space injection — URIs must include space segments explicitly.
        """
        real_ctx = self._ctx_or_default(ctx)
        account_id = real_ctx.account_id
        canonical_uri = canonicalize_uri(uri, real_ctx)
        _, parts = self._normalized_uri_parts(canonical_uri)
        if not parts:
            return f"/local/{account_id}"

        safe_parts = [self._shorten_component(p, self._MAX_FILENAME_BYTES) for p in parts]
        return f"/local/{account_id}/{'/'.join(safe_parts)}"

    _INTERNAL_NAMES = {"_system", "tasks", ".path.ovlock"}
    _ROOT_PATH = "/local"

    async def _ls_entries(
        self, path: str, ctx: Optional[RequestContext] = None
    ) -> List[Dict[str, Any]]:
        """List directory entries, filtering out internal directories.

        At account root (/local/{account}), uses LISTABLE_SCOPES whitelist.
        At other levels, uses _INTERNAL_NAMES blacklist.
        """
        entries = await self._async_agfs.ls(path)
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) == 2 and parts[0] == "local":
            return [e for e in entries if e.get("name") in VikingURI.LISTABLE_SCOPES]
        return [e for e in entries if e.get("name") not in self._INTERNAL_NAMES]

    def _path_to_uri(self, path: str, ctx: Optional[RequestContext] = None) -> str:
        """/local/{account}/... -> viking://...

        Pure prefix replacement: strips /local/{account_id}/ and prepends viking://.
        No implicit space stripping.
        """
        if path.startswith("viking://"):
            return path
        elif path.startswith("/local/"):
            inner = path[7:].strip("/")
            if not inner:
                return "viking://"
            real_ctx = self._ctx_or_default(ctx)
            parts = [p for p in inner.split("/") if p]
            if parts and parts[0] == real_ctx.account_id:
                parts = parts[1:]
            if not parts:
                return "viking://"
            return f"viking://{'/'.join(parts)}"
        elif path.startswith("/"):
            return f"viking:/{path}"
        else:
            return f"viking://{path}"

    def _looks_like_legacy_temp_leaf(self, value: str) -> bool:
        return bool(re.match(r"^\d{8}_[0-9a-f]{6}$", value or ""))

    def _is_legacy_temp_uri_parts(self, parts: List[str]) -> bool:
        if len(parts) < 2 or parts[0] != "temp" or not self._looks_like_legacy_temp_leaf(parts[1]):
            return False
        if len(parts) == 2:
            return True
        return not self._looks_like_legacy_temp_leaf(parts[2])

    def _extract_space_from_uri(self, uri: str) -> Optional[str]:
        """Extract space segment from URI if present.

        URIs are WYSIWYG: viking://{scope}/{space}/...
        For user, the second segment is space unless it's a known structure dir.
        For session, the second segment is always space (when 3+ parts).
        Legacy temp URIs keep the historical shape viking://temp/<temp-id> and therefore
        intentionally have no space segment.
        """
        _, parts = self._normalized_uri_parts(uri)
        if len(parts) < 2:
            return None
        scope = parts[0]
        second = parts[1]
        # Treat scope-root metadata files as not having a tenant space segment.
        if len(parts) == 2 and second in {".abstract.md", ".overview.md"}:
            return None
        if self._is_legacy_temp_uri_parts(parts):
            return None
        if scope == "upload":
            return None
        if scope == "user" and second not in self._USER_STRUCTURE_DIRS:
            return second
        if scope == "agent" and second not in self._AGENT_STRUCTURE_DIRS:
            return second
        if scope == "session" and len(parts) >= 2:
            return second
        return None

    def _is_accessible(self, uri: str, ctx: RequestContext) -> bool:
        """Check whether a URI is visible/accessible under current request context."""
        normalized_uri, parts = self._normalized_uri_parts(uri)
        if ctx.role == Role.ROOT:
            return True
        if not parts:
            return True
        if is_watch_task_control_uri(normalized_uri):
            return False

        scope = parts[0]
        if scope == "resources":
            return True
        if scope == "temp":
            if len(parts) == 1:
                return True
            if parts[1] == ctx.user.user_space_name():
                return True
            return self._is_legacy_temp_uri_parts(parts)
        if scope == "upload":
            return ctx.role == Role.ROOT
        if scope == "_system":
            return False
        return namespace_is_accessible(normalized_uri, ctx)

    def _handle_agfs_read(self, result: Union[bytes, Any, None]) -> bytes:
        """Handle AGFSClient read return types consistently."""
        if isinstance(result, bytes):
            return result
        elif result is None:
            return b""
        elif hasattr(result, "content") and result.content is not None:
            return result.content
        else:
            # Try to convert to bytes
            try:
                return str(result).encode("utf-8")
            except Exception:
                return b""

    def _decode_bytes(self, data: bytes) -> str:
        """Robustly decode bytes to string."""
        if not data:
            return ""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                # Try common encoding for Windows/legacy files in China
                return data.decode("gbk")
            except UnicodeDecodeError:
                try:
                    return data.decode("latin-1")
                except UnicodeDecodeError:
                    return data.decode("utf-8", errors="replace")

    def _handle_agfs_content(self, result: Union[bytes, Any, None]) -> str:
        """Handle AGFSClient content return types consistently."""
        if isinstance(result, bytes):
            return self._decode_bytes(result)
        elif hasattr(result, "content") and result.content is not None:
            return self._decode_bytes(result.content)
        elif result is None:
            return ""
        else:
            # Try to convert to string
            try:
                return str(result)
            except Exception:
                return ""

    # ========== Vector Sync Helper Methods ==========

    async def _collect_uris(
        self, path: str, recursive: bool, ctx: Optional[RequestContext] = None
    ) -> List[str]:
        """Recursively collect all URIs (for rm/mv), including directories."""
        uris = []

        async def _collect(p: str):
            try:
                for entry in await self._ls_entries(p, ctx=ctx):
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
            except Exception:
                pass

        await _collect(path)
        return uris

    async def _delete_from_vector_store(
        self, uris: List[str], ctx: Optional[RequestContext] = None
    ) -> None:
        """Delete records with specified URIs from vector store.

        Uses tenant-safe URI deletion semantics from vector store.
        """
        vector_store = self._get_vector_store()
        if not vector_store:
            return
        real_ctx = self._ctx_or_default(ctx)

        try:
            await vector_store.delete_uris(real_ctx, uris)
            for uri in uris:
                logger.debug(f"[VikingFS] Deleted from vector store: {uri}")
        except Exception as e:
            logger.warning(f"[VikingFS] Failed to delete from vector store: {e}")

    async def _update_vector_store_uris(
        self,
        uris: List[str],
        old_base: str,
        new_base: str,
        ctx: Optional[RequestContext] = None,
        levels: Optional[List[int]] = None,
    ) -> None:
        """Update URIs in vector store (when moving files).

        Preserves vector data and updates URI-derived identifiers without regenerating embeddings.
        """
        vector_store = self._get_vector_store()
        if not vector_store:
            return

        old_base_uri = self._path_to_uri(old_base, ctx=ctx)
        new_base_uri = self._path_to_uri(new_base, ctx=ctx)

        for uri in uris:
            try:
                new_uri = uri.replace(old_base_uri, new_base_uri, 1)

                await vector_store.update_uri_mapping(
                    ctx=self._ctx_or_default(ctx),
                    uri=uri,
                    new_uri=new_uri,
                    levels=levels,
                )
                logger.debug(f"[VikingFS] Updated URI: {uri} -> {new_uri}")
            except Exception as e:
                logger.warning(f"[VikingFS] Failed to update {uri} in vector store: {e}")

    async def _mv_vector_store_l0_l1(
        self,
        old_uri: str,
        new_uri: str,
        ctx: Optional[RequestContext] = None,
        lock_handle: Optional["LockHandle"] = None,
    ) -> None:
        from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
        from openviking.storage.transaction import LockContext, get_lock_manager

        self._ensure_access(old_uri, ctx)
        self._ensure_access(new_uri, ctx)

        real_ctx = self._ctx_or_default(ctx)
        old_dir = VikingURI.normalize(old_uri).rstrip("/")
        new_dir = VikingURI.normalize(new_uri).rstrip("/")
        if old_dir == new_dir:
            return

        for uri in (old_dir, new_dir):
            if uri.endswith(("/.abstract.md", "/.overview.md")):
                raise ValueError(f"mv_vector_store expects directory URIs, got: {uri}")

        try:
            old_stat = await self.stat(old_dir, ctx=real_ctx)
        except Exception as e:
            raise FileNotFoundError(f"mv_vector_store old_uri not found: {old_dir}") from e
        try:
            new_stat = await self.stat(new_dir, ctx=real_ctx)
        except Exception as e:
            raise FileNotFoundError(f"mv_vector_store new_uri not found: {new_dir}") from e

        if not (isinstance(old_stat, dict) and old_stat.get("isDir", False)):
            raise ValueError(f"mv_vector_store expects old_uri to be a directory: {old_dir}")
        if not (isinstance(new_stat, dict) and new_stat.get("isDir", False)):
            raise ValueError(f"mv_vector_store expects new_uri to be a directory: {new_dir}")

        old_path = self._uri_to_path(old_dir, ctx=real_ctx)
        new_path = self._uri_to_path(new_dir, ctx=real_ctx)

        try:
            async with LockContext(
                get_lock_manager(),
                [old_path],
                lock_mode="mv",
                mv_dst_path=new_path,
                src_is_dir=True,
                handle=lock_handle,
            ):
                await self._update_vector_store_uris(
                    uris=[old_dir],
                    old_base=old_dir,
                    new_base=new_dir,
                    ctx=real_ctx,
                    levels=[0, 1],
                )

        except LockAcquisitionError:
            raise ResourceBusyError(f"Resource is being processed: {old_dir}", uri=old_dir)

    def _get_vector_store(self) -> Optional["VikingVectorIndexBackend"]:
        """Get vector store instance."""
        return self.vector_store

    def _get_embedder(self) -> Any:
        """Get embedder instance."""
        return self.query_embedder

    # ========== Parent Directory Creation ==========

    async def _ensure_parent_dirs(self, path: str, ctx: Optional[RequestContext] = None) -> None:
        """Recursively create all parent directories."""
        try:
            await self._async_agfs.ensure_parent_dirs(path)
        except Exception as e:
            logger.debug(f"Failed to ensure parent directories for {path}: {e}")
            parent = path.rstrip("/").rsplit("/", 1)[0]
            await self._mkdir_path_with_parents(parent, ctx=ctx)

    async def _mkdir_path_with_parents(
        self, dir_path: str, ctx: Optional[RequestContext] = None
    ) -> None:
        """Create a directory path segment-by-segment using the same fs context."""
        parts = [part for part in dir_path.strip("/").split("/") if part]
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                await self._async_agfs.mkdir(current)
            except Exception as e:
                message = str(e).lower()
                if "exist" in message or "already" in message:
                    continue
                logger.debug(f"Failed to create parent directory {current}: {e}")

    # ========== Relation Table Internal Methods ==========

    async def _read_relation_table(
        self, dir_path: str, ctx: Optional[RequestContext] = None
    ) -> List[RelationEntry]:
        """Read .relations.json."""
        table_path = f"{dir_path}/.relations.json"
        try:
            content = self._handle_agfs_read(await self._async_agfs.read(table_path))
            data = json.loads(content.decode("utf-8"))
        except FileNotFoundError:
            return []
        except Exception:
            # logger.warning(f"[VikingFS] Failed to read relation table {table_path}: {e}")
            return []

        entries = []
        # Compatible with old format (nested) and new format (flat)
        if isinstance(data, list):
            # New format: flat list
            for entry_data in data:
                entries.append(RelationEntry.from_dict(entry_data))
        elif isinstance(data, dict):
            # Old format: nested {namespace: {user: [entries]}}
            for _namespace, user_dict in data.items():
                for _user, entry_list in user_dict.items():
                    for entry_data in entry_list:
                        entries.append(RelationEntry.from_dict(entry_data))
        return entries

    async def _write_relation_table(
        self, dir_path: str, entries: List[RelationEntry], ctx: Optional[RequestContext] = None
    ) -> None:
        """Write .relations.json."""
        # Use flat list format
        data = [entry.to_dict() for entry in entries]

        content = json.dumps(data, ensure_ascii=False, indent=2)
        table_path = f"{dir_path}/.relations.json"
        if isinstance(content, str):
            content = content.encode("utf-8")

        await self._async_agfs.write(table_path, content)

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
    ) -> None:
        """Write file directly."""
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        await self._ensure_parent_dirs(path, ctx=ctx)

        if isinstance(content, str):
            content = content.encode("utf-8")

        await self._async_agfs.write(path, content)

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
        path = self._uri_to_path(uri, ctx=ctx)
        # Verify the file exists before reading, because AGFS read returns
        # empty bytes for non-existent files instead of raising an error.
        try:
            stat = await self._async_agfs.stat(path)
        except Exception:
            raise NotFoundError(uri, "file")
        if isinstance(stat, dict) and stat.get("isDir", False):
            raise InvalidArgumentError(
                f"Cannot read directory as file: {uri}",
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
        path = self._uri_to_path(uri, ctx=ctx)
        try:
            stat = await self._async_agfs.stat(path)
        except Exception:
            raise NotFoundError(uri, "file")
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
    ) -> None:
        """Write single binary file."""
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        await self._ensure_parent_dirs(path, ctx=ctx)

        await self._async_agfs.write(path, content)

    async def append_file(
        self,
        uri: str,
        content: str,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Append content to file."""
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)

        try:
            existing = ""
            try:
                existing_bytes = self._handle_agfs_read(await self._async_agfs.read(path))
                existing = self._decode_bytes(existing_bytes)
            except FileNotFoundError:
                pass
            except AGFSHTTPError as e:
                if e.status_code != 404:
                    raise
            except AGFSClientError:
                raise

            await self._ensure_parent_dirs(path, ctx=ctx)
            final_content = (existing + content).encode("utf-8")
            await self._async_agfs.write(path, final_content)

        except Exception as e:
            logger.error(f"[VikingFS] Failed to append to file {uri}: {e}")
            raise IOError(f"Failed to append to file {uri}: {e}")

    async def ls(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
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

        output="original"
        [{'name': '.abstract.md', 'size': 100, 'mode': 420, 'modTime': '2026-02-11T16:52:16.256334192+08:00', 'isDir': False, 'meta': {'Name': 'localfs', 'Type': 'local', 'Content': None}, 'uri': 'viking://resources/.abstract.md'}]

        output="agent"
        [{'name': '.abstract.md', 'size': 100, 'modTime': '2026-02-11(or 16:52:16 for today)', 'isDir': False, 'uri': 'viking://resources/.abstract.md', 'abstract': "..."}]
        """
        self._ensure_access(uri, ctx)
        if output == "original":
            return await self._ls_original(uri, show_all_hidden, node_limit, ctx=ctx)
        elif output == "agent":
            return await self._ls_agent(uri, abs_limit, show_all_hidden, node_limit, ctx=ctx)
        else:
            raise ValueError(f"Invalid output format: {output}")

    async def _ls_agent(
        self,
        uri: str,
        abs_limit: int,
        show_all_hidden: bool,
        node_limit: int = 1000,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """List directory contents (URI version)."""
        path = self._uri_to_path(uri, ctx=ctx)
        real_ctx = self._ctx_or_default(ctx)
        try:
            entries = await self._ls_entries(path, ctx=ctx)
        except Exception:
            raise NotFoundError(uri, "directory")
        # basic info
        now = datetime.now(timezone.utc)
        all_entries = []
        for entry in entries:
            if len(all_entries) >= node_limit:
                break
            name = entry.get("name", "")
            raw_time = entry.get("modTime", "")
            parsed_time = now
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
                "uri": self._path_to_uri(f"{path}/{name}", ctx=ctx),
                "size": 0 if is_dir else entry.get("size", 0),
                "isDir": is_dir,
                "modTime": format_simplified(parsed_time, now),
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
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """List directory contents (URI version)."""
        path = self._uri_to_path(uri, ctx=ctx)
        real_ctx = self._ctx_or_default(ctx)
        try:
            entries = await self._ls_entries(path, ctx=ctx)
            # AGFS returns read-only structure, need to create new dict
            all_entries = []
            for entry in entries:
                if len(all_entries) >= node_limit:
                    break
                name = entry.get("name", "")
                new_entry = dict(entry)  # Copy original data
                new_entry["uri"] = self._path_to_uri(f"{path}/{name}", ctx=ctx)
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
    ) -> None:
        """Persist an already-encrypted temp tree without rewriting file bytes."""
        self._ensure_access(temp_uri, ctx)
        self._ensure_mutable_access(target_uri, ctx)
        src_path = self._uri_to_path(temp_uri, ctx=ctx)
        dst_path = self._uri_to_path(target_uri, ctx=ctx)
        await self._ensure_parent_dirs(dst_path, ctx=ctx)
        await self._async_agfs.cp(src_path, dst_path, recursive=True)

    async def delete_temp(self, temp_uri: str, ctx: Optional[RequestContext] = None) -> None:
        """Delete temp directory and its contents."""
        self._ensure_mutable_access(temp_uri, ctx)
        path = self._uri_to_path(temp_uri, ctx=ctx)
        try:
            for entry in await self._ls_entries(path, ctx=ctx):
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                entry_path = f"{path}/{name}"
                if entry.get("isDir"):
                    await self.delete_temp(f"{temp_uri}/{name}", ctx=ctx)
                else:
                    await self._async_agfs.rm(entry_path)
            await self._async_agfs.rm(path)
        except Exception as e:
            logger.warning(f"[VikingFS] Failed to delete temp {temp_uri}: {e}")

    async def get_relations(self, uri: str, ctx: Optional[RequestContext] = None) -> List[str]:
        """Get all related URIs (backward compatible)."""
        entries = await self.get_relation_table(uri, ctx=ctx)
        all_uris = []
        for entry in entries:
            for related in entry.uris:
                if self._is_accessible(related, self._ctx_or_default(ctx)):
                    all_uris.append(related)
        return all_uris

    async def get_relations_with_content(
        self,
        uri: str,
        include_l0: bool = True,
        include_l1: bool = False,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Get related URIs and their content (backward compatible)."""
        relation_uris = await self.get_relations(uri, ctx=ctx)
        if not relation_uris:
            return []

        results = []
        abstracts = {}
        overviews = {}
        if include_l0:
            abstracts = await self.read_batch(relation_uris, level="l0", ctx=ctx)
        if include_l1:
            overviews = await self.read_batch(relation_uris, level="l1", ctx=ctx)

        for rel_uri in relation_uris:
            info = {"uri": rel_uri}
            if include_l0:
                info["abstract"] = abstracts.get(rel_uri, "")
            if include_l1:
                info["overview"] = overviews.get(rel_uri, "")
            results.append(info)

        return results

    async def write_context(
        self,
        uri: str,
        content: Union[str, bytes] = "",
        abstract: str = "",
        overview: str = "",
        content_filename: str = "content.md",
        is_leaf: bool = False,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Write context to AGFS (L0/L1/L2)."""

        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)

        try:
            await self._ensure_parent_dirs(path, ctx=ctx)
            try:
                await self._async_agfs.mkdir(path)
            except Exception as e:
                if "exist" not in str(e).lower():
                    raise

            if content:
                content_uri = f"{uri}/{content_filename}"
                await self.write_file(content_uri, content, ctx=ctx)

            if abstract:
                abstract_uri = f"{uri}/.abstract.md"
                await self.write_file(abstract_uri, abstract, ctx=ctx)

            if overview:
                overview_uri = f"{uri}/.overview.md"
                await self.write_file(overview_uri, overview, ctx=ctx)

        except Exception as e:
            logger.error(f"[VikingFS] Failed to write {uri}: {e}")
            raise IOError(f"Failed to write {uri}: {e}")
