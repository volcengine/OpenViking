# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Semantic DAG executor with event-driven lazy dispatch."""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional

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

    def __init__(self, processor: "SemanticProcessor", context_type: str, max_concurrent_llm: int):
        self._processor = processor
        self._context_type = context_type
        self._max_concurrent_llm = max_concurrent_llm
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
            pending = len(children_dirs) + len(file_paths)

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
            entries = await self._viking_fs.ls(uri)
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

    async def _file_summary_task(self, parent_uri: str, file_path: str) -> None:
        """Generate file summary and notify parent completion."""
        file_name = file_path.split("/")[-1]
        try:
            summary_dict = await self._processor._generate_single_file_summary(
                file_path, llm_sem=self._llm_sem
            )
        except Exception as e:
            logger.warning(f"Failed to generate summary for {file_path}: {e}")
            summary_dict = {"name": file_name, "summary": ""}
        finally:
            self._stats.done_nodes += 1
            self._stats.in_progress_nodes = max(0, self._stats.in_progress_nodes - 1)

        await self._on_file_done(parent_uri, file_path, summary_dict)

        # Vectorize file as soon as summary is ready to avoid waiting for overview.
        try:
            asyncio.create_task(
                self._processor._vectorize_single_file(
                    parent_uri=parent_uri,
                    context_type=self._context_type,
                    file_path=file_path,
                    summary_dict=summary_dict,
                )
            )
        except Exception as e:
            logger.error(f"Failed to schedule vectorization for {file_path}: {e}", exc_info=True)

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

        async with node.lock:
            file_summaries = self._finalize_file_summaries(node)
            children_abstracts = self._finalize_children_abstracts(node)

        try:
            async with self._llm_sem:
                overview = await self._processor._generate_overview(
                    dir_uri, file_summaries, children_abstracts
                )
            abstract = self._processor._extract_abstract_from_overview(overview)

            try:
                await self._viking_fs.write_file(f"{dir_uri}/.overview.md", overview)
                await self._viking_fs.write_file(f"{dir_uri}/.abstract.md", abstract)
            except Exception as e:
                logger.warning(f"Failed to write overview/abstract for {dir_uri}: {e}")

            try:
                await self._processor._vectorize_directory_simple(
                    dir_uri, self._context_type, abstract, overview
                )
            except Exception as e:
                logger.error(f"Failed to vectorize directory {dir_uri}: {e}", exc_info=True)

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
