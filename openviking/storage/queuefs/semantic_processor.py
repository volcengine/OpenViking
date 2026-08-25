# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticProcessor: Processes messages from SemanticQueue, generates .abstract.md and .overview.md."""

import asyncio
import re
import threading
from contextlib import nullcontext
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote, unquote, urlsplit

from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.parse.image_rewrite import (
    IMAGE_MAPPINGS_FILENAME,
    rewrite_image_uris,
)
from openviking.parse.parsers.constants import (
    CODE_EXTENSIONS,
    DOCUMENTATION_EXTENSIONS,
    FILE_TYPE_CODE,
    FILE_TYPE_DOCUMENTATION,
    FILE_TYPE_OTHER,
)
from openviking.parse.parsers.media.utils import (
    MPEG_TS_PROBE_BYTES,
    generate_audio_summary,
    generate_image_summary,
    generate_video_summary,
    get_media_type,
)
from openviking.prompts import render_prompt
from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import detach_task_context
from openviking.storage.abstract_overview import (
    AbstractOverviewFormatError,
    AbstractOverviewWriteResult,
    body_for_preview,
    deterministic_sample,
    freshness_metadata,
    plan_abstract_overview_refresh,
    write_abstract_overview,
)
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.semantic_dag import DagStats, SemanticDagExecutor
from openviking.storage.queuefs.semantic_lock import SemanticLockScope
from openviking.storage.queuefs.semantic_msg import SemanticMsg, build_semantic_coalesce_key
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
from openviking.storage.queuefs.semantic_queue import is_semantic_msg_stale
from openviking.storage.viking_fs import LS_ALL_NODES, SyncDiff, get_viking_fs
from openviking.telemetry import bind_telemetry, bind_telemetry_stage, resolve_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.span_models import create_root_span_attributes
from openviking.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    classify_api_error,
)
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.model_retry import ERROR_CLASS_INPUT_TOO_LARGE, ERROR_CLASS_PERMANENT
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import VikingURI
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class RequestQueueStats:
    processed: int = 0
    requeue_count: int = 0
    error_count: int = 0


class SemanticProcessor(DequeueHandlerBase):
    """
    Semantic processor, generates .abstract.md and .overview.md bottom-up.

    Processing flow:
    1. Concurrently generate summaries for files in directory
    2. Collect .abstract.md from subdirectories
    3. Generate .abstract.md and .overview.md for this directory
    4. Enqueue to EmbeddingQueue for vectorization
    """

    _stats_lock = threading.Lock()
    _dag_stats_by_telemetry_id: Dict[str, DagStats] = {}
    _dag_stats_by_uri: Dict[str, DagStats] = {}
    _dag_stats_order: List[Tuple[str, str]] = []
    _request_stats_by_telemetry_id: Dict[str, RequestQueueStats] = {}
    _request_stats_order: List[str] = []
    _max_cached_stats = 256

    def __init__(self, max_concurrent_llm: int = 32):
        """
        Initialize SemanticProcessor.

        Args:
            max_concurrent_llm: Maximum concurrent LLM calls
        """
        self.max_concurrent_llm = max_concurrent_llm
        self._default_ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        self._circuit_breaker = CircuitBreaker()

    @classmethod
    def _cache_dag_stats(cls, telemetry_id: str, uri: str, stats: DagStats) -> None:
        with cls._stats_lock:
            if telemetry_id:
                cls._dag_stats_by_telemetry_id[telemetry_id] = stats
            cls._dag_stats_by_uri[uri] = stats
            cls._dag_stats_order.append((telemetry_id, uri))
            if len(cls._dag_stats_order) > cls._max_cached_stats:
                old_telemetry_id, old_uri = cls._dag_stats_order.pop(0)
                if old_telemetry_id:
                    cls._dag_stats_by_telemetry_id.pop(old_telemetry_id, None)
                cls._dag_stats_by_uri.pop(old_uri, None)

    @classmethod
    def consume_dag_stats(
        cls,
        telemetry_id: str = "",
        uri: Optional[str] = None,
    ) -> Optional[DagStats]:
        with cls._stats_lock:
            if telemetry_id and telemetry_id in cls._dag_stats_by_telemetry_id:
                stats = cls._dag_stats_by_telemetry_id.pop(telemetry_id, None)
                if uri:
                    cls._dag_stats_by_uri.pop(uri, None)
                return stats
            if uri and uri in cls._dag_stats_by_uri:
                return cls._dag_stats_by_uri.pop(uri, None)
        return None

    @classmethod
    def _merge_request_stats(
        cls,
        telemetry_id: str,
        processed: int = 0,
        requeue_count: int = 0,
        error_count: int = 0,
    ) -> None:
        if not telemetry_id:
            return
        with cls._stats_lock:
            stats = cls._request_stats_by_telemetry_id.setdefault(telemetry_id, RequestQueueStats())
            stats.processed += processed
            stats.requeue_count += requeue_count
            stats.error_count += error_count
            cls._request_stats_order.append(telemetry_id)
            if len(cls._request_stats_order) > cls._max_cached_stats:
                old_telemetry_id = cls._request_stats_order.pop(0)
                if old_telemetry_id != telemetry_id:
                    cls._request_stats_by_telemetry_id.pop(old_telemetry_id, None)

    @classmethod
    def consume_request_stats(cls, telemetry_id: str) -> Optional[RequestQueueStats]:
        if not telemetry_id:
            return None
        with cls._stats_lock:
            return cls._request_stats_by_telemetry_id.pop(telemetry_id, None)

    @staticmethod
    def _ctx_from_semantic_msg(msg: SemanticMsg) -> RequestContext:
        role = Role(msg.role or Role.ROOT)
        return RequestContext(
            user=UserIdentifier(msg.account_id, msg.user_id),
            role=role,
        )

    def _detect_file_type(self, file_name: str) -> str:
        """
        Detect file type for summary prompt selection.

        Args:
            file_name: File name with extension

        Returns:
            FILE_TYPE_CODE, FILE_TYPE_DOCUMENTATION, or FILE_TYPE_OTHER
        """
        file_name_lower = file_name.lower()

        # Documentation prompts should win over broad code skeleton recognition.
        for ext in DOCUMENTATION_EXTENSIONS:
            if file_name_lower.endswith(ext):
                return FILE_TYPE_DOCUMENTATION

        from openviking.parse.parsers.code.ast.providers import supports_code_skeleton

        if supports_code_skeleton(file_name):
            return FILE_TYPE_CODE

        # Keep legacy extension-based routing for code-like text formats not
        # covered by tags queries or tree-sitter-language-pack.
        for ext in CODE_EXTENSIONS:
            if file_name_lower.endswith(ext):
                return FILE_TYPE_CODE

        # Default to other
        return FILE_TYPE_OTHER

    async def _reenqueue_semantic_msg(self, msg: SemanticMsg) -> None:
        """Re-enqueue a semantic message for later processing.

        Throttles with a sleep when the circuit breaker is open to prevent
        re-enqueue storms (messages cycling at 5/sec during OPEN window).
        """
        import asyncio

        from openviking.storage.queuefs import get_queue_manager

        # Throttle to prevent re-enqueue storm during OPEN window
        wait = self._circuit_breaker.retry_after
        if wait > 0:
            await asyncio.sleep(wait)

        queue_manager = get_queue_manager()
        if queue_manager is not None:
            semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC)
            await semantic_queue.enqueue(msg)
            logger.info(f"Re-enqueued semantic message: {msg.uri}")
        else:
            logger.warning(f"No queue manager available, cannot re-enqueue: {msg.uri}")

    async def _requeue_semantic_msg_after_error(
        self,
        msg: SemanticMsg,
        data: Optional[Dict[str, Any]],
        error: Exception,
    ) -> None:
        try:
            await self._reenqueue_semantic_msg(msg)
            self._merge_request_stats(msg.telemetry_id, requeue_count=1)
            get_request_wait_tracker().record_semantic_requeue(msg.telemetry_id)
            self.report_requeue()
        except Exception as requeue_err:
            logger.error(f"Failed to re-enqueue semantic message: {requeue_err}")
            self._merge_request_stats(msg.telemetry_id, error_count=1)
            get_request_wait_tracker().mark_semantic_failed(msg.telemetry_id, msg.id, str(error))
            self.report_error(str(error), data)
            return
        self.report_success()

    async def _enqueue_parent_refresh(
        self, msg: SemanticMsg, uri: str, *, l0_body_changed: bool
    ) -> None:
        if msg.context_type not in {"resource", "skill"}:
            return
        if not msg.propagate_to_parent:
            return
        parent = VikingURI(uri).parent
        if parent is None:
            return
        parent_uri = parent.uri.rstrip("/")
        if (
            not parent_uri
            or parent_uri in {"viking://", "viking:", "viking://user", "viking://agent"}
            or parent_uri == uri.rstrip("/")
        ):
            return
        semantic_config = get_openviking_config().semantic
        decision = await plan_abstract_overview_refresh(
            viking_fs=get_viking_fs(),
            dir_uri=parent_uri,
            changed_entries=1,
            ctx=self._ctx_from_semantic_msg(msg),
            l0_body_changed=l0_body_changed,
            # This helper handles automatic upward propagation only. Manual
            # refresh/ingest bypasses the threshold for its requested root,
            # not for every ancestor reached afterwards.
            force_refresh=False,
            overview_sample_limit=getattr(semantic_config, "overview_sample_limit", 32),
            refresh_ratio=getattr(semantic_config, "freshness_refresh_ratio", 0.10),
        )
        if decision.action is not FreshnessAction.REFRESH_NOW:
            logger.debug(
                "Parent semantic refresh %s for %s (pending=%d, total=%d)",
                decision.action.value,
                parent_uri,
                decision.pending_after,
                decision.total_entries,
            )
            return

        from openviking.storage.queuefs import get_queue_manager

        queue_manager = get_queue_manager()
        if queue_manager is None:
            return
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        parent_msg = SemanticMsg(
            uri=parent_uri,
            context_type=msg.context_type,
            recursive=False,
            account_id=msg.account_id,
            user_id=msg.user_id,
            peer_id=msg.peer_id,
            role=msg.role,
            skip_vectorization=msg.skip_vectorization,
            changes={"modified": [uri]},
            generation_trigger="parent_refresh",
            coalesce_key=build_semantic_coalesce_key(
                context_type=msg.context_type,
                uri=parent_uri,
                account_id=msg.account_id,
                user_id=msg.user_id,
                peer_id=msg.peer_id,
            ),
        )
        with detach_task_context():
            await semantic_queue.enqueue(parent_msg)
        logger.info("Enqueued parent semantic refresh: %s", parent_uri)

    async def on_dequeue(
        self,
        data: Optional[Dict[str, Any]],
        lock: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Process dequeued SemanticMsg, recursively process all subdirectories."""
        msg: Optional[SemanticMsg] = None
        collector = None
        try:
            import json

            if not data:
                return None

            if "data" in data and isinstance(data["data"], str):
                data = json.loads(data["data"])

            assert data is not None
            msg = SemanticMsg.from_dict(data)
            if VikingURI(msg.uri).parent is None:
                logger.warning("Skipping semantic generation for root URI: %s", msg.uri)
                if msg.telemetry_id and msg.id:
                    get_request_wait_tracker().mark_semantic_done(msg.telemetry_id, msg.id)
                self.report_success()
                return None
            if is_semantic_msg_stale(msg):
                live_file_changes = {
                    kind: list(msg.changes.get(kind, []))
                    for kind in ("added", "modified")
                    if msg.changes and msg.changes.get(kind)
                }
                if msg.generation_trigger == "content_write" and live_file_changes:
                    # Coalescing replaces directory aggregation, not per-file
                    # maintenance. Let the newest message aggregate while this
                    # one still summarizes/vectorizes its changed files.
                    logger.info(
                        "Downgrading stale semantic message to file-only work: "
                        "uri=%s version=%s",
                        msg.uri,
                        msg.coalesce_version,
                    )
                    msg.aggregate_directory = False
                    msg.changes = live_file_changes
                    msg.coalesce_key = ""
                    msg.coalesce_version = 0
                else:
                    logger.info(
                        "Skipping stale semantic message: uri=%s version=%s",
                        msg.uri,
                        msg.coalesce_version,
                    )
                    if msg.telemetry_id and msg.id:
                        get_request_wait_tracker().mark_semantic_done(msg.telemetry_id, msg.id)
                    self.report_success()
                    return None
            # Circuit breaker: if API is known-broken, re-enqueue and wait
            try:
                self._circuit_breaker.check()
            except CircuitBreakerOpen:
                logger.warning(
                    f"Circuit breaker is open, re-enqueueing semantic message: {msg.uri}"
                )
                await self._reenqueue_semantic_msg(msg)
                self._merge_request_stats(msg.telemetry_id, requeue_count=1)
                get_request_wait_tracker().record_semantic_requeue(msg.telemetry_id)
                self.report_requeue()
                self.report_success()
                return None
            collector = resolve_telemetry(msg.telemetry_id)
            telemetry_ctx = bind_telemetry(collector) if collector is not None else nullcontext()
            with telemetry_ctx:
                root_attrs = create_root_span_attributes(
                    http_method="QUEUE",
                    http_route=msg.context_type or "/queuefs/semantic",
                    request_id=msg.telemetry_id or msg.id,
                    url_path=msg.uri,
                )
                root_attrs.account_id = msg.account_id
                root_attrs.user_id = msg.user_id
                root_context_token = bind_root_observability_context(root_attrs)
                try:
                    current_ctx = self._ctx_from_semantic_msg(msg)
                    logger.info(
                        f"Processing semantic generation for: {msg.uri} (recursive={msg.recursive})"
                    )

                    logger.info(f"Processing semantic generation for: {msg})")

                    semantic_lock = await SemanticLockScope.resolve(
                        msg.lock_handoff,
                        caller_lock=lock,
                        fallback_path_factory=lambda: get_viking_fs()._uri_to_path(
                            msg.uri, ctx=current_ctx
                        ),
                    )
                    try:
                        # Regular memory writes keep their specialized update path.
                        # Callers must explicitly opt into directory aggregation; the
                        # trigger remains descriptive metadata, not an algorithm switch.
                        if msg.context_type == "memory" and not msg.use_hierarchical_aggregation:
                            await self._process_memory_directory(
                                msg,
                                ctx=current_ctx,
                                lock=semantic_lock.lock,
                            )
                        else:
                            is_incremental = False
                            target_uri = msg.target_uri
                            run_uri = msg.uri
                            changes = msg.changes
                            viking_fs = get_viking_fs()
                            if msg.target_uri:
                                target_exists = await viking_fs.exists(
                                    msg.target_uri, ctx=current_ctx
                                )
                                if msg.uri != msg.target_uri:
                                    logger.info(
                                        "Syncing semantic source into target before processing: "
                                        f"{msg.uri} -> {msg.target_uri}"
                                    )
                                    diff = await self._sync_topdown_recursive(
                                        msg.uri,
                                        msg.target_uri,
                                        ctx=current_ctx,
                                        lock=semantic_lock.lock,
                                    )
                                    logger.info(
                                        "[SyncDiff] Diff computed: "
                                        f"added_files={len(diff.added_files)}, "
                                        f"deleted_files={len(diff.deleted_files)}, "
                                        f"updated_files={len(diff.updated_files)}, "
                                        f"added_dirs={len(diff.added_dirs)}, "
                                        f"deleted_dirs={len(diff.deleted_dirs)}"
                                    )
                                    changes = diff.to_changes()
                                    is_incremental = True
                                    target_uri = msg.target_uri
                                    run_uri = msg.target_uri
                                elif target_exists and msg.changes and msg.uri == msg.target_uri:
                                    is_incremental = True
                                    logger.info(
                                        f"Using direct incremental semantic update for: {msg.uri}"
                                    )
                            elif msg.changes:
                                is_incremental = True
                                target_uri = msg.uri
                                logger.info(
                                    f"Using direct incremental semantic update for: {msg.uri}"
                                )

                            executor = SemanticDagExecutor(
                                processor=self,
                                context_type=msg.context_type,
                                max_concurrent_llm=self.max_concurrent_llm,
                                ctx=current_ctx,
                                incremental_update=is_incremental,
                                target_uri=target_uri,
                                recursive=msg.recursive,
                                lock=semantic_lock.lock,
                                is_code_repo=msg.is_code_repo,
                                changes=changes,
                                skip_vectorization=msg.skip_vectorization,
                                ingest_options=msg.ingest_options,
                                coalesce_key=msg.coalesce_key,
                                coalesce_version=msg.coalesce_version,
                                source=msg.source,
                                generation_trigger=msg.generation_trigger,
                                aggregate_directory=msg.aggregate_directory,
                            )
                            await executor.run(run_uri)
                            self._cache_dag_stats(
                                msg.telemetry_id,
                                run_uri,
                                executor.get_stats(),
                            )
                            if not executor.stale and msg.aggregate_directory:
                                write_result = getattr(
                                    executor,
                                    "root_write_result",
                                    AbstractOverviewWriteResult(
                                        wrote=True, abstract_body_changed=True
                                    ),
                                )
                                await self._enqueue_parent_refresh(
                                    msg,
                                    target_uri or msg.uri,
                                    l0_body_changed=write_result.abstract_body_changed,
                                )
                    finally:
                        await semantic_lock.close()
                    get_request_wait_tracker().mark_semantic_done(msg.telemetry_id, msg.id)
                    self._merge_request_stats(msg.telemetry_id, processed=1)
                    logger.info(f"Completed semantic generation for: {msg.uri}")
                    self.report_success()
                    self._circuit_breaker.record_success()
                    return None
                finally:
                    reset_root_observability_context(root_context_token)

        except Exception as e:
            if isinstance(e, LockAcquisitionError):
                logger.warning(
                    "Lock error processing semantic message, re-enqueueing without "
                    "tripping API circuit breaker: %s",
                    e,
                    exc_info=True,
                )
                if msg is not None:
                    await self._requeue_semantic_msg_after_error(msg, data, e)
                else:
                    self.report_error(str(e), data)
                return None

            error_class = classify_api_error(e)
            if error_class == ERROR_CLASS_INPUT_TOO_LARGE:
                logger.error(
                    f"Input too large processing semantic message, dropping: {e}",
                    exc_info=True,
                )
                if msg is not None:
                    self._merge_request_stats(msg.telemetry_id, error_count=1)
                    get_request_wait_tracker().mark_semantic_failed(
                        msg.telemetry_id, msg.id, str(e)
                    )
                self.report_error(str(e), data)
            elif error_class == ERROR_CLASS_PERMANENT:
                logger.critical(
                    f"Permanent API error processing semantic message, dropping: {e}",
                    exc_info=True,
                )
                self._circuit_breaker.record_failure(e)
                if msg is not None:
                    self._merge_request_stats(msg.telemetry_id, error_count=1)
                    get_request_wait_tracker().mark_semantic_failed(
                        msg.telemetry_id, msg.id, str(e)
                    )
                self.report_error(str(e), data)
            else:
                # Transient or unknown — re-enqueue for retry
                logger.warning(
                    f"Transient API error processing semantic message, re-enqueueing: {e}",
                    exc_info=True,
                )
                self._circuit_breaker.record_failure(e)
                if msg is not None:
                    await self._requeue_semantic_msg_after_error(msg, data, e)
                else:
                    self.report_error(str(e), data)
            return None

    async def on_cancelled(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Release a queued semantic lock before cancelled work is ACKed."""
        try:
            import json

            payload = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(payload, str):
                payload = json.loads(payload)
            msg = SemanticMsg.from_dict(payload)
        except (TypeError, ValueError) as exc:
            self.report_error(str(exc), data)
            return None

        if msg.telemetry_id and msg.id:
            get_request_wait_tracker().mark_semantic_done(msg.telemetry_id, msg.id)
        if msg.lock_handoff is not None:
            try:
                viking_fs = get_viking_fs()
                lock = await viking_fs._async_agfs.pathlock_adopt(msg.lock_handoff)
                await viking_fs._async_agfs.pathlock_release(lock)
            except Exception as exc:
                logger.warning("Failed to release cancelled semantic lock: %s", exc)
        self.report_success()
        return None

    def get_dag_stats(self) -> Optional["DagStats"]:
        return SemanticDagExecutor.get_active_stats()

    async def _process_memory_directory(
        self,
        msg: SemanticMsg,
        ctx: Optional[RequestContext] = None,
        lock: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Process a memory directory with special handling.

        For memory directories:
        - Generate file summaries in bounded batches
        - Enqueue each changed file for vectorization as soon as its summary is ready
        - Retain only compact summaries for the directory overview
        - Vectorize the generated abstract.md and overview.md

        Args:
            msg: The semantic message containing directory info and changes
        """
        viking_fs = get_viking_fs()
        dir_uri = msg.uri
        ctx = ctx or self._default_ctx
        llm_sem = asyncio.Semaphore(self.max_concurrent_llm)

        try:
            entries = await viking_fs.ls(dir_uri, node_limit=LS_ALL_NODES, ctx=ctx)
        except Exception as e:
            raise RuntimeError(f"Failed to list memory directory {dir_uri}: {e}") from e

        file_paths: List[str] = []
        for entry in entries:
            name = entry.get("name", "")
            if not name or name.startswith(".") or name in [".", ".."]:
                continue
            if not entry.get("isDir", False):
                item_uri = VikingURI(dir_uri).join(name).uri
                file_paths.append(item_uri)
        file_paths.sort()

        if not file_paths:
            logger.info(f"No memory files found in {dir_uri}")
            return

        existing_summaries: Dict[str, str] = {}
        if msg.changes:
            try:
                old_overview = await viking_fs.read_file(f"{dir_uri}/.overview.md", ctx=ctx)
                if old_overview:
                    existing_summaries = self._parse_overview_md(body_for_preview(old_overview))
                    logger.info(
                        f"Parsed {len(existing_summaries)} existing summaries from overview.md"
                    )
            except AbstractOverviewFormatError:
                raise
            except Exception as e:
                logger.debug(f"No existing overview.md found for {dir_uri}: {e}")

        changed_files: Set[str] = set()
        if msg.changes:
            changed_files = set(msg.changes.get("added", []) + msg.changes.get("modified", []))
            deleted_files = set(msg.changes.get("deleted", []))
            logger.info(
                f"Processing memory directory {dir_uri} with changes: "
                f"added={len(msg.changes.get('added', []))}, "
                f"modified={len(msg.changes.get('modified', []))}, "
                f"deleted={len(deleted_files)}"
            )

        pending_indices: List[Tuple[int, str]] = []
        file_summaries: List[Optional[Dict[str, str]]] = [None] * len(file_paths)
        paths_to_vectorize = changed_files if msg.changes else set(file_paths)

        for idx, file_path in enumerate(file_paths):
            file_name = file_path.split("/")[-1]
            if file_path not in changed_files and file_name in existing_summaries:
                file_summaries[idx] = {
                    "name": file_name,
                    "summary": existing_summaries[file_name],
                }
                logger.debug(f"Reused existing summary for {file_name}")
            else:
                pending_indices.append((idx, file_path))

        if file_paths and not pending_indices:
            try:
                from openviking.metrics.datasources.cache import CacheEventDataSource

                CacheEventDataSource.record_hit("L1")
            except Exception:
                pass
        elif file_paths and pending_indices:
            try:
                from openviking.metrics.datasources.cache import CacheEventDataSource

                if len(file_paths) > len(pending_indices):
                    CacheEventDataSource.record_hit("L1")
                CacheEventDataSource.record_miss("L1")
            except Exception:
                pass

        if pending_indices:
            logger.info(
                f"Generating summaries for {len(pending_indices)} changed files "
                f"(reused {len(file_paths) - len(pending_indices)} cached)"
            )

            async def _gen(idx: int, file_path: str) -> None:
                file_name = file_path.split("/")[-1]
                try:
                    summary_dict = await self._generate_single_file_summary(
                        file_path, llm_sem=llm_sem, ctx=ctx
                    )
                    logger.debug(f"Generated summary for {file_name}")
                except Exception as e:
                    logger.warning(f"Failed to generate summary for {file_path}: {e}")
                    summary_dict = {"name": file_name, "summary": ""}

                if file_path in paths_to_vectorize and not msg.skip_vectorization:
                    await self._vectorize_single_file(
                        parent_uri=dir_uri,
                        context_type="memory",
                        file_path=file_path,
                        summary_dict=summary_dict,
                        ctx=ctx,
                        preserve_existing_created_at=True,
                    )
                file_summaries[idx] = {
                    "name": str(summary_dict.get("name") or file_name),
                    "summary": str(summary_dict.get("summary") or ""),
                }

            batch_size = max(1, min(self.max_concurrent_llm, 10))
            for batch_start in range(0, len(pending_indices), batch_size):
                batch = pending_indices[batch_start : batch_start + batch_size]
                logger.info(
                    f"[MemorySemantic] Processing batch {batch_start // batch_size + 1}/"
                    f"{(len(pending_indices) + batch_size - 1) // batch_size} "
                    f"({len(batch)} files)"
                )
                await asyncio.gather(*[_gen(i, fp) for i, fp in batch])

        completed_summaries = [s for s in file_summaries if s is not None]
        sample_limit = getattr(
            get_openviking_config().semantic,
            "overview_sample_limit",
            32,
        )
        sampled_summaries = deterministic_sample(completed_summaries, sample_limit)
        generated_content = await self._generate_overview(
            dir_uri,
            sampled_summaries,
            [],
            llm_sem=llm_sem,
            total_files=len(file_paths),
        )
        overview, abstract = self._normalize_overview_generation(generated_content)

        try:
            wrote_semantics = await self._write_memory_directory_semantics(
                msg=msg,
                viking_fs=viking_fs,
                dir_uri=dir_uri,
                overview=overview,
                abstract=abstract,
                ctx=ctx,
                lock=lock,
                total_entries=len(file_paths),
                sampled_entries=len(sampled_summaries),
            )
        except Exception as e:
            raise RuntimeError(f"Failed to write abstract/overview for {dir_uri}: {e}") from e
        if not wrote_semantics.wrote:
            return
        logger.info(f"Generated abstract.md and overview.md for {dir_uri}")

        if msg.skip_vectorization:
            logger.info(f"Skipping vectorization for {dir_uri} (requested via SemanticMsg)")
            return
        if not (
            wrote_semantics.overview_body_changed
            or wrote_semantics.abstract_body_changed
        ):
            logger.info(
                "Skipping directory vectorization for %s (visible semantics unchanged)",
                dir_uri,
            )
            return
        await self._vectorize_directory(
            uri=dir_uri,
            context_type="memory",
            abstract=abstract,
            overview=overview,
            ctx=ctx,
        )
        logger.info(f"Vectorized abstract.md and overview.md for {dir_uri}")

    async def _write_memory_directory_semantics(
        self,
        *,
        msg: SemanticMsg,
        viking_fs: Any,
        dir_uri: str,
        overview: str,
        abstract: str,
        ctx: Optional[RequestContext],
        lock: Optional[Dict[str, Any]] = None,
        total_entries: int = 0,
        sampled_entries: int = 0,
    ) -> AbstractOverviewWriteResult:
        return await write_abstract_overview(
            viking_fs=viking_fs,
            dir_uri=dir_uri,
            overview=overview,
            abstract=abstract,
            ctx=ctx,
            is_stale=lambda: is_semantic_msg_stale(msg),
            metadata={
                **({"source": msg.source} if msg.source else {}),
                "generated_by": {
                    "component": "SemanticProcessor",
                    "trigger": msg.generation_trigger,
                },
                "freshness": freshness_metadata(total_entries, sampled_entries),
            },
            lock=lock,
            log_prefix="[MemorySemantic]",
        )

    async def _sync_topdown_recursive(
        self,
        root_uri: str,
        target_uri: str,
        ctx: Optional[RequestContext] = None,
        file_change_status: Optional[Dict[str, bool]] = None,
        lock: Optional[Dict[str, Any]] = None,
    ) -> SyncDiff:
        """Merge a temp/staging source tree into the target via VikingFS.sync_tree.

        The pure filesystem diff-and-move is delegated to
        :py:meth:`VikingFS.sync_tree`; this wrapper carries over parser
        sidecars (``.image_mappings.json``) and rewrites markdown image
        references once the visible files have been moved into place.
        Hidden entries are skipped by ``sync_tree``, which is why the
        sidecar handling lives here rather than in the FS layer.
        """
        viking_fs = get_viking_fs()
        if not await viking_fs.exists(root_uri, ctx=ctx):
            raise FileNotFoundError(
                f"Semantic source no longer exists; refusing to sync into {target_uri}: {root_uri}"
            )

        target_exists = await viking_fs.exists(target_uri, ctx=ctx)
        diff = await viking_fs.sync_tree(
            root_uri,
            target_uri,
            ctx=ctx,
            file_change_status=file_change_status,
            lease_ref=lock,
            delete_temp_after=False,
        )

        if not target_exists:
            # The whole temp tree (including the hidden .image_mappings.json
            # sidecar) was moved into the target; rewrite local image paths now.
            await self._rewrite_target_image_uris(root_uri, target_uri, ctx=ctx, lock=lock)
            return diff

        # sync_tree skips hidden files, so the .image_mappings.json sidecar is
        # still at the temp root. Carry it over and rewrite the synced markdown
        # before the temp tree is deleted below.
        await self._rewrite_target_image_uris(root_uri, target_uri, ctx=ctx, lock=lock)
        try:
            await viking_fs.delete_temp(root_uri, ctx=ctx)
        except Exception as e:
            logger.error(f"[SyncDiff] Failed to delete root directory {root_uri}: {e}")
        return diff

    async def _rewrite_target_image_uris(
        self,
        root_uri: str,
        target_uri: str,
        ctx: Optional[RequestContext] = None,
        lock: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Rewrite local image refs in the target after a temp-to-target sync.

        ``_sync_topdown_recursive`` MOVES the visible files into the target and
        skips hidden ones, so afterwards the temp tree holds only the
        ``.image_mappings.json`` sidecars written by the parser (one per
        document root, possibly nested for directory ingests). Discovery is
        therefore driven by the markdown files already synced into the TARGET:
        their ancestor directories, mirrored back onto the temp tree, are where
        sidecars can live. Carry each one over (when missing) so
        :func:`rewrite_image_uris` can resolve local image paths against the
        images that were synced into the final target.
        """
        viking_fs = get_viking_fs()
        root_prefix = root_uri.rstrip("/")
        target_prefix = target_uri.rstrip("/")
        mapping_name = IMAGE_MAPPINGS_FILENAME

        if root_prefix != target_prefix:
            try:
                glob_result = await viking_fs.glob("**/*.md", uri=target_prefix, ctx=ctx)
                target_md_uris = glob_result.get("matches", [])
            except Exception:
                target_md_uris = []

            # Ancestor dirs of the target md files, as paths relative to the
            # target root — the candidate sidecar locations on both trees.
            candidate_rels = set()
            for md_uri in target_md_uris:
                d = md_uri.rsplit("/", 1)[0]
                while d == target_prefix or d.startswith(target_prefix + "/"):
                    candidate_rels.add(d[len(target_prefix) :].lstrip("/"))
                    if d == target_prefix:
                        break
                    d = d.rsplit("/", 1)[0]

            for rel in candidate_rels:
                src_mapping = (
                    f"{root_prefix}/{rel}/{mapping_name}"
                    if rel
                    else f"{root_prefix}/{mapping_name}"
                )
                target_mapping = (
                    f"{target_prefix}/{rel}/{mapping_name}"
                    if rel
                    else f"{target_prefix}/{mapping_name}"
                )
                try:
                    await viking_fs.stat(target_mapping, ctx=ctx)
                    continue  # already carried over
                except Exception:
                    pass
                try:
                    mapping_content = await viking_fs.read_file(src_mapping, ctx=ctx)
                except Exception:
                    continue  # no sidecar at this level
                try:
                    # TODO: This must be optimized once pathlock is pushed down into ragfs.
                    await viking_fs.write_file(
                        target_mapping,
                        mapping_content,
                        ctx=ctx,
                        lease_ref=lock,
                    )
                except Exception:
                    # Target subtree may not exist (doc removed in sync); skip.
                    pass

        try:
            await rewrite_image_uris(target_uri, ctx=ctx, lease_ref=lock)
        except Exception as e:
            logger.error(f"[SyncDiff] Failed to rewrite image URIs for {target_uri}: {e}")

    async def _generate_text_summary(
        self,
        file_path: str,
        file_name: str,
        llm_sem: asyncio.Semaphore,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        """Generate summary for a single text file (code, documentation, or other text)."""
        viking_fs = get_viking_fs()
        vlm = get_openviking_config().vlm
        active_ctx = ctx or self._default_ctx

        content = await viking_fs.read_file(file_path, ctx=active_ctx)
        if isinstance(content, bytes):
            from openviking.utils.embedding_utils import _decode_text_bytes

            content = _decode_text_bytes(content)

        def result(summary: str) -> Dict[str, Any]:
            return {"name": file_name, "summary": summary}

        config = get_openviking_config()

        # Limit content length
        max_chars = config.semantic.max_file_content_chars
        if len(content) > max_chars:
            content = content[:max_chars] + "\n...(truncated)"

        # Detect file type and select appropriate prompt
        file_type = self._detect_file_type(file_name)

        if file_type == FILE_TYPE_CODE:
            from openviking.parse.parsers.code.ast import extract_skeleton_result

            extraction = extract_skeleton_result(file_name, content)
            if extraction.text:
                skeleton_text = extraction.text
                max_skeleton_chars = config.semantic.max_skeleton_chars
                if len(skeleton_text) > max_skeleton_chars:
                    skeleton_text = skeleton_text[:max_skeleton_chars]
                return result(skeleton_text)
            if not vlm.is_available():
                logger.warning("VLM not available for code summary fallback: %s", file_path)
                return result("")

            from openviking.session.memory.utils.language import resolve_output_language

            output_language = resolve_output_language(content, config=config)
            prompt = render_prompt(
                "semantic.code_summary",
                {"file_name": file_name, "content": content, "output_language": output_language},
            )
            async with llm_sem:
                with bind_telemetry_stage("resource_summarize"):
                    summary = await vlm.get_completion_async(prompt)
            return result(summary.strip())

        if not vlm.is_available():
            logger.warning("VLM not available, using empty summary")
            return result("")

        from openviking.session.memory.utils.language import resolve_output_language

        output_language = resolve_output_language(content, config=config)
        if file_type == FILE_TYPE_DOCUMENTATION:
            prompt_id = "semantic.document_summary"
        else:
            prompt_id = "semantic.file_summary"

        prompt = render_prompt(
            prompt_id,
            {"file_name": file_name, "content": content, "output_language": output_language},
        )

        async with llm_sem:
            with bind_telemetry_stage("resource_summarize"):
                summary = await vlm.get_completion_async(prompt)
        return result(summary.strip())

    async def _generate_single_file_summary(
        self,
        file_path: str,
        llm_sem: Optional[asyncio.Semaphore] = None,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        """Generate summary for a single file.

        Args:
            file_path: File path

        Returns:
            {"name": file_name, "summary": summary_content}
        """
        file_name = file_path.split("/")[-1]
        llm_sem = llm_sem or asyncio.Semaphore(self.max_concurrent_llm)
        media_type = get_media_type(file_name, None)
        if file_name.lower().endswith(".ts"):
            try:
                prefix = await get_viking_fs().read(
                    file_path,
                    offset=0,
                    size=MPEG_TS_PROBE_BYTES,
                    ctx=ctx,
                )
            except Exception:
                prefix = None
            media_type = get_media_type(file_name, None, content=prefix)
        if media_type == "image":
            return await generate_image_summary(file_path, file_name, llm_sem, ctx=ctx)
        elif media_type == "audio":
            return await generate_audio_summary(file_path, file_name, llm_sem, ctx=ctx)
        elif media_type == "video":
            return await generate_video_summary(file_path, file_name, llm_sem, ctx=ctx)
        else:
            return await self._generate_text_summary(file_path, file_name, llm_sem, ctx=ctx)

    def _child_summary_line(
        self,
        dir_uri: str,
        idx: int,
        item: Dict[str, str],
        link_map: Dict[str, str],
    ) -> str:
        """Render a subdirectory summary line with a collision-free link placeholder."""
        placeholder = f"viking://input_sample_c{idx}"
        link_map[placeholder] = self._markdown_link_target(dir_uri, item["name"])
        return f"- {item['name']}/ (link: {placeholder}): {item['abstract']}"

    @staticmethod
    def _markdown_link_target(dir_uri: str, entry_name: str) -> str:
        """Build a Markdown-safe target without changing the stored Viking URI."""
        entry_uri = VikingURI(dir_uri).join(entry_name).uri
        return quote(entry_uri, safe=":/")

    def _replace_link_references(self, generated_content: str, link_map: Dict[str, str]) -> str:
        """Resolve link placeholders (viking://input_sample_fN / cN) to real URIs.

        The model is fed compact, collision-free placeholders and asked to emit
        Markdown links against them; here we map each placeholder back to the
        entry's real URI. Unknown placeholders are left untouched.
        """

        def replace_link(match):
            return link_map.get(match.group(0), match.group(0))

        return re.sub(r"viking://input_sample_[fc]\d+", replace_link, generated_content)

    def _truncate_generated_text(self, text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text

        if max_chars <= 3:
            return text[:max_chars]

        first_sentence_end = None
        last_sentence_end_within_limit = None
        for sentence_end_match in re.finditer(r"\.(?!\d)(?=\s|$)|[!?](?=\s|$)|[。？！]", text):
            sentence_end = sentence_end_match.end()
            if first_sentence_end is None:
                first_sentence_end = sentence_end
            if sentence_end <= max_chars:
                last_sentence_end_within_limit = sentence_end
            elif last_sentence_end_within_limit is not None:
                break

        if last_sentence_end_within_limit is not None:
            return text[:last_sentence_end_within_limit].strip()
        if first_sentence_end is not None:
            return text[:first_sentence_end].strip()

        candidate = text[: max_chars - 3].rstrip()
        word_boundary = candidate.rfind(" ")
        if word_boundary > 0:
            return candidate[:word_boundary].rstrip() + "..."

        return candidate + "..."

    def _extract_abstract_from_overview(self, overview_content: str) -> str:
        """Extract an abstract from the Markdown overview brief description."""
        overview_content = body_for_preview(overview_content)
        lines = overview_content.split("\n")

        # Skip header lines (starting with #)
        content_lines = []
        in_header = True

        for line in lines:
            if in_header and line.startswith("#"):
                continue
            elif in_header and line.strip():
                in_header = False

            if not in_header:
                # Stop at first ##
                if line.startswith("##"):
                    break
                if line.strip():
                    content_lines.append(line.strip())

        return "\n".join(content_lines).strip()

    def _normalize_overview_generation(self, generated_content: str) -> Tuple[str, str]:
        """Convert raw Markdown overview output into final L1 overview and L0 abstract."""
        overview = body_for_preview(generated_content)
        abstract = self._extract_abstract_from_overview(overview)
        return self._enforce_size_limits(overview, abstract)

    def _enforce_size_limits(self, overview: str, abstract: str) -> Tuple[str, str]:
        """Enforce max size limits on overview and abstract."""
        semantic = get_openviking_config().semantic
        if len(overview) > semantic.overview_max_chars:
            overview = self._truncate_generated_text(overview, semantic.overview_max_chars)
        if len(abstract) > semantic.abstract_max_chars:
            abstract = self._truncate_generated_text(abstract, semantic.abstract_max_chars)
        return overview, abstract

    def _parse_overview_md(self, overview_content: str) -> Dict[str, str]:
        """Parse overview.md and extract file summaries.

        Args:
            overview_content: Content of the overview.md file

        Returns:
            Dictionary mapping file names to their summaries
        """
        import re

        summaries: Dict[str, str] = {}

        overview_content = body_for_preview(overview_content)
        if not overview_content or not overview_content.strip():
            return summaries

        lines = overview_content.split("\n")
        current_file = None
        current_summary_lines: List[str] = []

        for line in lines:
            header_match = re.match(r"^###\s+(.+?)\s*$", line)
            if header_match:
                if current_file and current_summary_lines:
                    summaries[current_file] = " ".join(current_summary_lines).strip()

                file_name = self._overview_heading_cache_key(header_match.group(1).strip())
                parts = file_name.split()
                if len(parts) >= 2 and parts[0] == parts[1]:
                    file_name = parts[0]

                current_file = file_name
                current_summary_lines = []
                continue

            numbered_match = re.match(r"^\[(\d+)\]\s+(.+?):\s*(.+)$", line)
            if numbered_match:
                if current_file and current_summary_lines:
                    summaries[current_file] = " ".join(current_summary_lines).strip()
                current_file = numbered_match.group(2).strip()
                current_summary_lines = [numbered_match.group(3).strip()]
                continue

            if current_file:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    current_summary_lines.append(stripped)

        if current_file and current_summary_lines:
            summaries[current_file] = " ".join(current_summary_lines).strip()

        return summaries

    @staticmethod
    def _overview_heading_cache_key(heading: str) -> str:
        """Return the entry name represented by a plain or linked H3 heading."""
        if heading.startswith("[") and heading.endswith(")"):
            destination_start = heading.rfind("](")
            if destination_start > 0:
                target = heading[destination_start + 2 : -1].strip()
                if target.startswith("<") and target.endswith(">"):
                    target = target[1:-1].strip()
                if target.startswith("viking://"):
                    path = unquote(urlsplit(target).path).rstrip("/")
                    if path:
                        return path.rsplit("/", 1)[-1]
        return heading

    async def _generate_overview(
        self,
        dir_uri: str,
        file_summaries: List[Dict[str, str]],
        children_abstracts: List[Dict[str, str]],
        llm_sem: Optional[asyncio.Semaphore] = None,
        total_files: Optional[int] = None,
        total_children: Optional[int] = None,
    ) -> str:
        """Generate raw directory overview model output.

        For small directories, generates a single overview from all file summaries.
        For large directories that would exceed the prompt budget, splits file
        summaries into batches, generates a partial overview per batch, then
        merges the partials into a final overview.

        Args:
            dir_uri: Directory URI
            file_summaries: File summary list (only entries participating in
                this aggregation, i.e. already sampled when applicable)
            children_abstracts: Subdirectory summary list (sampled when applicable)
            total_files: Total direct files in the directory (may exceed
                ``len(file_summaries)`` when the directory was sampled)
            total_children: Total direct subdirectories (may exceed
                ``len(children_abstracts)`` when sampled)

        Returns:
            Markdown overview content generated by the model.
        """

        config = get_openviking_config()
        vlm = config.vlm
        semantic = config.semantic

        if not vlm.is_available():
            logger.warning("VLM not available, using default overview")
            return f"# {dir_uri.split('/')[-1]}\n\n[Directory overview is not ready]"

        from openviking.session.memory.utils.language import resolve_output_language

        # Build coverage statement so the model knows the directory scale
        # and whether the provided summaries represent all entries or a sample.
        total_files = len(file_summaries) if total_files is None else total_files
        total_children = len(children_abstracts) if total_children is None else total_children
        total_direct = total_files + total_children
        provided = len(file_summaries) + len(children_abstracts)
        if total_direct <= provided:
            directory_coverage = (
                f"Total direct entries: {total_direct}. "
                "All direct entries are represented in the summaries below."
            )
        else:
            directory_coverage = (
                f"Total direct entries: {total_direct} "
                f"({total_files} files, {total_children} subdirectories).\n"
                f"Summaries provided for this aggregation: {provided}.\n"
                f"Direct entries not individually shown: {total_direct - provided}.\n"
                "Coverage: sampled. The summaries below are representative "
                "entries; generalize cautiously and do not treat them as exhaustive."
            )

        # Build link placeholder mapping and summary string.
        # The model is fed collision-free placeholders (viking://input_sample_fN)
        # and asked to emit Markdown links; placeholders are resolved to real
        # URIs in post-processing. This avoids the fragility of bare [N] indices
        # and of asking the model to reproduce long/CJK URIs verbatim.
        link_map: Dict[str, str] = {}
        file_summaries_lines = []
        for idx, item in enumerate(file_summaries, 1):
            placeholder = f"viking://input_sample_f{idx}"
            link_map[placeholder] = self._markdown_link_target(dir_uri, item["name"])
            file_summaries_lines.append(
                f"- {item['name']} (link: {placeholder}): {item['summary']}"
            )
        file_summaries_str = "\n".join(file_summaries_lines) if file_summaries_lines else "None"

        # Build subdirectory summary string
        children_abstracts_str = (
            "\n".join(
                self._child_summary_line(dir_uri, idx, item, link_map)
                for idx, item in enumerate(children_abstracts, 1)
            )
            if children_abstracts
            else "None"
        )

        language_source_parts = []
        if file_summaries:
            language_source_parts.append(file_summaries_str)
        if children_abstracts:
            language_source_parts.append(children_abstracts_str)
        if not language_source_parts:
            language_source_parts.append(dir_uri.split("/")[-1])
        output_language = resolve_output_language("\n".join(language_source_parts), config=config)

        # Budget guard: check if prompt would be oversized
        estimated_size = len(file_summaries_str) + len(children_abstracts_str)
        over_budget = estimated_size > semantic.max_overview_prompt_chars
        entry_count = len(file_summaries) + len(children_abstracts)
        many_entries = entry_count > semantic.overview_batch_size

        if over_budget and many_entries:
            # Many entries, oversized prompt → batch and merge
            logger.info(
                f"Overview prompt for {dir_uri} exceeds budget "
                f"({estimated_size} chars, {len(file_summaries)} files, "
                f"{len(children_abstracts)} subdirectories). "
                f"Splitting into batches of {semantic.overview_batch_size}."
            )
            overview = await self._batched_generate_overview(
                dir_uri,
                file_summaries,
                children_abstracts,
                link_map,
                llm_sem=llm_sem,
                output_language=output_language,
                directory_coverage=directory_coverage,
            )
        elif over_budget:
            # Few files but long summaries → truncate summaries to fit budget
            logger.info(
                f"Overview prompt for {dir_uri} exceeds budget "
                f"({estimated_size} chars) with {len(file_summaries)} files. "
                f"Truncating summaries to fit."
            )
            budget = semantic.max_overview_prompt_chars
            budget -= len(children_abstracts_str)
            per_file = max(100, budget // max(len(file_summaries), 1))
            truncated_lines = []
            for idx, item in enumerate(file_summaries, 1):
                summary = item["summary"][:per_file]
                truncated_lines.append(
                    f"- {item['name']} (link: viking://input_sample_f{idx}): {summary}"
                )
            file_summaries_str = "\n".join(truncated_lines)
            overview = await self._single_generate_overview(
                dir_uri,
                file_summaries_str,
                children_abstracts_str,
                link_map,
                output_language=output_language,
                directory_coverage=directory_coverage,
            )
        else:
            overview = await self._single_generate_overview(
                dir_uri,
                file_summaries_str,
                children_abstracts_str,
                link_map,
                output_language=output_language,
                directory_coverage=directory_coverage,
            )

        return overview

    async def _single_generate_overview(
        self,
        dir_uri: str,
        file_summaries_str: str,
        children_abstracts_str: str,
        link_map: Dict[str, str],
        output_language: str = "en",
        directory_coverage: str = "",
    ) -> str:
        """Generate overview from a single prompt (small directories)."""
        config = get_openviking_config()
        vlm = config.vlm

        try:
            prompt = render_prompt(
                "semantic.overview_generation",
                {
                    "dir_name": dir_uri.split("/")[-1],
                    "file_summaries": file_summaries_str,
                    "children_abstracts": children_abstracts_str,
                    "output_language": output_language,
                    "directory_coverage": directory_coverage,
                },
            )

            with bind_telemetry_stage("resource_summarize"):
                overview = await vlm.get_completion_async(prompt)

            overview = self._replace_link_references(overview, link_map)

            return overview.strip()

        except Exception as e:
            logger.error(
                f"Failed to generate overview for {dir_uri}: {e}",
                exc_info=True,
            )
            return f"# {dir_uri.split('/')[-1]}\n\n[Directory overview is not generated]"

    async def _batched_generate_overview(
        self,
        dir_uri: str,
        file_summaries: List[Dict[str, str]],
        children_abstracts: List[Dict[str, str]],
        link_map: Dict[str, str],
        llm_sem: Optional[asyncio.Semaphore] = None,
        output_language: str = "en",
        directory_coverage: str = "",
    ) -> str:
        """Generate overview by batching file and subdirectory summaries.

        Splits both input kinds into batches, generates a partial overview per
        batch, then merges the partials without repeating the raw inputs.
        """
        config = get_openviking_config()
        vlm = config.vlm
        semantic = config.semantic
        batch_size = semantic.overview_batch_size
        dir_name = dir_uri.split("/")[-1]

        work_items = [("file", index, item) for index, item in enumerate(file_summaries, 1)] + [
            ("child", index, item) for index, item in enumerate(children_abstracts, 1)
        ]
        batches = [work_items[i : i + batch_size] for i in range(0, len(work_items), batch_size)]
        logger.info(f"Generating overview for {dir_uri} in {len(batches)} batches")

        # Generate partial overviews concurrently using global link placeholders.
        if llm_sem is None:
            llm_sem = asyncio.Semaphore(self.max_concurrent_llm)
        partial_overviews = [None] * len(batches)
        batch_prompts: List[Tuple[int, str, Dict[str, str]]] = []

        for batch_idx, batch in enumerate(batches):
            batch_lines = []
            child_lines = []
            batch_link_map: Dict[str, str] = {}
            for entry_kind, global_idx, item in batch:
                assert global_idx is not None
                if entry_kind == "file":
                    placeholder = f"viking://input_sample_f{global_idx}"
                    batch_link_map[placeholder] = link_map.get(
                        placeholder, self._markdown_link_target(dir_uri, item["name"])
                    )
                    batch_lines.append(f"- {item['name']} (link: {placeholder}): {item['summary']}")
                else:
                    child_lines.append(
                        self._child_summary_line(dir_uri, global_idx, item, batch_link_map)
                    )

            prompt = render_prompt(
                "semantic.overview_generation",
                {
                    "dir_name": dir_name,
                    "file_summaries": "\n".join(batch_lines) or "None",
                    "children_abstracts": "\n".join(child_lines) or "None",
                    "output_language": output_language,
                    "directory_coverage": directory_coverage,
                },
            )
            batch_prompts.append((batch_idx, prompt, batch_link_map))

        async def _run_batch(batch_idx: int, prompt: str, batch_link_map: Dict[str, str]) -> None:
            try:
                async with llm_sem:
                    with bind_telemetry_stage("resource_summarize"):
                        partial = await vlm.get_completion_async(prompt)
                partial = self._replace_link_references(partial, batch_link_map)
                partial_overviews[batch_idx] = partial.strip()
            except Exception as e:
                logger.warning(
                    f"Failed to generate partial overview batch "
                    f"{batch_idx + 1}/{len(batches)} for {dir_uri}: {e}"
                )

        await asyncio.gather(*[_run_batch(*bp) for bp in batch_prompts])
        partial_overviews = [p for p in partial_overviews if p is not None]

        if not partial_overviews:
            return f"# {dir_name}\n\n[Directory overview is not generated]"

        # If only one batch succeeded, use it directly
        if len(partial_overviews) == 1:
            return partial_overviews[0]

        # Merge partials only; each child abstract is already represented once.
        # Partials already contain real URIs, but the shared generation prompt can
        # make the merge model emit placeholders again. Resolve the merged output
        # with the complete map before persisting it.
        combined = "\n\n---\n\n".join(partial_overviews)
        try:
            prompt = render_prompt(
                "semantic.overview_generation",
                {
                    "dir_name": dir_name,
                    "file_summaries": combined,
                    "children_abstracts": "None",
                    "output_language": output_language,
                    "directory_coverage": directory_coverage,
                },
            )
            with bind_telemetry_stage("resource_summarize"):
                overview = await vlm.get_completion_async(prompt)
            overview = self._replace_link_references(overview, link_map)
            return overview.strip()
        except Exception as e:
            logger.error(
                f"Failed to merge partial overviews for {dir_uri}: {e}",
                exc_info=True,
            )
            return partial_overviews[0]

    async def _vectorize_directory(
        self,
        uri: str,
        context_type: str,
        abstract: str,
        overview: str,
        ctx: Optional[RequestContext] = None,
        ingest_options: IngestOptions | None = None,
    ) -> None:
        """Create directory Context and enqueue to EmbeddingQueue."""

        from openviking.utils.embedding_utils import vectorize_directory_meta

        active_ctx = ctx or self._default_ctx
        await vectorize_directory_meta(
            uri=uri,
            abstract=abstract,
            overview=overview,
            context_type=context_type,
            ctx=active_ctx,
            ingest_options=ingest_options,
        )

    async def _vectorize_single_file(
        self,
        parent_uri: str,
        context_type: str,
        file_path: str,
        summary_dict: Dict[str, str],
        ctx: Optional[RequestContext] = None,
        use_summary: bool = False,
        preserve_existing_created_at: bool = False,
        ingest_options: IngestOptions | None = None,
    ) -> None:
        """Vectorize a single file using its content or summary."""
        from openviking.utils.embedding_utils import vectorize_file

        active_ctx = ctx or self._default_ctx
        await vectorize_file(
            file_path=file_path,
            summary_dict=summary_dict,
            parent_uri=parent_uri,
            context_type=context_type,
            ctx=active_ctx,
            use_summary=use_summary,
            preserve_existing_created_at=preserve_existing_created_at,
            ingest_options=ingest_options,
        )
