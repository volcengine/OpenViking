# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic DAG executor with event-driven lazy dispatch."""

import asyncio
import re
import threading
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Set
from weakref import WeakKeyDictionary

from openviking.parse.parsers.media import get_media_type
from openviking.server.identity import RequestContext
from openviking.service.task_work_index import bind_task_context, get_task_context
from openviking.storage.abstract_overview import (
    AbstractOverviewFormatError,
    AbstractOverviewWriteResult,
    body_for_preview,
    deterministic_sample,
    freshness_metadata,
    read_abstract_overview_pending_snapshot,
    write_abstract_overview,
)
from openviking.storage.viking_fs import LS_ALL_NODES, get_viking_fs
from openviking.telemetry import bind_telemetry, get_current_telemetry
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.utils import VikingURI
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

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
    dispatched: bool = False
    overview_scheduled: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class DagStats:
    total_nodes: int = 0
    pending_nodes: int = 0
    in_progress_nodes: int = 0
    done_nodes: int = 0


@dataclass(frozen=True)
class DagWork:
    """A scheduled unit of DAG work."""

    kind: str
    dir_uri: str
    parent_uri: Optional[str] = None
    file_path: Optional[str] = None


@dataclass(frozen=True)
class ScheduledDagWork:
    executor: "SemanticDagExecutor"
    work: DagWork


class SemanticNodeScheduler:
    """Shared node executor for semantic DAG work in one event loop."""

    _idle_timeout = 0.05

    def __init__(self, max_workers: int):
        self._max_workers = max(1, max_workers)
        self._queue: asyncio.Queue[ScheduledDagWork] = asyncio.Queue()
        self._workers: Set[asyncio.Task] = set()

    def configure(self, max_workers: int) -> None:
        self._max_workers = max(1, max_workers)
        self._ensure_workers()

    def submit(self, executor: "SemanticDagExecutor", work: DagWork) -> None:
        self._queue.put_nowait(ScheduledDagWork(executor=executor, work=work))
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


_node_schedulers: "WeakKeyDictionary[asyncio.AbstractEventLoop, SemanticNodeScheduler]" = (
    WeakKeyDictionary()
)


def get_semantic_node_scheduler(max_workers: int) -> SemanticNodeScheduler:
    loop = asyncio.get_running_loop()
    scheduler = _node_schedulers.get(loop)
    if scheduler is None:
        scheduler = SemanticNodeScheduler(max_workers=max_workers)
        _node_schedulers[loop] = scheduler
    else:
        scheduler.configure(max_workers)
    return scheduler


class SemanticDagExecutor:
    """Execute semantic generation with DAG-style, event-driven lazy dispatch."""

    _active_lock: ClassVar[threading.Lock] = threading.Lock()
    _active_executors: ClassVar[Set["SemanticDagExecutor"]] = set()

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
    ):
        self._processor = processor
        self._context_type = context_type
        self._ctx = ctx
        self._incremental_update = incremental_update
        self._target_uri = target_uri
        self._recursive = recursive
        self._lock = lock
        self._is_code_repo = is_code_repo
        self._changes = changes or {}
        self._skip_vectorization = skip_vectorization
        self._ingest_options = IngestOptions.from_value(ingest_options)
        self._coalesce_key = coalesce_key
        self._coalesce_version = coalesce_version
        self._source = dict(source) if source else None
        self._generation_trigger = generation_trigger
        self._aggregate_directory = aggregate_directory
        self._task_context = get_task_context()
        self._telemetry = get_current_telemetry()
        self._stale = False
        self._changed_paths = {
            path for key in ("added", "modified", "deleted") for path in self._changes.get(key, [])
        }
        self._node_concurrency = max(1, max_concurrent_llm)
        self._llm_sem = asyncio.Semaphore(max_concurrent_llm)
        self._viking_fs = get_viking_fs()
        self._nodes: Dict[str, DirNode] = {}
        self._parent: Dict[str, Optional[str]] = {}
        self._root_uri: Optional[str] = None
        self._root_done: Optional[asyncio.Event] = None
        self._scheduler: Optional[SemanticNodeScheduler] = None
        self._active_scheduled_work = 0
        self._active_work_idle = asyncio.Event()
        self._active_work_idle.set()
        self._closed = False
        self._failure: Optional[Exception] = None
        self._stats = DagStats()
        self._file_change_status: Dict[str, bool] = {}
        self._dir_change_status: Dict[str, bool] = {}
        self._overview_cache: Dict[str, Dict[str, str]] = {}
        self._overview_cache_lock = asyncio.Lock()
        self._root_write_result = AbstractOverviewWriteResult(wrote=False)

    async def run(self, root_uri: str) -> None:
        """Run DAG execution starting from root_uri."""
        self._root_uri = root_uri
        self._root_done = asyncio.Event()
        self._scheduler = get_semantic_node_scheduler(self._node_concurrency)

        try:
            self._register_active()
            self._schedule_dir(root_uri, parent_uri=None)
            await self._root_done.wait()
            if self._failure:
                raise self._failure
        except BaseException:
            self._closed = True
            await self._active_work_idle.wait()
            raise
        finally:
            self._closed = True
            await self._active_work_idle.wait()
            self._unregister_active()

    def _schedule_work(self, work: DagWork) -> None:
        if self._closed:
            return
        if self._scheduler is None:
            self._scheduler = get_semantic_node_scheduler(self._node_concurrency)
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
        self._schedule_work(DagWork(kind="dir", dir_uri=dir_uri, parent_uri=parent_uri))

    def _schedule_file(self, parent_uri: str, file_path: str) -> None:
        if self._closed:
            return
        self._stats.total_nodes += 1
        self._stats.pending_nodes += 1
        self._schedule_work(DagWork(kind="file", dir_uri=parent_uri, file_path=file_path))

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
    def get_active_stats(cls) -> DagStats:
        stats = DagStats()
        with cls._active_lock:
            executors = list(cls._active_executors)
        for executor in executors:
            current = executor.get_stats()
            stats.total_nodes += current.total_nodes
            stats.pending_nodes += current.pending_nodes
            stats.in_progress_nodes += current.in_progress_nodes
            stats.done_nodes += current.done_nodes
        return stats

    async def _run_work(self, work: DagWork) -> None:
        task_context = (
            bind_task_context(
                self._task_context.task_id,
                self._task_context.account_id,
                self._task_context.user_id,
            )
            if self._task_context is not None
            else nullcontext()
        )
        with bind_telemetry(self._telemetry), task_context:
            await self._run_work_bound(work)

    async def _run_work_bound(self, work: DagWork) -> None:
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
            sampled_children_dirs = {
                uri for kind, uri in sampled_entries if kind == "directory"
            }
            sampled_file_paths = {uri for kind, uri in sampled_entries if kind == "file"}
            pending_snapshot = (
                await read_abstract_overview_pending_snapshot(
                    viking_fs=self._viking_fs,
                    dir_uri=dir_uri,
                    ctx=self._ctx,
                    lock=self._lock,
                )
                if self._aggregate_directory
                else 0
            )
            file_index = {path: idx for idx, path in enumerate(file_paths)}
            child_index = {path: idx for idx, path in enumerate(children_dirs)}
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
        except Exception as e:
            logger.error(f"Failed to dispatch directory {dir_uri}: {e}", exc_info=True)
            if parent_uri:
                await self._on_child_done(parent_uri, dir_uri, "")
            elif self._root_done:
                self._root_done.set()
            return True

    async def _list_dir(self, uri: str, from_hint: str) -> tuple[list[str], list[str]]:
        """List directory entries and return (child_dirs, file_paths)."""
        try:
            entries = await self._viking_fs.ls(uri, node_limit=LS_ALL_NODES, ctx=self._ctx)
        except Exception as e:
            logger.warning(
                f"[SemanticDagExecutor] Failed to list directory {uri}: {e} from {from_hint}"
            )
            return [], []

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
        return (
            self._incremental_update
            and bool(self._changed_paths)
            and self._target_uri == self._root_uri
        )

    def _path_has_direct_change(self, uri: str) -> bool:
        if uri in self._changed_paths:
            return True
        prefix = uri.rstrip("/") + "/"
        return any(path.startswith(prefix) for path in self._changed_paths)

    async def _check_file_content_changed(self, file_path: str) -> bool:
        if self._is_direct_incremental_update():
            return file_path in self._changed_paths
        target_path = self._get_target_file_path(file_path)
        if not target_path:
            return True
        try:
            current_stat = await self._viking_fs.stat(file_path, ctx=self._ctx)
            target_stat = await self._viking_fs.stat(target_path, ctx=self._ctx)
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

        except AbstractOverviewFormatError:
            raise
        except Exception as e:
            logger.debug(f"Failed to read existing summary from overview.md for {file_path}: {e}")

        return None

    async def _check_dir_children_changed(
        self, dir_uri: str, current_files: List[str], current_dirs: List[str]
    ) -> bool:
        if self._is_direct_incremental_update():
            if self._path_has_direct_change(dir_uri):
                return True
            for current_file in current_files:
                if self._file_change_status.get(current_file, True):
                    return True
            for current_dir in current_dirs:
                if self._dir_change_status.get(current_dir, True):
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
        target_path = self._get_target_file_path(dir_uri)
        if not target_path:
            return None, None
        try:
            overview = await self._viking_fs.read_file(f"{target_path}/.overview.md", ctx=self._ctx)
            abstract = await self._viking_fs.read_file(f"{target_path}/.abstract.md", ctx=self._ctx)
            return body_for_preview(overview), body_for_preview(abstract)
        except AbstractOverviewFormatError:
            raise
        except Exception:
            return None, None

    async def _file_summary_task(self, parent_uri: str, file_path: str) -> None:
        """Generate file summary and notify parent completion."""

        file_name = file_path.split("/")[-1]
        need_vectorize = True
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
                summary_dict = await self._processor._generate_single_file_summary(
                    file_path, llm_sem=self._llm_sem, ctx=self._ctx
                )
        except AbstractOverviewFormatError:
            # A generated sidecar that opted into OKF must never be treated as
            # an empty file summary; doing so would silently feed metadata or
            # corrupted YAML into a later regeneration.
            raise
        except Exception as e:
            logger.warning(f"Failed to generate summary for {file_path}: {e}")
            summary_dict = {"name": file_name, "summary": ""}
        finally:
            self._stats.done_nodes += 1
            self._stats.in_progress_nodes = max(0, self._stats.in_progress_nodes - 1)

        if self._closed:
            return
        if need_vectorize and not self._skip_vectorization:
            use_summary = self._is_code_repo and bool(summary_dict.get("summary"))
            try:
                await self._processor._vectorize_single_file(
                    parent_uri=parent_uri,
                    context_type=self._context_type,
                    file_path=file_path,
                    summary_dict=summary_dict,
                    ctx=self._ctx,
                    use_summary=use_summary,
                    ingest_options=self._ingest_options,
                )
            except Exception as e:
                logger.error(
                    "Failed to schedule vectorization for %s: %s",
                    file_path,
                    e,
                    exc_info=True,
                )
                raise
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
        self._schedule_work(DagWork(kind="overview", dir_uri=dir_uri))

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
    ) -> AbstractOverviewWriteResult:
        metadata: Dict[str, Any] = {
            "generated_by": {
                "component": "SemanticProcessor",
                "trigger": self._generation_trigger,
            },
            "freshness": freshness_metadata(total_entries, sampled_entries),
        }
        if dir_uri == self._root_uri and self._source:
            metadata["source"] = self._source
        wrote = await write_abstract_overview(
            viking_fs=self._viking_fs,
            dir_uri=dir_uri,
            overview=overview,
            abstract=abstract,
            ctx=self._ctx,
            is_stale=self._is_stale,
            metadata=metadata,
            consume_pending=consume_pending,
            lock=self._lock,
            log_prefix="[SemanticDag]",
        )
        if not wrote.wrote:
            self._stale = True
        return wrote

    async def _overview_task(self, dir_uri: str) -> None:
        node = self._nodes.get(dir_uri)
        if not node:
            return
        if not self._aggregate_directory:
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
        abstract = ""
        try:
            overview = None
            abstract = None
            if self._incremental_update:
                children_changed = await self._check_dir_children_changed(
                    dir_uri, node.file_paths, node.children_dirs
                )

                if not children_changed:
                    need_vectorize = False
                    overview, abstract = await self._read_existing_overview_abstract(dir_uri)
                    should_write = overview is None or abstract is None
            if overview is None or abstract is None:
                async with node.lock:
                    file_summaries = self._finalize_file_summaries(node)
                    children_abstracts = await self._finalize_children_abstracts(node)
                # Freshness describes the directory itself, including direct
                # entries whose summaries failed. Those entries remain visible
                # as unsampled coverage instead of disappearing from the count.
                total_entries = len(node.file_paths) + len(node.children_dirs)
                # Sampling happened in _dispatch_dir, before summary work was
                # scheduled. Only the prepared bounded inputs reach the prompt.
                sampled_entries = len(file_summaries) + len(children_abstracts)
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

            # Write directly, protected by the outer semantic lock.
            if should_write:
                try:
                    wrote = await self._write_directory_semantics(
                        dir_uri,
                        overview,
                        abstract,
                        total_entries=total_entries,
                        sampled_entries=sampled_entries,
                        consume_pending=node.pending_snapshot,
                    )
                    if dir_uri == self._root_uri:
                        self._root_write_result = wrote
                    if not wrote.wrote:
                        need_vectorize = False
                except AbstractOverviewFormatError:
                    raise
                except Exception:
                    logger.info(f"[SemanticDag] {dir_uri} write failed, skipping")

        except AbstractOverviewFormatError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate overview for {dir_uri}: {e}", exc_info=True)
        else:
            if need_vectorize and not self._skip_vectorization:
                try:
                    await self._processor._vectorize_directory(
                        dir_uri,
                        context_type=self._context_type,
                        abstract=abstract,
                        overview=overview,
                        ctx=self._ctx,
                        ingest_options=self._ingest_options,
                    )
                except Exception as e:
                    logger.error(
                        "Failed to schedule vectorization for %s: %s",
                        dir_uri,
                        e,
                        exc_info=True,
                    )
                    raise
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

        await self._on_child_done(parent_uri, dir_uri, abstract)
        self._release_dir_node(dir_uri)

    def get_stats(self) -> DagStats:
        return DagStats(
            total_nodes=self._stats.total_nodes,
            pending_nodes=self._stats.pending_nodes,
            in_progress_nodes=self._stats.in_progress_nodes,
            done_nodes=self._stats.done_nodes,
        )

    @property
    def root_write_result(self) -> AbstractOverviewWriteResult:
        """Visible-body changes produced for the executor root."""

        return self._root_write_result


if False:  # pragma: no cover - for type checkers only
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor
