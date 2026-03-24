# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
VikingFS: OpenViking file system abstraction layer

Encapsulates AGFSClient, providing file operation interface based on Viking URI.
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
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from openviking.server.identity import RequestContext, Role
from openviking.telemetry import get_current_telemetry
from openviking.utils.time_utils import format_simplified, get_current_timestamp, parse_iso_datetime
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

if TYPE_CHECKING:
    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
    from openviking_cli.utils.config import RerankConfig

logger = get_logger(__name__)


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
    """AGFS-based OpenViking file system.

    APIs are divided into two categories:
    - AGFS basic commands (direct forwarding): read, ls, write, mkdir, rm, mv, grep, stat
    - VikingFS specific capabilities: abstract, overview, find, search, relations, link, unlink

    Supports two modes:
    - HTTP mode: Use AGFSClient to connect to AGFS server via HTTP
    - Binding mode: Use AGFSBindingClient to directly use AGFS implementation
    """

    def __init__(
        self,
        agfs: Any,
        query_embedder: Optional[Any] = None,
        rerank_config: Optional["RerankConfig"] = None,
        vector_store: Optional["VikingVectorIndexBackend"] = None,
        timeout: int = 10,
        encryptor: Optional[Any] = None,
    ):
        self.agfs = agfs
        self.query_embedder = query_embedder
        self.rerank_config = rerank_config
        self.vector_store = vector_store
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

    async def _encrypt_content(self, content: bytes, ctx: Optional[RequestContext] = None) -> bytes:
        """Encrypt content if encryption is enabled."""
        if not self._encryptor:
            return content
        real_ctx = self._ctx_or_default(ctx)
        return await self._encryptor.encrypt(real_ctx.account_id, content)

    async def _decrypt_content(self, content: bytes, ctx: Optional[RequestContext] = None) -> bytes:
        """Decrypt content if encryption is enabled."""
        if not self._encryptor:
            return content
        real_ctx = self._ctx_or_default(ctx)
        return await self._encryptor.decrypt(real_ctx.account_id, content)

    async def encrypt_bytes(self, account_id: str, data: bytes) -> bytes:
        """
        Encrypt bytes using the encryptor for the specified account.

        Args:
            account_id: Account ID to use for encryption
            data: Bytes to encrypt

        Returns:
            Encrypted bytes, or original bytes if encryption is disabled
        """
        if not self._encryptor:
            return data
        return await self._encryptor.encrypt(account_id, data)

    async def decrypt_bytes(self, account_id: str, data: bytes) -> bytes:
        """
        Decrypt bytes using the encryptor for the specified account.

        Args:
            account_id: Account ID to use for decryption
            data: Bytes to decrypt

        Returns:
            Decrypted bytes, or original bytes if encryption is disabled
        """
        if not self._encryptor:
            return data
        return await self._encryptor.decrypt(account_id, data)

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
                raise PermissionError(f"Unsafe URI traversal segment '{part}' in {normalized}")
            if "\\" in part:
                raise PermissionError(
                    f"Unsafe URI path separator '\\\\' in component '{part}' of {normalized}"
                )
            if len(part) >= 2 and part[1] == ":" and part[0].isalpha():
                raise PermissionError(
                    f"Unsafe URI drive-prefixed component '{part}' in {normalized}"
                )

        return normalized, parts

    def _ensure_access(self, uri: str, ctx: Optional[RequestContext]) -> None:
        real_ctx = self._ctx_or_default(ctx)
        normalized_uri, _ = self._normalized_uri_parts(uri)
        if not self._is_accessible(normalized_uri, real_ctx):
            raise PermissionError(f"Access denied for {uri}")

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

        if self._encryptor:
            # When encryption is enabled: must read entire file for decryption
            result = self.agfs.read(path, 0, -1)
            if isinstance(result, bytes):
                raw = result
            elif result is not None and hasattr(result, "content"):
                raw = result.content
            else:
                raw = b""

            raw = await self._decrypt_content(raw, ctx=ctx)

            # Apply slicing on decrypted plaintext
            if offset > 0 or size != -1:
                if size != -1:
                    raw = raw[offset : offset + size]
                else:
                    raw = raw[offset:]
        else:
            # When not encrypted: normal read
            result = self.agfs.read(path, offset, size)
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
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        if isinstance(data, str):
            data = data.encode("utf-8")

        data = await self._encrypt_content(data, ctx=ctx)
        return self.agfs.write(path, data)

    async def mkdir(
        self,
        uri: str,
        mode: str = "755",
        exist_ok: bool = False,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Create directory."""
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        # Always ensure parent directories exist before creating this directory
        await self._ensure_parent_dirs(path)

        if exist_ok:
            try:
                await self.stat(uri, ctx=ctx)
                return None
            except Exception:
                pass

        self.agfs.mkdir(path)

    async def rm(
        self, uri: str, recursive: bool = False, ctx: Optional[RequestContext] = None
    ) -> Dict[str, Any]:
        """Delete file/directory + recursively update vector index.

        This method is idempotent: deleting a non-existent file succeeds
        after cleaning up any orphan index records.

        Acquires a path lock, deletes VectorDB records, then FS files.
        Raises ResourceBusyError when the target is locked by an ongoing
        operation (e.g. semantic processing).
        """
        from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
        from openviking.storage.transaction import LockContext, get_lock_manager

        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        target_uri = self._path_to_uri(path, ctx=ctx)

        # Check existence and determine lock strategy
        try:
            stat = self.agfs.stat(path)
            is_dir = stat.get("isDir", False) if isinstance(stat, dict) else False
        except Exception:
            # Path does not exist: clean up any orphan index records and return
            uris_to_delete = await self._collect_uris(path, recursive, ctx=ctx)
            uris_to_delete.append(target_uri)
            await self._delete_from_vector_store(uris_to_delete, ctx=ctx)
            logger.info(f"[VikingFS] rm target not found, cleaned orphan index: {uri}")
            return {}

        if is_dir:
            lock_paths = [path]
            lock_mode = "subtree"
        else:
            parent = path.rsplit("/", 1)[0] if "/" in path else path
            lock_paths = [parent]
            lock_mode = "point"

        try:
            async with LockContext(get_lock_manager(), lock_paths, lock_mode=lock_mode):
                uris_to_delete = await self._collect_uris(path, recursive, ctx=ctx)
                uris_to_delete.append(target_uri)
                await self._delete_from_vector_store(uris_to_delete, ctx=ctx)
                result = self.agfs.rm(path, recursive=recursive)
                return result
        except LockAcquisitionError:
            raise ResourceBusyError(f"Resource is being processed: {uri}")

    async def mv(
        self,
        old_uri: str,
        new_uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        """Move file/directory + recursively update vector index.

        Implemented as cp + rm to avoid lock files being carried by FS mv.
        On VectorDB update failure the copy is cleaned up so the source stays intact.
        """
        from openviking.pyagfs.helpers import cp as agfs_cp
        from openviking.storage.transaction import LockContext, get_lock_manager

        self._ensure_access(old_uri, ctx)
        self._ensure_access(new_uri, ctx)
        old_path = self._uri_to_path(old_uri, ctx=ctx)
        new_path = self._uri_to_path(new_uri, ctx=ctx)
        target_uri = self._path_to_uri(old_path, ctx=ctx)

        # Verify source exists and determine type before locking
        try:
            stat = self.agfs.stat(old_path)
            is_dir = stat.get("isDir", False) if isinstance(stat, dict) else False
        except Exception:
            raise FileNotFoundError(f"mv source not found: {old_uri}")

        dst_parent = new_path.rsplit("/", 1)[0] if "/" in new_path else new_path

        async with LockContext(
            get_lock_manager(),
            [old_path],
            lock_mode="mv",
            mv_dst_parent_path=dst_parent,
            src_is_dir=is_dir,
        ):
            uris_to_move = await self._collect_uris(old_path, recursive=True, ctx=ctx)
            uris_to_move.append(target_uri)

            # Check if it's temp directory (files already encrypted)
            is_temp = old_uri.startswith("viking://temp/")

            # Copy source to destination (source still intact)
            try:
                if is_temp or not self._encryptor:
                    agfs_cp(self.agfs, old_path, new_path, recursive=is_dir)
                else:
                    if is_dir:
                        await self._recursive_copy_dir_with_encryption(old_uri, new_uri, ctx=ctx)
                    else:
                        await self.move_file(old_uri, new_uri, ctx=ctx)
            except Exception as e:
                if "not found" in str(e).lower():
                    await self._delete_from_vector_store(uris_to_move, ctx=ctx)
                    logger.info(f"[VikingFS] mv source not found, cleaned orphan index: {old_uri}")
                raise

            # Remove carried lock file from the copy (directory only)
            if is_dir and (is_temp or not self._encryptor):
                carried_lock = new_path.rstrip("/") + "/.path.ovlock"
                try:
                    self.agfs.rm(carried_lock)
                except Exception:
                    pass

            # Update VectorDB URIs (on failure, clean up the copy)
            try:
                await self._update_vector_store_uris(uris_to_move, old_uri, new_uri, ctx=ctx)
            except Exception:
                try:
                    if is_dir:
                        self.agfs.rm(new_path, recursive=True)
                    else:
                        self.agfs.rm(new_path)
                except Exception:
                    pass
                raise

            # Delete source
            self.agfs.rm(old_path, recursive=is_dir)
            return {}

    async def _recursive_copy_dir_with_encryption(
        self,
        old_uri: str,
        new_uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Recursively copy a directory, ensuring files are encrypted."""
        await self.mkdir(new_uri, exist_ok=True, ctx=ctx)

        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            entries = await self.ls(old_uri, ctx=ctx)
            if not entries:
                break

            for entry in entries:
                name = entry.get("name", "")
                if not name or name in (".", ".."):
                    continue
                old_child_uri = f"{old_uri.rstrip('/')}/{name}"
                new_child_uri = f"{new_uri.rstrip('/')}/{name}"
                if entry.get("isDir"):
                    await self._recursive_copy_dir_with_encryption(
                        old_child_uri, new_child_uri, ctx=ctx
                    )
                else:
                    await self.move_file(old_child_uri, new_child_uri, ctx=ctx)

            iteration += 1

    async def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        ctx: Optional[RequestContext] = None,
    ) -> Dict:
        """Content search by pattern or keywords.

        Grep search implemented at VikingFS layer, supports encrypted files.
        """
        self._ensure_access(uri, ctx)

        flags = re.IGNORECASE if case_insensitive else 0
        compiled_pattern = re.compile(pattern, flags)

        results = []

        async def search_recursive(current_uri: str):
            if node_limit and len(results) >= node_limit:
                return

            try:
                entries = await self.ls(current_uri, ctx=ctx)
            except Exception:
                return

            for entry in entries:
                if node_limit and len(results) >= node_limit:
                    break

                entry_uri = f"{current_uri.rstrip('/')}/{entry['name']}"

                if entry.get("isDir"):
                    await search_recursive(entry_uri)
                else:
                    try:
                        content = await self.read(entry_uri, ctx=ctx)
                        if isinstance(content, bytes):
                            content = content.decode("utf-8", errors="replace")

                        lines = content.split("\n")
                        for line_num, line in enumerate(lines, 1):
                            if compiled_pattern.search(line):
                                results.append(
                                    {
                                        "line": line_num,
                                        "uri": entry_uri,
                                        "content": line,
                                    }
                                )
                                if node_limit and len(results) >= node_limit:
                                    break
                    except Exception as e:
                        logger.debug(f"Failed to grep {entry_uri}: {e}")

        await search_recursive(uri)

        return {"matches": results}

    async def stat(self, uri: str, ctx: Optional[RequestContext] = None) -> Dict[str, Any]:
        """
        File/directory information.

        example: {'name': 'resources', 'size': 128, 'mode': 2147484141, 'modTime': '2026-02-10T21:26:02.934376379+08:00', 'isDir': True, 'meta': {'Name': 'localfs', 'Type': 'local', 'Content': {'local_path': '...'}}}
        """
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        return self.agfs.stat(path)

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
        entries = await self.tree(uri, node_limit=1000000, ctx=ctx)
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
        """Batch fetch abstracts for entries.

        Args:
            entries: List of entries to fetch abstracts for
            abs_limit: Maximum length for abstract truncation
        """
        semaphore = asyncio.Semaphore(6)

        async def fetch_abstract(index: int, entry: Dict[str, Any]) -> tuple[int, str]:
            async with semaphore:
                if not entry.get("isDir", False):
                    return index, ""
                try:
                    abstract = await self.abstract(entry["uri"], ctx=ctx)
                    return index, abstract
                except Exception:
                    return index, "[.abstract.md is not ready]"

        tasks = [fetch_abstract(i, entry) for i, entry in enumerate(entries)]
        abstract_results = await asyncio.gather(*tasks)
        for index, abstract in abstract_results:
            if len(abstract) > abs_limit:
                abstract = abstract[: abs_limit - 3] + "..."
            entries[index]["abstract"] = abstract

    async def tree(
        self,
        uri: str = "viking://",
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        level_limit: int = 3,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recursively list all contents (includes rel_path).

        Args:
            uri: Viking URI
            output: str = "original" or "agent"
            abs_limit: int = 256 (for agent output abstract truncation)
            show_all_hidden: bool = False (list all hidden files, like -a)
            node_limit: int = 1000 (maximum number of nodes to list)
            level_limit: int = 3 (maximum depth level to traverse)

        output="original"
        [{'name': '.abstract.md', 'size': 100, 'mode': 420, 'modTime': '2026-02-11T16:52:16.256334192+08:00', 'isDir': False, 'meta': {...}, 'rel_path': '.abstract.md', 'uri': 'viking://resources...'}]

        output="agent"
        [{'name': '.abstract.md', 'size': 100, 'modTime': '2026-02-11 16:52:16', 'isDir': False, 'rel_path': '.abstract.md', 'uri': 'viking://resources...', 'abstract': "..."}]
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
        node_limit: int = 1000,
        level_limit: int = 3,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Recursively list all contents (original format)."""
        path = self._uri_to_path(uri, ctx=ctx)
        all_entries = []
        real_ctx = self._ctx_or_default(ctx)

        async def _walk(current_path: str, current_rel: str, current_depth: int):
            if len(all_entries) >= node_limit or current_depth >= level_limit:
                return
            for entry in self._ls_entries(current_path):
                if len(all_entries) >= node_limit:
                    break
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                rel_path = f"{current_rel}/{name}" if current_rel else name
                new_entry = dict(entry)
                new_entry["rel_path"] = rel_path
                new_entry["uri"] = self._path_to_uri(f"{current_path}/{name}", ctx=ctx)
                if not self._is_accessible(new_entry["uri"], real_ctx):
                    continue
                if entry.get("isDir"):
                    all_entries.append(new_entry)
                    await _walk(f"{current_path}/{name}", rel_path, current_depth + 1)
                elif not name.startswith("."):
                    all_entries.append(new_entry)
                elif show_all_hidden:
                    all_entries.append(new_entry)

        await _walk(path, "", 0)
        return all_entries

    async def _tree_agent(
        self,
        uri: str,
        abs_limit: int,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        level_limit: int = 3,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Recursively list all contents (agent format with abstracts)."""
        path = self._uri_to_path(uri, ctx=ctx)
        all_entries = []
        now = datetime.now()
        real_ctx = self._ctx_or_default(ctx)

        async def _walk(current_path: str, current_rel: str, current_depth: int):
            if len(all_entries) >= node_limit or current_depth >= level_limit:
                return
            for entry in self._ls_entries(current_path):
                if len(all_entries) >= node_limit:
                    break
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                rel_path = f"{current_rel}/{name}" if current_rel else name
                new_entry = {
                    "uri": self._path_to_uri(f"{current_path}/{name}", ctx=ctx),
                    "size": entry.get("size", 0),
                    "isDir": entry.get("isDir", False),
                    "modTime": format_simplified(parse_iso_datetime(entry.get("modTime", "")), now),
                }
                new_entry["rel_path"] = rel_path
                if not self._is_accessible(new_entry["uri"], real_ctx):
                    continue
                if entry.get("isDir"):
                    all_entries.append(new_entry)
                    await _walk(f"{current_path}/{name}", rel_path, current_depth + 1)
                elif not name.startswith("."):
                    all_entries.append(new_entry)
                elif show_all_hidden:
                    all_entries.append(new_entry)

        await _walk(path, "", 0)

        await self._batch_fetch_abstracts(all_entries, abs_limit, ctx=ctx)

        return all_entries

    # ========== VikingFS Specific Capabilities ==========

    async def abstract(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read directory's L0 summary (.abstract.md)."""
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        info = self.agfs.stat(path)
        if not info.get("isDir"):
            raise ValueError(f"{uri} is not a directory")
        file_path = f"{path}/.abstract.md"
        content_bytes = self._handle_agfs_read(self.agfs.read(file_path))

        if self._encryptor:
            real_ctx = self._ctx_or_default(ctx)
            content_bytes = await self._encryptor.decrypt(real_ctx.account_id, content_bytes)

        return self._decode_bytes(content_bytes)

    async def overview(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> str:
        """Read directory's L1 overview (.overview.md)."""
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        info = self.agfs.stat(path)
        if not info.get("isDir"):
            raise ValueError(f"{uri} is not a directory")
        file_path = f"{path}/.overview.md"
        content_bytes = self._handle_agfs_read(self.agfs.read(file_path))

        if self._encryptor:
            real_ctx = self._ctx_or_default(ctx)
            content_bytes = await self._encryptor.decrypt(real_ctx.account_id, content_bytes)

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
        target_uri: str = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        ctx: Optional[RequestContext] = None,
    ):
        """Semantic search.

        Args:
            query: Search query
            target_uri: Target directory URI
            limit: Return count
            score_threshold: Score threshold
            filter: Metadata filter

        Returns:
            FindResult
        """
        telemetry = get_current_telemetry()
        from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
        from openviking_cli.retrieve import (
            ContextType,
            FindResult,
            TypedQuery,
        )

        if target_uri and target_uri not in {"/", "viking://"}:
            self._ensure_access(target_uri, ctx)

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
        )

        # Infer context_type (None = search all types)
        context_type = self._infer_context_type(target_uri) if target_uri else None

        typed_query = TypedQuery(
            query=query,
            context_type=context_type,
            intent="",
            target_directories=[target_uri] if target_uri else None,
        )

        real_ctx = self._ctx_or_default(ctx)
        logger.debug(
            f"[VikingFS.find] Calling retriever.retrieve with ctx.account_id={real_ctx.account_id}, ctx.user={real_ctx.user}"
        )

        result = await retriever.retrieve(
            typed_query,
            ctx=real_ctx,
            limit=limit,
            score_threshold=score_threshold,
            scope_dsl=filter,
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
        telemetry = get_current_telemetry()
        from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
        from openviking.retrieve.intent_analyzer import IntentAnalyzer
        from openviking_cli.retrieve import (
            ContextType,
            FindResult,
            QueryPlan,
            TypedQuery,
        )

        # Normalize target_uri to list
        target_uri_list = [target_uri] if isinstance(target_uri, str) else (target_uri or [])
        # Use first URI for context inference and access check
        primary_target_uri = target_uri_list[0] if target_uri_list else ""

        session_summary = (
            str(session_info.get("latest_archive_overview") or "") if session_info else ""
        )
        current_messages = session_info.get("current_messages") if session_info else None

        query_plan: Optional[QueryPlan] = None
        if primary_target_uri and primary_target_uri not in {"/", "viking://"}:
            self._ensure_access(primary_target_uri, ctx)

        # When target_uri exists: read abstract, infer context_type
        target_context_type: Optional[ContextType] = None
        target_abstract = ""
        if primary_target_uri:
            target_context_type = self._infer_context_type(primary_target_uri)
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
                context_type=target_context_type,
                target_abstract=target_abstract,
            )
            typed_queries = query_plan.queries
            # Set target_directories
            if target_uri_list:
                for tq in typed_queries:
                    tq.target_directories = target_uri_list
        else:
            # No session context: create query directly
            if target_context_type:
                # Has target_uri: only query that type
                typed_queries = [
                    TypedQuery(
                        query=query,
                        context_type=target_context_type,
                        intent="",
                        priority=1,
                        target_directories=target_uri_list,
                    )
                ]
            else:
                # No target_uri: query all types
                typed_queries = [
                    TypedQuery(
                        query=query,
                        context_type=ctx_type,
                        intent="",
                        priority=1,
                        target_directories=target_uri_list,
                    )
                    for ctx_type in [ContextType.MEMORY, ContextType.RESOURCE, ContextType.SKILL]
                ]
        telemetry.set("search.typed_queries_count", len(typed_queries))

        # Concurrent execution
        storage = self._get_vector_store()
        embedder = self._get_embedder()
        retriever = HierarchicalRetriever(
            storage=storage,
            embedder=embedder,
            rerank_config=self.rerank_config,
        )

        async def _execute(tq: TypedQuery):
            real_ctx = self._ctx_or_default(ctx)
            logger.debug(
                f"[VikingFS.search._execute] Calling retriever.retrieve with ctx.account_id={real_ctx.account_id}, ctx.user={real_ctx.user}"
            )
            return await retriever.retrieve(
                tq,
                ctx=real_ctx,
                limit=limit,
                score_threshold=score_threshold,
                scope_dsl=filter,
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
        _, parts = self._normalized_uri_parts(uri)
        if not parts:
            return f"/local/{account_id}"

        safe_parts = [self._shorten_component(p, self._MAX_FILENAME_BYTES) for p in parts]
        return f"/local/{account_id}/{'/'.join(safe_parts)}"

    _INTERNAL_NAMES = {"_system", ".path.ovlock"}
    _ROOT_PATH = "/local"

    def _ls_entries(self, path: str) -> List[Dict[str, Any]]:
        """List directory entries, filtering out internal directories.

        At account root (/local/{account}), uses VALID_SCOPES whitelist.
        At other levels, uses _INTERNAL_NAMES blacklist.
        """
        entries = self.agfs.ls(path)
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) == 2 and parts[0] == "local":
            return [e for e in entries if e.get("name") in VikingURI.VALID_SCOPES]
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

    def _extract_space_from_uri(self, uri: str) -> Optional[str]:
        """Extract space segment from URI if present.

        URIs are WYSIWYG: viking://{scope}/{space}/...
        For user/agent, the second segment is space unless it's a known structure dir.
        For session, the second segment is always space (when 3+ parts).
        """
        _, parts = self._normalized_uri_parts(uri)
        if len(parts) < 2:
            return None
        scope = parts[0]
        second = parts[1]
        # Treat scope-root metadata files as not having a tenant space segment.
        if len(parts) == 2 and second in {".abstract.md", ".overview.md"}:
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

        scope = parts[0]
        if scope in {"resources", "temp"}:
            return True
        if scope == "_system":
            return False

        space = self._extract_space_from_uri(normalized_uri)
        if space is None:
            return True

        if scope in {"user", "session"}:
            return space == ctx.user.user_space_name()
        if scope == "agent":
            return space == ctx.user.agent_space_name()
        return True

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

    def _infer_context_type(self, uri: str):
        """Infer context_type from URI. Returns None when ambiguous."""
        from openviking_cli.retrieve import ContextType

        if "/memories" in uri:
            return ContextType.MEMORY
        elif "/skills" in uri:
            return ContextType.SKILL
        elif "/resources" in uri:
            return ContextType.RESOURCE
        return None

    # ========== Vector Sync Helper Methods ==========

    async def _collect_uris(
        self, path: str, recursive: bool, ctx: Optional[RequestContext] = None
    ) -> List[str]:
        """Recursively collect all URIs (for rm/mv), including directories."""
        uris = []

        async def _collect(p: str):
            try:
                for entry in self._ls_entries(p):
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

        Preserves vector data, only updates uri and parent_uri fields, no need to regenerate embeddings.
        """
        vector_store = self._get_vector_store()
        if not vector_store:
            return

        old_base_uri = self._path_to_uri(old_base, ctx=ctx)
        new_base_uri = self._path_to_uri(new_base, ctx=ctx)

        for uri in uris:
            try:
                new_uri = uri.replace(old_base_uri, new_base_uri, 1)
                new_parent_uri = VikingURI(new_uri).parent.uri

                await vector_store.update_uri_mapping(
                    ctx=self._ctx_or_default(ctx),
                    uri=uri,
                    new_uri=new_uri,
                    new_parent_uri=new_parent_uri,
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
        dst_parent = new_path.rsplit("/", 1)[0] if "/" in new_path else new_path

        try:
            async with LockContext(
                get_lock_manager(),
                [old_path],
                lock_mode="mv",
                mv_dst_parent_path=dst_parent,
                src_is_dir=True,
            ):
                await self._update_vector_store_uris(
                    uris=[old_dir],
                    old_base=old_dir,
                    new_base=new_dir,
                    ctx=real_ctx,
                    levels=[0, 1],
                )

        except LockAcquisitionError:
            raise ResourceBusyError(f"Resource is being processed: {old_dir}")

    def _get_vector_store(self) -> Optional["VikingVectorIndexBackend"]:
        """Get vector store instance."""
        return self.vector_store

    def _get_embedder(self) -> Any:
        """Get embedder instance."""
        return self.query_embedder

    # ========== Parent Directory Creation ==========

    async def _ensure_parent_dirs(self, path: str) -> None:
        """Recursively create all parent directories."""
        # Remove leading slash if present, then split
        parts = path.lstrip("/").split("/")
        # If it's a file path (not just a directory), we need to create parent directories
        # We create directories up to the last component (which might be a file)
        for i in range(1, len(parts)):
            parent = "/" + "/".join(parts[:i])
            try:
                self.agfs.mkdir(parent)
            except Exception as e:
                # Log the error but continue, as parent might already exist
                # or we might be creating it in the next iteration
                if "exist" not in str(e).lower() and "already" not in str(e).lower():
                    logger.debug(f"Failed to create parent directory {parent}: {e}")

    # ========== Relation Table Internal Methods ==========

    async def _read_relation_table(
        self, dir_path: str, ctx: Optional[RequestContext] = None
    ) -> List[RelationEntry]:
        """Read .relations.json."""
        table_path = f"{dir_path}/.relations.json"
        try:
            content = self._handle_agfs_read(self.agfs.read(table_path))
            content = await self._decrypt_content(content, ctx=ctx)
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

        content = await self._encrypt_content(content, ctx=ctx)
        self.agfs.write(table_path, content)

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
        await self._ensure_parent_dirs(path)

        if isinstance(content, str):
            content = content.encode("utf-8")

        content = await self._encrypt_content(content, ctx=ctx)
        self.agfs.write(path, content)

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
            self.agfs.stat(path)
        except Exception:
            raise NotFoundError(uri, "file")
        try:
            content = self.agfs.read(path)
            if isinstance(content, bytes):
                raw = content
            elif content is not None and hasattr(content, "content"):
                raw = content.content
            else:
                raw = b""

            # If encryption is enabled, always decrypt full file first
            if self._encryptor:
                raw = await self._decrypt_content(raw, ctx=ctx)

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
            raw = self._handle_agfs_read(self.agfs.read(path))
            raw = await self._decrypt_content(raw, ctx=ctx)
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
        await self._ensure_parent_dirs(path)

        content = await self._encrypt_content(content, ctx=ctx)
        self.agfs.write(path, content)

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
                existing_bytes = self._handle_agfs_read(self.agfs.read(path))
                existing_bytes = await self._decrypt_content(existing_bytes, ctx=ctx)
                existing = self._decode_bytes(existing_bytes)
            except Exception:
                pass

            await self._ensure_parent_dirs(path)
            final_content = (existing + content).encode("utf-8")
            final_content = await self._encrypt_content(final_content, ctx=ctx)
            self.agfs.write(path, final_content)

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
            entries = self._ls_entries(path)
        except Exception:
            raise NotFoundError(uri, "directory")
        # basic info
        now = datetime.now()
        all_entries = []
        for entry in entries:
            if len(all_entries) >= node_limit:
                break
            name = entry.get("name", "")
            # After modification: compatible with 7+ digits of microseconds by truncating
            raw_time = entry.get("modTime", "")
            if raw_time and len(raw_time) > 26 and "+" in raw_time:
                # Handle strings like 2026-02-21T13:20:23.1470042+08:00
                # Truncate to 2026-02-21T13:20:23.147004+08:00
                parts = raw_time.split("+")
                # Keep time part at most 26 characters (YYYY-MM-DDTHH:MM:SS.mmmmmm)
                raw_time = parts[0][:26] + "+" + parts[1]
            new_entry = {
                "uri": self._path_to_uri(f"{path}/{name}", ctx=ctx),
                "size": entry.get("size", 0),
                "isDir": entry.get("isDir", False),
                "modTime": format_simplified(parse_iso_datetime(raw_time), now),
            }
            if not self._is_accessible(new_entry["uri"], real_ctx):
                continue
            if entry.get("isDir"):
                all_entries.append(new_entry)
            elif not name.startswith("."):
                all_entries.append(new_entry)
            elif show_all_hidden:
                all_entries.append(new_entry)
        # call abstract in parallel 6 threads
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
            entries = self._ls_entries(path)
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
        self._ensure_access(from_uri, ctx)
        self._ensure_access(to_uri, ctx)
        from_path = self._uri_to_path(from_uri, ctx=ctx)

        content_bytes = await self.read_file_bytes(from_uri, ctx=ctx)
        await self.write_file(to_uri, content_bytes, ctx=ctx)
        self.agfs.rm(from_path)

    # ========== Temp File Operations (backward compatible) ==========

    def create_temp_uri(self) -> str:
        """Create temp directory URI."""
        return VikingURI.create_temp_uri()

    async def delete_temp(self, temp_uri: str, ctx: Optional[RequestContext] = None) -> None:
        """Delete temp directory and its contents."""
        path = self._uri_to_path(temp_uri, ctx=ctx)
        try:
            for entry in self._ls_entries(path):
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                entry_path = f"{path}/{name}"
                if entry.get("isDir"):
                    await self.delete_temp(f"{temp_uri}/{name}", ctx=ctx)
                else:
                    self.agfs.rm(entry_path)
            self.agfs.rm(path)
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
            await self._ensure_parent_dirs(path)
            try:
                self.agfs.mkdir(path)
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
