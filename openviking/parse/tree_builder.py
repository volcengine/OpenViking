# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
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
from typing import Optional

from openviking.core.building_tree import BuildingTree
from openviking.core.context import Context
from openviking.parse.parsers.media.utils import get_media_base_uri, get_media_type
from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils import parse_code_hosting_url
from openviking_cli.utils.uri import VikingURI

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

    async def _resolve_unique_uri(self, uri: str, max_attempts: int = 100) -> str:
        """Return a URI that does not collide with an existing resource.

        If *uri* is free, return it unchanged.  Otherwise append ``_1``,
        ``_2``, ... until a free name is found.
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

    # ============================================================================
    # v5.0 Methods (temporary directory + SemanticQueue architecture)
    # ============================================================================

    async def finalize_from_temp(
        self,
        temp_dir_path: str,
        ctx: RequestContext,
        scope: str = "resources",
        to_uri: Optional[str] = None,
        parent_uri: Optional[str] = None,
        source_path: Optional[str] = None,
        source_format: Optional[str] = None,
    ) -> "BuildingTree":
        """
        Finalize processing by moving from temp to AGFS.

        Args:
            to_uri: Exact target URI (must not exist)
            parent_uri: Target parent URI (must exist)
        """

        viking_fs = get_viking_fs()
        temp_uri = temp_dir_path

        def is_resources_root(uri: Optional[str]) -> bool:
            return (uri or "").rstrip("/") == "viking://resources"

        # 1. Find document root directory
        entries = await viking_fs.ls(temp_uri, ctx=ctx)
        doc_dirs = [e for e in entries if e.get("isDir") and e["name"] not in [".", ".."]]

        if len(doc_dirs) != 1:
            logger.error(
                f"[TreeBuilder] Expected 1 document directory in {temp_uri}, found {len(doc_dirs)}"
            )
            raise ValueError(
                f"[TreeBuilder] Expected 1 document directory in {temp_uri}, found {len(doc_dirs)}"
            )

        original_name = doc_dirs[0]["name"]
        doc_name = VikingURI.sanitize_segment(original_name)
        temp_doc_uri = f"{temp_uri}/{original_name}"  # use original name to find temp dir
        if original_name != doc_name:
            logger.debug(f"[TreeBuilder] Sanitized doc name: {original_name!r} -> {doc_name!r}")

        # Check if source_path is a GitHub/GitLab URL and extract org/repo
        final_doc_name = doc_name
        if source_path and source_format == "repository":
            parsed_org_repo = parse_code_hosting_url(source_path)
            if parsed_org_repo:
                final_doc_name = parsed_org_repo

        # 2. Determine base_uri and final document name with org/repo for GitHub/GitLab
        auto_base_uri = self._get_base_uri(scope, source_path, source_format)
        base_uri = parent_uri or auto_base_uri
        use_to_as_parent = is_resources_root(to_uri)
        # 3. Determine candidate_uri
        if to_uri and not use_to_as_parent:
            candidate_uri = to_uri
        else:
            effective_parent_uri = parent_uri or to_uri if use_to_as_parent else parent_uri
            if effective_parent_uri:
                # Parent URI must exist and be a directory
                try:
                    stat_result = await viking_fs.stat(effective_parent_uri, ctx=ctx)
                except Exception as e:
                    raise FileNotFoundError(
                        f"Parent URI does not exist: {effective_parent_uri}"
                    ) from e
                if not stat_result.get("isDir"):
                    raise ValueError(f"Parent URI is not a directory: {effective_parent_uri}")
                base_uri = effective_parent_uri
            candidate_uri = VikingURI(base_uri).join(final_doc_name).uri

        if to_uri and not use_to_as_parent:
            final_uri = candidate_uri
        elif use_to_as_parent:
            # Treat an explicit resources root target as "import under this
            # directory" while preserving the child URI so downstream logic can
            # incrementally update viking://resources/<child> when it exists.
            final_uri = candidate_uri
        else:
            final_uri = await self._resolve_unique_uri(candidate_uri)

        tree = BuildingTree(
            source_path=source_path,
            source_format=source_format,
        )
        tree._root_uri = final_uri
        if not to_uri or use_to_as_parent:
            tree._candidate_uri = candidate_uri

        # Create a minimal Context object for the root so that tree.root is not None
        root_context = Context(uri=final_uri, temp_uri=temp_doc_uri)
        tree.add_context(root_context)

        return tree
