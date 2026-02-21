# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Context Processor for OpenViking.

Handles coordinated writes and self-iteration processes
as described in the OpenViking design document.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional

from openviking.parse.tree_builder import TreeBuilder
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import get_logger
from openviking_cli.utils.storage import StoragePath

if TYPE_CHECKING:
    from openviking.parse.vlm import VLMProcessor

logger = get_logger(__name__)


class ResourceProcessor:
    """
    Handles coordinated write operations.

    When new data is added, automatically:
    1. Download if URL (prefer PDF format)
    2. Parse and structure the content (Parser writes to temp directory)
    3. Extract images/tables for mixed content
    4. Use VLM to understand non-text content
    5. TreeBuilder finalizes from temp (move to AGFS)
    6. SemanticQueue generates L0/L1 and vectorizes asynchronously
    """

    def __init__(
        self,
        vikingdb: VikingDBManager,
        media_storage: Optional["StoragePath"] = None,
        max_context_size: int = 2000,
        max_split_depth: int = 3,
    ):
        """Initialize coordinated writer."""
        self.vikingdb = vikingdb
        self.embedder = vikingdb.get_embedder()
        self.media_storage = media_storage
        self.tree_builder = TreeBuilder()
        self._vlm_processor = None
        self._media_processor = None

    def _get_vlm_processor(self) -> "VLMProcessor":
        """Lazy initialization of VLM processor."""
        if self._vlm_processor is None:
            from openviking.parse.vlm import VLMProcessor

            self._vlm_processor = VLMProcessor()
        return self._vlm_processor

    def _get_media_processor(self):
        """Lazy initialization of unified media processor."""
        if self._media_processor is None:
            from openviking.utils.media_processor import UnifiedResourceProcessor

            self._media_processor = UnifiedResourceProcessor(
                vlm_processor=self._get_vlm_processor(),
                storage=self.media_storage,
            )
        return self._media_processor

    async def process_resource(
        self,
        path: str,
        reason: str = "",
        instruction: str = "",
        scope: str = "resources",
        user: Optional[str] = None,
        target: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Process and store a new resource.

        Workflow:
        1. Parse source (writes to temp directory)
        2. TreeBuilder moves to AGFS
        3. SemanticQueue generates L0/L1 and vectorizes asynchronously
        """
        result = {
            "status": "success",
            "errors": [],
            "source_path": None,
        }

        # ============ Phase 1: Parse source (Parser generates L0/L1 and writes to temp) ============
        try:
            media_processor = self._get_media_processor()
            parse_result = await media_processor.process(
                source=path,
                instruction=instruction,
                **kwargs,
            )
            result["source_path"] = parse_result.source_path or path
            result["meta"] = parse_result.meta

            # Only abort when no temp content was produced at all.
            # For directory imports partial success (some files failed) is
            # normal – finalization should still proceed.
            if not parse_result.temp_dir_path:
                result["status"] = "error"
                result["errors"].extend(
                    parse_result.warnings or ["Parse failed: no content generated"],
                )
                return result

            if parse_result.warnings:
                result["errors"].extend(parse_result.warnings)

        except Exception as e:
            result["status"] = "error"
            result["errors"].append(f"Parse error: {e}")
            logger.error(f"[ResourceProcessor] Parse error: {e}")
            return result

        # parse_result contains:
        # - root: ResourceNode tree (with L0/L1 in meta)
        # - temp_dir_path: Temporary directory path (Parser wrote all files)
        # - source_path, source_format

        # ============ Phase 2: Determine target location ============
        located_uri = None
        if target:
            if target.startswith("viking://"):
                located_uri = target
            else:
                located_uri = f"viking://resources/{target}"
            logger.info(f"Using target location: {located_uri}")

        # ============ Phase 3: TreeBuilder finalizes from temp (scan + move to AGFS) ============
        try:
            context_tree = await self.tree_builder.finalize_from_temp(
                temp_dir_path=parse_result.temp_dir_path,
                scope=scope,
                base_uri=located_uri,
                source_path=parse_result.source_path,
                source_format=parse_result.source_format,
            )
        except Exception as e:
            result["status"] = "error"
            result["errors"].append(f"Finalize from temp error: {e}")

            # Cleanup temporary directory on error (via VikingFS)
            try:
                if parse_result.temp_dir_path:
                    await get_viking_fs().delete_temp(parse_result.temp_dir_path)
            except Exception:
                pass

            return result

        # Check media strategy

        result["root_uri"] = context_tree._root_uri
        return result
