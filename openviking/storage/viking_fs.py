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
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from pyagfs.exceptions import AGFSHTTPError

from openviking.server.identity import RequestContext, Role
from openviking.utils.time_utils import format_simplified, get_current_timestamp, parse_iso_datetime
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
) -> "VikingFS":
    """Initialize VikingFS singleton.

    Args:
        agfs: Pre-initialized AGFS client (HTTP or Binding)
        agfs_config: AGFS configuration object for backend settings
        query_embedder: Embedder instance
        rerank_config: Rerank configuration
        vector_store: Vector store instance
        enable_recorder: Whether to enable IO recording
    """
    global _instance

    _instance = VikingFS(
        agfs=agfs,
        query_embedder=query_embedder,
        rerank_config=rerank_config,
        vector_store=vector_store,
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
    ):
        self.agfs = agfs
        self.query_embedder = query_embedder
        self.rerank_config = rerank_config
        self.vector_store = vector_store
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

    def _ensure_access(self, uri: str, ctx: Optional[RequestContext]) -> None:
        real_ctx = self._ctx_or_default(ctx)
        if not self._is_accessible(uri, real_ctx):
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
        result = self.agfs.read(path, offset, size)
        if isinstance(result, bytes):
            return result
        elif result is not None and hasattr(result, "content"):
            return result.content
        else:
            return b""

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
        """
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        target_uri = self._path_to_uri(path, ctx=ctx)
        uris_to_delete = await self._collect_uris(path, recursive, ctx=ctx)
        uris_to_delete.append(target_uri)
        result = self.agfs.rm(path, recursive=recursive)
        await self._delete_from_vector_store(uris_to_delete, ctx=ctx)
        return result

    async def mv(
        self,
        old_uri: str,
        new_uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        """Move file/directory + recursively update vector index."""
        self._ensure_access(old_uri, ctx)
        self._ensure_access(new_uri, ctx)
        old_path = self._uri_to_path(old_uri, ctx=ctx)
        new_path = self._uri_to_path(new_uri, ctx=ctx)
        target_uri = self._path_to_uri(old_path, ctx=ctx)
        uris_to_move = await self._collect_uris(old_path, recursive=True, ctx=ctx)
        uris_to_move.append(target_uri)

        try:
            result = self.agfs.mv(old_path, new_path)
            await self._update_vector_store_uris(uris_to_move, old_uri, new_uri, ctx=ctx)
            return result
        except AGFSHTTPError as e:
            if e.status_code == 404:
                await self._delete_from_vector_store(uris_to_move, ctx=ctx)
                logger.info(f"[VikingFS] mv source not found, cleaned orphan index: {old_uri}")
            raise

    async def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        ctx: Optional[RequestContext] = None,
    ) -> Dict:
        """Content search by pattern or keywords."""
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        result = self.agfs.grep(path, pattern, True, case_insensitive)
        if result.get("matches", None) is None:
            result["matches"] = []
        new_matches = []
        for match in result.get("matches", []):
            new_match = {
                "line": match.get("line"),
                "uri": self._path_to_uri(match.get("file"), ctx=ctx),
                "content": match.get("content"),
            }
            new_matches.append(new_match)
        result["matches"] = new_matches
        return result

    async def stat(self, uri: str, ctx: Optional[RequestContext] = None) -> Dict[str, Any]:
        """
        File/directory information.

        example: {'name': 'resources', 'size': 128, 'mode': 2147484141, 'modTime': '2026-02-10T21:26:02.934376379+08:00', 'isDir': True, 'meta': {'Name': 'localfs', 'Type': 'local', 'Content': {'local_path': '...'}}}
        """
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        return self.agfs.stat(path)

    async def glob(
        self,
        pattern: str,
        uri: str = "viking://",
        node_limit: int = 1000,
        ctx: Optional[RequestContext] = None,
    ) -> Dict:
        """File pattern matching, supports **/*.md recursive."""
        entries = await self.tree(uri, node_limit=node_limit, ctx=ctx)
        base_uri = uri.rstrip("/")
        matches = []
        for entry in entries:
            rel_path = entry.get("rel_path", "")
            if PurePath(rel_path).match(pattern):
                matches.append(f"{base_uri}/{rel_path}")
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
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recursively list all contents (includes rel_path).

        Args:
            uri: Viking URI
            output: str = "original" or "agent"
            abs_limit: int = 256 (for agent output abstract truncation)
            show_all_hidden: bool = False (list all hidden files, like -a)

        output="original"
        [{'name': '.abstract.md', 'size': 100, 'mode': 420, 'modTime': '2026-02-11T16:52:16.256334192+08:00', 'isDir': False, 'meta': {...}, 'rel_path': '.abstract.md', 'uri': 'viking://resources...'}]

        output="agent"
        [{'name': '.abstract.md', 'size': 100, 'modTime': '2026-02-11 16:52:16', 'isDir': False, 'rel_path': '.abstract.md', 'uri': 'viking://resources...', 'abstract': "..."}]
        """
        self._ensure_access(uri, ctx)
        if output == "original":
            return await self._tree_original(uri, show_all_hidden, node_limit, ctx=ctx)
        elif output == "agent":
            return await self._tree_agent(uri, abs_limit, show_all_hidden, node_limit, ctx=ctx)
        else:
            raise ValueError(f"Invalid output format: {output}")

    async def _tree_original(
        self,
        uri: str,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Recursively list all contents (original format)."""
        path = self._uri_to_path(uri, ctx=ctx)
        all_entries = []
        real_ctx = self._ctx_or_default(ctx)

        async def _walk(current_path: str, current_rel: str):
            if len(all_entries) >= node_limit:
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
                    await _walk(f"{current_path}/{name}", rel_path)
                elif not name.startswith("."):
                    all_entries.append(new_entry)
                elif show_all_hidden:
                    all_entries.append(new_entry)

        await _walk(path, "")
        return all_entries

    async def _tree_agent(
        self,
        uri: str,
        abs_limit: int,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Recursively list all contents (agent format with abstracts)."""
        path = self._uri_to_path(uri, ctx=ctx)
        all_entries = []
        now = datetime.now()
        real_ctx = self._ctx_or_default(ctx)

        async def _walk(current_path: str, current_rel: str):
            if len(all_entries) >= node_limit:
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
                    await _walk(f"{current_path}/{name}", rel_path)
                elif not name.startswith("."):
                    all_entries.append(new_entry)
                elif show_all_hidden:
                    all_entries.append(new_entry)

        await _walk(path, "")

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
        content = self.agfs.read(file_path)
        return self._handle_agfs_content(content)

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
        content = self.agfs.read(file_path)
        return self._handle_agfs_content(content)

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
        from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
        from openviking_cli.retrieve import (
            ContextType,
            FindResult,
            TypedQuery,
        )

        if not self.rerank_config:
            raise RuntimeError("rerank_config is required for find")
        if target_uri:
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

        result = await retriever.retrieve(
            typed_query,
            ctx=self._ctx_or_default(ctx),
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

        return FindResult(
            memories=memories,
            resources=resources,
            skills=skills,
        )

    async def search(
        self,
        query: str,
        target_uri: str = "",
        session_info: Optional[Dict] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
        ctx: Optional[RequestContext] = None,
    ):
        """Complex search with session context.

        Args:
            query: Search query
            target_uri: Target directory URI
            session_info: Session information
            limit: Return count
            filter: Metadata filter

        Returns:
            FindResult
        """
        from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
        from openviking.retrieve.intent_analyzer import IntentAnalyzer
        from openviking_cli.retrieve import (
            ContextType,
            FindResult,
            QueryPlan,
            TypedQuery,
        )

        session_summary = session_info.get("summary") if session_info else None
        recent_messages = session_info.get("recent_messages") if session_info else None

        query_plan: Optional[QueryPlan] = None
        if target_uri:
            self._ensure_access(target_uri, ctx)

        # When target_uri exists: read abstract, infer context_type
        target_context_type: Optional[ContextType] = None
        target_abstract = ""
        if target_uri:
            target_context_type = self._infer_context_type(target_uri)
            try:
                target_abstract = await self.abstract(target_uri, ctx=ctx)
            except Exception:
                target_abstract = ""

        # With session context: intent analysis
        if session_summary or recent_messages:
            analyzer = IntentAnalyzer(max_recent_messages=5)
            query_plan = await analyzer.analyze(
                compression_summary=session_summary or "",
                messages=recent_messages or [],
                current_message=query,
                context_type=target_context_type,
                target_abstract=target_abstract,
            )
            typed_queries = query_plan.queries
            # Set target_directories
            if target_uri:
                for tq in typed_queries:
                    tq.target_directories = [target_uri]
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
                        target_directories=[target_uri] if target_uri else [],
                    )
                ]
            else:
                # No target_uri: query all types
                typed_queries = [
                    TypedQuery(query=query, context_type=ctx_type, intent="", priority=1)
                    for ctx_type in [ContextType.MEMORY, ContextType.RESOURCE, ContextType.SKILL]
                ]

        # Concurrent execution
        storage = self._get_vector_store()
        embedder = self._get_embedder()
        retriever = HierarchicalRetriever(
            storage=storage,
            embedder=embedder,
            rerank_config=self.rerank_config,
        )

        async def _execute(tq: TypedQuery):
            return await retriever.retrieve(
                tq,
                ctx=self._ctx_or_default(ctx),
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

        return FindResult(
            memories=memories,
            resources=resources,
            skills=skills,
            query_plan=query_plan,
            query_results=query_results,
        )

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

        entries = await self._read_relation_table(from_path)
        existing_ids = {e.id for e in entries}

        link_id = next(f"link_{i}" for i in range(1, 10000) if f"link_{i}" not in existing_ids)

        entries.append(RelationEntry(id=link_id, uris=uris, reason=reason))

        await self._write_relation_table(from_path, entries)
        logger.info(f"[VikingFS] Created link: {from_uri} -> {uris}")

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
            entries = await self._read_relation_table(from_path)

            entry_to_modify = None
            for entry in entries:
                if uri in entry.uris:
                    entry_to_modify = entry
                    break

            if not entry_to_modify:
                logger.warning(f"[VikingFS] URI not found in relations: {uri}")
                return

            entry_to_modify.uris.remove(uri)

            if not entry_to_modify.uris:
                entries.remove(entry_to_modify)
                logger.info(f"[VikingFS] Removed empty entry: {entry_to_modify.id}")

            await self._write_relation_table(from_path, entries)
            logger.info(f"[VikingFS] Removed link: {from_uri} -> {uri}")

        except Exception as e:
            logger.error(f"[VikingFS] Failed to unlink {from_uri} -> {uri}: {e}")
            raise IOError(f"Failed to unlink: {e}")

    async def get_relation_table(
        self, uri: str, ctx: Optional[RequestContext] = None
    ) -> List[RelationEntry]:
        """Get relation table."""
        self._ensure_access(uri, ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        return await self._read_relation_table(path)

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
        remainder = uri[len("viking://") :].strip("/") if uri.startswith("viking://") else uri
        if not remainder:
            return f"/local/{account_id}"

        parts = [p for p in remainder.split("/") if p]
        safe_parts = [self._shorten_component(p, self._MAX_FILENAME_BYTES) for p in parts]
        return f"/local/{account_id}/{'/'.join(safe_parts)}"

    _INTERNAL_DIRS = {"_system"}
    _ROOT_PATH = "/local"

    def _ls_entries(self, path: str) -> List[Dict[str, Any]]:
        """List directory entries, filtering out internal directories.

        At account root (/local/{account}), uses VALID_SCOPES whitelist.
        At other levels, uses _INTERNAL_DIRS blacklist.
        """
        entries = self.agfs.ls(path)
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) == 2 and parts[0] == "local":
            return [e for e in entries if e.get("name") in VikingURI.VALID_SCOPES]
        return [e for e in entries if e.get("name") not in self._INTERNAL_DIRS]

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
        if not uri.startswith("viking://"):
            return None
        parts = [p for p in uri[len("viking://") :].strip("/").split("/") if p]
        if len(parts) < 2:
            return None
        scope = parts[0]
        second = parts[1]
        if scope == "user" and second not in self._USER_STRUCTURE_DIRS:
            return second
        if scope == "agent" and second not in self._AGENT_STRUCTURE_DIRS:
            return second
        if scope == "session" and len(parts) >= 2:
            return second
        return None

    def _is_accessible(self, uri: str, ctx: RequestContext) -> bool:
        """Check whether a URI is visible/accessible under current request context."""
        if ctx.role == Role.ROOT:
            return True
        if not uri.startswith("viking://"):
            return False

        parts = [p for p in uri[len("viking://") :].strip("/").split("/") if p]
        if not parts:
            return True

        scope = parts[0]
        if scope in {"resources", "temp", "transactions"}:
            return True
        if scope == "_system":
            return False

        space = self._extract_space_from_uri(uri)
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
        """Handle AGFSClient content return types consistently."""
        if isinstance(result, bytes):
            return result.decode("utf-8")
        elif hasattr(result, "content"):
            return result.content.decode("utf-8")
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
        """Recursively collect all URIs (for rm/mv)."""
        uris = []

        async def _collect(p: str):
            try:
                for entry in self._ls_entries(p):
                    name = entry.get("name", "")
                    if name in [".", ".."]:
                        continue
                    full_path = f"{p}/{name}".replace("//", "/")
                    if entry.get("isDir"):
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
                logger.info(f"[VikingFS] Deleted from vector store: {uri}")
        except Exception as e:
            logger.warning(f"[VikingFS] Failed to delete from vector store: {e}")

    async def _update_vector_store_uris(
        self,
        uris: List[str],
        old_base: str,
        new_base: str,
        ctx: Optional[RequestContext] = None,
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
                records = await vector_store.get_context_by_uri(
                    account_id=self._ctx_or_default(ctx).account_id,
                    uri=uri,
                    limit=1,
                )

                if not records or "id" not in records[0]:
                    continue

                record = records[0]

                new_uri = uri.replace(old_base_uri, new_base_uri, 1)

                old_parent_uri = record.get("parent_uri", "")
                new_parent_uri = (
                    old_parent_uri.replace(old_base_uri, new_base_uri, 1) if old_parent_uri else ""
                )

                await vector_store.update_uri_mapping(
                    ctx=self._ctx_or_default(ctx),
                    uri=uri,
                    new_uri=new_uri,
                    new_parent_uri=new_parent_uri,
                )
                logger.info(f"[VikingFS] Updated URI: {uri} -> {new_uri}")
            except Exception as e:
                logger.warning(f"[VikingFS] Failed to update {uri} in vector store: {e}")

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

    async def _read_relation_table(self, dir_path: str) -> List[RelationEntry]:
        """Read .relations.json."""
        table_path = f"{dir_path}/.relations.json"
        try:
            content = self._handle_agfs_read(self.agfs.read(table_path))
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

    async def _write_relation_table(self, dir_path: str, entries: List[RelationEntry]) -> None:
        """Write .relations.json."""
        # Use flat list format
        data = [entry.to_dict() for entry in entries]

        content = json.dumps(data, ensure_ascii=False, indent=2)
        table_path = f"{dir_path}/.relations.json"
        if isinstance(content, str):
            content = content.encode("utf-8")
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
        try:
            content = self.agfs.read(path)
        except Exception as e:
            raise FileNotFoundError(f"Failed to read {uri}: {e}")
        text = self._handle_agfs_content(content)
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
            return self._handle_agfs_read(self.agfs.read(path))
        except Exception as e:
            raise FileNotFoundError(f"Failed to read {uri}: {e}")

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
                existing = self._decode_bytes(existing_bytes)
            except Exception:
                pass

            await self._ensure_parent_dirs(path)
            self.agfs.write(path, (existing + content).encode("utf-8"))

        except Exception as e:
            logger.error(f"[VikingFS] Failed to append to file {uri}: {e}")
            raise IOError(f"Failed to append to file {uri}: {e}")

    async def ls(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """
        List directory contents (URI version).

        Args:
            uri: Viking URI
            output: str = "original"
            abs_limit: int = 256
            show_all_hidden: bool = False (list all hidden files, like -a)

        output="original"
        [{'name': '.abstract.md', 'size': 100, 'mode': 420, 'modTime': '2026-02-11T16:52:16.256334192+08:00', 'isDir': False, 'meta': {'Name': 'localfs', 'Type': 'local', 'Content': None}, 'uri': 'viking://resources/.abstract.md'}]

        output="agent"
        [{'name': '.abstract.md', 'size': 100, 'modTime': '2026-02-11(or 16:52:16 for today)', 'isDir': False, 'uri': 'viking://resources/.abstract.md', 'abstract': "..."}]
        """
        self._ensure_access(uri, ctx)
        if output == "original":
            return await self._ls_original(uri, show_all_hidden, ctx=ctx)
        elif output == "agent":
            return await self._ls_agent(uri, abs_limit, show_all_hidden, ctx=ctx)
        else:
            raise ValueError(f"Invalid output format: {output}")

    async def _ls_agent(
        self,
        uri: str,
        abs_limit: int,
        show_all_hidden: bool,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """List directory contents (URI version)."""
        path = self._uri_to_path(uri, ctx=ctx)
        real_ctx = self._ctx_or_default(ctx)
        try:
            entries = self._ls_entries(path)
        except Exception as e:
            raise FileNotFoundError(f"Failed to list {uri}: {e}")
        # basic info
        now = datetime.now()
        all_entries = []
        for entry in entries:
            name = entry.get("name", "")
            # 修改后：通过截断字符串来兼容 7 位或更多位的微秒
            raw_time = entry.get("modTime", "")
            if raw_time and len(raw_time) > 26 and "+" in raw_time:
                # 处理像 2026-02-21T13:20:23.1470042+08:00 这样的字符串
                # 截断为 2026-02-21T13:20:23.147004+08:00
                parts = raw_time.split("+")
                # 保持时间部分最多 26 位 (YYYY-MM-DDTHH:MM:SS.mmmmmm)
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
        except Exception as e:
            raise FileNotFoundError(f"Failed to list {uri}: {e}")

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
        to_path = self._uri_to_path(to_uri, ctx=ctx)
        content = self.agfs.read(from_path)
        await self._ensure_parent_dirs(to_path)
        self.agfs.write(to_path, content)
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
                content_path = f"{path}/{content_filename}"
                if isinstance(content, str):
                    content = content.encode("utf-8")
                self.agfs.write(content_path, content)

            if abstract:
                abstract_path = f"{path}/.abstract.md"
                self.agfs.write(abstract_path, abstract.encode("utf-8"))

            if overview:
                overview_path = f"{path}/.overview.md"
                self.agfs.write(overview_path, overview.encode("utf-8"))

        except Exception as e:
            logger.error(f"[VikingFS] Failed to write {uri}: {e}")
            raise IOError(f"Failed to write {uri}: {e}")
