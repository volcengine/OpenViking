# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Semantic DAG executor with event-driven lazy dispatch."""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import VikingURI
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


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
    dispatched: bool = False
    overview_scheduled: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class DagStats:
    total_nodes: int = 0
    pending_nodes: int = 0
    in_progress_nodes: int = 0
    done_nodes: int = 0


class SemanticDagExecutor:
    """Execute semantic generation with DAG-style, event-driven lazy dispatch."""

    def __init__(
        self,
        processor: "SemanticProcessor",
        context_type: str,
        max_concurrent_llm: int,
        ctx: RequestContext,
        incremental_update: bool = False,
        target_uri: Optional[str] = None,
        semantic_msg_id: Optional[str] = None,
        recursive: bool = True,
    ):
        self._processor = processor
        self._context_type = context_type
        self._max_concurrent_llm = max_concurrent_llm
        self._ctx = ctx
        self._incremental_update = incremental_update
        self._target_uri = target_uri
        self._semantic_msg_id = semantic_msg_id
        self._recursive = recursive
        self._llm_sem = asyncio.Semaphore(max_concurrent_llm)
        self._viking_fs = get_viking_fs()
        self._nodes: Dict[str, DirNode] = {}
        self._parent: Dict[str, Optional[str]] = {}
        self._root_uri: Optional[str] = None
        self._root_done: Optional[asyncio.Event] = None
        self._stats = DagStats()

    async def run(self, root_uri: str) -> None:
        """Run DAG execution starting from root_uri."""
        self._root_uri = root_uri
        self._root_done = asyncio.Event()
        await self._dispatch_dir(root_uri, parent_uri=None)
        await self._root_done.wait()

    async def _dispatch_dir(self, dir_uri: str, parent_uri: Optional[str]) -> None:
        """Lazy-dispatch tasks for a directory when it is triggered."""
        if dir_uri in self._nodes:
            return

        self._parent[dir_uri] = parent_uri

        try:
            children_dirs, file_paths = await self._list_dir(dir_uri)
            file_index = {path: idx for idx, path in enumerate(file_paths)}
            child_index = {path: idx for idx, path in enumerate(children_dirs)}
            if self._recursive:
                pending = len(children_dirs) + len(file_paths)
            else:
                pending = len(file_paths)
            node = DirNode(
                uri=dir_uri,
                children_dirs=children_dirs,
                file_paths=file_paths,
                file_index=file_index,
                child_index=child_index,
                file_summaries=[None] * len(file_paths),
                children_abstracts=[None] * len(children_dirs),
                pending=pending,
                dispatched=True,
            )
            self._nodes[dir_uri] = node
            self._stats.total_nodes += 1
            self._stats.pending_nodes += 1

            if pending == 0:
                self._schedule_overview(dir_uri)
                return

            for file_path in file_paths:
                self._stats.total_nodes += 1
                # File nodes are scheduled immediately: pending -> in_progress.
                self._stats.pending_nodes += 1
                self._stats.pending_nodes = max(0, self._stats.pending_nodes - 1)
                self._stats.in_progress_nodes += 1
                asyncio.create_task(self._file_summary_task(dir_uri, file_path))

            if children_dirs:
                if self._recursive:
                    for child_uri in children_dirs:
                        asyncio.create_task(self._dispatch_dir(child_uri, dir_uri))
        except Exception as e:
            logger.error(f"Failed to dispatch directory {dir_uri}: {e}", exc_info=True)
            if parent_uri:
                await self._on_child_done(parent_uri, dir_uri, "")
            elif self._root_done:
                self._root_done.set()

    async def _list_dir(self, uri: str) -> tuple[list[str], list[str]]:
        """List directory entries and return (child_dirs, file_paths)."""
        try:
            entries = await self._viking_fs.ls(uri, ctx=self._ctx)
        except Exception as e:
            logger.warning(f"Failed to list directory {uri}: {e}")
            return [], []

        children_dirs: List[str] = []
        file_paths: List[str] = []

        for entry in entries:
            name = entry.get("name", "")
            if not name or name.startswith(".") or name in [".", ".."]:
                continue

            item_uri = VikingURI(uri).join(name).uri
            if entry.get("isDir", False):
                children_dirs.append(item_uri)
            else:
                file_paths.append(item_uri)

        return children_dirs, file_paths

    def _get_target_file_path(self, current_uri: str) -> Optional[str]:
        if not self._incremental_update or not self._target_uri or not self._root_uri:
            logger.warning(
                f"Invalid target_uri or root_uri for incremental update: target_uri={self._target_uri}, root_uri={self._root_uri}"
            )
            return None
        try:
            relative_path = current_uri[len(self._root_uri) :]
            if relative_path.startswith("/"):
                relative_path = relative_path[1:]
            return f"{self._target_uri}/{relative_path}" if relative_path else self._target_uri
        except Exception:
            return None

    async def _check_file_content_changed(self, file_path: str) -> bool:
        target_path = self._get_target_file_path(file_path)
        if not target_path:
            return True
        try:
            current_content = await self._viking_fs.read_file(file_path, ctx=self._ctx)
            target_content = await self._viking_fs.read_file(target_path, ctx=self._ctx)
            return current_content != target_content
        except Exception:
            return True

    async def _read_existing_summary(self, file_path: str) -> Optional[Dict[str, str]]:
        target_path = self._get_target_file_path(file_path)
        if not target_path:
            return None
        try:
            vector_store = self._viking_fs._get_vector_store()
            if not vector_store:
                return None
            records = await vector_store.get_context_by_uri(
                account_id=self._ctx.account_id,
                uri=target_path,
                limit=1,
            )
            if records and len(records) > 0:
                record = records[0]
                summary = record.get("abstract", "")
                if summary:
                    file_name = file_path.split("/")[-1]
                    return {"name": file_name, "summary": summary}
        except Exception:
            pass
        return None

    async def _check_dir_children_changed(
        self, dir_uri: str, current_files: List[str], current_dirs: List[str]
    ) -> bool:
        target_path = self._get_target_file_path(dir_uri)
        if not target_path:
            return True
        try:
            target_dirs, target_files = await self._list_dir(target_path)
            current_file_names = {f.split("/")[-1] for f in current_files}
            target_file_names = {f.split("/")[-1] for f in target_files}
            if current_file_names != target_file_names:
                return True
            current_dir_names = {d.split("/")[-1] for d in current_dirs}
            target_dir_names = {d.split("/")[-1] for d in target_dirs}
            if current_dir_names != target_dir_names:
                return True
            for current_file in current_files:
                if await self._check_file_content_changed(current_file):
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
            return overview, abstract
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

                if not content_changed:
                    summary_dict = await self._read_existing_summary(file_path)
                    need_vectorize = False
            if summary_dict is None:
                summary_dict = await self._processor._generate_single_file_summary(
                    file_path, llm_sem=self._llm_sem, ctx=self._ctx
                )
        except Exception as e:
            logger.warning(f"Failed to generate summary for {file_path}: {e}")
            summary_dict = {"name": file_name, "summary": ""}
        finally:
            self._stats.done_nodes += 1
            self._stats.in_progress_nodes = max(0, self._stats.in_progress_nodes - 1)

        try:
            if need_vectorize:
                asyncio.create_task(
                    self._processor._vectorize_single_file(
                        parent_uri=parent_uri,
                        context_type=self._context_type,
                        file_path=file_path,
                        summary_dict=summary_dict,
                        ctx=self._ctx,
                        semantic_msg_id=self._semantic_msg_id,
                    )
                )
        except Exception as e:
            logger.error(f"Failed to schedule vectorization for {file_path}: {e}", exc_info=True)
        await self._on_file_done(parent_uri, file_path, summary_dict)

    async def _on_file_done(
        self, parent_uri: str, file_path: str, summary_dict: Dict[str, str]
    ) -> None:
        node = self._nodes.get(parent_uri)
        if not node:
            return

        async with node.lock:
            idx = node.file_index.get(file_path)
            if idx is not None:
                node.file_summaries[idx] = summary_dict
            node.pending -= 1
            if node.pending == 0 and not node.overview_scheduled:
                node.overview_scheduled = True
                self._stats.pending_nodes = max(0, self._stats.pending_nodes - 1)
                self._stats.in_progress_nodes += 1
                asyncio.create_task(self._overview_task(parent_uri))

    async def _on_child_done(self, parent_uri: str, child_uri: str, abstract: str) -> None:
        node = self._nodes.get(parent_uri)
        if not node:
            return

        child_name = child_uri.split("/")[-1]
        async with node.lock:
            idx = node.child_index.get(child_uri)
            if idx is not None:
                node.children_abstracts[idx] = {"name": child_name, "abstract": abstract}
            node.pending -= 1
            if node.pending == 0 and not node.overview_scheduled:
                node.overview_scheduled = True
                self._stats.pending_nodes = max(0, self._stats.pending_nodes - 1)
                self._stats.in_progress_nodes += 1
                asyncio.create_task(self._overview_task(parent_uri))

    def _schedule_overview(self, dir_uri: str) -> None:
        node = self._nodes.get(dir_uri)
        if not node:
            return
        if node.overview_scheduled:
            return
        node.overview_scheduled = True
        self._stats.pending_nodes = max(0, self._stats.pending_nodes - 1)
        self._stats.in_progress_nodes += 1
        asyncio.create_task(self._overview_task(dir_uri))

    def _finalize_file_summaries(self, node: DirNode) -> List[Dict[str, str]]:
        summaries: List[Dict[str, str]] = []
        for idx, file_path in enumerate(node.file_paths):
            item = node.file_summaries[idx]
            if item is None:
                summaries.append({"name": file_path.split("/")[-1], "summary": ""})
            else:
                summaries.append(item)
        return summaries

    def _finalize_children_abstracts(self, node: DirNode) -> List[Dict[str, str]]:
        results: List[Dict[str, str]] = []
        for idx, child_uri in enumerate(node.children_dirs):
            item = node.children_abstracts[idx]
            if item is None:
                results.append({"name": child_uri.split("/")[-1], "abstract": ""})
            else:
                results.append(item)
        return results

    async def _overview_task(self, dir_uri: str) -> None:
        node = self._nodes.get(dir_uri)
        if not node:
            return
        need_vectorize = True
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
            if overview is None or abstract is None:
                async with node.lock:
                    file_summaries = self._finalize_file_summaries(node)
                    children_abstracts = self._finalize_children_abstracts(node)
                async with self._llm_sem:
                    overview = await self._processor._generate_overview(
                        dir_uri, file_summaries, children_abstracts
                    )
                abstract = self._processor._extract_abstract_from_overview(overview)

            try:
                await self._viking_fs.write_file(f"{dir_uri}/.overview.md", overview, ctx=self._ctx)
                await self._viking_fs.write_file(f"{dir_uri}/.abstract.md", abstract, ctx=self._ctx)
            except Exception as e:
                logger.warning(f"Failed to write overview/abstract for {dir_uri}: {e}")

            try:
                if need_vectorize:
                    asyncio.create_task(
                        self._processor._vectorize_directory(
                            dir_uri,
                            self._context_type,
                            abstract,
                            overview,
                            ctx=self._ctx,
                            semantic_msg_id=self._semantic_msg_id,
                        )
                    )
            except Exception as e:
                logger.error(f"Failed to schedule vectorization for {dir_uri}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Failed to generate overview for {dir_uri}: {e}", exc_info=True)
            abstract = ""
        finally:
            self._stats.done_nodes += 1
            self._stats.in_progress_nodes = max(0, self._stats.in_progress_nodes - 1)

        parent_uri = self._parent.get(dir_uri)
        if parent_uri is None:
            if self._root_done:
                self._root_done.set()
            return

        await self._on_child_done(parent_uri, dir_uri, abstract)

    def get_stats(self) -> DagStats:
        return DagStats(
            total_nodes=self._stats.total_nodes,
            pending_nodes=self._stats.pending_nodes,
            in_progress_nodes=self._stats.in_progress_nodes,
            done_nodes=self._stats.done_nodes,
        )


if False:  # pragma: no cover - for type checkers only
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor
