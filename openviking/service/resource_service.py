# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Resource Service for OpenViking.

Provides resource management operations: add_resource, add_skill, wait_processed.
"""

from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.storage import VikingDBManager
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.viking_fs import VikingFS
from openviking.utils.resource_processor import ResourceProcessor
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.exceptions import (
    DeadlineExceededError,
    InvalidArgumentError,
    NotInitializedError,
)
from openviking_cli.utils import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)


class ResourceService:
    """Resource management service."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        viking_fs: Optional[VikingFS] = None,
        resource_processor: Optional[ResourceProcessor] = None,
        skill_processor: Optional[SkillProcessor] = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._resource_processor = resource_processor
        self._skill_processor = skill_processor

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        viking_fs: VikingFS,
        resource_processor: ResourceProcessor,
        skill_processor: SkillProcessor,
    ) -> None:
        """Set dependencies (for deferred initialization)."""
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._resource_processor = resource_processor
        self._skill_processor = skill_processor

    def _ensure_initialized(self) -> None:
        """Ensure all dependencies are initialized."""
        if not self._resource_processor:
            raise NotInitializedError("ResourceProcessor")
        if not self._skill_processor:
            raise NotInitializedError("SkillProcessor")
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")

    async def add_resource(
        self,
        path: str,
        ctx: RequestContext,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
        build_index: bool = True,
        summarize: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking (only supports resources scope).

        Args:
            path: Resource path (local file or URL)
            target: Target URI
            reason: Reason for adding
            instruction: Processing instruction
            wait: Whether to wait for semantic extraction and vectorization to complete
            timeout: Wait timeout in seconds
            build_index: Whether to build vector index immediately (default: True).
            summarize: Whether to generate summary (default: False).
            **kwargs: Extra options forwarded to the parser chain.

        Returns:
            Processing result
        """
        self._ensure_initialized()

        # add_resource only supports resources scope
        if to and to.startswith("viking://"):
            parsed = VikingURI(to)
            if parsed.scope != "resources":
                raise InvalidArgumentError(
                    f"add_resource only supports resources scope, use dedicated interface to add {parsed.scope} content"
                )
        if parent and parent.startswith("viking://"):
            parsed = VikingURI(parent)
            if parsed.scope != "resources":
                raise InvalidArgumentError(
                    f"add_resource only supports resources scope, use dedicated interface to add {parsed.scope} content"
                )

        result = await self._resource_processor.process_resource(
            path=path,
            ctx=ctx,
            reason=reason,
            instruction=instruction,
            scope="resources",
            to=to,
            parent=parent,
            build_index=build_index,
            summarize=summarize,
            **kwargs,
        )

        if wait:
            qm = get_queue_manager()
            try:
                status = await qm.wait_complete(timeout=timeout)
            except TimeoutError as exc:
                raise DeadlineExceededError("queue processing", timeout) from exc
            result["queue_status"] = {
                name: {
                    "processed": s.processed,
                    "error_count": s.error_count,
                    "errors": [{"message": e.message} for e in s.errors],
                }
                for name, s in status.items()
            }

        return result

    async def add_skill(
        self,
        data: Any,
        ctx: RequestContext,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking.

        Args:
            data: Skill data (directory path, file path, string, or dict)
            wait: Whether to wait for vectorization to complete
            timeout: Wait timeout in seconds

        Returns:
            Processing result
        """
        self._ensure_initialized()

        result = await self._skill_processor.process_skill(
            data=data,
            viking_fs=self._viking_fs,
            ctx=ctx,
        )

        if wait:
            qm = get_queue_manager()
            try:
                status = await qm.wait_complete(timeout=timeout)
            except TimeoutError as exc:
                raise DeadlineExceededError("queue processing", timeout) from exc
            result["queue_status"] = {
                name: {
                    "processed": s.processed,
                    "error_count": s.error_count,
                    "errors": [{"message": e.message} for e in s.errors],
                }
                for name, s in status.items()
            }

        return result

    async def build_index(
        self, resource_uris: List[str], ctx: RequestContext, **kwargs
    ) -> Dict[str, Any]:
        """Manually trigger index building.

        Args:
            resource_uris: List of resource URIs to index.
            ctx: Request context.

        Returns:
            Processing result
        """
        self._ensure_initialized()
        return await self._resource_processor.build_index(resource_uris, ctx, **kwargs)

    async def summarize(
        self, resource_uris: List[str], ctx: RequestContext, **kwargs
    ) -> Dict[str, Any]:
        """Manually trigger summarization.

        Args:
            resource_uris: List of resource URIs to summarize.
            ctx: Request context.

        Returns:
            Processing result
        """
        self._ensure_initialized()
        return await self._resource_processor.summarize(resource_uris, ctx, **kwargs)

    async def reindex(
        self,
        uri: str = "viking://resources/",
        ctx: Optional["RequestContext"] = None,
        force: bool = False,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Re-generate L0/L1 summaries and rebuild vector index for stale or new entries.

        Scans the AGFS tree under *uri*.  For every leaf directory that
        contains L2 content files:

        * **New** – no ``.abstract.md`` exists → enqueue for semantic processing.
        * **Modified** – L2 file ``modTime`` is newer than ``.abstract.md`` →
          delete old vectors, enqueue for re-processing.
        * **force=True** – unconditionally re-process everything.

        Args:
            uri: Root URI to scan (default: all resources).
            ctx: Request context.
            force: Re-process even if L0/L1 appear up-to-date.
            wait: Block until all queued processing finishes.
            timeout: Maximum seconds to wait (only when *wait* is True).

        Returns:
            Summary dict with counts of *skipped*, *new*, *modified*, and
            *enqueued* entries.
        """
        self._ensure_initialized()

        viking_fs = self._viking_fs
        vikingdb = self._vikingdb

        stats: Dict[str, int] = {"scanned": 0, "skipped": 0, "new": 0, "modified": 0, "enqueued": 0}
        dirs_to_reindex: list[str] = []

        # Walk the tree and decide which directories need re-processing.
        entries = await viking_fs.ls(uri, show_all_hidden=True, node_limit=100000, ctx=ctx)
        await self._collect_stale_dirs(viking_fs, uri, entries, force, dirs_to_reindex, stats, ctx)

        # Delete existing vectors and enqueue semantic processing for each stale dir.
        if dirs_to_reindex:
            queue_manager = get_queue_manager()
            semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)

            for dir_uri in dirs_to_reindex:
                # Remove old vectors so they get rebuilt
                if vikingdb:
                    try:
                        await vikingdb.remove_by_uri(dir_uri, ctx=ctx)
                    except Exception as e:
                        logger.warning(f"Failed to remove old vectors for {dir_uri}: {e}")

                msg = SemanticMsg(
                    uri=dir_uri,
                    context_type=self._context_type_for_uri(dir_uri),
                    recursive=False,  # we already collected individual dirs
                    account_id=ctx.account_id if ctx else "default",
                    user_id=ctx.user.user_id if ctx else "default",
                    agent_id=ctx.user.agent_id if ctx else "default",
                    role=ctx.role.value if ctx else "root",
                )
                await semantic_queue.enqueue(msg)
                stats["enqueued"] += 1

            logger.info(f"Reindex enqueued {stats['enqueued']} directories under {uri}")

        if wait:
            qm = get_queue_manager()
            try:
                status = await qm.wait_complete(timeout=timeout)
            except TimeoutError as exc:
                raise DeadlineExceededError("queue processing", timeout) from exc
            stats["queue_status"] = {
                name: {
                    "processed": s.processed,
                    "error_count": s.error_count,
                    "errors": [{"message": e.message} for e in s.errors],
                }
                for name, s in status.items()
            }

        return stats

    async def _collect_stale_dirs(
        self,
        viking_fs: "VikingFS",
        parent_uri: str,
        entries: List[Dict[str, Any]],
        force: bool,
        result: list[str],
        stats: Dict[str, int],
        ctx: Optional["RequestContext"] = None,
    ) -> None:
        """Recursively collect directory URIs that need re-indexing."""
        from datetime import datetime

        abstract_mtime = None
        has_l2_files = False
        max_l2_mtime = None

        for entry in entries:
            name = entry.get("name", "")
            is_dir = entry.get("isDir", False)
            entry_uri = entry.get("uri", f"{parent_uri}/{name}")

            if is_dir and not name.startswith("."):
                # Recurse into subdirectories
                sub_entries = await viking_fs.ls(entry_uri, show_all_hidden=True, node_limit=100000, ctx=ctx)
                await self._collect_stale_dirs(viking_fs, entry_uri, sub_entries, force, result, stats, ctx)
                continue

            mod_time_str = entry.get("modTime", "")

            if name == ".abstract.md" and mod_time_str:
                try:
                    abstract_mtime = datetime.fromisoformat(mod_time_str)
                except (ValueError, TypeError):
                    pass
                continue

            # Skip other hidden/meta files
            if name.startswith("."):
                continue

            # This is an L2 content file
            if not is_dir:
                has_l2_files = True
                if mod_time_str:
                    try:
                        file_mtime = datetime.fromisoformat(mod_time_str)
                        if max_l2_mtime is None or file_mtime > max_l2_mtime:
                            max_l2_mtime = file_mtime
                    except (ValueError, TypeError):
                        pass

        stats["scanned"] += 1

        if not has_l2_files:
            stats["skipped"] += 1
            return

        if force:
            stats["modified"] += 1
            result.append(parent_uri)
        elif abstract_mtime is None:
            # No .abstract.md → new, never indexed
            stats["new"] += 1
            result.append(parent_uri)
        elif max_l2_mtime and max_l2_mtime > abstract_mtime:
            # L2 files modified after last indexing
            stats["modified"] += 1
            result.append(parent_uri)
        else:
            stats["skipped"] += 1

    @staticmethod
    def _context_type_for_uri(uri: str) -> str:
        """Derive context_type from URI scope."""
        if "memory" in uri or "memories" in uri:
            return "memory"
        if "skill" in uri:
            return "skill"
        return "resource"

    async def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Wait for all queued processing to complete.

        Args:
            timeout: Wait timeout in seconds

        Returns:
            Queue status
        """
        qm = get_queue_manager()
        try:
            status = await qm.wait_complete(timeout=timeout)
        except TimeoutError as exc:
            raise DeadlineExceededError("queue processing", timeout) from exc
        return {
            name: {
                "processed": s.processed,
                "error_count": s.error_count,
                "errors": [{"message": e.message} for e in s.errors],
            }
            for name, s in status.items()
        }
