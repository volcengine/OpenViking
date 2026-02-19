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
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from pyagfs import AGFSClient

from openviking.storage.vikingdb_interface import VikingDBInterface
from openviking.utils.time_utils import format_simplified, get_current_timestamp
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

if TYPE_CHECKING:
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
    agfs_url: str = "http://localhost:8080",
    query_embedder: Optional[Any] = None,
    rerank_config: Optional["RerankConfig"] = None,
    vector_store: Optional["VikingDBInterface"] = None,
    timeout: int = 10,
) -> "VikingFS":
    """Initialize VikingFS singleton.

    Args:
        agfs_url: AGFS service URL
        query_embedder: Embedder instance
        rerank_config: Rerank configuration
        vector_store: Vector store instance
        timeout: Request timeout in seconds
    """
    global _instance
    _instance = VikingFS(
        agfs_url=agfs_url,
        query_embedder=query_embedder,
        rerank_config=rerank_config,
        vector_store=vector_store,
        timeout=timeout,
    )
    return _instance


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
    """

    def __init__(
        self,
        agfs_url: str = "http://localhost:8080",
        query_embedder: Optional[Any] = None,
        rerank_config: Optional["RerankConfig"] = None,
        vector_store: Optional["VikingDBInterface"] = None,
        timeout: int = 10,
    ):
        self.agfs = AGFSClient(api_base_url=agfs_url, timeout=timeout)
        self.query_embedder = query_embedder
        self.rerank_config = rerank_config
        self.vector_store = vector_store
        logger.info(f"[VikingFS] Initialized with agfs_url={agfs_url}")

    # ========== AGFS Basic Commands ==========

    async def read(self, uri: str, offset: int = 0, size: int = -1) -> bytes:
        """Read file"""
        path = self._uri_to_path(uri)
        result = self.agfs.read(path, offset, size)
        if isinstance(result, bytes):
            return result
        elif result is not None and hasattr(result, "content"):
            return result.content
        else:
            return b""

    async def write(self, uri: str, data: Union[bytes, str]) -> str:
        """Write file"""
        path = self._uri_to_path(uri)
        if isinstance(data, str):
            data = data.encode("utf-8")
        return self.agfs.write(path, data)

    async def mkdir(self, uri: str, mode: str = "755", exist_ok: bool = False) -> None:
        """Create directory."""
        path = self._uri_to_path(uri)
        # Always ensure parent directories exist before creating this directory
        await self._ensure_parent_dirs(path)

        if exist_ok:
            try:
                await self.stat(uri)
                return None
            except Exception:
                pass

        self.agfs.mkdir(path)

    async def rm(self, uri: str, recursive: bool = False) -> Dict[str, Any]:
        """Delete file/directory + recursively update vector index."""
        path = self._uri_to_path(uri)
        uris_to_delete = await self._collect_uris(path, recursive)
        result = self.agfs.rm(path, recursive)
        if uris_to_delete:
            await self._delete_from_vector_store(uris_to_delete)
        return result

    async def mv(self, old_uri: str, new_uri: str) -> Dict[str, Any]:
        """Move file/directory + recursively update vector index."""
        old_path = self._uri_to_path(old_uri)
        new_path = self._uri_to_path(new_uri)
        uris_to_move = await self._collect_uris(old_path, recursive=True)
        result = self.agfs.mv(old_path, new_path)
        if uris_to_move:
            await self._update_vector_store_uris(uris_to_move, old_uri, new_uri)
        return result

    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict:
        """Content search by pattern or keywords."""
        path = self._uri_to_path(uri)
        result = self.agfs.grep(path, pattern, True, case_insensitive)
        if result.get("matches", None) is None:
            result["matches"] = []
        new_matches = []
        for match in result.get("matches", []):
            new_match = {
                "line": match.get("line"),
                "uri": self._path_to_uri(match.get("file")),
                "content": match.get("content"),
            }
            new_matches.append(new_match)
        result["matches"] = new_matches
        return result

    async def stat(self, uri: str) -> Dict[str, Any]:
        """
        File/directory information.

        example: {'name': 'resources', 'size': 128, 'mode': 2147484141, 'modTime': '2026-02-10T21:26:02.934376379+08:00', 'isDir': True, 'meta': {'Name': 'localfs', 'Type': 'local', 'Content': {'local_path': '...'}}}
        """
        path = self._uri_to_path(uri)
        return self.agfs.stat(path)

    async def glob(self, pattern: str, uri: str = "viking://", node_limit: int = 1000) -> Dict:
        """File pattern matching, supports **/*.md recursive."""
        entries = await self.tree(uri, node_limit=node_limit)
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
                    abstract = await self.abstract(entry["uri"])
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
        if output == "original":
            return await self._tree_original(uri, show_all_hidden, node_limit)
        elif output == "agent":
            return await self._tree_agent(uri, abs_limit, show_all_hidden, node_limit)
        else:
            raise ValueError(f"Invalid output format: {output}")

    async def _tree_original(
        self, uri: str, show_all_hidden: bool = False, node_limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Recursively list all contents (original format)."""
        path = self._uri_to_path(uri)
        all_entries = []

        async def _walk(current_path: str, current_rel: str):
            if len(all_entries) >= node_limit:
                return
            for entry in self.agfs.ls(current_path):
                if len(all_entries) >= node_limit:
                    break
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                rel_path = f"{current_rel}/{name}" if current_rel else name
                new_entry = dict(entry)
                new_entry["rel_path"] = rel_path
                new_entry["uri"] = self._path_to_uri(f"{current_path}/{name}")
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
        self, uri: str, abs_limit: int, show_all_hidden: bool = False, node_limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Recursively list all contents (agent format with abstracts)."""
        path = self._uri_to_path(uri)
        all_entries = []
        now = datetime.now()

        async def _walk(current_path: str, current_rel: str):
            if len(all_entries) >= node_limit:
                return
            for entry in self.agfs.ls(current_path):
                if len(all_entries) >= node_limit:
                    break
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                rel_path = f"{current_rel}/{name}" if current_rel else name
                new_entry = {
                    "uri": str(VikingURI(uri).join(rel_path)),
                    "size": entry.get("size", 0),
                    "isDir": entry.get("isDir", False),
                    "modTime": format_simplified(
                        datetime.fromisoformat(entry.get("modTime", "")), now
                    ),
                }
                if entry.get("isDir"):
                    all_entries.append(new_entry)
                    await _walk(f"{current_path}/{name}", rel_path)
                elif not name.startswith("."):
                    all_entries.append(new_entry)
                elif show_all_hidden:
                    all_entries.append(new_entry)

        await _walk(path, "")

        await self._batch_fetch_abstracts(all_entries, abs_limit)

        return all_entries

    # ========== VikingFS Specific Capabilities ==========

    async def abstract(
        self,
        uri: str,
    ) -> str:
        """Read directory's L0 summary (.abstract.md)."""
        path = self._uri_to_path(uri)
        info = self.agfs.stat(path)
        if not info.get("isDir"):
            raise ValueError(f"{uri} is not a directory")
        file_path = f"{path}/.abstract.md"
        content = self.agfs.read(file_path)
        return self._handle_agfs_content(content)

    async def overview(
        self,
        uri: str,
    ) -> str:
        """Read directory's L1 overview (.overview.md)."""
        path = self._uri_to_path(uri)
        info = self.agfs.stat(path)
        if not info.get("isDir"):
            raise ValueError(f"{uri} is not a directory")
        file_path = f"{path}/.overview.md"
        content = self.agfs.read(file_path)
        return self._handle_agfs_content(content)

    async def relations(
        self,
        uri: str,
    ) -> List[Dict[str, Any]]:
        """Get relation list.

        Returns: [{"uri": "...", "reason": "..."}, ...]
        """
        entries = await self.get_relation_table(uri)
        result = []
        for entry in entries:
            for u in entry.uris:
                result.append({"uri": u, "reason": entry.reason})
        return result

    async def find(
        self,
        query: str,
        target_uri: str = "",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict] = None,
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

        # Infer context_type
        context_type = self._infer_context_type(target_uri) if target_uri else ContextType.RESOURCE

        typed_query = TypedQuery(
            query=query,
            context_type=context_type,
            intent="",
            target_directories=[target_uri] if target_uri else None,
        )

        result = await retriever.retrieve(
            typed_query,
            limit=limit,
            score_threshold=score_threshold,
            metadata_filter=filter,
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

        # When target_uri exists: read abstract, infer context_type
        target_context_type: Optional[ContextType] = None
        target_abstract = ""
        if target_uri:
            target_context_type = self._infer_context_type(target_uri)
            try:
                target_abstract = await self.abstract(target_uri)
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
                limit=limit,
                score_threshold=score_threshold,
                metadata_filter=filter,
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
    ) -> None:
        """Create relation (maintained in .relations.json)."""
        if isinstance(uris, str):
            uris = [uris]

        from_path = self._uri_to_path(from_uri)

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
    ) -> None:
        """Delete relation."""
        from_path = self._uri_to_path(from_uri)

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

    async def get_relation_table(self, uri: str) -> List[RelationEntry]:
        """Get relation table."""
        path = self._uri_to_path(uri)
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

    def _uri_to_path(self, uri: str) -> str:
        """viking://user/memories/preferences/test -> /local/user/memories/preferences/test"""
        remainder = uri[len("viking://") :].strip("/")
        if not remainder:
            return "/local"
        # Ensure each path component does not exceed filesystem filename limit
        parts = remainder.split("/")
        safe_parts = [self._shorten_component(p, self._MAX_FILENAME_BYTES) for p in parts]
        return f"/local/{'/'.join(safe_parts)}"

    def _path_to_uri(self, path: str) -> str:
        """/local/user/memories/preferences -> viking://user/memories/preferences"""
        if path.startswith("viking://"):
            return path
        elif path.startswith("/local/"):
            return f"viking://{path[7:]}"  # Remove /local prefix
        elif path.startswith("/"):
            return f"viking:/{path}"
        else:
            return f"viking://{path}"

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

    def _handle_agfs_content(self, result: Union[bytes, Any, None]) -> str:
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
        """Infer context_type from URI."""
        from openviking_cli.retrieve import ContextType

        if "/memories" in uri:
            return ContextType.MEMORY
        elif "/skills" in uri:
            return ContextType.SKILL
        return ContextType.RESOURCE

    # ========== Vector Sync Helper Methods ==========

    async def _collect_uris(self, path: str, recursive: bool) -> List[str]:
        """Recursively collect all URIs (for rm/mv)."""
        uris = []

        async def _collect(p: str):
            try:
                for entry in self.agfs.ls(p):
                    name = entry.get("name", "")
                    if name in [".", ".."]:
                        continue
                    full_path = f"{p}/{name}".replace("//", "/")
                    if entry.get("isDir"):
                        if recursive:
                            await _collect(full_path)
                    else:
                        uris.append(self._path_to_uri(full_path))
            except Exception:
                pass

        await _collect(path)
        return uris

    async def _delete_from_vector_store(self, uris: List[str]) -> None:
        """Delete records with specified URIs from vector store.

        Uses storage.remove_by_uri method, which implements recursive deletion of child nodes.
        """
        storage = self._get_vector_store()
        if not storage:
            return

        for uri in uris:
            try:
                await storage.remove_by_uri("context", uri)
                logger.info(f"[VikingFS] Deleted from vector store: {uri}")
            except Exception as e:
                logger.warning(f"[VikingFS] Failed to delete {uri} from vector store: {e}")

    async def _update_vector_store_uris(
        self, uris: List[str], old_base: str, new_base: str
    ) -> None:
        """Update URIs in vector store (when moving files).

        Preserves vector data, only updates uri and parent_uri fields, no need to regenerate embeddings.
        """
        storage = self._get_vector_store()
        if not storage:
            return

        old_base_uri = self._path_to_uri(old_base)
        new_base_uri = self._path_to_uri(new_base)

        for uri in uris:
            try:
                records = await storage.filter(
                    collection="context",
                    filter={"op": "must", "field": "uri", "conds": [uri]},
                    limit=1,
                )

                if not records or "id" not in records[0]:
                    continue

                record = records[0]
                record_id = record["id"]

                new_uri = uri.replace(old_base_uri, new_base_uri, 1)

                old_parent_uri = record.get("parent_uri", "")
                new_parent_uri = (
                    old_parent_uri.replace(old_base_uri, new_base_uri, 1) if old_parent_uri else ""
                )

                await storage.update(
                    "context",
                    record_id,
                    {
                        "uri": new_uri,
                        "parent_uri": new_parent_uri,
                    },
                )
                logger.info(f"[VikingFS] Updated URI: {uri} -> {new_uri}")
            except Exception as e:
                logger.warning(f"[VikingFS] Failed to update {uri} in vector store: {e}")

    def _get_vector_store(self) -> Optional["VikingDBInterface"]:
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

    async def read_batch(self, uris: List[str], level: str = "l0") -> Dict[str, str]:
        """Batch read content from multiple URIs."""
        results = {}
        for uri in uris:
            try:
                content = ""
                if level == "l0":
                    content = await self.abstract(uri)
                elif level == "l1":
                    content = await self.overview(uri)
                results[uri] = content
            except Exception:
                pass
        return results

    # ========== Other Preserved Methods ==========

    async def write_file(
        self,
        uri: str,
        content: Union[str, bytes],
    ) -> None:
        """Write file directly."""
        path = self._uri_to_path(uri)
        await self._ensure_parent_dirs(path)

        if isinstance(content, str):
            content = content.encode("utf-8")
        self.agfs.write(path, content)

    async def read_file(
        self,
        uri: str,
    ) -> str:
        """Read single file."""
        path = self._uri_to_path(uri)
        content = self.agfs.read(path)
        return self._handle_agfs_content(content)

    async def read_file_bytes(
        self,
        uri: str,
    ) -> bytes:
        """Read single binary file."""
        path = self._uri_to_path(uri)
        try:
            return self._handle_agfs_read(self.agfs.read(path))
        except Exception as e:
            raise FileNotFoundError(f"Failed to read {uri}: {e}")

    async def write_file_bytes(
        self,
        uri: str,
        content: bytes,
    ) -> None:
        """Write single binary file."""
        path = self._uri_to_path(uri)
        await self._ensure_parent_dirs(path)
        self.agfs.write(path, content)

    async def append_file(
        self,
        uri: str,
        content: str,
    ) -> None:
        """Append content to file."""
        path = self._uri_to_path(uri)

        try:
            existing = ""
            try:
                existing_bytes = self._handle_agfs_read(self.agfs.read(path))
                existing = existing_bytes.decode("utf-8")
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
        if output == "original":
            return await self._ls_original(uri, show_all_hidden)
        elif output == "agent":
            return await self._ls_agent(uri, abs_limit, show_all_hidden)
        else:
            raise ValueError(f"Invalid output format: {output}")

    async def _ls_agent(
        self, uri: str, abs_limit: int, show_all_hidden: bool
    ) -> List[Dict[str, Any]]:
        """List directory contents (URI version)."""
        path = self._uri_to_path(uri)
        try:
            entries = self.agfs.ls(path)
        except Exception as e:
            raise FileNotFoundError(f"Failed to list {uri}: {e}")
        # basic info
        now = datetime.now()
        all_entries = []
        for entry in entries:
            name = entry.get("name", "")
            new_entry = {
                "uri": str(VikingURI(uri).join(name)),
                "size": entry.get("size", 0),
                "isDir": entry.get("isDir", False),
                "modTime": format_simplified(datetime.fromisoformat(entry.get("modTime", "")), now),
            }
            if entry.get("isDir"):
                all_entries.append(new_entry)
            elif not name.startswith("."):
                all_entries.append(new_entry)
            elif show_all_hidden:
                all_entries.append(new_entry)
        # call abstract in parallel 6 threads
        await self._batch_fetch_abstracts(all_entries, abs_limit)
        return all_entries

    async def _ls_original(self, uri: str, show_all_hidden: bool = False) -> List[Dict[str, Any]]:
        """List directory contents (URI version)."""
        path = self._uri_to_path(uri)
        try:
            entries = self.agfs.ls(path)
            # AGFS returns read-only structure, need to create new dict
            all_entries = []
            for entry in entries:
                name = entry.get("name", "")
                new_entry = dict(entry)  # Copy original data
                new_entry["uri"] = str(VikingURI(uri).join(name))
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
    ) -> None:
        """Move file."""
        from_path = self._uri_to_path(from_uri)
        to_path = self._uri_to_path(to_uri)
        content = self.agfs.read(from_path)
        await self._ensure_parent_dirs(to_path)
        self.agfs.write(to_path, content)
        self.agfs.rm(from_path)

    # ========== Temp File Operations (backward compatible) ==========

    def create_temp_uri(self) -> str:
        """Create temp directory URI."""
        return VikingURI.create_temp_uri()

    async def delete_temp(self, temp_uri: str) -> None:
        """Delete temp directory and its contents."""
        path = self._uri_to_path(temp_uri)
        try:
            for entry in self.agfs.ls(path):
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                entry_path = f"{path}/{name}"
                if entry.get("isDir"):
                    await self.delete_temp(f"{temp_uri}/{name}")
                else:
                    self.agfs.rm(entry_path)
            self.agfs.rm(path)
        except Exception as e:
            logger.warning(f"[VikingFS] Failed to delete temp {temp_uri}: {e}")

    async def get_relations(self, uri: str) -> List[str]:
        """Get all related URIs (backward compatible)."""
        entries = await self.get_relation_table(uri)
        all_uris = []
        for entry in entries:
            all_uris.extend(entry.uris)
        return all_uris

    async def get_relations_with_content(
        self,
        uri: str,
        include_l0: bool = True,
        include_l1: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get related URIs and their content (backward compatible)."""
        relation_uris = await self.get_relations(uri)
        if not relation_uris:
            return []

        results = []
        abstracts = {}
        overviews = {}
        if include_l0:
            abstracts = await self.read_batch(relation_uris, level="l0")
        if include_l1:
            overviews = await self.read_batch(relation_uris, level="l1")

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
    ) -> None:
        """Write context to AGFS (L0/L1/L2)."""
        path = self._uri_to_path(uri)

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
