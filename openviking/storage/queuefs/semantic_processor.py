# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SemanticProcessor: Processes messages from SemanticQueue, generates .abstract.md and .overview.md."""

import asyncio
import threading
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from openviking.parse.parsers.constants import (
    CODE_EXTENSIONS,
    DOCUMENTATION_EXTENSIONS,
    FILE_TYPE_CODE,
    FILE_TYPE_DOCUMENTATION,
    FILE_TYPE_OTHER,
)
from openviking.parse.parsers.media.utils import (
    generate_audio_summary,
    generate_image_summary,
    generate_video_summary,
    get_media_type,
)
from openviking.prompts import render_prompt
from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.semantic_dag import DagStats, SemanticDagExecutor
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import bind_telemetry, resolve_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    classify_api_error,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import VikingURI
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DiffResult:
    """Directory diff result for sync operations."""

    added_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    updated_files: List[str] = field(default_factory=list)
    added_dirs: List[str] = field(default_factory=list)
    deleted_dirs: List[str] = field(default_factory=list)


class RequestQueueStats:
    processed: int = 0
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

    def __init__(self, max_concurrent_llm: int = 100):
        """
        Initialize SemanticProcessor.

        Args:
            max_concurrent_llm: Maximum concurrent LLM calls
        """
        self.max_concurrent_llm = max_concurrent_llm
        self._dag_executor: Optional[SemanticDagExecutor] = None
        self._current_ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        self._current_msg: Optional[SemanticMsg] = None
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
        error_count: int = 0,
    ) -> None:
        if not telemetry_id:
            return
        with cls._stats_lock:
            stats = cls._request_stats_by_telemetry_id.setdefault(telemetry_id, RequestQueueStats())
            stats.processed += processed
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
    def _owner_space_for_uri(uri: str, ctx: RequestContext) -> str:
        """Derive owner_space from a URI.

        Resources (viking://resources/...) always get owner_space="" so they
        are globally visible.  User / agent / session URIs inherit the
        caller's space name.
        """
        if uri.startswith("viking://agent/"):
            return ctx.user.agent_space_name()
        if uri.startswith("viking://user/") or uri.startswith("viking://session/"):
            return ctx.user.user_space_name()
        # resources and anything else → shared (empty owner_space)
        return ""

    @staticmethod
    def _ctx_from_semantic_msg(msg: SemanticMsg) -> RequestContext:
        role = Role(msg.role) if msg.role in {r.value for r in Role} else Role.ROOT
        return RequestContext(
            user=UserIdentifier(msg.account_id, msg.user_id, msg.agent_id),
            role=role,
        )

    def _detect_file_type(self, file_name: str) -> str:
        """
        Detect file type based on extension using constants from code parser.

        Args:
            file_name: File name with extension

        Returns:
            FILE_TYPE_CODE, FILE_TYPE_DOCUMENTATION, or FILE_TYPE_OTHER
        """
        file_name_lower = file_name.lower()

        # Check if file is a code file
        for ext in CODE_EXTENSIONS:
            if file_name_lower.endswith(ext):
                return FILE_TYPE_CODE

        # Check if file is a documentation file
        for ext in DOCUMENTATION_EXTENSIONS:
            if file_name_lower.endswith(ext):
                return FILE_TYPE_DOCUMENTATION

        # Default to other
        return FILE_TYPE_OTHER

    async def _check_file_content_changed(
        self, file_path: str, target_file: str, ctx: Optional[RequestContext] = None
    ) -> bool:
        """Check if file content has changed compared to target file."""
        viking_fs = get_viking_fs()
        try:
            current_stat = await viking_fs.stat(file_path, ctx=ctx)
            target_stat = await viking_fs.stat(target_file, ctx=ctx)
            current_size = current_stat.get("size") if isinstance(current_stat, dict) else None
            target_size = target_stat.get("size") if isinstance(target_stat, dict) else None
            if current_size is not None and target_size is not None and current_size != target_size:
                return True
            current_content = await viking_fs.read_file(file_path, ctx=ctx)
            target_content = await viking_fs.read_file(target_file, ctx=ctx)
            return current_content != target_content
        except Exception:
            return True

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

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Process dequeued SemanticMsg, recursively process all subdirectories."""
        msg: Optional[SemanticMsg] = None
        collector = None
        release_lock_in_finally = True
        try:
            import json

            if not data:
                return None

            if "data" in data and isinstance(data["data"], str):
                data = json.loads(data["data"])

            assert data is not None
            msg = SemanticMsg.from_dict(data)
            # Circuit breaker: if API is known-broken, re-enqueue and wait
            try:
                self._circuit_breaker.check()
            except CircuitBreakerOpen:
                logger.warning(
                    f"Circuit breaker is open, re-enqueueing semantic message: {msg.uri}"
                )
                await self._reenqueue_semantic_msg(msg)
                self.report_success()
                return None
            collector = resolve_telemetry(msg.telemetry_id)
            telemetry_ctx = bind_telemetry(collector) if collector is not None else nullcontext()
            with telemetry_ctx:
                self._current_msg = msg
                self._current_ctx = self._ctx_from_semantic_msg(msg)
                logger.info(
                    f"Processing semantic generation for: {msg.uri} (recursive={msg.recursive})"
                )

                logger.info(f"Processing semantic generation for: {msg})")

                if msg.context_type == "memory":
                    await self._process_memory_directory(msg)
                else:
                    is_incremental = False
                    viking_fs = get_viking_fs()
                    if msg.target_uri:
                        target_exists = await viking_fs.exists(
                            msg.target_uri, ctx=self._current_ctx
                        )
                        # Check if target URI exists and is not the same as the source URI（避免重复处理）
                        if target_exists and msg.uri != msg.target_uri:
                            is_incremental = True
                            logger.info(
                                f"Target URI exists, using incremental update: {msg.target_uri}"
                            )

                    # Re-acquire lifecycle lock if handle was lost (e.g. server restart)
                    if msg.lifecycle_lock_handle_id:
                        lock_uri = msg.target_uri or msg.uri
                        msg.lifecycle_lock_handle_id = await self._ensure_lifecycle_lock(
                            msg.lifecycle_lock_handle_id,
                            viking_fs._uri_to_path(lock_uri, ctx=self._current_ctx),
                        )

                    executor = SemanticDagExecutor(
                        processor=self,
                        context_type=msg.context_type,
                        max_concurrent_llm=self.max_concurrent_llm,
                        ctx=self._current_ctx,
                        incremental_update=is_incremental,
                        target_uri=msg.target_uri,
                        semantic_msg_id=msg.id,
                        telemetry_id=msg.telemetry_id,
                        recursive=msg.recursive,
                        lifecycle_lock_handle_id=msg.lifecycle_lock_handle_id,
                        is_code_repo=msg.is_code_repo,
                    )
                    self._dag_executor = executor
                    if msg.lifecycle_lock_handle_id:
                        # The DAG owns lifecycle lock release after this point.
                        release_lock_in_finally = False
                    await executor.run(msg.uri)
                    self._cache_dag_stats(
                        msg.telemetry_id,
                        msg.uri,
                        executor.get_stats(),
                    )
                self._merge_request_stats(msg.telemetry_id, processed=1)
                logger.info(f"Completed semantic generation for: {msg.uri}")
                self.report_success()
                self._circuit_breaker.record_success()
                return None

        except Exception as e:
            error_class = classify_api_error(e)
            if error_class == "permanent":
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
                    try:
                        await self._reenqueue_semantic_msg(msg)
                    except Exception as requeue_err:
                        logger.error(f"Failed to re-enqueue semantic message: {requeue_err}")
                        self._merge_request_stats(msg.telemetry_id, error_count=1)
                        get_request_wait_tracker().mark_semantic_failed(
                            msg.telemetry_id, msg.id, str(e)
                        )
                        self.report_error(str(e), data)
                        return None
                    self.report_success()
                else:
                    self.report_error(str(e), data)
            return None
        finally:
            # Safety net: release lifecycle lock if still held (e.g. on exception
            # before the DAG executor took ownership)
            if release_lock_in_finally and msg and msg.lifecycle_lock_handle_id:
                try:
                    from openviking.storage.transaction import get_lock_manager

                    lm = get_lock_manager()
                    handle = lm.get_handle(msg.lifecycle_lock_handle_id)
                    if handle:
                        await lm.release(handle)
                        logger.info(
                            f"[SemanticProcessor] Safety-net released lifecycle lock "
                            f"{msg.lifecycle_lock_handle_id}"
                        )
                except Exception:
                    pass
            self._current_msg = None
            self._current_ctx = None

    def get_dag_stats(self) -> Optional["DagStats"]:
        if not self._dag_executor:
            return None
        return self._dag_executor.get_stats()

    @staticmethod
    async def _ensure_lifecycle_lock(handle_id: str, lock_path: str) -> str:
        """If the handle is missing (server restart), re-acquire a SUBTREE lock.

        Returns the (possibly new) handle ID, or "" on failure.
        """
        from openviking.storage.transaction import get_lock_manager

        lm = get_lock_manager()
        if lm.get_handle(handle_id):
            return handle_id
        new_handle = lm.create_handle()
        if await lm.acquire_subtree(new_handle, lock_path):
            logger.info(f"Re-acquired lifecycle lock on {lock_path} (handle {new_handle.id})")
            return new_handle.id
        logger.warning(f"Failed to re-acquire lifecycle lock on {lock_path}")
        await lm.release(new_handle)
        return ""

    async def _process_memory_directory(self, msg: SemanticMsg) -> None:
        """Process a memory directory with special handling.

        For memory directories:
        - Memory files are already vectorized via embedding queue
        - Only generate abstract.md and overview.md
        - Vectorize the generated abstract.md and overview.md

        Args:
            msg: The semantic message containing directory info and changes
        """
        viking_fs = get_viking_fs()
        dir_uri = msg.uri
        ctx = self._current_ctx
        llm_sem = asyncio.Semaphore(self.max_concurrent_llm)
        request_wait_tracker = get_request_wait_tracker()

        def _mark_done() -> None:
            if msg.telemetry_id and msg.id:
                request_wait_tracker.mark_semantic_done(msg.telemetry_id, msg.id)

        def _mark_failed(message: str) -> None:
            if msg.telemetry_id and msg.id:
                request_wait_tracker.mark_semantic_failed(msg.telemetry_id, msg.id, message)

        try:
            entries = await viking_fs.ls(dir_uri, ctx=ctx)
        except Exception as e:
            logger.warning(f"Failed to list memory directory {dir_uri}: {e}")
            _mark_failed(str(e))
            if msg.lifecycle_lock_handle_id:
                await self._release_memory_lifecycle_lock(msg.lifecycle_lock_handle_id)
            return

        file_paths: List[str] = []
        for entry in entries:
            name = entry.get("name", "")
            if not name or name.startswith(".") or name in [".", ".."]:
                continue
            if not entry.get("isDir", False):
                item_uri = VikingURI(dir_uri).join(name).uri
                file_paths.append(item_uri)

        if not file_paths:
            logger.info(f"No memory files found in {dir_uri}")
            _mark_done()
            if msg.lifecycle_lock_handle_id:
                await self._release_memory_lifecycle_lock(msg.lifecycle_lock_handle_id)
            return

        file_summaries: List[Dict[str, str]] = []
        existing_summaries: Dict[str, str] = {}

        if msg.changes:
            try:
                old_overview = await viking_fs.read_file(f"{dir_uri}/.overview.md", ctx=ctx)
                if old_overview:
                    existing_summaries = self._parse_overview_md(old_overview)
                    logger.info(
                        f"Parsed {len(existing_summaries)} existing summaries from overview.md"
                    )
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

        # Separate cached from changed files to allow concurrent VLM calls
        pending_indices: List[Tuple[int, str]] = []
        file_summaries: List[Optional[Dict[str, str]]] = [None] * len(file_paths)

        for idx, file_path in enumerate(file_paths):
            file_name = file_path.split("/")[-1]

            if file_path not in changed_files and file_name in existing_summaries:
                file_summaries[idx] = {"name": file_name, "summary": existing_summaries[file_name]}
                logger.debug(f"Reused existing summary for {file_name}")
            else:
                pending_indices.append((idx, file_path))

        if pending_indices:
            logger.info(
                f"Generating summaries for {len(pending_indices)} changed files concurrently "
                f"(reused {len(file_paths) - len(pending_indices)} cached)"
            )

            async def _gen(idx: int, file_path: str) -> None:
                file_name = file_path.split("/")[-1]
                try:
                    summary_dict = await self._generate_single_file_summary(
                        file_path, llm_sem=llm_sem, ctx=ctx
                    )
                    file_summaries[idx] = summary_dict
                    logger.debug(f"Generated summary for {file_name}")
                except Exception as e:
                    logger.warning(f"Failed to generate summary for {file_path}: {e}")
                    file_summaries[idx] = {"name": file_name, "summary": ""}

            await asyncio.gather(*[_gen(i, fp) for i, fp in pending_indices])

        file_summaries = [s for s in file_summaries if s is not None]

        overview = await self._generate_overview(dir_uri, file_summaries, [], llm_sem=llm_sem)
        abstract = self._extract_abstract_from_overview(overview)
        overview, abstract = self._enforce_size_limits(overview, abstract)

        try:
            await viking_fs.write_file(f"{dir_uri}/.overview.md", overview, ctx=ctx)
            await viking_fs.write_file(f"{dir_uri}/.abstract.md", abstract, ctx=ctx)
            logger.info(f"Generated abstract.md and overview.md for {dir_uri}")
        except Exception as e:
            logger.error(f"Failed to write abstract/overview for {dir_uri}: {e}")
            _mark_failed(str(e))
            if msg.lifecycle_lock_handle_id:
                await self._release_memory_lifecycle_lock(msg.lifecycle_lock_handle_id)
            return

        try:
            if msg.telemetry_id and msg.id:
                from openviking.storage.queuefs.embedding_tracker import EmbeddingTaskTracker

                async def _on_complete() -> None:
                    get_request_wait_tracker().mark_semantic_done(msg.telemetry_id, msg.id)

                tracker = EmbeddingTaskTracker.get_instance()
                await tracker.register(
                    semantic_msg_id=msg.id,
                    total_count=2,
                    on_complete=_on_complete,
                    metadata={"uri": dir_uri},
                )
            await self._vectorize_directory(
                uri=dir_uri,
                context_type="memory",
                abstract=abstract,
                overview=overview,
                ctx=ctx,
                semantic_msg_id=msg.id,
            )
            logger.info(f"Vectorized abstract.md and overview.md for {dir_uri}")
        finally:
            if msg.lifecycle_lock_handle_id:
                await self._release_memory_lifecycle_lock(msg.lifecycle_lock_handle_id)

    async def _release_memory_lifecycle_lock(self, handle_id: str) -> None:
        """Release a lifecycle lock held by in-place memory refresh."""
        try:
            from openviking.storage.transaction import get_lock_manager

            handle = get_lock_manager().get_handle(handle_id)
            if handle:
                await get_lock_manager().release(handle)
        except Exception as e:
            logger.warning(f"[SemanticProcessor] Failed to release memory lifecycle lock: {e}")

    async def _sync_topdown_recursive(
        self,
        root_uri: str,
        target_uri: str,
        ctx: Optional[RequestContext] = None,
        file_change_status: Optional[Dict[str, bool]] = None,
        lifecycle_lock_handle_id: str = "",
    ) -> DiffResult:
        viking_fs = get_viking_fs()
        diff = DiffResult()
        lock_handle = None
        if lifecycle_lock_handle_id:
            from openviking.storage.transaction import get_lock_manager

            lock_handle = get_lock_manager().get_handle(lifecycle_lock_handle_id)

        async def list_children(dir_uri: str) -> Tuple[Dict[str, str], Dict[str, str]]:
            files: Dict[str, str] = {}
            dirs: Dict[str, str] = {}
            try:
                entries = await viking_fs.ls(dir_uri, show_all_hidden=True, ctx=ctx)
            except Exception as e:
                logger.error(f"[SyncDiff] Failed to list {dir_uri}: {e}")
                return files, dirs

            for entry in entries:
                name = entry.get("name", "")
                if not name or name in [".", ".."]:
                    continue
                if name.startswith(".") and name not in [".abstract.md", ".overview.md"]:
                    continue
                item_uri = VikingURI(dir_uri).join(name).uri
                if entry.get("isDir", False):
                    dirs[name] = item_uri
                else:
                    files[name] = item_uri
            return files, dirs

        async def sync_dir(root_dir: str, target_dir: str) -> None:
            root_files, root_dirs = await list_children(root_dir)
            target_files, target_dirs = await list_children(target_dir)

            try:
                await viking_fs._mv_vector_store_l0_l1(
                    root_dir,
                    target_dir,
                    ctx=ctx,
                    lock_handle=lock_handle,
                )
            except Exception as e:
                logger.error(
                    f"[SyncDiff] Failed to move L0/L1 index: {root_dir} -> {target_dir}, error={e}"
                )

            file_names = set(root_files.keys()) | set(target_files.keys())
            for name in sorted(file_names):
                root_file = root_files.get(name)
                target_file = target_files.get(name)

                if root_file and name in target_dirs:
                    target_conflict_dir = target_dirs[name]
                    try:
                        await viking_fs.rm(
                            target_conflict_dir,
                            recursive=True,
                            ctx=ctx,
                            lock_handle=lock_handle,
                        )
                        diff.deleted_dirs.append(target_conflict_dir)
                        target_dirs.pop(name, None)
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to delete directory for file conflict: {target_conflict_dir}, error={e}"
                        )
                    target_file = None

                if target_file and name in root_dirs and not root_file:
                    try:
                        await viking_fs.rm(target_file, ctx=ctx, lock_handle=lock_handle)
                        diff.deleted_files.append(target_file)
                        target_files.pop(name, None)
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to delete file for dir conflict: {target_file}, error={e}"
                        )
                    continue

                if target_file and not root_file:
                    try:
                        await viking_fs.rm(target_file, ctx=ctx, lock_handle=lock_handle)
                        diff.deleted_files.append(target_file)
                    except Exception as e:
                        logger.error(f"[SyncDiff] Failed to delete file: {target_file}, error={e}")
                    continue

                if root_file and target_file:
                    changed = False
                    if file_change_status and root_file in file_change_status:
                        changed = file_change_status[root_file]
                    else:
                        try:
                            changed = await self._check_file_content_changed(
                                root_file, target_file, ctx=ctx
                            )
                        except Exception as e:
                            logger.error(
                                f"[SyncDiff] Failed to compare file content for {root_file}: {e}, treating as unchanged"
                            )
                            changed = False
                    if changed:
                        diff.updated_files.append(root_file)
                        try:
                            await viking_fs.rm(target_file, ctx=ctx, lock_handle=lock_handle)
                        except Exception as e:
                            logger.error(
                                f"[SyncDiff] Failed to remove old file before update: {target_file}, error={e}"
                            )
                        try:
                            await viking_fs.mv(
                                root_file,
                                target_file,
                                ctx=ctx,
                                lock_handle=lock_handle,
                            )
                        except Exception as e:
                            logger.error(
                                f"[SyncDiff] Failed to move updated file: {root_file} -> {target_file}, error={e}"
                            )
                    continue

                if root_file and not target_file:
                    diff.added_files.append(root_file)
                    target_file_uri = VikingURI(target_dir).join(name).uri
                    try:
                        await viking_fs.mv(
                            root_file,
                            target_file_uri,
                            ctx=ctx,
                            lock_handle=lock_handle,
                        )
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to move added file: {root_file} -> {target_file_uri}, error={e}"
                        )

            dir_names = set(root_dirs.keys()) | set(target_dirs.keys())
            for name in sorted(dir_names):
                root_subdir = root_dirs.get(name)
                target_subdir = target_dirs.get(name)

                if root_subdir and name in target_files:
                    target_conflict_file = target_files[name]
                    try:
                        await viking_fs.rm(
                            target_conflict_file,
                            ctx=ctx,
                            lock_handle=lock_handle,
                        )
                        diff.deleted_files.append(target_conflict_file)
                        target_files.pop(name, None)
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to delete file for dir conflict: {target_conflict_file}, error={e}"
                        )
                    target_subdir = None

                if target_subdir and not root_subdir:
                    try:
                        await viking_fs.rm(
                            target_subdir,
                            recursive=True,
                            ctx=ctx,
                            lock_handle=lock_handle,
                        )
                        diff.deleted_dirs.append(target_subdir)
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to delete directory: {target_subdir}, error={e}"
                        )
                    continue

                if root_subdir and not target_subdir:
                    diff.added_dirs.append(root_subdir)
                    target_subdir_uri = VikingURI(target_dir).join(name).uri
                    try:
                        await viking_fs.mv(
                            root_subdir,
                            target_subdir_uri,
                            ctx=ctx,
                            lock_handle=lock_handle,
                        )
                    except Exception as e:
                        logger.error(
                            f"[SyncDiff] Failed to move added directory: {root_subdir} -> {target_subdir_uri}, error={e}"
                        )
                    continue

                if root_subdir and target_subdir:
                    await sync_dir(root_subdir, target_subdir)

        target_exists = await viking_fs.exists(target_uri, ctx=ctx)
        if not target_exists:
            parent_uri = VikingURI(target_uri).parent
            if parent_uri:
                await viking_fs.mkdir(parent_uri.uri, exist_ok=True, ctx=ctx)
            diff.added_dirs.append(root_uri)
            await viking_fs.mv(root_uri, target_uri, ctx=ctx, lock_handle=lock_handle)
            return diff

        await sync_dir(root_uri, target_uri)
        try:
            await viking_fs.delete_temp(root_uri, ctx=ctx)
        except Exception as e:
            logger.error(f"[SyncDiff] Failed to delete root directory {root_uri}: {e}")
        return diff

    async def _collect_children_abstracts(
        self, children_uris: List[str], ctx: Optional[RequestContext] = None
    ) -> List[Dict[str, str]]:
        """Collect .abstract.md from subdirectories."""
        viking_fs = get_viking_fs()
        results = []

        for child_uri in children_uris:
            abstract = await viking_fs.abstract(child_uri, ctx=ctx)
            dir_name = child_uri.split("/")[-1]
            results.append({"name": dir_name, "abstract": abstract})
        return results

    async def _generate_text_summary(
        self,
        file_path: str,
        file_name: str,
        llm_sem: asyncio.Semaphore,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, str]:
        """Generate summary for a single text file (code, documentation, or other text)."""
        viking_fs = get_viking_fs()
        vlm = get_openviking_config().vlm
        active_ctx = ctx or self._current_ctx

        content = await viking_fs.read_file(file_path, ctx=active_ctx)
        if isinstance(content, bytes):
            # Try to decode with error handling for text files
            try:
                content = content.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning(f"Failed to decode file as UTF-8, skipping: {file_path}")
                return {"name": file_name, "summary": ""}

        # Limit content length
        max_chars = get_openviking_config().semantic.max_file_content_chars
        if len(content) > max_chars:
            content = content[:max_chars] + "\n...(truncated)"

        # Generate summary
        if not vlm.is_available():
            logger.warning("VLM not available, using empty summary")
            return {"name": file_name, "summary": ""}

        from openviking.session.memory.utils.language import _detect_language_from_text

        fallback_language = (get_openviking_config().language_fallback or "en").strip() or "en"
        output_language = _detect_language_from_text(content, fallback_language)

        # Detect file type and select appropriate prompt
        file_type = self._detect_file_type(file_name)

        if file_type == FILE_TYPE_CODE:
            code_mode = get_openviking_config().code.code_summary_mode

            if code_mode in ("ast", "ast_llm") and len(content.splitlines()) >= 100:
                from openviking.parse.parsers.code.ast import extract_skeleton

                verbose = code_mode == "ast_llm"
                skeleton_text = extract_skeleton(file_name, content, verbose=verbose)
                if skeleton_text:
                    max_skeleton_chars = get_openviking_config().semantic.max_skeleton_chars
                    if len(skeleton_text) > max_skeleton_chars:
                        skeleton_text = skeleton_text[:max_skeleton_chars]
                    if code_mode == "ast":
                        return {"name": file_name, "summary": skeleton_text}
                    else:  # ast_llm
                        prompt = render_prompt(
                            "semantic.code_ast_summary",
                            {
                                "file_name": file_name,
                                "skeleton": skeleton_text,
                                "output_language": output_language,
                            },
                        )
                        async with llm_sem:
                            summary = await vlm.get_completion_async(prompt)
                        return {"name": file_name, "summary": summary.strip()}
                if skeleton_text is None:
                    logger.info("AST unsupported language, fallback to LLM: %s", file_path)
                else:
                    logger.info("AST empty skeleton, fallback to LLM: %s", file_path)

            # "llm" mode or fallback when skeleton is None/empty
            prompt = render_prompt(
                "semantic.code_summary",
                {"file_name": file_name, "content": content, "output_language": output_language},
            )
            async with llm_sem:
                summary = await vlm.get_completion_async(prompt)
            return {"name": file_name, "summary": summary.strip()}

        elif file_type == FILE_TYPE_DOCUMENTATION:
            prompt_id = "semantic.document_summary"
        else:
            prompt_id = "semantic.file_summary"

        prompt = render_prompt(
            prompt_id,
            {"file_name": file_name, "content": content, "output_language": output_language},
        )

        async with llm_sem:
            summary = await vlm.get_completion_async(prompt)
        return {"name": file_name, "summary": summary.strip()}

    async def _generate_single_file_summary(
        self,
        file_path: str,
        llm_sem: Optional[asyncio.Semaphore] = None,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, str]:
        """Generate summary for a single file.

        Args:
            file_path: File path

        Returns:
            {"name": file_name, "summary": summary_content}
        """
        file_name = file_path.split("/")[-1]
        llm_sem = llm_sem or asyncio.Semaphore(self.max_concurrent_llm)
        media_type = get_media_type(file_name, None)
        if media_type == "image":
            return await generate_image_summary(file_path, file_name, llm_sem, ctx=ctx)
        elif media_type == "audio":
            return await generate_audio_summary(file_path, file_name, llm_sem, ctx=ctx)
        elif media_type == "video":
            return await generate_video_summary(file_path, file_name, llm_sem, ctx=ctx)
        else:
            return await self._generate_text_summary(file_path, file_name, llm_sem, ctx=ctx)

    def _extract_abstract_from_overview(self, overview_content: str) -> str:
        """Extract abstract from overview.md."""
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

    def _enforce_size_limits(self, overview: str, abstract: str) -> Tuple[str, str]:
        """Enforce max size limits on overview and abstract."""
        semantic = get_openviking_config().semantic
        if len(overview) > semantic.overview_max_chars:
            overview = overview[: semantic.overview_max_chars]
        if len(abstract) > semantic.abstract_max_chars:
            abstract = abstract[: semantic.abstract_max_chars - 3] + "..."
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

                file_name = header_match.group(1).strip()
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

    async def _generate_overview(
        self,
        dir_uri: str,
        file_summaries: List[Dict[str, str]],
        children_abstracts: List[Dict[str, str]],
        llm_sem: Optional[asyncio.Semaphore] = None,
    ) -> str:
        """Generate directory's .overview.md (L1).

        For small directories, generates a single overview from all file summaries.
        For large directories that would exceed the prompt budget, splits file
        summaries into batches, generates a partial overview per batch, then
        merges the partials into a final overview.

        Args:
            dir_uri: Directory URI
            file_summaries: File summary list
            children_abstracts: Subdirectory summary list

        Returns:
            Overview content
        """

        config = get_openviking_config()
        vlm = config.vlm
        semantic = config.semantic

        if not vlm.is_available():
            logger.warning("VLM not available, using default overview")
            return f"# {dir_uri.split('/')[-1]}\n\nDirectory overview"

        from openviking.session.memory.utils.language import _detect_language_from_text

        fallback_language = (config.language_fallback or "en").strip() or "en"

        # Build file index mapping and summary string
        file_index_map = {}
        file_summaries_lines = []
        for idx, item in enumerate(file_summaries, 1):
            file_index_map[idx] = item["name"]
            file_summaries_lines.append(f"[{idx}] {item['name']}: {item['summary']}")
        file_summaries_str = "\n".join(file_summaries_lines) if file_summaries_lines else "None"

        output_language = _detect_language_from_text(file_summaries_str, fallback_language)

        # Build subdirectory summary string
        children_abstracts_str = (
            "\n".join(f"- {item['name']}/: {item['abstract']}" for item in children_abstracts)
            if children_abstracts
            else "None"
        )

        # Budget guard: check if prompt would be oversized
        estimated_size = len(file_summaries_str) + len(children_abstracts_str)
        over_budget = estimated_size > semantic.max_overview_prompt_chars
        many_files = len(file_summaries) > semantic.overview_batch_size

        if over_budget and many_files:
            # Many files, oversized prompt → batch and merge
            logger.info(
                f"Overview prompt for {dir_uri} exceeds budget "
                f"({estimated_size} chars, {len(file_summaries)} files). "
                f"Splitting into batches of {semantic.overview_batch_size}."
            )
            overview = await self._batched_generate_overview(
                dir_uri,
                file_summaries,
                children_abstracts,
                file_index_map,
                llm_sem=llm_sem,
                output_language=output_language,
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
                truncated_lines.append(f"[{idx}] {item['name']}: {summary}")
            file_summaries_str = "\n".join(truncated_lines)
            overview = await self._single_generate_overview(
                dir_uri,
                file_summaries_str,
                children_abstracts_str,
                file_index_map,
                output_language=output_language,
            )
        else:
            overview = await self._single_generate_overview(
                dir_uri,
                file_summaries_str,
                children_abstracts_str,
                file_index_map,
                output_language=output_language,
            )

        return overview

    async def _single_generate_overview(
        self,
        dir_uri: str,
        file_summaries_str: str,
        children_abstracts_str: str,
        file_index_map: Dict[int, str],
        output_language: str = "en",
    ) -> str:
        """Generate overview from a single prompt (small directories)."""
        import re

        vlm = get_openviking_config().vlm

        try:
            prompt = render_prompt(
                "semantic.overview_generation",
                {
                    "dir_name": dir_uri.split("/")[-1],
                    "file_summaries": file_summaries_str,
                    "children_abstracts": children_abstracts_str,
                    "output_language": output_language,
                },
            )

            overview = await vlm.get_completion_async(prompt)

            # Post-process: replace [number] with actual file name
            def replace_index(match):
                idx = int(match.group(1))
                return file_index_map.get(idx, match.group(0))

            overview = re.sub(r"\[(\d+)\]", replace_index, overview)

            return overview.strip()

        except Exception as e:
            logger.error(
                f"Failed to generate overview for {dir_uri}: {e}",
                exc_info=True,
            )
            return f"# {dir_uri.split('/')[-1]}\n\nDirectory overview"

    async def _batched_generate_overview(
        self,
        dir_uri: str,
        file_summaries: List[Dict[str, str]],
        children_abstracts: List[Dict[str, str]],
        file_index_map: Dict[int, str],
        llm_sem: Optional[asyncio.Semaphore] = None,
        output_language: str = "en",
    ) -> str:
        """Generate overview by batching file summaries and merging.

        Splits file summaries into batches, generates a partial overview per
        batch, then merges all partials into a final overview.
        """
        import re

        vlm = get_openviking_config().vlm
        semantic = get_openviking_config().semantic
        batch_size = semantic.overview_batch_size
        dir_name = dir_uri.split("/")[-1]

        # Split file summaries into batches
        batches = [
            file_summaries[i : i + batch_size] for i in range(0, len(file_summaries), batch_size)
        ]
        logger.info(f"Generating overview for {dir_uri} in {len(batches)} batches")

        # Build children abstracts string (used in first batch + merge)
        children_abstracts_str = (
            "\n".join(f"- {item['name']}/: {item['abstract']}" for item in children_abstracts)
            if children_abstracts
            else "None"
        )

        # Generate partial overview per batch concurrently using global file indices
        if llm_sem is None:
            llm_sem = asyncio.Semaphore(self.max_concurrent_llm)
        partial_overviews = [None] * len(batches)
        global_offset = 0
        batch_prompts: List[Tuple[int, str, Dict[int, str]]] = []

        for batch_idx, batch in enumerate(batches):
            # Build per-batch index map using global offsets
            batch_lines = []
            batch_index_map = {}
            for local_idx, item in enumerate(batch):
                global_idx = global_offset + local_idx + 1
                batch_index_map[global_idx] = item["name"]
                batch_lines.append(f"[{global_idx}] {item['name']}: {item['summary']}")
            batch_str = "\n".join(batch_lines)
            global_offset += len(batch)

            # Include children abstracts in the first batch
            children_str = children_abstracts_str if batch_idx == 0 else "None"

            prompt = render_prompt(
                "semantic.overview_generation",
                {
                    "dir_name": dir_name,
                    "file_summaries": batch_str,
                    "children_abstracts": children_str,
                    "output_language": output_language,
                },
            )
            batch_prompts.append((batch_idx, prompt, batch_index_map))

        def make_replacer(idx_map):
            def replacer(match):
                idx = int(match.group(1))
                return idx_map.get(idx, match.group(0))

            return replacer

        async def _run_batch(batch_idx: int, prompt: str, batch_index_map: Dict[int, str]) -> None:
            try:
                async with llm_sem:
                    partial = await vlm.get_completion_async(prompt)
                partial = re.sub(r"\[(\d+)\]", make_replacer(batch_index_map), partial)
                partial_overviews[batch_idx] = partial.strip()
            except Exception as e:
                logger.warning(
                    f"Failed to generate partial overview batch "
                    f"{batch_idx + 1}/{len(batches)} for {dir_uri}: {e}"
                )

        await asyncio.gather(*[_run_batch(*bp) for bp in batch_prompts])
        partial_overviews = [p for p in partial_overviews if p is not None]

        if not partial_overviews:
            return f"# {dir_name}\n\nDirectory overview"

        # If only one batch succeeded, use it directly
        if len(partial_overviews) == 1:
            return partial_overviews[0]

        # Merge partials into a final overview (include children for context)
        combined = "\n\n---\n\n".join(partial_overviews)
        try:
            prompt = render_prompt(
                "semantic.overview_generation",
                {
                    "dir_name": dir_name,
                    "file_summaries": combined,
                    "children_abstracts": children_abstracts_str,
                    "output_language": output_language,
                },
            )
            overview = await vlm.get_completion_async(prompt)
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
        semantic_msg_id: Optional[str] = None,
    ) -> None:
        """Create directory Context and enqueue to EmbeddingQueue."""

        if self._current_msg and getattr(self._current_msg, "skip_vectorization", False):
            logger.info(f"Skipping vectorization for {uri} (requested via SemanticMsg)")
            return

        from openviking.utils.embedding_utils import vectorize_directory_meta

        active_ctx = ctx or self._current_ctx
        await vectorize_directory_meta(
            uri=uri,
            abstract=abstract,
            overview=overview,
            context_type=context_type,
            ctx=active_ctx,
            semantic_msg_id=semantic_msg_id,
        )

    async def _vectorize_single_file(
        self,
        parent_uri: str,
        context_type: str,
        file_path: str,
        summary_dict: Dict[str, str],
        ctx: Optional[RequestContext] = None,
        semantic_msg_id: Optional[str] = None,
        use_summary: bool = False,
    ) -> None:
        """Vectorize a single file using its content or summary."""
        from openviking.utils.embedding_utils import vectorize_file

        active_ctx = ctx or self._current_ctx
        await vectorize_file(
            file_path=file_path,
            summary_dict=summary_dict,
            parent_uri=parent_uri,
            context_type=context_type,
            ctx=active_ctx,
            semantic_msg_id=semantic_msg_id,
            use_summary=use_summary,
        )
