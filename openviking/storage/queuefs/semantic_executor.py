# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic DAG executor with event-driven lazy dispatch."""

import asyncio
import re
import threading
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Set
from weakref import WeakKeyDictionary

from openviking.core.namespace import classify_uri
from openviking.parse.parsers.media import get_media_type
from openviking.server.identity import RequestContext
from openviking.service.task_processing_time import pause_task_processing, processing_owner
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import (
    bind_task_context,
    detach_task_context,
    get_task_context,
)
from openviking.storage.abstract_overview import (
    AbstractOverviewFormatError,
    AbstractOverviewWriteResult,
    body_for_preview,
    deterministic_sample,
    freshness_metadata,
    read_abstract_overview_pending_snapshot,
    write_abstract_overview,
)
from openviking.storage.context_update_plan import FileVectorSource, SemanticAction
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.index_action import FieldPatch
from openviking.storage.viking_fs import LS_ALL_NODES, get_viking_fs
from openviking.telemetry import bind_telemetry, get_current_telemetry
from openviking.utils.content_hash import content_md5
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.utils import VikingURI
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from openviking.storage.context_update_plan import SemanticPlan, SemanticTreeEntry

# Session-internal files that should never be summarized by the semantic pipeline.
# These are canonical archives (e.g. session transcripts) whose content provides
# no additional retrieval value and would only waste tokens and add latency.
_SKIP_FILENAMES = frozenset({"messages.jsonl"})


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
    transfer_inputs_ready: bool = True
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
        semantic_plan: Optional["SemanticPlan"] = None,
        telemetry_id: str | None = None,
    ):
        self._processor = processor
        self._context_type = context_type
        self._ctx = ctx
        self._incremental_update = incremental_update
        self._target_uri = target_uri
        self._recursive = recursive
        self._lock = lock
        self._is_code_repo = bool(
            is_code_repo
            or (
                semantic_plan is not None
                and semantic_plan.file_vector_source is FileVectorSource.SUMMARY_WHEN_AVAILABLE
            )
        )
        self._changes_provided = changes is not None
        self._changes = changes or {}
        self._semantic_plan = semantic_plan
        self._semantic_resource_root = (
            semantic_plan.root_uri.rstrip("/") if semantic_plan is not None else None
        )
        self._skip_vectorization = (
            not semantic_plan.vectorize if semantic_plan is not None else skip_vectorization
        )
        self._ingest_options = IngestOptions.from_value(
            semantic_plan.ingest_options if semantic_plan is not None else ingest_options
        )
        self._coalesce_key = coalesce_key
        self._coalesce_version = coalesce_version
        self._source = dict(source) if source else None
        self._generation_trigger = generation_trigger
        self._aggregate_directory = aggregate_directory
        self._copy_source_uri = copy_source_uri
        self._task_context = get_task_context()
        self._processing_index = None
        self._telemetry = get_current_telemetry()
        self._telemetry_id = telemetry_id
        self._stale = False
        self._changed_paths = {
            path for key in ("added", "modified", "deleted") for path in self._changes.get(key, [])
        }
        self._tree_changed_paths = {
            path.rstrip("/") for key in ("added", "deleted") for path in self._changes.get(key, [])
        }
        # Per-file md5 (target-URI keyed) supplied by local incremental apply, so
        # re-vectorization records the fresh fingerprint instead of leaving it stale.
        self._file_md5s = dict(file_md5s or {})
        self._artifact_files = [path.strip("/") for path in (artifact_files or []) if path]
        self._file_abstracts = dict(file_abstracts or {})
        self._plan_entries_by_uri: Dict[str, "SemanticTreeEntry"] = {}
        self._plan_children: Dict[str, tuple[List[str], List[str]]] = {}
        self._plan_active_dirs: Set[str] = set()
        if semantic_plan is not None:
            self._initialize_semantic_plan(semantic_plan)
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
        self._file_change_status: Dict[str, bool] = {}
        self._dir_change_status: Dict[str, bool] = {}
        self._overview_cache: Dict[str, Dict[str, str]] = {}
        self._overview_cache_lock = asyncio.Lock()
        self._root_write_result = AbstractOverviewWriteResult(wrote=False)
        self._scheduled_vector_record_ids: set[str] = set()

    @staticmethod
    def _entry_record_abstract(entry: "SemanticTreeEntry", level: int) -> str:
        slot = entry.slot(level)
        return slot.abstract if slot is not None else ""

    def _initialize_semantic_plan(self, plan: "SemanticPlan") -> None:
        from openviking.storage.context_update_plan import SemanticAction

        root = plan.root_uri.rstrip("/")
        current_entries = list(plan.tree.entries)
        children: Dict[str, tuple[List[str], List[str]]] = {}
        for entry in current_entries:
            uri = root if not entry.relative_path else f"{root}/{entry.relative_path}"
            self._plan_entries_by_uri[uri] = entry
            if not entry.relative_path:
                continue
            parent_rel = entry.relative_path.rsplit("/", 1)[0] if "/" in entry.relative_path else ""
            parent_uri = root if not parent_rel else f"{root}/{parent_rel}"
            dirs, files = children.setdefault(parent_uri, ([], []))
            (dirs if entry.kind == "directory" else files).append(uri)

        changed_uris: List[str] = []
        for entry in plan.tree.entries:
            if entry.semantic_action == SemanticAction.REUSE:
                continue
            uri = root if not entry.relative_path else f"{root}/{entry.relative_path}"
            changed_uris.append(uri)
            if entry.kind == "directory" and entry.semantic_action == SemanticAction.AGGREGATE:
                self._plan_active_dirs.add(uri)
            if entry.relative_path:
                self._plan_active_dirs.add(uri.rsplit("/", 1)[0])
        for active_uri in list(self._plan_active_dirs):
            current = active_uri
            while current != root and current.startswith(root + "/"):
                parent = current.rsplit("/", 1)[0]
                if parent not in self._plan_entries_by_uri:
                    break
                self._plan_active_dirs.add(parent)
                current = parent

        self._plan_children = {
            uri: (sorted(dirs), sorted(files)) for uri, (dirs, files) in children.items()
        }
        self._incremental_update = True
        self._target_uri = root
        self._recursive = True
        self._changes_provided = True
        self._changed_paths = set(changed_uris)
        self._tree_changed_paths = {
            root if not entry.relative_path else f"{root}/{entry.relative_path}"
            for entry in plan.tree.entries
            if entry.membership_changed
        }
        self._file_md5s = {
            uri: str(entry.md5)
            for uri, entry in self._plan_entries_by_uri.items()
            if entry.kind == "file" and entry.md5
        }
        self._file_abstracts = {
            uri: abstract
            for uri, entry in self._plan_entries_by_uri.items()
            if entry.kind == "file" and (abstract := self._entry_record_abstract(entry, 2))
        }

    def _record_skill_failure(self, uri: str, error: Exception) -> None:
        if self._context_type == "skill":
            self._stats.failures.append(f"{uri}: {error}")

    async def run(self, root_uri: str) -> None:
        """Run DAG execution starting from root_uri."""
        self._root_uri = root_uri
        self._root_done = asyncio.Event()
        self._scheduler = get_semantic_tree_scheduler(self._node_concurrency)
        if self._semantic_plan is not None:
            from openviking.storage.context_update_plan import (
                SemanticAction,
                SemanticTreeEntry,
            )

            entry = self._plan_entries_by_uri.get(root_uri.rstrip("/"))
            if (
                isinstance(entry, SemanticTreeEntry)
                and entry.semantic_action == SemanticAction.REUSE
            ):
                self._stats.total_nodes = 1
                self._stats.done_nodes = 1
                return

        owner = processing_owner.get()
        if owner is not None and self._task_context is not None:
            if owner[1] == self._task_context.task_id:
                self._processing_index = owner[0]
        with pause_task_processing():
            try:
                self._register_active()
                self._schedule_dir(root_uri, parent_uri=None)
                await self._root_done.wait()
                if self._failure:
                    raise self._failure
            except BaseException:
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
            )
            if self._task_context is not None
            else detach_task_context()
        )
        timing = (
            self._processing_index.measure_processing(self._task_context.task_id)
            if self._processing_index is not None and self._task_context is not None
            else nullcontext()
        )
        # Shared workers inherit their creator's ContextVars. Never let an
        # unrelated node pause the creator's clock when it has no timing owner.
        token = processing_owner.set(None)
        try:
            with bind_telemetry(self._telemetry), task_context, timing:
                await self._run_work_bound(work)
        finally:
            processing_owner.reset(token)

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

        try:
            children_dirs, file_paths = await self._list_dir(dir_uri, "_dispatch_dir")
            if self._generation_trigger == "content_copy":
                node = await self._prepare_transfer_node(dir_uri, children_dirs, file_paths)
                self._nodes[dir_uri] = node
                self._schedule_overview(dir_uri)
                return False

            sample_limit = getattr(
                get_openviking_config().semantic,
                "overview_sample_limit",
                32,
            )
            direct_entries = sorted(
                [("directory", uri) for uri in children_dirs]
                + [("file", uri) for uri in file_paths],
                key=lambda item: item[1].rsplit("/", 1)[-1],
            )
            sampled_entries = deterministic_sample(direct_entries, sample_limit)
            sampled_children_dirs = {uri for kind, uri in sampled_entries if kind == "directory"}
            sampled_file_paths = {uri for kind, uri in sampled_entries if kind == "file"}
            pending_snapshot = 0
            if self._aggregate_directory:
                try:
                    pending_snapshot = await read_abstract_overview_pending_snapshot(
                        viking_fs=self._viking_fs,
                        dir_uri=dir_uri,
                        ctx=self._ctx,
                        lock=self._lock,
                    )
                except LockAcquisitionError:
                    if self._generation_trigger != "content_write":
                        raise
                    # Content writes run one directory; retain their file work.
                    self._aggregate_directory = False
                    logger.info("Skipping busy parent semantic refresh: %s", dir_uri)
            file_index = {path: idx for idx, path in enumerate(file_paths)}
            child_index = {path: idx for idx, path in enumerate(children_dirs)}
            if self._semantic_plan is not None:
                required_file_paths = {
                    path
                    for path in file_paths
                    if self._plan_entries_by_uri[path].semantic_action is not SemanticAction.REUSE
                }
                required_children_dirs = {
                    path for path in children_dirs if path in self._plan_active_dirs
                }
                file_summaries: List[Optional[Dict[str, str]]] = [None] * len(file_paths)
                children_abstracts: List[Optional[Dict[str, str]]] = [None] * len(children_dirs)
                for path in file_paths:
                    if path in required_file_paths or path not in sampled_file_paths:
                        continue
                    abstract = self._entry_record_abstract(self._plan_entries_by_uri[path], 2)
                    if not abstract:
                        raise RuntimeError(f"Semantic plan lacks L2 abstract for {path}")
                    file_summaries[file_index[path]] = {
                        "name": path.rsplit("/", 1)[-1],
                        "summary": abstract,
                    }
                for path in children_dirs:
                    if path in required_children_dirs or path not in sampled_children_dirs:
                        continue
                    abstract = self._entry_record_abstract(self._plan_entries_by_uri[path], 0)
                    if not abstract:
                        raise RuntimeError(f"Semantic plan lacks L0 abstract for {path}")
                    children_abstracts[child_index[path]] = {
                        "name": path.rsplit("/", 1)[-1],
                        "abstract": abstract,
                    }
                pending = len(required_file_paths) + len(required_children_dirs)
                node = DirNode(
                    uri=dir_uri,
                    children_dirs=children_dirs,
                    file_paths=file_paths,
                    file_index=file_index,
                    child_index=child_index,
                    file_summaries=file_summaries,
                    children_abstracts=children_abstracts,
                    pending=pending,
                    pending_snapshot=pending_snapshot,
                    sampled_children_dirs=sampled_children_dirs,
                    sampled_file_paths=sampled_file_paths,
                    dispatched=True,
                )
                self._nodes[dir_uri] = node
                for file_path in sorted(required_file_paths):
                    self._schedule_file(dir_uri, file_path)
                for child_uri in sorted(required_children_dirs):
                    self._schedule_dir(child_uri, dir_uri)
                if pending == 0:
                    self._schedule_overview(dir_uri)
                return False
            # Recursive/initial work still maintains every file. Incremental
            # parent aggregation prepares only sampled inputs plus files that
            # changed and therefore need their own vector maintenance.
            required_file_paths = (
                set(file_paths)
                if self._recursive
                else (
                    set(file_paths) & self._changed_paths
                    if not self._aggregate_directory
                    else (
                        sampled_file_paths | (set(file_paths) & self._changed_paths)
                        if self._incremental_update
                        else sampled_file_paths
                    )
                )
            )
            if self._recursive:
                pending = len(children_dirs) + len(required_file_paths)
            else:
                pending = len(required_file_paths)

            node = DirNode(
                uri=dir_uri,
                children_dirs=children_dirs,
                file_paths=file_paths,
                file_index=file_index,
                child_index=child_index,
                file_summaries=[None] * len(file_paths),
                children_abstracts=[None] * len(children_dirs),
                pending=pending,
                pending_snapshot=pending_snapshot,
                sampled_children_dirs=sampled_children_dirs,
                sampled_file_paths=sampled_file_paths,
                dispatched=True,
            )
            self._nodes[dir_uri] = node

            if pending == 0:
                self._schedule_overview(dir_uri)
                return False

            for file_path in file_paths:
                if file_path not in required_file_paths:
                    continue
                self._schedule_file(dir_uri, file_path)

            if children_dirs:
                if self._recursive:
                    for child_uri in children_dirs:
                        self._schedule_dir(child_uri, dir_uri)
            return False
        except LockAcquisitionError:
            # A content-write message can be dequeued before the writer releases
            # its exact-path lease. Let SemanticProcessor re-enqueue this
            # recoverable conflict instead of acknowledging unfinished file work.
            raise
        except Exception as e:
            logger.error(f"Failed to dispatch directory {dir_uri}: {e}", exc_info=True)
            self._record_skill_failure(dir_uri, e)
            if self._semantic_plan is not None or self._generation_trigger == "content_copy":
                raise
            if parent_uri:
                await self._on_child_done(parent_uri, dir_uri, "")
            elif self._root_done:
                self._root_done.set()
            return True

    @staticmethod
    def _transfer_summary_ready(value: str) -> bool:
        summary = value.strip()
        return bool(summary) and not summary.endswith(
            ("[Directory abstract is not ready]", "[Directory overview is not ready]")
        )

    async def _load_transfer_candidates(
        self,
        candidates: List[tuple[str, str]],
    ) -> Dict[tuple[str, str], Dict[str, str]]:
        file_paths = [uri for kind, uri in candidates if kind == "file"]
        file_summaries = await self._processor._load_transfer_file_summaries(
            file_paths, ctx=self._ctx
        )
        loaded: Dict[tuple[str, str], Dict[str, str]] = {}
        for file_path in file_paths:
            summary = str(file_summaries.get(file_path) or "").strip()
            if self._transfer_summary_ready(summary):
                loaded[("file", file_path)] = {
                    "name": file_path.rsplit("/", 1)[-1],
                    "summary": summary,
                }

        child_uris = [uri for kind, uri in candidates if kind == "directory"]
        if child_uris:
            results = await asyncio.gather(
                *[self._viking_fs.abstract(uri, ctx=self._ctx) for uri in child_uris],
                return_exceptions=True,
            )
            for child_uri, result in zip(child_uris, results, strict=True):
                if isinstance(result, BaseException):
                    continue
                abstract = str(result or "").strip()
                if self._transfer_summary_ready(abstract):
                    loaded[("directory", child_uri)] = {
                        "name": child_uri.rsplit("/", 1)[-1],
                        "abstract": abstract,
                    }
        return loaded

    async def _prepare_transfer_node(
        self,
        dir_uri: str,
        children_dirs: List[str],
        file_paths: List[str],
    ) -> DirNode:
        """Sample the target directory first, then read only existing summaries."""
        candidates = sorted(
            [("file", uri) for uri in file_paths] + [("directory", uri) for uri in children_dirs],
            key=lambda item: (item[1].rsplit("/", 1)[-1], item[0], item[1]),
        )
        sample_limit = getattr(get_openviking_config().semantic, "overview_sample_limit", 32)
        primary = deterministic_sample(candidates, sample_limit)
        loaded = await self._load_transfer_candidates(primary)
        selected = [candidate for candidate in primary if candidate in loaded]

        inspected = set(primary)
        selected.sort(key=lambda item: (item[1].rsplit("/", 1)[-1], item[0], item[1]))
        selected_files = [uri for kind, uri in selected if kind == "file"]
        selected_dirs = [uri for kind, uri in selected if kind == "directory"]
        missing_count = (
            sum(1 for candidate in inspected if candidate not in loaded)
            if len(inspected) == len(candidates)
            else None
        )
        pending_snapshot = await read_abstract_overview_pending_snapshot(
            viking_fs=self._viking_fs,
            dir_uri=dir_uri,
            ctx=self._ctx,
            lock=self._lock,
        )
        return DirNode(
            uri=dir_uri,
            children_dirs=selected_dirs,
            file_paths=selected_files,
            file_index={uri: idx for idx, uri in enumerate(selected_files)},
            child_index={uri: idx for idx, uri in enumerate(selected_dirs)},
            file_summaries=[loaded[("file", uri)] for uri in selected_files],
            children_abstracts=[loaded[("directory", uri)] for uri in selected_dirs],
            pending=0,
            pending_snapshot=pending_snapshot,
            total_entries=len(candidates),
            missing_summary_entries=missing_count,
            transfer_inputs_ready=not candidates or bool(selected),
            dispatched=True,
        )

    async def _list_dir(self, uri: str, from_hint: str) -> tuple[list[str], list[str]]:
        """List directory entries and return (child_dirs, file_paths)."""
        if self._semantic_plan is not None:
            return self._plan_children.get(uri.rstrip("/"), ([], []))
        if self._artifact_files and self._root_uri:
            return self._list_artifact_dir(uri)
        try:
            entries = await self._viking_fs.ls(uri, node_limit=LS_ALL_NODES, ctx=self._ctx)
        except Exception as e:
            logger.warning(
                f"[SemanticTreeExecutor] Failed to list directory {uri}: {e} from {from_hint}"
            )
            raise

        children_dirs: List[str] = []
        file_paths: List[str] = []

        for entry in entries:
            name = entry.get("name", "")
            if not name or name.startswith(".") or name in [".", ".."] or name in _SKIP_FILENAMES:
                continue

            item_uri = VikingURI(uri).join(name).uri
            if entry.get("isDir", False):
                children_dirs.append(item_uri)
            else:
                file_paths.append(item_uri)

        return sorted(children_dirs), sorted(file_paths)

    def _list_artifact_dir(self, uri: str) -> tuple[list[str], list[str]]:
        root = self._root_uri.rstrip("/")
        current = uri.rstrip("/")
        if current == root:
            prefix = ""
        elif current.startswith(f"{root}/"):
            prefix = current[len(root) + 1 :] + "/"
        else:
            return [], []

        child_dirs: set[str] = set()
        file_paths: list[str] = []
        for rel_path in self._artifact_files:
            if not rel_path.startswith(prefix):
                continue
            remainder = rel_path[len(prefix) :]
            if not remainder:
                continue
            head, separator, _ = remainder.partition("/")
            if not head or head.startswith(".") or head in _SKIP_FILENAMES:
                continue
            child_uri = VikingURI(current).join(head).uri
            if separator:
                child_dirs.add(child_uri)
            else:
                file_paths.append(child_uri)
        return sorted(child_dirs), sorted(file_paths)

    def _get_target_file_path(self, current_uri: str) -> Optional[str]:
        if not self._incremental_update or not self._target_uri or not self._root_uri:
            logger.warning(
                "Invalid target_uri or root_uri for incremental update: "
                f"target_uri={self._target_uri}, root_uri={self._root_uri}"
            )
            return None
        if self._target_uri != self._root_uri:
            logger.warning(
                "Incremental semantic update expects target_uri == root_uri: "
                f"target_uri={self._target_uri}, root_uri={self._root_uri}"
            )
            return None
        return current_uri

    def _is_direct_incremental_update(self) -> bool:
        return self._semantic_plan is not None or (
            self._incremental_update
            and self._changes_provided
            and self._target_uri == self._root_uri
        )

    def _directory_entries_changed(self, dir_uri: str) -> bool:
        """Return whether this directory subtree gained or lost an entry."""
        normalized = dir_uri.rstrip("/")
        prefix = normalized + "/"
        return any(
            path == normalized or path.startswith(prefix) for path in self._tree_changed_paths
        )

    def _ingest_options_for_file(self, file_path: str) -> IngestOptions:
        if self._generation_trigger == "content_write" and file_path not in self._changed_paths:
            return IngestOptions(acl_update=self._ingest_options.acl_update)
        return self._ingest_options

    def _ingest_options_for_directory(self) -> IngestOptions:
        if self._generation_trigger == "content_write":
            return IngestOptions(acl_update=self._ingest_options.acl_update)
        return self._ingest_options

    def _plan_scalar_override(self, uri: str, level: int) -> Optional[Dict[str, Any]]:
        entry = self._plan_entries_by_uri.get(uri.rstrip("/"))
        if entry is None:
            return None
        slot = entry.slot(level)
        return slot.scalar_override() if slot is not None else None

    def _plan_field_patch(self, uri: str, level: int) -> FieldPatch | None:
        entry = self._plan_entries_by_uri.get(uri.rstrip("/"))
        slot = entry.slot(level) if entry is not None else None
        return slot.field_patch if slot is not None else None

    async def _check_file_content_changed(self, file_path: str) -> bool:
        if self._is_direct_incremental_update():
            return file_path in self._changed_paths
        target_path = self._get_target_file_path(file_path)
        if not target_path:
            return True
        try:
            current_stat = await self._viking_fs.stat(file_path, ctx=self._ctx, skip_count=True)
            target_stat = await self._viking_fs.stat(target_path, ctx=self._ctx, skip_count=True)
            current_size = current_stat.get("size") if isinstance(current_stat, dict) else None
            target_size = target_stat.get("size") if isinstance(target_stat, dict) else None
            if current_size is not None and target_size is not None and current_size != target_size:
                return True
            current_content = await self._viking_fs.read_file(file_path, ctx=self._ctx)
            target_content = await self._viking_fs.read_file(target_path, ctx=self._ctx)
            return current_content != target_content
        except Exception:
            return True

    async def _read_existing_summary(self, file_path: str) -> Optional[Dict[str, str]]:
        """Read existing summary from parent directory's .overview.md.

        Args:
            file_path: Current file path

        Returns:
            Summary dict with 'name' and 'summary' keys, or None if not found
        """
        target_path = self._get_target_file_path(file_path)
        if not target_path:
            return None
        vector_abstract = self._file_abstracts.get(target_path.rstrip("/"))
        if vector_abstract:
            return {
                "name": file_path.rsplit("/", 1)[-1],
                "summary": vector_abstract,
            }

        try:
            parent_uri = "/".join(target_path.rsplit("/", 1)[:-1])
            if not parent_uri:
                return None

            if parent_uri not in self._overview_cache:
                try:
                    from openviking.metrics.datasources.cache import CacheEventDataSource

                    CacheEventDataSource.record_miss("L1")
                except Exception:
                    pass
                async with self._overview_cache_lock:
                    if parent_uri not in self._overview_cache:
                        overview_path = f"{parent_uri}/.overview.md"
                        overview_content = await self._viking_fs.read_file(
                            overview_path, ctx=self._ctx
                        )
                        if overview_content:
                            self._overview_cache[parent_uri] = self._processor._parse_overview_md(
                                body_for_preview(overview_content)
                            )
                        else:
                            self._overview_cache[parent_uri] = {}
            else:
                try:
                    from openviking.metrics.datasources.cache import CacheEventDataSource

                    CacheEventDataSource.record_hit("L1")
                except Exception:
                    pass

            existing_summaries = self._overview_cache.get(parent_uri, {})
            file_name = file_path.split("/")[-1]

            if file_name in existing_summaries:
                return {"name": file_name, "summary": existing_summaries[file_name]}

        except Exception as e:
            logger.debug(f"Failed to read existing summary from overview.md for {file_path}: {e}")

        return None

    async def _check_dir_children_changed(
        self, dir_uri: str, current_files: List[str], current_dirs: List[str]
    ) -> bool:
        if self._is_direct_incremental_update():
            node = self._nodes.get(dir_uri)
            if node is not None and node.pending_snapshot > 0:
                return True
            if self._directory_entries_changed(dir_uri):
                return True
            for current_file in current_files:
                default_changed = (
                    self._plan_entries_by_uri[current_file].semantic_action
                    is not SemanticAction.REUSE
                    if self._semantic_plan is not None
                    else True
                )
                if self._file_change_status.get(current_file, default_changed):
                    return True
            for current_dir in current_dirs:
                default_changed = (
                    self._plan_entries_by_uri[current_dir].semantic_action
                    is not SemanticAction.REUSE
                    if self._semantic_plan is not None
                    else True
                )
                if self._dir_change_status.get(current_dir, default_changed):
                    return True
            return False

        target_path = self._get_target_file_path(dir_uri)
        if not target_path:
            return True
        try:
            target_dirs, target_files = await self._list_dir(
                target_path, "_check_dir_children_changed"
            )
            current_file_names = {f.split("/")[-1] for f in current_files}
            target_file_names = {f.split("/")[-1] for f in target_files}
            if current_file_names != target_file_names:
                return True
            current_dir_names = {d.split("/")[-1] for d in current_dirs}
            target_dir_names = {d.split("/")[-1] for d in target_dirs}
            if current_dir_names != target_dir_names:
                return True
            for current_file in current_files:
                if self._file_change_status.get(current_file, True):
                    return True
            for current_dir in current_dirs:
                if self._dir_change_status.get(current_dir, True):
                    return True
            return False
        except Exception:
            return True

    async def _read_existing_overview_abstract(
        self, dir_uri: str
    ) -> tuple[Optional[str], Optional[str]]:
        if self._semantic_plan is not None:
            entry = self._plan_entries_by_uri.get(dir_uri.rstrip("/"))
            if entry is None:
                return None, None
            overview = self._entry_record_abstract(entry, 1) or None
            abstract = self._entry_record_abstract(entry, 0) or None
            return overview, abstract
        target_path = self._get_target_file_path(dir_uri)
        if not target_path:
            return None, None
        try:
            overview = await self._viking_fs.read_file(f"{target_path}/.overview.md", ctx=self._ctx)
            abstract = await self._viking_fs.read_file(f"{target_path}/.abstract.md", ctx=self._ctx)
            return body_for_preview(overview), body_for_preview(abstract)
        except Exception:
            return None, None

    async def _file_summary_task(self, parent_uri: str, file_path: str) -> None:
        """Generate file summary and notify parent completion."""

        file_name = file_path.split("/")[-1]
        need_vectorize = True
        semantic_succeeded = True
        file_content = None
        try:
            summary_dict = None
            if self._incremental_update:
                content_changed = await self._check_file_content_changed(file_path)
                self._file_change_status[file_path] = content_changed
                node = self._nodes.get(parent_uri)
                regenerate_sampled_summary = bool(
                    self._aggregate_directory
                    and node is not None
                    and node.pending_snapshot > 0
                    and node.sampled_file_paths is not None
                    and file_path in node.sampled_file_paths
                )

                if not content_changed and not regenerate_sampled_summary:
                    summary_dict = await self._read_existing_summary(file_path)
                    if summary_dict is not None:
                        need_vectorize = False
                    else:
                        self._file_change_status[file_path] = True
                elif not content_changed:
                    # Pending freshness only records a count, not the changed
                    # child URIs. Once that debt triggers aggregation, rebuild
                    # every sampled input instead of trusting the old L1 body.
                    # Deferred messages already maintained file vectors, so
                    # this forced summary rebuild does not imply vector work.
                    need_vectorize = False
            else:
                self._file_change_status[file_path] = True
            if summary_dict is None:
                file_content = None
                if hasattr(self._viking_fs, "read_file_bytes"):
                    file_content = await self._viking_fs.read_file_bytes(file_path, ctx=self._ctx)
                summary_kwargs: Dict[str, Any] = {
                    "llm_sem": self._llm_sem,
                    "ctx": self._ctx,
                }
                if file_content is not None:
                    summary_kwargs["file_content"] = file_content
                summary_dict = await self._processor._generate_single_file_summary(
                    file_path, **summary_kwargs
                )
        except (AbstractOverviewFormatError, FileNotFoundError, ValueError):
            # A generated sidecar that opted into OKF must never be treated as
            # an empty file summary; doing so would silently feed metadata or
            # corrupted YAML into a later regeneration.
            raise
        except Exception as e:
            logger.warning(f"Failed to generate summary for {file_path}: {e}")
            self._record_skill_failure(file_path, e)
            summary_dict = {"name": file_name, "summary": ""}
            semantic_succeeded = False
        finally:
            self._stats.done_nodes += 1
            self._stats.in_progress_nodes = max(0, self._stats.in_progress_nodes - 1)

        if self._closed:
            return
        from openviking.storage.context_update_plan import (
            IndexAction,
            SemanticTreeEntry,
        )

        entry = self._plan_entries_by_uri.get(file_path.rstrip("/"))
        slot = entry.slot(2) if isinstance(entry, SemanticTreeEntry) else None
        if isinstance(entry, SemanticTreeEntry):
            need_vectorize = bool(
                semantic_succeeded
                and slot is not None
                and slot.action in {IndexAction.UPSERT, IndexAction.MERGE}
            )
        if need_vectorize and not self._skip_vectorization:
            use_summary = self._is_code_repo and bool(summary_dict.get("summary"))
            try:
                vectorize_kwargs: Dict[str, Any] = {}
                if file_content is not None:
                    vectorize_kwargs["file_content"] = file_content
                if self._telemetry_id is not None:
                    vectorize_kwargs["telemetry_id"] = self._telemetry_id
                manifest_md5 = self._file_md5s.get(file_path.rstrip("/")) or None
                if file_content is not None and not manifest_md5:
                    file_md5 = content_md5(file_content)
                else:
                    file_md5 = manifest_md5
                if self._semantic_plan is not None:
                    vectorize_kwargs["scalar_override"] = self._plan_scalar_override(file_path, 2)
                    vectorize_kwargs["field_patch"] = self._plan_field_patch(file_path, 2)
                    vectorize_kwargs["action"] = slot.action.value if slot is not None else "upsert"
                enqueued = await self._processor._vectorize_single_file(
                    parent_uri=parent_uri,
                    context_type=self._context_type,
                    file_path=file_path,
                    summary_dict=summary_dict,
                    ctx=self._ctx,
                    use_summary=use_summary,
                    ingest_options=(
                        IngestOptions(acl_update=self._ingest_options.acl_update)
                        if self._semantic_plan is not None
                        else self._ingest_options_for_file(file_path)
                    ),
                    file_md5=file_md5,
                    **vectorize_kwargs,
                )
                if enqueued and slot is not None:
                    self._scheduled_vector_record_ids.add(slot.record_id)
            except Exception as e:
                logger.error(
                    "Failed to schedule vectorization for %s: %s",
                    file_path,
                    e,
                    exc_info=True,
                )
                if self._context_type != "skill":
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

    def _finalize_file_summaries(self, node: DirNode) -> List[Dict[str, str]]:
        summaries: List[Dict[str, str]] = []
        for idx, file_path in enumerate(node.file_paths):
            if node.sampled_file_paths is not None and file_path not in node.sampled_file_paths:
                continue
            item = node.file_summaries[idx]
            if item is None:
                summaries.append({"name": file_path.split("/")[-1], "summary": ""})
            else:
                summaries.append(item)
        return summaries

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

    async def _finalize_children_abstracts(self, node: DirNode) -> List[Dict[str, str]]:
        results: List[Dict[str, str]] = []
        for idx, child_uri in enumerate(node.children_dirs):
            if (
                node.sampled_children_dirs is not None
                and child_uri not in node.sampled_children_dirs
            ):
                continue
            item = node.children_abstracts[idx]
            if item is None:
                try:
                    abstract = await self._viking_fs.abstract(child_uri, ctx=self._ctx)
                except Exception:
                    abstract = ""
                results.append({"name": child_uri.split("/")[-1], "abstract": abstract})
            else:
                results.append(item)
        return results

    def _is_stale(self) -> bool:
        from openviking.storage.queuefs.semantic_queue import is_semantic_coalesce_stale

        return is_semantic_coalesce_stale(self._coalesce_key, self._coalesce_version)

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
            )
        )
        if not wrote.wrote:
            self._stale = True
        return wrote

    async def _overview_task(self, dir_uri: str) -> None:
        node = self._nodes.get(dir_uri)
        if not node:
            return
        skill_definition_changed = (
            self._context_type == "skill"
            and classify_uri(dir_uri).is_skill_root
            and f"{dir_uri}/SKILL.md" in self._changed_paths
        )
        if not self._aggregate_directory and not skill_definition_changed:
            # Deferred aggregation still ran changed-file work. Finish without
            # touching directory sidecars or their vectors.
            self._stats.done_nodes += 1
            self._stats.in_progress_nodes = max(0, self._stats.in_progress_nodes - 1)
            self._release_dir_node(dir_uri)
            if dir_uri == self._root_uri and self._root_done:
                self._root_done.set()
            return
        need_vectorize = True
        children_changed = True
        should_write = True
        write_result = AbstractOverviewWriteResult(wrote=False)
        abstract: Optional[str] = None
        overview: Optional[str] = None
        total_entries = (
            node.total_entries
            if node.total_entries is not None
            else len(node.file_paths) + len(node.children_dirs)
        )
        sampled_inputs: List[tuple[str, Dict[str, str]]] = []
        sampled_entries = 0
        try:
            if self._context_type == "skill" and classify_uri(dir_uri).is_skill_root:
                # The package root describes the skill definition, independently
                # of the summaries produced for its attachments.
                definition_changed = f"{dir_uri}/SKILL.md" in self._changed_paths
                overview, abstract = await self._processor._skill_root_semantics(
                    dir_uri,
                    ctx=self._ctx,
                    regenerate=self._generation_trigger == "reindex" or definition_changed,
                    lock=self._lock,
                )
                should_write = False
                need_vectorize = not self._incremental_update or definition_changed
                children_changed = definition_changed
            elif self._generation_trigger == "content_copy" and not node.transfer_inputs_ready:
                need_vectorize = False
                should_write = False
            elif self._incremental_update and self._generation_trigger != "content_copy":
                children_changed = await self._check_dir_children_changed(
                    dir_uri, node.file_paths, node.children_dirs
                )

                if not children_changed:
                    overview, abstract = await self._read_existing_overview_abstract(dir_uri)
                    should_write = overview is None or abstract is None
                    # Rebuilt sidecars must also replace their stale vectors.
                    need_vectorize = should_write
                    children_changed = should_write
            if should_write and (overview is None or abstract is None):
                async with node.lock:
                    file_summaries = self._finalize_file_summaries(node)
                    children_abstracts = await self._finalize_children_abstracts(node)
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
                if self._generation_trigger != "content_copy":
                    overview = self._select_direct_media_overview(node, file_summaries)
                if overview is None:
                    async with self._llm_sem:
                        overview = await self._processor._generate_overview(
                            dir_uri,
                            file_summaries,
                            children_abstracts,
                            total_files=len(node.file_paths),
                            total_children=len(node.children_dirs),
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
                    if self._semantic_plan is not None:
                        self.fail(exc)
                        return
                    raise

        except AbstractOverviewFormatError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate overview for {dir_uri}: {e}", exc_info=True)
            self._record_skill_failure(dir_uri, e)
        else:
            slots = {}
            if self._semantic_plan is not None:
                entry = self._plan_entries_by_uri.get(dir_uri.rstrip("/"))
                slots = {slot.level: slot for slot in entry.index_slots} if entry else {}

            if need_vectorize and not self._skip_vectorization:
                assert overview is not None and abstract is not None
                try:
                    directory_vector_kwargs: Dict[str, Any] = {}
                    include_abstract = True
                    include_overview = True
                    if self._semantic_plan is not None:
                        from openviking.storage.context_update_plan import (
                            IndexAction,
                        )

                        def should_emit(level: int) -> bool:
                            slot = slots.get(level)
                            if slot is None or slot.action not in {
                                IndexAction.UPSERT,
                                IndexAction.MERGE,
                            }:
                                return False
                            old_body = slot.abstract
                            new_body = abstract if level == 0 else overview
                            return not old_body or old_body != new_body

                        include_abstract = should_emit(0)
                        include_overview = should_emit(1)
                        directory_vector_kwargs = {
                            "scalar_overrides": {
                                level: values
                                for level in (0, 1)
                                if (values := self._plan_scalar_override(dir_uri, level))
                            },
                            "actions": {
                                level: slot.action
                                for level, slot in slots.items()
                                if slot.action in {IndexAction.UPSERT, IndexAction.MERGE}
                            },
                            "field_patches": {
                                level: slot.field_patch
                                for level, slot in slots.items()
                                if slot.field_patch is not None
                            },
                            "include_abstract": include_abstract,
                            "include_overview": include_overview,
                        }
                    if self._telemetry_id is not None:
                        directory_vector_kwargs["telemetry_id"] = self._telemetry_id
                    if include_abstract or include_overview:
                        enqueued_levels = await self._processor._vectorize_directory(
                            dir_uri,
                            context_type=self._context_type,
                            abstract=abstract,
                            overview=overview,
                            ctx=self._ctx,
                            ingest_options=(
                                IngestOptions(acl_update=self._ingest_options.acl_update)
                                if self._semantic_plan is not None
                                else self._ingest_options_for_directory()
                            ),
                            **(
                                {"skill_source_path": (self._source or {}).get("path", "")}
                                if self._context_type == "skill"
                                and classify_uri(dir_uri).is_skill_root
                                else {}
                            ),
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
                    if self._context_type != "skill":
                        raise
                    self._record_skill_failure(dir_uri, e)
            else:
                enqueued_levels = set()

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

            if self._semantic_plan is not None:
                self._scheduled_vector_record_ids.update(
                    slot.record_id for slot in slots.values() if slot.level in enqueued_levels
                )
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
