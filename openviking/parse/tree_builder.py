# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tree Builder for OpenViking.

Converts parsed document trees into OpenViking context objects with proper
L0/L1/L2 content and URI structure.

v5.0 Architecture:
1. Parser: parse + create directory structure in temp VikingFS
2. TreeBuilder: build final URI metadata and keep temp references
3. ResourceProcessor: source commit from temp to final VikingFS path
4. SemanticProcessor: async generate L0/L1 + vectorize

IMPORTANT (v5.0 Architecture):
- Parser creates directory structure directly, no LLM calls
- TreeBuilder does not move files; source commit is handled after URI metadata is built
- SemanticProcessor handles all semantic generation asynchronously
- Temporary directory approach eliminates memory pressure and enables concurrency
- Resource objects are lightweight (no content fields)
- Content splitting is handled by Parser, not TreeBuilder
"""

from typing import Optional

from openviking.core.building_tree import BuildingTree
from openviking.core.context import Context
from openviking.parse.parsers.media.utils import get_media_base_uri, get_media_type
from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils import parse_code_hosting_url
from openviking_cli.utils import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)


class TreeBuilder:
    """
    Builds OpenViking context tree from parsed documents (v5.0).

    New v5.0 Architecture:
    - Parser creates directory structure in temp VikingFS (no LLM calls)
    - TreeBuilder builds final URI metadata while preserving temp URIs
    - ResourceProcessor commits temp content to the final source path
    - SemanticProcessor handles semantic generation asynchronously

    Process flow:
    1. Parser creates directory structure with files in temp VikingFS
    2. TreeBuilder.finalize_from_temp() returns final URI and temp URI metadata
    3. ResourceProcessor performs source commit with short path locks
    4. SemanticProcessor generates .abstract.md and .overview.md asynchronously
    5. SemanticProcessor directly vectorizes and inserts to collection

    Key changes from v4.0:
    - Semantic generation moved from Parser to SemanticQueue
    - ResourceProcessor enqueues directories for async processing
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
        ctx: RequestContext,
        scope: str = "resources",
        to_uri: Optional[str] = None,
        parent_uri: Optional[str] = None,
        source_path: Optional[str] = None,
        source_format: Optional[str] = None,
        create_parent: bool = False,
    ) -> "BuildingTree":
        """
        Finalize URI metadata for a temp parse result.

        Args:
            to_uri: Exact target URI, or resources root to import under
            parent_uri: Target parent URI (must exist unless create_parent is True)
            create_parent: Whether to automatically create parent directory if it doesn't exist
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
        # This is critical for getting the full "org/repo" path instead of just repo name!
        # For example:
        #   - source_path = "https://github.com/volcengine/OpenViking"
        #   - parsed_org_repo = "volcengine/OpenViking"
        #   - final root_uri = "viking://resources/volcengine/OpenViking"
        #
        # Without this, we'd just get "viking://resources/OpenViking" without the org prefix
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
                effective_parent_uri = effective_parent_uri.rstrip("/")
            if effective_parent_uri:
                # Parent URI must exist and be a directory, or create it if requested
                try:
                    # First check if parent exists
                    parent_exists = await viking_fs.exists(effective_parent_uri, ctx=ctx)
                    if not parent_exists:
                        if create_parent:
                            # Automatically create parent directory
                            logger.info(
                                f"[TreeBuilder] Parent URI does not exist, creating: {effective_parent_uri}"
                            )
                            await viking_fs.mkdir(effective_parent_uri, exist_ok=True, ctx=ctx)
                        else:
                            raise FileNotFoundError(
                                f"Parent URI does not exist: {effective_parent_uri}. "
                                f"Use --parent-auto-create/-p to automatically create it."
                            )
                    # Now get stat result
                    stat_result = await viking_fs.stat(effective_parent_uri, ctx=ctx)
                except FileNotFoundError:
                    # Re-raise without wrapping
                    raise
                except Exception as e:
                    raise FileNotFoundError(
                        f"Parent URI does not exist: {effective_parent_uri}"
                    ) from e
                if not stat_result.get("isDir"):
                    raise ValueError(f"Parent URI is not a directory: {effective_parent_uri}")
                base_uri = effective_parent_uri
            candidate_uri = VikingURI(base_uri).join(final_doc_name).uri

        tree = BuildingTree(
            source_path=source_path,
            source_format=source_format,
        )
        tree._root_uri = candidate_uri
        if not to_uri or use_to_as_parent:
            tree._candidate_uri = candidate_uri

        # Create a minimal Context object for the root so that tree.root is not None
        root_context = Context(uri=candidate_uri, temp_uri=temp_doc_uri)
        tree.add_context(root_context)

        return tree
