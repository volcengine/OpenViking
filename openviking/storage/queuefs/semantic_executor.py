# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic DAG executor with event-driven lazy dispatch."""

import asyncio
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Mapping, Optional, Set
from weakref import WeakKeyDictionary

from openviking.parse.parsers.media import get_media_type
from openviking.server.identity import RequestContext
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import (
    bind_task_context,
    detach_task_context,
    get_task_context,
)
from openviking.storage.abstract_overview import (
    AbstractOverviewFormatError,
    AbstractOverviewWriteResult,
    freshness_metadata,
    parse_overview_file_summaries,
    write_abstract_overview,
)
from openviking.storage.context_update_plan import (
    SemanticAction,
    SemanticExecutionPlan,
    SemanticRefreshIntent,
)
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.semantic_planning import (
    SemanticNodePreparation,
    directory_index_arguments,
    prepare_node,
    resolve_semantic_execution,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import bind_ingestion_source_type, bind_telemetry, get_current_telemetry
from openviking.utils.content_hash import content_md5
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.log_correlation import log_correlation
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from openviking.storage.context_update_plan import SemanticPlan


@dataclass
class _ParentAggregationStats:
    started_at: float
    files: int = 0
    dirs: int = 0
    sources: Dict[str, str] = field(default_factory=dict)
    failed_files: Set[str] = field(default_factory=set)
    status: str = "completed"


@dataclass
class DirNode:
    """Directory node state for DAG execution."""

    uri: str
    children_dirs: List[str]
    file_paths: List[str]
    file_index: Dict[str, int]
    child_index: Dict[str, int]
    file_summaries: List[Optional[Dict[str, str]]]
    children_abstracts: List[Optional[Dict[str, str]]]
    pending: int
    pending_snapshot: int = 0
    sampled_children_dirs: Optional[Set[str]] = None
    sampled_file_paths: Optional[Set[str]] = None
    total_entries: Optional[int] = None
    missing_summary_entries: Optional[int] = None
    dispatched: bool = False
    overview_scheduled: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class SemanticTreeStats:
    total_nodes: int = 0
    pending_nodes: int = 0
    in_progress_nodes: int = 0
    done_nodes: int = 0
    failures: List[str] = field(default_factory=list)
    indexed_records: int = 0


@dataclass(frozen=True)
class SemanticTreeWork:
    """A scheduled unit of DAG work."""

    kind: str
    dir_uri: str
    parent_uri: Optional[str] = None
    file_path: Optional[str] = None


@dataclass(frozen=True)
class ScheduledTreeWork:
    executor: "SemanticTreeExecutor"
    work: SemanticTreeWork


class SemanticTreeScheduler:
    """Shared node executor for semantic DAG work in one event loop."""

    _idle_timeout = 0.05

    def __init__(self, max_workers: int):
        self._max_workers = max(1, max_workers)
        self._queue: asyncio.Queue[ScheduledTreeWork] = asyncio.Queue()
        self._workers: Set[asyncio.Task] = set()

    def configure(self, max_workers: int) -> None:
        self._max_workers = max(1, max_workers)
        self._ensure_workers()

    def submit(self, executor: "SemanticTreeExecutor", work: SemanticTreeWork) -> None:
        self._queue.put_nowait(ScheduledTreeWork(executor=executor, work=work))
        self._ensure_workers()

    def _ensure_workers(self) -> None:
        self._workers = {task for task in self._workers if not task.done()}
        target = min(self._max_workers, self._queue.qsize())
        while len(self._workers) < target:
            task = asyncio.create_task(self._worker())
            task.add_done_callback(self._workers.discard)
            self._workers.add(task)

    async def _worker(self) -> None:
        while True:
            try:
                item = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self._idle_timeout,
                )
            except asyncio.TimeoutError:
                if self._queue.empty():
                    return
                continue

            item.executor._start_scheduled_work()
            try:
                if not item.executor.closed:
                    await item.executor._run_work(item.work)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                item.executor.fail(exc)
            finally:
                item.executor._finish_scheduled_work()
                self._queue.task_done()


_node_schedulers: "WeakKeyDictionary[asyncio.AbstractEventLoop, SemanticTreeScheduler]" = (
    WeakKeyDictionary()
)


def get_semantic_tree_scheduler(max_workers: int) -> SemanticTreeScheduler:
    loop = asyncio.get_running_loop()
    scheduler = _node_schedulers.get(loop)
    if scheduler is None:
        scheduler = SemanticTreeScheduler(max_workers=max_workers)
        _node_schedulers[loop] = scheduler
    else:
        scheduler.configure(max_workers)
    return scheduler


class SemanticTreeExecutor:
    """Execute semantic generation with DAG-style, event-driven lazy dispatch."""

    _active_lock: ClassVar[threading.Lock] = threading.Lock()
    _active_executors: ClassVar[Set["SemanticTreeExecutor"]] = set()

    def __init__(
        self,
        processor: "SemanticProcessor",
        context_type: str,
        max_concurrent_llm: int,
        ctx: RequestContext,
        incremental_update: bool = False,
        target_uri: Optional[str] = None,
        recursive: bool = True,
        lock: Optional[Dict[str, Any]] = None,
        is_code_repo: bool = False,
        changes: Optional[Dict[str, List[str]]] = None,
        skip_vectorization: bool = False,
        ingest_options: IngestOptions | None = None,
        coalesce_key: str = "",
        coalesce_version: int = 0,
        source: Optional[Dict[str, str]] = None,
        generation_trigger: str = "semantic_refresh",
        aggregate_directory: bool = True,
        copy_source_uri: str = "",
        file_md5s: Optional[Dict[str, str]] = None,
        artifact_files: Optional[List[str]] = None,
        file_abstracts: Optional[Dict[str, str]] = None,
        file_vector_actions: Optional[Dict[str, str]] = None,
        file_ingest_options: Optional[Dict[str, IngestOptions | Dict[str, Any]]] = None,
        semantic_plan: Optional["SemanticPlan"] = None,
        execution_plan: Optional["SemanticExecutionPlan"] = None,
        telemetry_id: str | None = None,
        source_contents: Optional[Mapping[tuple[str, int], str | bytes]] = None,
        source_raw_contents: Optional[Mapping[tuple[str, int], str | bytes]] = None,
        materialize_content: bool = False,
    ):
        self._processor = processor
        self._ctx = ctx
        self._lock = lock
        self._execution_plan = execution_plan or resolve_semantic_execution(
            semantic_plan
            or SemanticRefreshIntent(
                uri=target_uri or "viking://resources",
                context_type=context_type,
                target_uri=target_uri or "",
                incremental_update=incremental_update,
                recursive=recursive,
                skip_vectorization=skip_vectorization,
                is_code_repo=is_code_repo,
                ingest_options=IngestOptions.from_value(ingest_options),
                changes=changes,
                source=source,
                generation_trigger=generation_trigger,
                aggregate_directory=aggregate_directory,
                copy_source_uri=copy_source_uri,
                coalesce_key=coalesce_key,
                coalesce_version=coalesce_version,
                file_md5s=file_md5s or {},
                artifact_files=tuple(artifact_files or ()),
                file_abstracts=file_abstracts or {},
                file_vector_actions=file_vector_actions or {},
                file_ingest_options={
                    uri: IngestOptions.from_value(options)
                    for uri, options in (file_ingest_options or {}).items()
                },
            ),
            generation_trigger=generation_trigger,
        )
        request = self._execution_plan.refresh
        self._context_type = self._execution_plan.context_type
        self._is_code_repo = request.is_code_repo
        self._source_type = (
            ("repository" if self._is_code_repo else "document")
            if context_type == "resource"
            else None
        )
        self._source_contents = dict(source_contents or {})
        self._source_raw_contents = source_raw_contents
        self._materialize_content = materialize_content
        self._semantic_resource_root = self._execution_plan.root_uri
        self._coalesce_key = request.coalesce_key
        self._coalesce_version = request.coalesce_version
        self._source = request.source
        self._generation_trigger = request.generation_trigger
        self._file_md5s = request.file_md5s
        self._task_context = get_task_context()
        self._telemetry = get_current_telemetry()
        self._telemetry_id = telemetry_id
        self._stale = False
        self._node_concurrency = max(1, max_concurrent_llm)
        self._llm_sem = asyncio.Semaphore(max_concurrent_llm)
        self._viking_fs = get_viking_fs()
        self._nodes: Dict[str, DirNode] = {}
        self._parent: Dict[str, Optional[str]] = {}
        self._root_uri: Optional[str] = None
        self._root_done: Optional[asyncio.Event] = None
        self._scheduler: Optional[SemanticTreeScheduler] = None
        self._active_scheduled_work = 0
        self._skill_node_tasks: Set[asyncio.Task] = set()
        self._active_work_idle = asyncio.Event()
        self._active_work_idle.set()
        self._closed = False
        self._failure: Optional[Exception] = None
        self._stats = SemanticTreeStats()
        self._preparation = SemanticNodePreparation(
            self._execution_plan,
            viking_fs=self._viking_fs,
            ctx=ctx,
            lock=lock,
            source_contents=source_contents,
            source_raw_contents=source_raw_contents,
            load_file_summaries=getattr(processor, "_load_transfer_file_summaries", None),
            parse_file_summaries=getattr(
                processor, "_parse_overview_md", parse_overview_file_summaries
            ),
        )
        self._file_change_status = self._preparation.file_change_status
        self._dir_change_status = self._preparation.dir_change_status
        self._parent_aggregation_stats: Dict[str, _ParentAggregationStats] = {}
        self._root_write_result = AbstractOverviewWriteResult(wrote=False)
        self._scheduled_vector_record_ids: set[str] = set()

    def _record_skill_failure(self, uri: str, error: Exception) -> None:
        if self._execution_plan.record_failures:
            self._stats.failures.append(f"{uri}: {error}")

    async def run(self, root_uri: str) -> None:
        """Run DAG execution starting from root_uri."""
        self._root_uri = root_uri
        self._root_done = asyncio.Event()
        if self._is_parent_aggregation():
            # Include waiting for the shared DAG scheduler's initial directory work.
            self._parent_aggregation_stats[root_uri] = _ParentAggregationStats(
                started_at=perf_counter()
            )
        self._scheduler = get_semantic_tree_scheduler(self._node_concurrency)
        self._preparation._root_uri = root_uri
        self._semantic_resource_root = (
            self._execution_plan.root_uri if self._execution_plan.fixed_actions else root_uri
        )
        entry = self._execution_plan.bindings.get(root_uri.rstrip("/"))
        if (
            entry is not None
            and entry.semantic_action is SemanticAction.REUSE
            and root_uri not in self._execution_plan.active_directories
            and not any(slot.action.value != "none" for slot in entry.index_slots)
        ):
            self._stats.total_nodes = self._stats.done_nodes = 1
            return
        if entry is not None and entry.kind == "file":
            self._stats.total_nodes = self._stats.in_progress_nodes = 1
            with bind_ingestion_source_type(self._source_type):
                await self._file_summary_task(root_uri, root_uri)
            self._root_write_result = AbstractOverviewWriteResult(
                wrote=True, abstract_body_changed=True
            )
            return

        try:
            self._register_active()
            self._schedule_dir(root_uri, parent_uri=None)
            await self._root_done.wait()
            if self._failure:
                raise self._failure
        except BaseException as exc:
            for aggregation in self._parent_aggregation_stats.values():
                aggregation.status = (
                    "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                )
            self._closed = True
            if self._context_type == "skill":
                for task in self._skill_node_tasks:
                    task.cancel()
                await run_to_completion(
                    lambda: asyncio.gather(*self._skill_node_tasks, return_exceptions=True)
                )
            await self._active_work_idle.wait()
            raise
        finally:
            self._closed = True
            try:
                if self._context_type == "skill":
                    await run_to_completion(self._active_work_idle.wait)
                else:
                    await self._active_work_idle.wait()
            finally:
                self._unregister_active()
                self._log_parent_aggregations()

    def _is_parent_aggregation(self) -> bool:
        return self._execution_plan.observe_parent_aggregation

    def _record_summary_source(self, dir_uri: str, file_path: str, source: str) -> None:
        if aggregation := self._parent_aggregation_stats.get(dir_uri):
            # Each file has one source even when generation itself retries.
            aggregation.sources[file_path] = source

    def _record_summary_failure(self, dir_uri: str, file_path: str) -> None:
        if aggregation := self._parent_aggregation_stats.get(dir_uri):
            aggregation.failed_files.add(file_path)

    def _log_parent_aggregations(self) -> None:
        for dir_uri, aggregation in self._parent_aggregation_stats.items():
            counts = Counter(aggregation.sources.values())
            status = aggregation.status
            if status == "completed" and self._stale:
                status = "stale"
            logger.info(
                "[ParentAggregation] %s uri=%s trigger=%s files=%d dirs=%d "
                "abstract=%d overview=%d generated=%d failed_files=%d total_ms=%.3f status=%s",
                log_correlation(
                    task_id=self._task_context.task_id if self._task_context else "",
                    telemetry_id=self._telemetry_id or "",
                ),
                dir_uri,
                self._generation_trigger,
                aggregation.files,
                aggregation.dirs,
                counts["abstract"],
                counts["overview"],
                counts["generated"],
                len(aggregation.failed_files),
                (perf_counter() - aggregation.started_at) * 1000.0,
                status,
            )
        self._parent_aggregation_stats.clear()

    def _schedule_work(self, work: SemanticTreeWork) -> None:
        if self._closed:
            return
        if self._scheduler is None:
            self._scheduler = get_semantic_tree_scheduler(self._node_concurrency)
        self._scheduler.submit(self, work)

    def _start_scheduled_work(self) -> None:
        self._active_scheduled_work += 1
        self._active_work_idle.clear()

    def _finish_scheduled_work(self) -> None:
        self._active_scheduled_work = max(0, self._active_scheduled_work - 1)
        if self._active_scheduled_work == 0:
            self._active_work_idle.set()

    def _schedule_dir(self, dir_uri: str, parent_uri: Optional[str]) -> None:
        if self._closed:
            return
        self._stats.total_nodes += 1
        self._stats.pending_nodes += 1
        self._schedule_work(SemanticTreeWork(kind="dir", dir_uri=dir_uri, parent_uri=parent_uri))

    def _schedule_file(self, parent_uri: str, file_path: str) -> None:
        if self._closed:
            return
        self._stats.total_nodes += 1
        self._stats.pending_nodes += 1
        self._schedule_work(SemanticTreeWork(kind="file", dir_uri=parent_uri, file_path=file_path))

    def _mark_node_started(self) -> None:
        self._stats.pending_nodes = max(0, self._stats.pending_nodes - 1)
        self._stats.in_progress_nodes += 1

    def _mark_node_waiting(self) -> None:
        self._stats.in_progress_nodes = max(0, self._stats.in_progress_nodes - 1)
        self._stats.pending_nodes += 1

    def _mark_node_done(self) -> None:
        self._stats.done_nodes += 1
        self._stats.in_progress_nodes = max(0, self._stats.in_progress_nodes - 1)

    def _release_dir_node(self, dir_uri: str) -> None:
        self._nodes.pop(dir_uri, None)
        self._parent.pop(dir_uri, None)
        self._preparation.directories.pop(dir_uri, None)

    @property
    def closed(self) -> bool:
        return self._closed

    def fail(self, exc: Exception) -> None:
        if self._failure is None:
            self._failure = exc
        self._closed = True
        if self._root_done:
            self._root_done.set()

    def _register_active(self) -> None:
        with self._active_lock:
            self._active_executors.add(self)

    def _unregister_active(self) -> None:
        with self._active_lock:
            self._active_executors.discard(self)

    @classmethod
    def get_active_stats(cls) -> SemanticTreeStats:
        stats = SemanticTreeStats()
        with cls._active_lock:
            executors = list(cls._active_executors)
        for executor in executors:
            current = executor.get_stats()
            stats.total_nodes += current.total_nodes
            stats.pending_nodes += current.pending_nodes
            stats.in_progress_nodes += current.in_progress_nodes
            stats.done_nodes += current.done_nodes
        return stats

    async def _run_work(self, work: SemanticTreeWork) -> None:
        if self._context_type == "skill":
            # Shared scheduler workers must not be cancelled with one package.
            # Each Skill node has its own cancellable task and ownership context.
            task = asyncio.create_task(self._run_work_with_context(work))
            self._skill_node_tasks.add(task)
            try:
                await task
            except asyncio.CancelledError:
                if not self._closed:
                    raise
            finally:
                self._skill_node_tasks.discard(task)
        else:
            await self._run_work_with_context(work)

    async def _run_work_with_context(self, work: SemanticTreeWork) -> None:
        task_context = (
            bind_task_context(
                self._task_context.task_id,
                self._task_context.account_id,
                self._task_context.user_id,
                enqueued_work=self._task_context.enqueued_work,
                work_id=self._task_context.work_id,
                task_type=self._task_context.task_type,
                mode=self._task_context.mode,
            )
            if self._task_context is not None
            else detach_task_context()
        )
        with (
            bind_telemetry(self._telemetry),
            bind_ingestion_source_type(self._source_type),
            task_context,
        ):
            await self._run_work_bound(work)

    async def _await_write(self, operation):
        if self._context_type == "skill":
            return await run_to_completion(lambda: operation)
        return await operation

    async def _run_work_bound(self, work: SemanticTreeWork) -> None:
        self._mark_node_started()

        if work.kind == "dir":
            terminal = False
            try:
                terminal = await self._dispatch_dir(work.dir_uri, work.parent_uri)
            finally:
                if terminal:
                    self._mark_node_done()
                else:
                    self._mark_node_waiting()
            return

        if work.kind == "file":
            if work.file_path is None:
                self._mark_node_done()
                return
            await self._file_summary_task(work.dir_uri, work.file_path)
            return

        if work.kind == "overview":
            await self._overview_task(work.dir_uri)
            return

        self._mark_node_done()
        logger.warning("Unknown semantic DAG work kind: %s", work.kind)

    async def _dispatch_dir(self, dir_uri: str, parent_uri: Optional[str]) -> bool:
        """Lazy-dispatch tasks for a directory when it is triggered."""
        if dir_uri in self._nodes:
            return True

        self._parent[dir_uri] = parent_uri
        aggregation = self._parent_aggregation_stats.get(dir_uri)
        if aggregation is None and self._is_parent_aggregation():
            aggregation = _ParentAggregationStats(started_at=perf_counter())
            self._parent_aggregation_stats[dir_uri] = aggregation

        try:
            prepared = await prepare_node(self._preparation, dir_uri, phase="directory")
            children_dirs, file_paths = prepared.children_dirs, prepared.file_paths
            if aggregation is not None:
                aggregation.files, aggregation.dirs = len(file_paths), len(children_dirs)
                if prepared.binding.semantic_action is SemanticAction.REUSE:
                    aggregation.status = "skipped"
            pending = len(prepared.required_files) + len(prepared.required_directories)
            node = DirNode(
                uri=dir_uri,
                children_dirs=children_dirs,
                file_paths=file_paths,
                file_index={path: idx for idx, path in enumerate(file_paths)},
                child_index={path: idx for idx, path in enumerate(children_dirs)},
                file_summaries=prepared.file_summaries,
                children_abstracts=prepared.children_abstracts,
                pending=pending,
                pending_snapshot=prepared.pending_snapshot,
                sampled_children_dirs=prepared.sampled_directories,
                sampled_file_paths=prepared.sampled_files,
                total_entries=prepared.total_entries,
                missing_summary_entries=prepared.missing_summary_entries,
                dispatched=True,
            )
            self._nodes[dir_uri] = node
            for file_path in sorted(prepared.required_files):
                self._schedule_file(dir_uri, file_path)
            for child_uri in sorted(prepared.required_directories):
                self._schedule_dir(child_uri, dir_uri)
            if pending == 0:
                self._schedule_overview(dir_uri)
            return False
        except LockAcquisitionError:
            # A content-write message can be dequeued before the writer releases
            # its exact-path lease. Let SemanticProcessor re-enqueue this
            # recoverable conflict instead of acknowledging unfinished file work.
            if aggregation is not None:
                aggregation.status = "failed"
            raise
        except Exception as e:
            if aggregation is not None:
                aggregation.status = "failed"
            logger.error(f"Failed to dispatch directory {dir_uri}: {e}", exc_info=True)
            self._record_skill_failure(dir_uri, e)
            if self._execution_plan.fail_dispatch:
                raise
            if parent_uri:
                await self._on_child_done(parent_uri, dir_uri, "")
            elif self._root_done:
                self._root_done.set()
            return True

    async def _file_summary_task(self, parent_uri: str, file_path: str) -> None:
        """Generate file summary and notify parent completion."""

        file_name = file_path.split("/")[-1]
        semantic_succeeded = True
        file_content = None
        prepared = None
        try:
            prepared = await prepare_node(
                self._preparation, file_path, phase="file", parent_uri=parent_uri
            )
            summary_dict = prepared.summary
            file_content = prepared.file_content
            self._record_summary_source(parent_uri, file_path, prepared.summary_source)
            if summary_dict is None:
                summary_kwargs: Dict[str, Any] = {
                    "llm_sem": self._llm_sem,
                    "ctx": self._ctx,
                }
                if file_content is not None:
                    summary_kwargs["file_content"] = file_content
                self._record_summary_source(parent_uri, file_path, "generated")
                summary_dict = await self._processor._generate_single_file_summary(
                    file_path, **summary_kwargs
                )
        except (AbstractOverviewFormatError, FileNotFoundError, ValueError):
            self._record_summary_failure(parent_uri, file_path)
            # A generated sidecar that opted into OKF must never be treated as
            # an empty file summary; doing so would silently feed metadata or
            # corrupted YAML into a later regeneration.
            raise
        except Exception as e:
            self._record_summary_failure(parent_uri, file_path)
            logger.warning(f"Failed to generate summary for {file_path}: {e}")
            self._record_skill_failure(file_path, e)
            summary_dict = {"name": file_name, "summary": ""}
            semantic_succeeded = False
        finally:
            self._stats.done_nodes += 1
            self._stats.in_progress_nodes = max(0, self._stats.in_progress_nodes - 1)

        prepared = self._preparation.files.pop(file_path)
        if self._closed:
            return
        slot = prepared.binding.slot(2)
        need_vectorize = prepared.vectorize
        if not semantic_succeeded and not self._execution_plan.index_failed_file:
            need_vectorize = False
        if need_vectorize:
            use_summary = self._is_code_repo and bool(summary_dict.get("summary"))
            try:
                vectorize_kwargs: Dict[str, Any] = {}
                if file_content is not None:
                    vectorize_kwargs["file_content"] = file_content
                if self._telemetry_id is not None:
                    vectorize_kwargs["telemetry_id"] = self._telemetry_id
                if self._materialize_content:
                    vectorize_kwargs["materialize_content"] = True
                manifest_md5 = self._file_md5s.get(file_path.rstrip("/")) or None
                if file_content is not None and not manifest_md5:
                    file_md5: str | None = content_md5(file_content)
                else:
                    file_md5 = manifest_md5
                vectorize_kwargs["action"] = prepared.index_action
                if prepared.scalar_override is not None:
                    vectorize_kwargs["scalar_override"] = prepared.scalar_override
                if prepared.field_patch is not None:
                    vectorize_kwargs["field_patch"] = prepared.field_patch
                enqueued = await self._processor._vectorize_single_file(
                    parent_uri=parent_uri,
                    context_type=self._context_type,
                    file_path=file_path,
                    summary_dict=summary_dict,
                    ctx=self._ctx,
                    use_summary=use_summary,
                    ingest_options=prepared.ingest_options,
                    file_md5=file_md5,
                    **vectorize_kwargs,
                )
                if enqueued and slot is not None:
                    self._scheduled_vector_record_ids.add(slot.record_id)
                    self._stats.indexed_records += 1
            except Exception as e:
                logger.error(
                    "Failed to schedule vectorization for %s: %s",
                    file_path,
                    e,
                    exc_info=True,
                )
                if not self._execution_plan.tolerate_index_failure:
                    raise
                self._record_skill_failure(file_path, e)
        await self._on_file_done(
            parent_uri,
            file_path,
            {
                "name": str(summary_dict.get("name") or file_name),
                "summary": str(summary_dict.get("summary") or ""),
            },
        )

    async def _on_file_done(
        self, parent_uri: str, file_path: str, summary_dict: Dict[str, str]
    ) -> None:
        node = self._nodes.get(parent_uri)
        if not node:
            return

        async with node.lock:
            idx = node.file_index.get(file_path)
            if idx is not None and (
                node.sampled_file_paths is None or file_path in node.sampled_file_paths
            ):
                node.file_summaries[idx] = summary_dict
            node.pending -= 1
            if node.pending == 0 and not node.overview_scheduled:
                self._schedule_overview(parent_uri)

    async def _on_child_done(self, parent_uri: str, child_uri: str, abstract: str) -> None:
        node = self._nodes.get(parent_uri)
        if not node:
            return

        child_name = child_uri.split("/")[-1]
        async with node.lock:
            idx = node.child_index.get(child_uri)
            if idx is not None and (
                node.sampled_children_dirs is None or child_uri in node.sampled_children_dirs
            ):
                node.children_abstracts[idx] = {"name": child_name, "abstract": abstract}
            node.pending -= 1
            if node.pending == 0 and not node.overview_scheduled:
                self._schedule_overview(parent_uri)

    def _schedule_overview(self, dir_uri: str) -> None:
        node = self._nodes.get(dir_uri)
        if not node or node.overview_scheduled:
            return
        node.overview_scheduled = True
        self._schedule_work(SemanticTreeWork(kind="overview", dir_uri=dir_uri))

    def _select_direct_media_overview(
        self,
        node: DirNode,
        file_summaries: List[Dict[str, str]],
    ) -> Optional[str]:
        if len(node.file_paths) != 1 or node.children_dirs or len(file_summaries) != 1:
            return None

        file_path = node.file_paths[0]
        filename = file_path.rsplit("/", 1)[-1]
        if get_media_type(file_path, None) not in {"audio", "video"}:
            return None

        summary = str(file_summaries[0].get("summary") or "").strip()
        if not summary.startswith("# "):
            return None

        lines = summary.splitlines()
        brief_start = next((idx for idx in range(1, len(lines)) if lines[idx].strip()), None)
        if brief_start is None or lines[brief_start].lstrip().startswith("#"):
            return None

        brief_end = brief_start
        while brief_end < len(lines) and lines[brief_end].strip():
            if lines[brief_end].lstrip().startswith("#"):
                return None
            brief_end += 1

        filename_heading = re.compile(rf"^###\s+{re.escape(filename)}\s*$", re.MULTILINE)
        if not any(filename_heading.fullmatch(line) for line in lines[brief_end:]):
            return None
        return summary

    @property
    def stale(self) -> bool:
        return self._stale

    async def _is_stale(self) -> bool:
        if not self._coalesce_key or self._coalesce_version <= 0:
            return False
        from openviking.storage.queuefs.semantic_queue import is_semantic_coalesce_stale

        return await is_semantic_coalesce_stale(
            self._coalesce_key, self._coalesce_version, agfs=self._viking_fs._async_agfs
        )

    async def _write_directory_semantics(
        self,
        dir_uri: str,
        overview: str,
        abstract: str,
        *,
        total_entries: int,
        sampled_entries: int,
        consume_pending: int,
        missing_summary_entries: Optional[int] = None,
    ) -> AbstractOverviewWriteResult:
        metadata: Dict[str, Any] = {
            "generated_by": {
                "component": "SemanticProcessor",
                "trigger": self._generation_trigger,
            },
            "freshness": freshness_metadata(
                total_entries,
                sampled_entries,
                missing_summary_entries=missing_summary_entries,
            ),
        }
        source_root = self._semantic_resource_root or self._root_uri
        if dir_uri == source_root and self._source:
            metadata["source"] = self._source
        wrote = await self._await_write(
            write_abstract_overview(
                viking_fs=self._viking_fs,
                dir_uri=dir_uri,
                overview=overview,
                abstract=abstract,
                ctx=self._ctx,
                is_stale=self._is_stale,
                metadata=metadata,
                consume_pending=consume_pending,
                lock=self._lock,
                log_prefix="[SemanticTree]",
                existing_raw=(
                    {
                        level: raw
                        for level in (0, 1)
                        if (raw := self._source_raw_contents.get((dir_uri, level))) is not None
                    }
                    if self._source_raw_contents is not None
                    else None
                ),
            )
        )
        if not wrote.wrote:
            self._stale = True
        return wrote

    async def _overview_task(self, dir_uri: str) -> None:
        node = self._nodes.get(dir_uri)
        if not node:
            return
        need_vectorize = False
        children_changed = True
        should_write = False
        write_result = AbstractOverviewWriteResult(wrote=False)
        abstract: str | None = None
        overview: str | None = None
        total_entries = (
            node.total_entries
            if node.total_entries is not None
            else len(node.file_paths) + len(node.children_dirs)
        )
        sampled_inputs: List[tuple[str, Dict[str, str]]] = []
        sampled_entries = 0
        try:
            prepared = await prepare_node(self._preparation, dir_uri, phase="overview")
            need_vectorize = prepared.vectorize
            children_changed = prepared.input_changed
            should_write = prepared.write_semantics
            abstract, overview = prepared.abstract, prepared.overview
            if prepared.definition_source:
                overview, abstract = await self._processor._skill_root_semantics(
                    dir_uri,
                    ctx=self._ctx,
                    regenerate=prepared.regenerate_definition,
                    lock=self._lock,
                    skill_content=self._source_contents.get((f"{dir_uri}/SKILL.md", 2)),
                )
            if should_write and (overview is None or abstract is None):
                async with node.lock:
                    file_summaries, children_abstracts = await self._preparation.aggregation_inputs(
                        dir_uri
                    )
                # Freshness describes the directory itself, including direct
                # entries whose summaries failed. Those entries remain visible
                # as unsampled coverage instead of disappearing from the count.
                # Sampling happened in _dispatch_dir, before summary work was
                # scheduled. Transfer refreshes similarly prepare only target
                # entries whose existing L2 summaries are ready.
                sampled_inputs = sorted(
                    [("file", item) for item in file_summaries]
                    + [("directory", item) for item in children_abstracts],
                    key=lambda tagged: str(tagged[1].get("name") or ""),
                )
                sampled_entries = len(sampled_inputs)
                if prepared.use_direct_media:
                    overview = self._select_direct_media_overview(node, file_summaries)
                if overview is None:
                    async with self._llm_sem:
                        overview = await self._processor._generate_overview(
                            dir_uri,
                            file_summaries,
                            children_abstracts,
                            total_files=len(node.file_paths),
                            total_children=len(node.children_dirs),
                            ctx=self._ctx,
                        )
                overview, abstract = self._processor._normalize_overview_generation(overview)

            if self._closed:
                return

            # Persist sidecars before publishing their directory vectors.
            if should_write:
                assert overview is not None and abstract is not None
                try:
                    write_result = await self._write_directory_semantics(
                        dir_uri,
                        overview,
                        abstract,
                        total_entries=total_entries,
                        sampled_entries=sampled_entries,
                        consume_pending=node.pending_snapshot,
                        missing_summary_entries=node.missing_summary_entries,
                    )
                    if dir_uri == self._root_uri:
                        self._root_write_result = write_result
                    if not write_result.wrote:
                        need_vectorize = False
                except AbstractOverviewFormatError:
                    raise
                except Exception as exc:
                    self._record_skill_failure(dir_uri, exc)
                    if self._execution_plan.fail_directory_write:
                        self.fail(exc)
                        return
                    raise

        except AbstractOverviewFormatError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate overview for {dir_uri}: {e}", exc_info=True)
            if aggregation := self._parent_aggregation_stats.get(dir_uri):
                aggregation.status = "failed"
            self._record_skill_failure(dir_uri, e)
        else:
            slots = {slot.level: slot for slot in prepared.binding.index_slots}
            enqueued_levels: set[int] = set()
            if need_vectorize:
                assert overview is not None and abstract is not None
                try:
                    directory_vector_kwargs = directory_index_arguments(
                        self._execution_plan,
                        prepared,
                        overview,
                        abstract,
                    )
                    include_abstract = directory_vector_kwargs.get("include_abstract", True)
                    include_overview = directory_vector_kwargs.get("include_overview", True)
                    if prepared.definition_source:
                        directory_vector_kwargs["skill_source_path"] = (self._source or {}).get(
                            "path", ""
                        )
                    if self._telemetry_id is not None:
                        directory_vector_kwargs["telemetry_id"] = self._telemetry_id
                    if include_abstract or include_overview:
                        enqueued_levels = await self._processor._vectorize_directory(
                            dir_uri,
                            context_type=self._context_type,
                            abstract=abstract,
                            overview=overview,
                            ctx=self._ctx,
                            ingest_options=prepared.ingest_options,
                            **directory_vector_kwargs,
                        )
                    else:
                        enqueued_levels = set()
                except Exception as e:
                    logger.error(
                        "Failed to schedule vectorization for %s: %s",
                        dir_uri,
                        e,
                        exc_info=True,
                    )
                    if not self._execution_plan.tolerate_index_failure:
                        raise
                    self._record_skill_failure(dir_uri, e)

            for level, slot in sorted(slots.items()):
                if (
                    level in enqueued_levels
                    or not slot.fallback_to_patch
                    or slot.field_patch is None
                ):
                    continue
                enqueued = await self._processor._update_vector_fields(
                    record_id=slot.record_id,
                    uri=dir_uri,
                    level=level,
                    field_patch=slot.field_patch.with_seed(
                        {
                            **dict(slot.existing_fields or {}),
                            "uri": dir_uri,
                            "level": level,
                            "account_id": self._ctx.account_id,
                        }
                    ),
                    ctx=self._ctx,
                    **(
                        {"telemetry_id": self._telemetry_id}
                        if self._telemetry_id is not None
                        else {}
                    ),
                )
                if enqueued:
                    enqueued_levels.add(level)

            if slots:
                self._scheduled_vector_record_ids.update(
                    slot.record_id for slot in slots.values() if slot.level in enqueued_levels
                )
                self._stats.indexed_records += len(enqueued_levels)
        finally:
            self._stats.done_nodes += 1
            self._stats.in_progress_nodes = max(0, self._stats.in_progress_nodes - 1)

        self._dir_change_status[dir_uri] = children_changed

        parent_uri = self._parent.get(dir_uri)
        if parent_uri is None:
            self._release_dir_node(dir_uri)
            if self._root_done:
                self._root_done.set()
            return

        await self._on_child_done(parent_uri, dir_uri, abstract or "")
        self._release_dir_node(dir_uri)

    def get_stats(self) -> SemanticTreeStats:
        return SemanticTreeStats(
            total_nodes=self._stats.total_nodes,
            pending_nodes=self._stats.pending_nodes,
            in_progress_nodes=self._stats.in_progress_nodes,
            done_nodes=self._stats.done_nodes,
            failures=list(self._stats.failures),
            indexed_records=self._stats.indexed_records,
        )

    @property
    def root_write_result(self) -> AbstractOverviewWriteResult:
        """Visible-body changes produced for the executor root."""

        return self._root_write_result

    @property
    def scheduled_vector_record_ids(self) -> frozenset[str]:
        return frozenset(self._scheduled_vector_record_ids)


if False:  # pragma: no cover - for type checkers only
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor
