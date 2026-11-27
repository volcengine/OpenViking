# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticProcessor: Processes messages from SemanticQueue, generates .abstract.md and .overview.md."""

import asyncio
import json
import re
import threading
import time
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Mapping, Optional, Tuple

import yaml

from openviking.core.context import ContextLevel
from openviking.core.namespace import classify_uri
from openviking.models.model_error import ModelErrorKind, model_error_kind
from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.observability.log_safety import safe_uri_for_log
from openviking.observability.reporting.token_sources import (
    bind_dashboard_token_source_type,
    get_dashboard_token_source_type,
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
from openviking.pyagfs.exceptions import AGFSNotADirectoryError
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import (
    get_task_tracker,
    run_tracked_operation,
    wait_for_enqueued_work,
)
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import get_task_context
from openviking.storage.abstract_overview import (
    AbstractOverviewWriteResult,
    body_for_preview,
    parse_abstract_overview,
    read_sidecar_if_exists,
)
from openviking.storage.errors import (
    HandoffRecoveryBlocked,
    InvalidPathLockRequestError,
    LockAcquisitionError,
)
from openviking.storage.index_action import FieldPatch
from openviking.storage.pathlock_recovery import prepare_handoff_retry
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor, SemanticTreeStats
from openviking.storage.queuefs.semantic_lock import SemanticLockScope
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_ops.directory_refresh import enqueue_directory_refresh
from openviking.storage.queuefs.semantic_queue import is_semantic_msg_stale
from openviking.storage.queuefs.semantic_work import SemanticMessageWork, SkillSemanticMessageWork
from openviking.storage.viking_fs import SyncDiff, get_viking_fs
from openviking.telemetry import (
    bind_telemetry,
    bind_telemetry_stage,
)
from openviking.telemetry.span_models import create_root_span_attributes
from openviking.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import VikingURI
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.config.vlm import VLMHandle, VLMResolver

logger = get_logger(__name__)

_REQUIRED_DIRECTORY_REFRESH_TRIGGERS = {"resource_ingest", "reindex"}

_PERMANENT_STORAGE_ERRORS = (
    FileNotFoundError,
    PermissionError,
    IsADirectoryError,
    NotADirectoryError,
    AGFSNotADirectoryError,
    InvalidPathLockRequestError,
)


def _is_permanent_storage_error(error: BaseException) -> bool:
    return any(isinstance(item, _PERMANENT_STORAGE_ERRORS) for item in (error, error.__cause__))


def _executor_lock(msg: SemanticMsg, lock: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Reuse content-write ownership only when it covers semantic sidecars."""
    if msg.generation_trigger != "content_write" or msg.lock_handoff is None:
        return lock
    covered_paths = msg.lock_handoff.get("covered_paths")
    if isinstance(covered_paths, list) and any(
        isinstance(item, dict) and item.get("kind") == "tree" for item in covered_paths
    ):
        return lock
    return None


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

    @staticmethod
    async def _cleanup_local_artifact(msg: SemanticMsg) -> None:
        try:
            if not msg.artifact_ref:
                return
            from openviking.parse.output import ParseArtifactRef, store_for_artifact_ref

            artifact_ref = ParseArtifactRef.from_dict(msg.artifact_ref)
            if artifact_ref.backend != "local":
                return
            store = store_for_artifact_ref(artifact_ref)
            await store.cleanup(artifact_ref)
        except Exception as exc:
            logger.info("Failed to clean local parse artifact: %s", type(exc).__name__)

    _tree_stats_by_telemetry_id: Dict[str, SemanticTreeStats] = {}
    _tree_stats_by_uri: Dict[str, SemanticTreeStats] = {}
    _tree_stats_order: List[Tuple[str, str]] = []
    _request_stats_by_telemetry_id: Dict[str, RequestQueueStats] = {}
    _request_stats_order: List[str] = []
    _max_cached_stats = 256

    def __init__(
        self,
        max_concurrent_llm: int = 32,
        *,
        embedding_worker_stopped: Optional[Callable[[], bool]] = None,
        vlm_resolver: Optional["VLMResolver"] = None,
        runtime_config_manager: Any = None,
    ):
        """
        Initialize SemanticProcessor.

        Args:
            max_concurrent_llm: Maximum concurrent LLM calls
        """
        self.max_concurrent_llm = max_concurrent_llm
        self._default_ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._breaker_lock = threading.Lock()
        self._embedding_worker_stopped = embedding_worker_stopped
        self._vlm_resolver = vlm_resolver
        self._runtime_config_manager = runtime_config_manager

    def _account_breaker(self, account_id: str) -> CircuitBreaker:
        with self._breaker_lock:
            breaker = self._circuit_breakers.get(account_id)
            if breaker is None:
                breaker = self._circuit_breakers[account_id] = CircuitBreaker()
            return breaker

    async def _get_vlm_config(self, ctx: RequestContext) -> "VLMHandle":
        if self._vlm_resolver is None:
            raise RuntimeError("SemanticProcessor requires a VLM resolver for account-owned work")
        return await self._vlm_resolver.get_vlm(ctx.account_id)

    @classmethod
    def _cache_tree_stats(cls, telemetry_id: str, uri: str, stats: SemanticTreeStats) -> None:
        with cls._stats_lock:
            if telemetry_id:
                previous = cls._tree_stats_by_telemetry_id.get(telemetry_id)
                if previous is not None:
                    stats = SemanticTreeStats(
                        total_nodes=previous.total_nodes + stats.total_nodes,
                        pending_nodes=previous.pending_nodes + stats.pending_nodes,
                        in_progress_nodes=previous.in_progress_nodes + stats.in_progress_nodes,
                        done_nodes=previous.done_nodes + stats.done_nodes,
                        failures=[*previous.failures, *stats.failures],
                        indexed_records=previous.indexed_records + stats.indexed_records,
                    )
                cls._tree_stats_by_telemetry_id[telemetry_id] = stats
            cls._tree_stats_by_uri[uri] = stats
            cls._tree_stats_order.append((telemetry_id, uri))
            if len(cls._tree_stats_order) > cls._max_cached_stats:
                old_telemetry_id, old_uri = cls._tree_stats_order.pop(0)
                if old_telemetry_id:
                    cls._tree_stats_by_telemetry_id.pop(old_telemetry_id, None)
                cls._tree_stats_by_uri.pop(old_uri, None)

    @classmethod
    def consume_tree_stats(
        cls,
        telemetry_id: str = "",
        uri: Optional[str] = None,
    ) -> Optional[SemanticTreeStats]:
        with cls._stats_lock:
            if telemetry_id and telemetry_id in cls._tree_stats_by_telemetry_id:
                stats = cls._tree_stats_by_telemetry_id.pop(telemetry_id, None)
                if uri:
                    cls._tree_stats_by_uri.pop(uri, None)
                return stats
            if uri and uri in cls._tree_stats_by_uri:
                return cls._tree_stats_by_uri.pop(uri, None)
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
        return RequestContext(
            user=UserIdentifier(msg.account_id, msg.user_id),
            role=Role(msg.role),
            group_ids=tuple(msg.group_ids),
            bypass_acl=True,
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

    async def _reenqueue_semantic_msg(
        self,
        msg: SemanticMsg,
        *,
        enqueue: Optional[Callable[[Any, SemanticMsg], Awaitable[None]]] = None,
    ) -> None:
        """Re-enqueue a semantic message for later processing.

        Throttles with a sleep when the circuit breaker is open to prevent
        re-enqueue storms (messages cycling at 5/sec during OPEN window).
        """
        import asyncio

        from openviking.storage.queuefs import get_queue_manager

        # Throttle to prevent re-enqueue storm during OPEN window
        wait = self._account_breaker(msg.account_id).retry_after
        if wait > 0:
            await asyncio.sleep(wait)

        queue_manager = get_queue_manager()
        if queue_manager is not None:
            semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC)
            if enqueue is None:
                await semantic_queue.enqueue(msg)
            else:
                await enqueue(semantic_queue, msg)
            logger.info("Re-enqueued semantic message: %s", safe_uri_for_log(str(msg.uri)))
        else:
            logger.error(
                "No queue manager available, cannot re-enqueue: %s", safe_uri_for_log(str(msg.uri))
            )

    async def _enqueue_semantic_retry(
        self, queue, msg: SemanticMsg, scope: SemanticLockScope
    ) -> None:
        """Transfer the live semantic lease to a retry before releasing this worker.

        Reusing the consumed handoff would require acquiring an unrelated lock,
        which conflicts with an update request still waiting under its outer lease.
        """
        agfs = get_viking_fs()._async_agfs
        lock_guard = PathLockGuard(agfs, scope.lock)
        handoff = await lock_guard.to_handoff()
        msg.lock_handoff = handoff

        def accept(_: str) -> bool:
            scope.lock = None
            scope._owned = False
            return True

        try:
            await lock_guard.handoff_and_dispatch(
                handoff, lambda: queue.enqueue(msg), accepted=accept
            )
        finally:
            if lock_guard.lease is not None:
                scope.lock = lock_guard.lease
                scope._owned = True

    async def _enqueue_skill_retry(self, queue, msg: SemanticMsg, scope: SemanticLockScope) -> None:
        """Compatibility wrapper for existing skill retry callers."""
        await self._enqueue_semantic_retry(queue, msg, scope)

    async def _requeue_semantic_msg_after_error(
        self,
        msg: SemanticMsg,
        error: Exception,
        *,
        work: SemanticMessageWork,
    ) -> ProcessResult:
        if isinstance(error, HandoffRecoveryBlocked):
            payload = await prepare_handoff_retry(msg, error, queue="Semantic")
            if payload is None:
                self._merge_request_stats(msg.telemetry_id, error_count=1)
                await self._cleanup_local_artifact(msg)
                return ProcessResult.failed(f"Pathlock recovery retry limit reached: {error}")
            msg.lock_handoff_retry = payload["lock_handoff_retry"]
        try:
            await work.reenqueue_after_error()
            self._merge_request_stats(msg.telemetry_id, requeue_count=1)
        except Exception as requeue_err:
            if isinstance(error, HandoffRecoveryBlocked):
                # No replacement is durable: propagate so the original delivery
                # remains recoverable rather than being ACKed as failed.
                raise
            logger.error("Failed to re-enqueue semantic message: %s", type(requeue_err).__name__)
            self._merge_request_stats(msg.telemetry_id, error_count=1)
            await self._cleanup_local_artifact(msg)
            return ProcessResult.failed(str(error))
        return ProcessResult.requeued()

    async def _enqueue_parent_refresh(
        self, msg: SemanticMsg, uri: str, *, l0_body_changed: bool
    ) -> None:
        if msg.generation_trigger == "content_copy" or not l0_body_changed:
            return
        if msg.context_type not in {"resource", "skill", "memory"}:
            return
        if not msg.propagate_to_parent:
            return
        parent = VikingURI(uri).parent
        if parent is None:
            return
        parent_uri = parent.uri
        if parent_uri in {"viking://", "viking://user", "viking://agent"}:
            return
        classification = classify_uri(parent_uri)
        if msg.context_type == "memory" and (
            not classification.is_memory or classification.is_memory_root
        ):
            return
        if msg.context_type == "skill":
            if (
                not classification.is_skill
                or classification.is_skill_namespace
                or classification.is_skill_root
            ):
                return
        await enqueue_directory_refresh(
            parent_uri,
            1,
            ctx=self._ctx_from_semantic_msg(msg),
            skip_vectorization=msg.skip_vectorization,
            dashboard_source_type=msg.dashboard_source_type
            or get_dashboard_token_source_type(generation_trigger=msg.generation_trigger)
            or "",
            required=msg.generation_trigger in _REQUIRED_DIRECTORY_REFRESH_TRIGGERS,
        )

    def _message_work(
        self, msg: SemanticMsg, lock: Optional[Dict[str, Any]] = None
    ) -> SemanticMessageWork:
        work_type = (
            SkillSemanticMessageWork
            if msg.context_type == "skill" and msg.generation_trigger != "directory_refresh"
            else SemanticMessageWork
        )
        return work_type(self, msg, lock)

    async def on_dequeue(
        self,
        data: Optional[Dict[str, Any]],
        lock: Optional[Dict[str, Any]] = None,
    ) -> ProcessResult:
        """Process dequeued SemanticMsg, recursively process all subdirectories."""
        import json

        if not data:
            return ProcessResult.success()
        try:
            if "data" in data and isinstance(data["data"], str):
                data = json.loads(data["data"])
            msg = SemanticMsg.from_dict(data)
        except Exception as exc:
            logger.error("Invalid semantic message: %s", type(exc).__name__)
            return ProcessResult.failed(str(exc))

        if msg.context_type != "skill" or get_task_context() is not None:
            return await self._process_message(msg, lock)

        # Parent refreshes and recovered taskless Skill messages need their own
        # child-work collector so package ownership lasts through vector writes.
        result: Optional[ProcessResult] = None

        async def process() -> Dict[str, Any]:
            nonlocal result
            result = await self._process_message(msg, lock)
            if result.error is not None:
                context = get_task_context()
                assert context is not None
                await get_task_tracker().fail(
                    context.task_id,
                    result.error,
                    account_id=msg.account_id,
                    user_id=msg.user_id,
                )
            return {"uri": msg.uri, "outcome": result.outcome.value}

        await run_tracked_operation(
            "semantic_refresh",
            account_id=msg.account_id,
            user_id=msg.user_id,
            resource_id=msg.uri,
            operation=process,
            wait=False,
        )
        assert result is not None
        return result

    async def _process_message(
        self,
        msg: SemanticMsg,
        lock: Optional[Dict[str, Any]],
    ) -> ProcessResult:
        collector = None
        work: Optional[SemanticMessageWork] = None
        execute_started_at: float | None = None
        queue_wait_ms = 0.0
        execute_status = "ok"
        breaker = None
        syncing_source = False
        directory_refresh = msg.generation_trigger == "directory_refresh"
        uses_model = msg.context_type != "memory" or not directory_refresh
        try:
            if uses_model:
                breaker = self._account_breaker(msg.account_id)
            work = self._message_work(msg, lock)
            work.start()
            execute_started_at = time.perf_counter()
            if msg.queue_enqueued_at > 0:
                queue_wait_ms = max((time.time() - msg.queue_enqueued_at) * 1000.0, 0.0)
            if VikingURI(msg.uri).parent is None:
                logger.info(
                    "Skipping semantic generation for root URI: %s", safe_uri_for_log(str(msg.uri))
                )
                await self._cleanup_local_artifact(msg)
                return ProcessResult.success()
            if msg.coalesce_key and await is_semantic_msg_stale(
                msg, agfs=get_viking_fs()._async_agfs
            ):
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
                        "Downgrading stale semantic message to file-only work: uri=%s version=%s",
                        safe_uri_for_log(str(msg.uri)),
                        msg.coalesce_version,
                    )
                    msg.aggregate_directory = False
                    msg.changes = live_file_changes
                    msg.coalesce_key = ""
                    msg.coalesce_version = 0
                else:
                    logger.info(
                        "Skipping stale semantic message: uri=%s version=%s",
                        safe_uri_for_log(str(msg.uri)),
                        msg.coalesce_version,
                    )
                    await work.skip()
                    await self._cleanup_local_artifact(msg)
                    return ProcessResult.success()
            # Circuit breaker: if API is known-broken, re-enqueue and wait
            try:
                if breaker is not None:
                    breaker.check()
            except CircuitBreakerOpen as _log_error:
                logger.info(
                    "Circuit breaker is open, re-enqueueing semantic message: %s error_type=%s",
                    safe_uri_for_log(str(msg.uri)),
                    type(_log_error).__name__,
                )
                await work.reenqueue()
                self._merge_request_stats(msg.telemetry_id, requeue_count=1)
                return ProcessResult.requeued()
            collector = work.resolve_telemetry()
            telemetry_ctx = bind_telemetry(collector) if collector is not None else nullcontext()
            with (
                telemetry_ctx,
                bind_dashboard_token_source_type(
                    msg.dashboard_source_type
                    or get_dashboard_token_source_type(generation_trigger=msg.generation_trigger)
                ),
            ):
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
                    logger.debug(
                        "Processing semantic generation for: %s (recursive=%s)",
                        safe_uri_for_log(str(msg.uri)),
                        msg.recursive,
                    )

                    logger.debug(
                        "Processing semantic message id=%s uri=%s",
                        type(msg.id).__name__,
                        safe_uri_for_log(str(msg.uri)),
                    )

                    # Resolving a deleted root can recreate it for lock metadata.
                    # Settle queued ownership before acknowledging skipped work.
                    if not await get_viking_fs().exists(msg.uri, ctx=current_ctx):
                        logger.info(
                            "Skipping semantic message for missing root: uri=%s",
                            safe_uri_for_log(str(msg.uri)),
                        )
                        await work.skip()
                        return ProcessResult.success()

                    if uses_model and self._vlm_resolver is None:
                        raise RuntimeError(
                            "SemanticProcessor requires a VLM resolver for account-owned work"
                        )
                    if not await work.acquire_lock(current_ctx):
                        return ProcessResult.success()
                    semantic_lock = work.scope
                    assert semantic_lock is not None
                    dag_stats = None
                    processing_succeeded = False
                    try:
                        if msg.plan is not None:
                            if msg.uri.rstrip("/") != msg.plan.root_uri:
                                raise ValueError("semantic message URI must match plan root_uri")
                            if msg.context_type != msg.plan.context_type:
                                raise ValueError(
                                    "semantic message context_type must match semantic plan"
                                )
                            for run_uri in msg.plan.execution_root_uris():
                                executor = SemanticTreeExecutor(
                                    processor=self,
                                    context_type=msg.context_type,
                                    max_concurrent_llm=self.max_concurrent_llm,
                                    ctx=current_ctx,
                                    lock=semantic_lock.lock,
                                    source=msg.plan.source_metadata,
                                    semantic_plan=msg.plan,
                                    telemetry_id=msg.telemetry_id,
                                    generation_trigger=msg.generation_trigger,
                                )
                                await executor.run(run_uri)
                                self._cache_tree_stats(
                                    msg.telemetry_id, run_uri, executor.get_stats()
                                )
                                if not executor.stale:
                                    write_result = getattr(
                                        executor,
                                        "root_write_result",
                                        AbstractOverviewWriteResult(
                                            wrote=True, abstract_body_changed=True
                                        ),
                                    )
                                    await self._enqueue_parent_refresh(
                                        msg,
                                        run_uri,
                                        l0_body_changed=write_result.abstract_body_changed,
                                    )
                            from collections import Counter

                            entries = msg.plan.tree.entries
                            action_counts = Counter(
                                entry.semantic_action.value for entry in entries
                            )
                            vector_slots = sum(
                                slot.action.value in {"upsert", "merge"}
                                for entry in entries
                                for slot in entry.index_slots
                            )
                            logger.debug(
                                "root=%s execution_roots=%d semantic_entries=%d actions=%s planned_vector_upserts=%s",
                                safe_uri_for_log(str(msg.plan.root_uri)),
                                len(msg.plan.execution_root_uris()),
                                len(entries),
                                dict(action_counts),
                                type(vector_slots).__name__,
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
                                    logger.debug(
                                        "Syncing semantic source into target before processing: %s -> %s",
                                        safe_uri_for_log(str(msg.uri)),
                                        safe_uri_for_log(str(msg.target_uri)),
                                    )
                                    syncing_source = True
                                    diff = await work.run_write(
                                        lambda: self._sync_topdown_recursive(
                                            msg.uri,
                                            msg.target_uri,
                                            ctx=current_ctx,
                                            lock=semantic_lock.lock,
                                        )
                                    )
                                    syncing_source = False
                                    logger.debug(
                                        "Diff computed: added_files=%s, deleted_files=%s, updated_files=%s, added_dirs=%s, deleted_dirs=%s",
                                        len(diff.added_files),
                                        len(diff.deleted_files),
                                        len(diff.updated_files),
                                        len(diff.added_dirs),
                                        len(diff.deleted_dirs),
                                    )
                                    changes = diff.to_changes()
                                    is_incremental = True
                                    target_uri = msg.target_uri
                                    run_uri = msg.target_uri
                                elif (
                                    target_exists
                                    and msg.changes is not None
                                    and msg.uri == msg.target_uri
                                ):
                                    is_incremental = True
                                    logger.debug(
                                        "Using direct incremental semantic update for: %s",
                                        safe_uri_for_log(str(msg.uri)),
                                    )
                            elif msg.changes is not None or directory_refresh:
                                is_incremental = True
                                target_uri = msg.uri
                                logger.debug(
                                    "Using direct incremental semantic update for: %s",
                                    safe_uri_for_log(str(msg.uri)),
                                )

                            defer_directory_refresh = (
                                msg.generation_trigger
                                in {
                                    "content_write",
                                    "resource_ingest",
                                    "semantic_refresh",
                                    "content_transfer_rebuild",
                                }
                                and msg.context_type in {"resource", "skill"}
                                and not msg.recursive
                                and not msg.skip_vectorization
                                and msg.changes is not None
                                and msg.aggregate_directory
                            )
                            executor = SemanticTreeExecutor(
                                processor=self,
                                context_type=msg.context_type,
                                max_concurrent_llm=self.max_concurrent_llm,
                                ctx=current_ctx,
                                incremental_update=is_incremental,
                                target_uri=target_uri,
                                recursive=msg.recursive,
                                lock=_executor_lock(msg, semantic_lock.lock),
                                is_code_repo=msg.is_code_repo,
                                changes=changes,
                                skip_vectorization=msg.skip_vectorization,
                                ingest_options=msg.ingest_options,
                                coalesce_key=msg.coalesce_key,
                                coalesce_version=msg.coalesce_version,
                                source=msg.source,
                                generation_trigger=msg.generation_trigger,
                                aggregate_directory=(
                                    msg.aggregate_directory and not defer_directory_refresh
                                ),
                                copy_source_uri=msg.copy_source_uri,
                                file_md5s=msg.file_md5s,
                                artifact_files=msg.artifact_files,
                                file_abstracts=msg.file_abstracts,
                                file_vector_actions=msg.file_vector_actions,
                                file_ingest_options=msg.file_ingest_options,
                                telemetry_id=msg.telemetry_id,
                            )
                            await executor.run(run_uri)
                            dag_stats = executor.get_stats()
                            self._cache_tree_stats(
                                msg.telemetry_id,
                                run_uri,
                                dag_stats,
                            )
                            if executor.stale and directory_refresh:
                                processing_succeeded = True
                                return ProcessResult.success({"skipped": True})
                            if not executor.stale and defer_directory_refresh:
                                # The shared directory task reads committed file abstracts.
                                await wait_for_enqueued_work(
                                    "Embedding",
                                    stop_waiting=self._embedding_worker_stopped,
                                )
                                await enqueue_directory_refresh(
                                    run_uri,
                                    len({uri for uris in changes.values() for uri in uris}),
                                    ctx=current_ctx,
                                    propagate_to_parent=msg.propagate_to_parent,
                                    dashboard_source_type=msg.dashboard_source_type
                                    or get_dashboard_token_source_type(
                                        generation_trigger=msg.generation_trigger
                                    )
                                    or "",
                                    required=msg.generation_trigger
                                    in _REQUIRED_DIRECTORY_REFRESH_TRIGGERS,
                                )
                            elif not executor.stale and msg.aggregate_directory:
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
                        processing_succeeded = True
                    finally:
                        await work.finish_processing(processing_succeeded)
                    failure = work.failure_result(dag_stats)
                    if failure is not None:
                        return failure
                    self._merge_request_stats(msg.telemetry_id, processed=1)
                    logger.debug(
                        "Completed semantic generation for: %s", safe_uri_for_log(str(msg.uri))
                    )
                    if breaker is not None:
                        breaker.record_success()
                    await self._cleanup_local_artifact(msg)
                    return ProcessResult.success()
                finally:
                    reset_root_observability_context(root_context_token)

        except asyncio.CancelledError:
            if work is not None:
                await work.cancel()
            raise
        except Exception as e:
            # A move-based sync may already have consumed part of its source.
            if isinstance(e, LockAcquisitionError) and not syncing_source:
                execute_status = "requeued"
                logger.info(
                    "Lock error processing semantic message, re-enqueueing without tripping API circuit breaker: %s",
                    type(e).__name__,
                )
                if msg is not None and work is not None:
                    result = await self._requeue_semantic_msg_after_error(
                        msg,
                        e,
                        work=work,
                    )
                    if result.error is not None:
                        execute_status = "error"
                    return result
                return ProcessResult.failed(str(e))

            kind = model_error_kind(e)
            if kind is ModelErrorKind.INPUT_TOO_LARGE:
                execute_status = "error"
                logger.info(
                    "Semantic input too large; dropping work error_type=%s", type(e).__name__
                )
                if msg is not None:
                    self._merge_request_stats(msg.telemetry_id, error_count=1)
                if msg is not None:
                    await self._cleanup_local_artifact(msg)
                return ProcessResult.failed(str(e))
            elif _is_permanent_storage_error(e) or kind in {
                ModelErrorKind.MODEL_NOT_FOUND,
                ModelErrorKind.UNSUPPORTED,
                ModelErrorKind.INVALID_REQUEST,
                ModelErrorKind.CONTENT_SAFETY,
            }:
                execute_status = "error"
                (
                    logger.info
                    if kind in {ModelErrorKind.INVALID_REQUEST, ModelErrorKind.CONTENT_SAFETY}
                    else logger.error
                )(
                    "Semantic work permanently failed; dropping work error_type=%s",
                    type(e).__name__,
                )
                if breaker is not None and kind is not None:
                    breaker.record_failure(e)
                if msg is not None:
                    self._merge_request_stats(msg.telemetry_id, error_count=1)
                if msg is not None:
                    await self._cleanup_local_artifact(msg)
                return ProcessResult.failed(str(e))
            else:
                execute_status = "error"
                if breaker is not None and kind is not None:
                    breaker.record_failure(e)
                logger.error(
                    "Error processing semantic message, not re-enqueueing: %s", type(e).__name__
                )
                if msg is not None:
                    self._merge_request_stats(msg.telemetry_id, error_count=1)
                    await self._cleanup_local_artifact(msg)
                return ProcessResult.failed(str(e))
        finally:
            if msg is not None and execute_started_at is not None:
                from openviking.metrics.datasources.resource import ResourceIngestionEventDataSource

                ResourceIngestionEventDataSource.record_stage(
                    stage="semantic_queue_wait",
                    status=execute_status,
                    duration_seconds=queue_wait_ms / 1000.0,
                    account_id=msg.account_id,
                )
                ResourceIngestionEventDataSource.record_stage(
                    stage="semantic_execute",
                    status=execute_status,
                    duration_seconds=(time.perf_counter() - execute_started_at),
                    account_id=msg.account_id,
                )
            if work is not None:
                await work.close()

    async def on_cancelled(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        """Release a queued semantic lock before cancelled work is ACKed."""
        try:
            import json

            payload = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(payload, str):
                payload = json.loads(payload)
            msg = SemanticMsg.from_dict(payload)
        except (TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))

        await self._message_work(msg).cancel_queued()
        await self._cleanup_local_artifact(msg)
        return ProcessResult.cancelled()

    async def _release_cancelled_semantic_lock(self, msg: SemanticMsg) -> None:
        if msg.lock_handoff is not None:
            lock_guard = PathLockGuard(get_viking_fs()._async_agfs)
            try:
                await lock_guard.adopt(msg.lock_handoff)
            except Exception as exc:
                logger.info("Failed to release cancelled semantic lock: %s", type(exc).__name__)
            finally:
                await lock_guard.close()

    async def _resolve_skill_semantic_lock(
        self,
        msg: SemanticMsg,
        ctx: RequestContext,
        caller_lock: Optional[Dict[str, Any]],
    ) -> SemanticLockScope:
        scope = None

        async def acquire():
            nonlocal scope
            viking_fs = get_viking_fs()
            scope = await SemanticLockScope.resolve(
                msg.lock_handoff,
                caller_lock=caller_lock,
                ctx=ctx,
                fallback_path_factory=lambda: viking_fs._uri_to_path(msg.uri, ctx=ctx),
            )
            if scope.lock is None and await viking_fs.exists(msg.uri, ctx=ctx):
                lease = await viking_fs._async_agfs.pathlock_acquire_tree(
                    viking_fs._uri_to_path(msg.uri, ctx=ctx)
                )
                scope = SemanticLockScope(lease, _owned=True)
            return scope

        try:
            return await run_to_completion(acquire)
        except asyncio.CancelledError:
            # Acquiring/adopting a lease can itself outlive a cancelled await.
            if scope is not None:
                await run_to_completion(scope.close)
            raise

    def get_tree_stats(self, account_id: str | None = None) -> Optional["SemanticTreeStats"]:
        return SemanticTreeExecutor.get_active_stats(account_id=account_id)

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
            logger.error(
                "Failed to delete root directory %s: %s",
                safe_uri_for_log(str(root_uri)),
                type(e).__name__,
            )
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
                    await viking_fs.stat(target_mapping, ctx=ctx, skip_count=True)
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
            logger.info(
                "Failed to rewrite image URIs for %s: %s",
                safe_uri_for_log(str(target_uri)),
                type(e).__name__,
            )

    async def _generate_text_summary(
        self,
        file_path: str,
        file_name: str,
        llm_sem: asyncio.Semaphore,
        ctx: Optional[RequestContext] = None,
        file_content: Optional[bytes] = None,
        materialize_content: bool = False,
    ) -> Dict[str, Any]:
        """Generate summary for a single text file (code, documentation, or other text)."""
        viking_fs = get_viking_fs()
        active_ctx = ctx or self._default_ctx

        content = (
            file_content
            if file_content is not None
            else await viking_fs.read_file(file_path, ctx=active_ctx)
        )
        if isinstance(content, bytes):
            from openviking.utils.embedding_utils import _decode_text_bytes

            content = _decode_text_bytes(content)

        def result(summary: str) -> Dict[str, Any]:
            return {"name": file_name, "summary": summary}

        config = get_openviking_config()
        live_context = self._parse_live_context_metadata(content)

        # Limit content length
        max_chars = config.semantic.max_file_content_chars
        if len(content) > max_chars:
            content = content[:max_chars] + "\n...(truncated)"

        # Live context nodes need an operational summary rather than a generic
        # Markdown document summary.
        file_type = self._detect_file_type(file_name)
        if live_context is not None:
            prompt_id = "semantic.live_context_summary"
        elif file_type == FILE_TYPE_CODE:
            from openviking.parse.parsers.code.ast import extract_skeleton_result

            extraction = await asyncio.to_thread(extract_skeleton_result, file_name, content)
            if extraction.text:
                skeleton_text = extraction.text
                max_skeleton_chars = config.semantic.max_skeleton_chars
                if len(skeleton_text) > max_skeleton_chars:
                    skeleton_text = skeleton_text[:max_skeleton_chars]
                return result(skeleton_text)
            prompt_id = "semantic.code_summary"
        elif file_type == FILE_TYPE_DOCUMENTATION:
            prompt_id = "semantic.document_summary"
        else:
            prompt_id = "semantic.file_summary"

        vlm = await self._get_vlm_config(active_ctx)
        if not vlm.is_available():
            if file_type == FILE_TYPE_CODE:
                logger.info(
                    "Code summary model unavailable; using empty summary target=%s degraded=true",
                    safe_uri_for_log(file_path),
                )
            else:
                logger.info("Summary model unavailable; using empty summary degraded=true")
            return result("")

        from openviking.config.generation import get_output_language_override
        from openviking.session.memory.utils.language import resolve_output_language

        output_language_override = await get_output_language_override(
            self._runtime_config_manager, active_ctx.account_id, cluster_config=config
        )
        output_language = resolve_output_language(
            content, output_language_override=output_language_override
        )
        if live_context is not None:
            tool_uri = live_context["tool_uri"]
            user_tools_prefix = f"viking://user/{active_ctx.user.user_id}/tools/"
            if tool_uri.startswith(user_tools_prefix):
                tool_uri = "viking://~/tools/" + tool_uri[len(user_tools_prefix) :]
            prompt_variables = {
                "file_name": file_name,
                "title": live_context["title"],
                "description": live_context["description"],
                "tool_uri": tool_uri,
                "arguments": json.dumps(
                    live_context["arguments"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "output_language": output_language,
            }
        else:
            prompt_variables = {
                "file_name": file_name,
                "content": content,
                "output_language": output_language,
            }
        prompt = render_prompt(prompt_id, prompt_variables)

        async with llm_sem:
            with bind_telemetry_stage("semantic_execute"):
                if prompt_id == "semantic.code_summary":
                    logger.info(
                        "Code skeleton unavailable; using LLM fallback target=%s",
                        safe_uri_for_log(file_path),
                    )
                summary = await vlm.get_completion_async(prompt)
        return result(summary.strip())

    @staticmethod
    def _parse_live_context_metadata(content: str) -> Optional[Dict[str, Any]]:
        lines = content.splitlines()
        if not lines or lines[0] != "---":
            return None
        try:
            closing_index = lines[1:].index("---") + 1
            metadata = yaml.safe_load("\n".join(lines[1:closing_index]))
        except (ValueError, yaml.YAMLError):
            return None
        if not isinstance(metadata, dict) or metadata.get("type") != "OpenViking Live Context":
            return None
        extension = metadata.get("x-openviking")
        if not isinstance(extension, dict) or extension.get("type") != "mcp":
            return None
        tool_uri = extension.get("definition")
        arguments = extension.get("args", {})
        if not isinstance(tool_uri, str) or not isinstance(arguments, dict):
            return None
        return {
            "title": str(metadata.get("title") or ""),
            "description": str(metadata.get("description") or ""),
            "tool_uri": tool_uri,
            "arguments": arguments,
        }

    async def _generate_single_file_summary(
        self,
        file_path: str,
        llm_sem: Optional[asyncio.Semaphore] = None,
        ctx: Optional[RequestContext] = None,
        file_content: Optional[bytes] = None,
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
                prefix = (
                    file_content[:MPEG_TS_PROBE_BYTES]
                    if file_content is not None
                    else await get_viking_fs().read(
                        file_path,
                        offset=0,
                        size=MPEG_TS_PROBE_BYTES,
                        ctx=ctx,
                    )
                )
            except Exception:
                prefix = None
            media_type = get_media_type(file_name, None, content=prefix)
        if media_type in {"image", "audio", "video"}:
            vlm = await self._get_vlm_config(ctx or self._default_ctx)
            if media_type == "image":
                return await generate_image_summary(
                    file_path,
                    file_name,
                    llm_sem,
                    ctx=ctx,
                    vlm=vlm,
                )
            from openviking.config.generation import get_output_language_override

            output_language_override = await get_output_language_override(
                self._runtime_config_manager, (ctx or self._default_ctx).account_id
            )
            if media_type == "audio":
                return await generate_audio_summary(
                    file_path,
                    file_name,
                    llm_sem,
                    ctx=ctx,
                    vlm=vlm,
                    output_language_override=output_language_override,
                )
            return await generate_video_summary(
                file_path,
                file_name,
                llm_sem,
                ctx=ctx,
                vlm=vlm,
                output_language_override=output_language_override,
            )
        return await self._generate_text_summary(
            file_path, file_name, llm_sem, ctx=ctx, file_content=file_content
        )

    async def _generate_directory_semantics(
        self,
        dir_uri: str,
        file_summaries: List[Dict[str, str]],
        children_abstracts: List[Dict[str, str]],
        *,
        ctx: RequestContext,
        total_entries: int,
        existing_raw: Mapping[int, str | bytes] | None = None,
    ):
        from openviking.config.generation import get_output_language_override
        from openviking.server.error_mapping import is_not_found_error
        from openviking.session.memory.utils.language import resolve_output_language
        from openviking.storage.queuefs.semantic_ops.directory_generation import (
            DirectoryGeneration,
            generate_directory_semantics,
        )

        raw = (
            await read_sidecar_if_exists(get_viking_fs(), f"{dir_uri}/.abstract.md", ctx)
            if existing_raw is None
            else existing_raw.get(ContextLevel.ABSTRACT)
        )
        previous = parse_abstract_overview(raw) if raw is not None else None
        entries = {
            item["name"]: {
                "kind": "file",
                "summary": item["summary"],
                **({"title": item["title"]} if item.get("title") else {}),
            }
            for item in file_summaries
        }
        entries.update(
            {
                item["name"]: {"kind": "directory", "summary": item["abstract"]}
                for item in children_abstracts
            }
        )
        navigation_renderer = None
        preset_abstract = None
        from openviking.core.namespace import context_type_for_uri

        if context_type_for_uri(dir_uri) == "memory":
            if not total_entries:
                return DirectoryGeneration(
                    abstract=previous.body if previous is not None else None, overview=None
                )
            from openviking.session.memory.memory_type_registry import get_default_registry
            from openviking.session.memory.memory_updater import MemoryUpdater
            from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
            from openviking.session.memory.utils.uri import render_template

            memory_type = MemoryUpdater.memory_type_from_uri(dir_uri)
            schema = get_default_registry().get(memory_type) if memory_type else None
            overview_template = schema.overview_template if schema else None
            abstract_template = schema.abstract_template if schema else None
            template_context = {
                "memory_type": memory_type,
                "directory_name": dir_uri.rsplit("/", 1)[-1],
                "subdirectory": dir_uri.partition(f"/memories/{memory_type}/")[2],
            }
            preset_abstract = (
                render_template(abstract_template, template_context).strip()
                if abstract_template and previous is None
                else ""
            )
            fields = {}
            for name, entry in list(entries.items()):
                if entry["kind"] != "file":
                    fields[name] = {}
                    continue
                file_uri = f"{dir_uri}/{name}"
                try:
                    raw = await get_viking_fs().read_file(file_uri, ctx=ctx)
                except Exception as exc:
                    if not is_not_found_error(exc):
                        raise
                    entries.pop(name)
                    continue
                memory = MemoryFileUtils.read(raw, uri=file_uri)
                fields[name] = memory.to_metadata()
                if not entry["summary"]:
                    summary = memory.extra_fields.get("summary")
                    entry["summary"] = (
                        summary if isinstance(summary, str) and summary.strip() else memory.content
                    ) or ""

            if overview_template:

                def navigation_renderer(names: list[str]) -> str:
                    rendered = render_template(
                        overview_template,
                        {
                            **template_context,
                            "items": [
                                {
                                    "file_name": name,
                                    "file_content": fields[name],
                                    "summary": entries[name]["summary"],
                                }
                                for name in names
                            ],
                        },
                    )
                    return re.sub(r"\A# [^\n]*\n*", "", rendered).strip()

        config = get_openviking_config()
        override = await get_output_language_override(
            self._runtime_config_manager, ctx.account_id, cluster_config=config
        )
        language = resolve_output_language(
            "\n".join(f"- {name}: {entry['summary']}" for name, entry in entries.items()),
            output_language_override=override,
        )

        async def complete(prompt: str) -> str:
            vlm = await self._get_vlm_config(ctx)
            if not vlm.is_available():
                raise RuntimeError("VLM is unavailable for directory abstract generation")
            with bind_telemetry_stage("semantic_execute"):
                return await vlm.get_completion_async(prompt)

        return await generate_directory_semantics(
            dir_uri,
            entries,
            previous=previous,
            config=config.semantic,
            language=language,
            complete=complete,
            max_concurrent_llm=self.max_concurrent_llm,
            total_entries=total_entries,
            navigation_renderer=navigation_renderer,
            preset_abstract=preset_abstract,
        )

    async def _skill_root_semantics(
        self,
        uri: str,
        *,
        ctx: RequestContext,
        skill_content: str | bytes | None = None,
    ) -> tuple[str, str]:
        """Generate the root from the Skill definition."""
        from openviking.core.skill_loader import SkillLoader
        from openviking.utils.skill_processor import SkillProcessor

        if skill_content is None:
            skill_content = await get_viking_fs().read_file(f"{uri}/SKILL.md", ctx=ctx)
        if isinstance(skill_content, bytes):
            skill_content = skill_content.decode("utf-8")
        definition = SkillLoader.parse(skill_content)
        return definition.get("content", ""), SkillProcessor._build_skill_abstract(definition)

    async def _vectorize_directory(
        self,
        uri: str,
        context_type: str,
        abstract: str,
        overview: str,
        ctx: Optional[RequestContext] = None,
        ingest_options: IngestOptions | None = None,
        skill_source_path: str = "",
        scalar_overrides: Optional[Dict[int, Dict[str, Any]]] = None,
        actions: Optional[Dict[int, str]] = None,
        field_patches: Optional[Dict[int, FieldPatch]] = None,
        include_abstract: bool = True,
        include_overview: bool = True,
        telemetry_id: str | None = None,
        md5s: Optional[Dict[int, str]] = None,
    ) -> set[int]:
        """Create directory Context and enqueue to EmbeddingQueue."""

        from openviking.utils.embedding_utils import vectorize_directory_meta

        active_ctx = ctx or self._default_ctx
        skill_meta = None
        if context_type == "skill" and classify_uri(uri).is_skill_root:
            import yaml

            parsed = yaml.safe_load(body_for_preview(abstract))
            if isinstance(parsed, dict):
                skill_meta = {
                    key: parsed.get(key, [] if key in {"tags", "allowed_tools"} else "")
                    for key in ("name", "description", "tags", "allowed_tools")
                }
                skill_meta["source_path"] = skill_source_path
        return await vectorize_directory_meta(
            uri=uri,
            abstract=abstract,
            overview=overview,
            context_type=context_type,
            ctx=active_ctx,
            ingest_options=ingest_options,
            content_is_body=context_type == "skill",
            **({"meta": skill_meta} if skill_meta is not None else {}),
            scalar_overrides=scalar_overrides,
            actions=actions,
            field_patches=field_patches,
            include_abstract=include_abstract,
            include_overview=include_overview,
            telemetry_id=telemetry_id,
            md5s=md5s,
        )

    async def _load_indexed_file_summaries(
        self,
        file_paths: List[str],
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, str]:
        """Load existing summaries from the files' L2 index records."""
        if not file_paths:
            return {}
        viking_fs = get_viking_fs()
        vector_store = viking_fs._get_vector_store()
        if vector_store is None:
            return {}
        active_ctx = ctx or self._default_ctx
        return await vector_store.get_l2_abstracts_by_uris(file_paths, ctx=active_ctx)

    async def _update_vector_fields(
        self,
        *,
        record_id: str,
        uri: str,
        level: int,
        field_patch: FieldPatch,
        ctx: RequestContext,
        telemetry_id: str | None = None,
    ) -> bool:
        from openviking.storage.queuefs import get_queue_manager
        from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
        from openviking.telemetry import get_current_telemetry
        from openviking.utils.embedding_utils import _enqueue_embedding_message

        embedding_msg = EmbeddingMsg.for_update_fields(
            record_id=record_id,
            field_patch=field_patch,
            context_data={
                "uri": uri,
                "level": level,
                "account_id": ctx.account_id,
                "owner_user_id": ctx.user.user_id,
            },
            telemetry_id=(
                get_current_telemetry().telemetry_id if telemetry_id is None else telemetry_id
            ),
        )
        queue_manager = get_queue_manager()
        embedding_queue = queue_manager.get_queue(queue_manager.EMBEDDING, allow_create=True)
        return await _enqueue_embedding_message(
            embedding_queue,
            embedding_msg,
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
        file_md5: Optional[str] = None,
        file_content: Optional[bytes] = None,
        materialize_content: bool = False,
        scalar_override: Optional[Dict[str, Any]] = None,
        field_patch: FieldPatch | None = None,
        action: str = "merge",
        telemetry_id: str | None = None,
    ) -> bool:
        """Vectorize a single file using its content or summary."""
        from openviking.utils.embedding_utils import vectorize_file

        active_ctx = ctx or self._default_ctx
        return await vectorize_file(
            file_path=file_path,
            summary_dict=summary_dict,
            parent_uri=parent_uri,
            context_type=context_type,
            ctx=active_ctx,
            use_summary=use_summary,
            preserve_existing_created_at=preserve_existing_created_at,
            ingest_options=ingest_options,
            file_md5=file_md5,
            file_content=file_content,
            materialize_content=materialize_content,
            scalar_override=scalar_override,
            field_patch=field_patch,
            action=action,
            telemetry_id=telemetry_id,
        )
