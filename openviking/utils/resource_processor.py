# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Context Processor for OpenViking.

Handles coordinated writes and self-iteration processes
as described in the OpenViking design document.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openviking.parse.tree_builder import TreeBuilder
from openviking.server.identity import RequestContext
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import get_current_telemetry
from openviking.utils.embedding_utils import index_resource
from openviking.utils.summarizer import Summarizer
from openviking_cli.exceptions import OpenVikingError
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
        self._summarizer = None

    def _get_summarizer(self) -> "Summarizer":
        """Lazy initialization of Summarizer."""
        if self._summarizer is None:
            self._summarizer = Summarizer(self._get_vlm_processor())
        return self._summarizer

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

    async def build_index(
        self, resource_uris: List[str], ctx: RequestContext, **kwargs
    ) -> Dict[str, Any]:
        """Expose index building as a standalone method."""
        for uri in resource_uris:
            await index_resource(uri, ctx)
        return {"status": "success", "message": f"Indexed {len(resource_uris)} resources"}

    async def summarize(
        self, resource_uris: List[str], ctx: RequestContext, **kwargs
    ) -> Dict[str, Any]:
        """Expose summarization as a standalone method."""
        return await self._get_summarizer().summarize(resource_uris, ctx, **kwargs)

    async def process_resource(
        self,
        path: str,
        ctx: RequestContext,
        reason: str = "",
        instruction: str = "",
        scope: str = "resources",
        user: Optional[str] = None,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        summarize: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Process and store a new resource.

        Workflow:
        1. Parse source (writes to temp directory)
        2. TreeBuilder builds final URI metadata
        3. Source commit moves temp content to the final path
        4. (Optional) Build vector index
        5. (Optional) Summarize
        """
        result = {
            "status": "success",
            "errors": [],
            "source_path": None,
        }
        telemetry = get_current_telemetry()

        with telemetry.measure("resource.process"):
            # ============ Phase 1: Parse source and writes to temp viking fs ============
            try:
                from openviking.metrics.datasources.resource import (
                    ResourceIngestionEventDataSource,
                )

                parse_start = time.perf_counter()
                stage_start = time.perf_counter()
                stage_status = "ok"
                media_processor = self._get_media_processor()
                viking_fs = get_viking_fs()
                # Use reason as instruction fallback so it influences L0/L1
                # generation and improves search relevance as documented.
                effective_instruction = instruction or reason
                with viking_fs.bind_request_context(ctx):
                    parse_result = await media_processor.process(
                        source=path,
                        instruction=effective_instruction,
                        **kwargs,
                    )
                result["source_path"] = parse_result.source_path or path
                result["meta"] = parse_result.meta

                # Only abort when no temp content was produced at all.
                # For directory imports partial success (some files failed) is
                # normal - finalization should still proceed.
                if not parse_result.temp_dir_path:
                    result["status"] = "error"
                    result["errors"].extend(
                        parse_result.warnings or ["Parse failed: no content generated"],
                    )
                    stage_status = "error"
                    return result

                if parse_result.warnings and kwargs.get("strict", False):
                    result.setdefault("warnings", []).extend(parse_result.warnings)

                telemetry.set(
                    "resource.parse.duration_ms",
                    round((time.perf_counter() - parse_start) * 1000, 3),
                )
                telemetry.set("resource.parse.warnings_count", len(parse_result.warnings or []))

            except OpenVikingError:
                stage_status = "error"
                raise
            except Exception as e:
                result["status"] = "error"
                result["errors"].append(f"Parse error: {e}")
                logger.error(f"[ResourceProcessor] Parse error: {e}")
                telemetry.set_error("resource_processor.parse", "PROCESSING_ERROR", str(e))
                import traceback

                traceback.print_exc()
                stage_status = "error"
                return result
            finally:
                try:
                    ResourceIngestionEventDataSource.record_stage(
                        stage="parse",
                        status=str(stage_status),
                        duration_seconds=float(time.perf_counter() - stage_start),
                        account_id=getattr(ctx, "account_id", None),
                    )
                except Exception:
                    pass

            # parse_result contains:
            # - root: ResourceNode tree (with L0/L1 in meta)
            # - temp_dir_path: Temporary directory path (Parser wrote all files)
            # - source_path, source_format

            # ============ Phase 3: TreeBuilder finalizes from temp (scan + move to AGFS) ============
            try:
                stage_start = time.perf_counter()
                stage_status = "ok"
                finalize_start = time.perf_counter()
                with get_viking_fs().bind_request_context(ctx):
                    context_tree = await self.tree_builder.finalize_from_temp(
                        temp_dir_path=parse_result.temp_dir_path,
                        ctx=ctx,
                        scope=scope,
                        to_uri=to,
                        parent_uri=parent,
                        source_path=parse_result.source_path,
                        source_format=parse_result.source_format,
                        create_parent=kwargs.get("create_parent", False),
                    )
                    if context_tree and context_tree.root:
                        result["root_uri"] = context_tree.root.uri
                        result["temp_uri"] = context_tree.root.temp_uri
                telemetry.set(
                    "resource.finalize.duration_ms",
                    round((time.perf_counter() - finalize_start) * 1000, 3),
                )
            except Exception as e:
                result["status"] = "error"
                result["errors"].append(f"Finalize from temp error: {e}")
                telemetry.set_error("resource_processor.finalize", "PROCESSING_ERROR", str(e))
                stage_status = "error"

                # Cleanup temporary directory on error (via VikingFS)
                try:
                    if parse_result.temp_dir_path:
                        await get_viking_fs().delete_temp(parse_result.temp_dir_path, ctx=ctx)
                except Exception:
                    pass

                return result
            finally:
                try:
                    ResourceIngestionEventDataSource.record_stage(
                        stage="finalize",
                        status=str(stage_status),
                        duration_seconds=float(time.perf_counter() - stage_start),
                        account_id=getattr(ctx, "account_id", None),
                    )
                except Exception:
                    pass

            # ============ Phase 3.5: Source commit + lifecycle lock ============
            root_uri = result.get("root_uri")
            temp_uri = result.get("temp_uri")  # temp_doc_uri
            original_temp_uri = temp_uri  # 保存原始 temp_uri 用于最终输出
            candidate_uri = getattr(context_tree, "_candidate_uri", None) if context_tree else None
            lifecycle_lock_handle_id = ""

            if root_uri and temp_uri:
                from openviking.storage.transaction import get_lock_manager

                stage_start = time.perf_counter()
                stage_status = "ok"
                viking_fs = get_viking_fs()
                lock_manager = get_lock_manager()
                try:
                    if candidate_uri:
                        root_uri, lifecycle_lock_handle_id = await self._commit_unique_candidate(
                            candidate_uri=candidate_uri,
                            ctx=ctx,
                        )
                        result["root_uri"] = root_uri
                    else:
                        dst_path = viking_fs._uri_to_path(root_uri, ctx=ctx)
                        handle = lock_manager.create_handle()
                        try:
                            lifecycle_lock_handle_id = await self._acquire_lifecycle_lock(
                                lock_manager, dst_path, uri=root_uri, handle=handle
                            )
                        except Exception:
                            await lock_manager.release(handle)
                            lifecycle_lock_handle_id = ""
                            raise
                except Exception:
                    stage_status = "error"
                    raise
                finally:
                    try:
                        ResourceIngestionEventDataSource.record_stage(
                            stage="persist",
                            status=str(stage_status),
                            duration_seconds=float(time.perf_counter() - stage_start),
                            account_id=getattr(ctx, "account_id", None),
                        )
                    except Exception:
                        pass

            # ============ Phase 4: Optional Steps ============
            build_index = kwargs.get("build_index", True)
            temp_uri_for_summarize = temp_uri or parse_result.temp_dir_path
            should_summarize = summarize or build_index
            if should_summarize:
                skip_vec = not build_index
                is_code_repo = parse_result.source_format == "repository"
                try:
                    stage_start = time.perf_counter()
                    stage_status = "ok"
                    with telemetry.measure("resource.summarize"):
                        await self._get_summarizer().summarize(
                            resource_uris=[result["root_uri"]],
                            ctx=ctx,
                            skip_vectorization=skip_vec,
                            lifecycle_lock_handle_id=lifecycle_lock_handle_id,
                            temp_uris=[temp_uri_for_summarize],
                            is_code_repo=is_code_repo,
                            **kwargs,
                        )
                except Exception as e:
                    logger.error(f"Summarization failed: {e}")
                    result["warnings"] = result.get("warnings", []) + [f"Summarization failed: {e}"]
                    stage_status = "error"
                finally:
                    try:
                        ResourceIngestionEventDataSource.record_stage(
                            stage="summarize",
                            status=str(stage_status),
                            duration_seconds=float(time.perf_counter() - stage_start),
                            account_id=getattr(ctx, "account_id", None),
                        )
                    except Exception:
                        pass
            elif lifecycle_lock_handle_id:
                # No downstream worker will sync temp content or release the lifecycle lock.
                from openviking.storage.transaction import get_lock_manager

                lock_manager = get_lock_manager()
                handle = lock_manager.get_handle(lifecycle_lock_handle_id)
                if handle:
                    if temp_uri:
                        from openviking.pyagfs.helpers import cp as agfs_cp

                        viking_fs = get_viking_fs()
                        src_path = viking_fs._uri_to_path(temp_uri, ctx=ctx)
                        dst_path = viking_fs._uri_to_path(root_uri, ctx=ctx)
                        await asyncio.to_thread(
                            agfs_cp,
                            viking_fs.agfs,
                            src_path,
                            dst_path,
                            recursive=True,
                        )
                        await viking_fs.delete_temp(parse_result.temp_dir_path, ctx=ctx)
                    await lock_manager.release(handle)

            # 恢复原始 temp_uri 用于输出
            if original_temp_uri is not None:
                result["temp_uri"] = original_temp_uri

            return result

    async def _commit_unique_candidate(
        self,
        *,
        candidate_uri: str,
        ctx: RequestContext,
        max_attempts: int = 100,
    ) -> tuple[str, str]:
        """Pick the first free candidate URI and reserve it with a lifecycle TreeLock."""
        from openviking.storage.errors import ResourceBusyError
        from openviking.storage.transaction import get_lock_manager

        viking_fs = get_viking_fs()
        lock_manager = get_lock_manager()

        for attempt in range(max_attempts + 1):
            root_uri = candidate_uri if attempt == 0 else f"{candidate_uri}_{attempt}"
            if await viking_fs.exists(root_uri, ctx=ctx):
                continue

            dst_path = viking_fs._uri_to_path(root_uri, ctx=ctx)
            handle = lock_manager.create_handle()
            try:
                lifecycle_lock_handle_id = await self._acquire_lifecycle_lock(
                    lock_manager, dst_path, uri=root_uri, handle=handle, timeout=0.0
                )
                return root_uri, lifecycle_lock_handle_id
            except ResourceBusyError:
                await lock_manager.release(handle)
                continue
            except Exception:
                await lock_manager.release(handle)
                raise

        raise FileExistsError(
            f"Cannot resolve unique name for {candidate_uri} after {max_attempts} attempts"
        )

    @staticmethod
    async def _acquire_lifecycle_lock(
        lock_manager,
        path: str,
        *,
        uri: str = "",
        handle=None,
        timeout: Optional[float] = None,
    ) -> str:
        """Acquire per-resource TreeLock lifecycle lock or raise a structured conflict."""
        from openviking.storage.errors import ResourceBusyError

        owns_handle = handle is None
        if handle is None:
            handle = lock_manager.create_handle()
        if await lock_manager.acquire_tree(handle, path, timeout=timeout):
            return handle.id
        if owns_handle:
            await lock_manager.release(handle)
        logger.warning(f"[ResourceProcessor] Failed to acquire lifecycle lock on {path}")
        raise ResourceBusyError(
            f"Resource is busy: {uri or path}",
            uri=uri or path,
            conflict_type="path_busy",
            retryable=True,
        )
