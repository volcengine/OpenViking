# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tree Builder for OpenViking.

Converts parsed document trees into OpenViking context objects with proper
L0/L1/L2 content and URI structure.

v5.0 Architecture:
1. Parser: parse + create directory structure in temp VikingFS
2. TreeBuilder: move to AGFS + enqueue to SemanticQueue + create Resources
3. SemanticProcessor: async generate L0/L1 + vectorize

IMPORTANT (v5.0 Architecture):
- Parser creates directory structure directly, no LLM calls
- TreeBuilder moves files and enqueues to SemanticQueue
- SemanticProcessor handles all semantic generation asynchronously
- Temporary directory approach eliminates memory pressure and enables concurrency
- Resource objects are lightweight (no content fields)
- Content splitting is handled by Parser, not TreeBuilder
"""

import logging
from typing import TYPE_CHECKING, Optional

from openviking.core.building_tree import BuildingTree
from openviking.parse.parsers.media.utils import get_media_base_uri, get_media_type
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.uri import VikingURI

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TreeBuilder:
    """
    Builds OpenViking context tree from parsed documents (v5.0).

    New v5.0 Architecture:
    - Parser creates directory structure in temp VikingFS (no LLM calls)
    - TreeBuilder moves to AGFS + enqueues to SemanticQueue + creates Resources
    - SemanticProcessor handles semantic generation asynchronously

    Process flow:
    1. Parser creates directory structure with files in temp VikingFS
    2. TreeBuilder.finalize_from_temp() moves to AGFS, enqueues to SemanticQueue, creates Resources
    3. SemanticProcessor generates .abstract.md and .overview.md asynchronously
    4. SemanticProcessor directly vectorizes and inserts to collection

    Key changes from v4.0:
    - Semantic generation moved from Parser to SemanticQueue
    - TreeBuilder enqueues directories for async processing
    - Direct vectorization in SemanticProcessor (no EmbeddingQueue)
    """

    def __init__(self):
        """Initialize TreeBuilder."""
        pass

    def _get_base_uri(
        self, scope: str, source_path: Optional[str] = None, source_format: Optional[str] = None
    ) -> str:
        """Get base URI for scope, with special handling for media files."""
        # Check if it's a media file first
        if scope == "resources":
            media_type = get_media_type(source_path, source_format)
            if media_type:
                return get_media_base_uri(media_type)
            return "viking://resources"
        if scope == "user":
            # user resources go to memories (no separate resources dir)
            return "viking://user"
        # Agent scope
        return "viking://agent"

    # ============================================================================
    # v5.0 Methods (temporary directory + SemanticQueue architecture)
    # ============================================================================

    async def finalize_from_temp(
        self,
        temp_dir_path: str,
        scope: str,
        base_uri: Optional[str] = None,
        source_path: Optional[str] = None,
        source_format: Optional[str] = None,
    ) -> "BuildingTree":
        """
        Finalize tree from temporary directory (v5.0 architecture).

        New architecture:
        1. Move directory to AGFS
        2. Enqueue to SemanticQueue for async semantic generation
        3. Scan and create Resource objects (for compatibility)

        Args:
            temp_dir_path: Temporary directory Viking URI (e.g., viking://temp/xxx)
            scope: Scope ("resources", "user", or "agent")
            base_uri: Base URI (None = use scope default)
            source_node: Source ResourceNode
            source_path: Source file path
            source_format: Source file format

        Returns:
            Complete BuildingTree with all resources moved to AGFS
        """

        viking_fs = get_viking_fs()
        temp_uri = temp_dir_path

        # 1. Find document root directory
        entries = await viking_fs.ls(temp_uri)
        doc_dirs = [e for e in entries if e.get("isDir") and e["name"] not in [".", ".."]]

        if len(doc_dirs) != 1:
            logger.error(
                f"[TreeBuilder] Expected 1 document directory in {temp_uri}, found {len(doc_dirs)}"
            )
            raise ValueError(
                f"[TreeBuilder] Expected 1 document directory in {temp_uri}, found {len(doc_dirs)}"
            )

        doc_name = VikingURI.sanitize_segment(doc_dirs[0]["name"])
        temp_doc_uri = f"{temp_uri}/{doc_name}"

        # 2. Determine base_uri
        if base_uri is None:
            base_uri = self._get_base_uri(scope, source_path, source_format)

        # 3. Build final URI, auto-renaming on conflict (e.g. doc_1, doc_2, ...)
        candidate_uri = VikingURI(base_uri).join(doc_name).uri
        final_uri = await self._resolve_unique_uri(candidate_uri)
        if final_uri != candidate_uri:
            logger.info(f"[TreeBuilder] Resolved name conflict: {candidate_uri} -> {final_uri}")
        else:
            logger.info(f"[TreeBuilder] Finalizing from temp: {final_uri}")

        # 4. Move directory tree from temp to final location in AGFS
        await self._move_directory_in_agfs(temp_doc_uri, final_uri)
        logger.info(f"[TreeBuilder] Moved temp tree: {temp_doc_uri} -> {final_uri}")

        # 5. Cleanup temporary root directory
        try:
            await viking_fs.delete_temp(temp_uri)
            logger.info(f"[TreeBuilder] Cleaned up temp root: {temp_uri}")
        except Exception as e:
            logger.warning(f"[TreeBuilder] Failed to cleanup temp root: {e}")

        # 6. Enqueue to SemanticQueue for async semantic generation
        try:
            await self._enqueue_semantic_generation(final_uri, "resource")
            logger.info(f"[TreeBuilder] Enqueued semantic generation for: {final_uri}")
        except Exception as e:
            logger.error(f"[TreeBuilder] Failed to enqueue semantic generation: {e}", exc_info=True)

        # 7. Return simple BuildingTree (no scanning needed)
        tree = BuildingTree(
            source_path=source_path,
            source_format=source_format,
        )
        tree._root_uri = final_uri

        return tree

    async def _resolve_unique_uri(self, uri: str, max_attempts: int = 100) -> str:
        """Return a URI that does not collide with an existing resource.

        If *uri* is free, return it unchanged.  Otherwise append ``_1``,
        ``_2``, … until a free name is found (like macOS Finder / Windows
        Explorer).
        """
        viking_fs = get_viking_fs()

        async def _exists(u: str) -> bool:
            try:
                await viking_fs.stat(u)
                return True
            except Exception:
                return False

        if not await _exists(uri):
            return uri

        for i in range(1, max_attempts + 1):
            candidate = f"{uri}_{i}"
            if not await _exists(candidate):
                return candidate

        raise FileExistsError(f"Cannot resolve unique name for {uri} after {max_attempts} attempts")

    async def _move_directory_in_agfs(self, src_uri: str, dst_uri: str) -> None:
        """Recursively move AGFS directory tree (copy + delete)."""
        viking_fs = get_viking_fs()

        # 1. Ensure parent directories exist
        await self._ensure_parent_dirs(dst_uri)

        # 2. Create target directory
        await viking_fs.mkdir(dst_uri)

        # 3. List source directory contents
        entries = await viking_fs.ls(src_uri)

        for entry in entries:
            name = entry.get("name", "")
            if not name or name in [".", ".."]:
                continue

            src_item = f"{src_uri}/{name}"
            dst_item = f"{dst_uri}/{name}"

            if entry.get("isDir"):
                # Recursively move subdirectory
                await self._move_directory_in_agfs(src_item, dst_item)
            else:
                # Move file
                await viking_fs.move_file(src_item, dst_item)

        # 4. Delete source directory (should be empty now)
        try:
            await viking_fs.rm(src_uri)
        except Exception:
            pass  # Ignore error when deleting empty directory

    async def _ensure_parent_dirs(self, uri: str) -> None:
        """Recursively create parent directories."""
        viking_fs = get_viking_fs()
        parent = VikingURI(uri).parent
        if not parent:
            return
        parent_uri = parent.uri
        # Recursively ensure parent's parent exists
        await self._ensure_parent_dirs(parent_uri)

        # Create parent directory (ignore if already exists)
        try:
            await viking_fs.mkdir(parent_uri)
            logger.debug(f"Created parent directory: {parent_uri}")
        except Exception as e:
            # Directory may already exist, ignore error
            if "exist" not in str(e).lower():
                logger.debug(f"Parent dir {parent_uri} may already exist: {e}")

    async def _enqueue_semantic_generation(self, uri: str, context_type: str) -> None:
        """
        Enqueue a directory for semantic generation.

        Args:
            uri: Directory URI to enqueue
            context_type: resource/memory/skill
        """

        queue_manager = get_queue_manager()

        # Get semantic queue
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)

        # Sort by depth (descending) for bottom-up processing
        msg = SemanticMsg(
            uri=uri,
            context_type=context_type,
        )
        await semantic_queue.enqueue(msg)

    async def _load_content(self, uri: str, content_type: str) -> str:
        """Helper to load content with proper type handling"""
        import json

        if content_type == "abstract":
            result = await get_viking_fs().abstract(uri)
        elif content_type == "overview":
            result = await get_viking_fs().overview(uri)
        elif content_type == "detail":
            result = await get_viking_fs().read_file(uri)
        else:
            return ""

        # Handle different return types
        if isinstance(result, str):
            return result
        elif isinstance(result, bytes):
            return result.decode("utf-8")
        elif hasattr(result, "to_dict") and not isinstance(result, list):
            # Handle FindResult by converting to dict (skip lists)
            return str(result.to_dict())
        elif isinstance(result, list):
            # Handle list results
            return json.dumps(result)
        else:
            return str(result)
