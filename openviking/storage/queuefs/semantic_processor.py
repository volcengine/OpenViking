# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""SemanticProcessor: Processes messages from SemanticQueue, generates .abstract.md and .overview.md."""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

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
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import VikingURI
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

from .embedding_tracker import EmbeddingTaskTracker

logger = get_logger(__name__)


@dataclass
class DiffResult:
    """Directory diff result for sync operations."""

    added_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    updated_files: List[str] = field(default_factory=list)
    added_dirs: List[str] = field(default_factory=list)
    deleted_dirs: List[str] = field(default_factory=list)


class SemanticProcessor(DequeueHandlerBase):
    """
    Semantic processor, generates .abstract.md and .overview.md bottom-up.

    Processing flow:
    1. Concurrently generate summaries for files in directory
    2. Collect .abstract.md from subdirectories
    3. Generate .abstract.md and .overview.md for this directory
    4. Enqueue to EmbeddingQueue for vectorization
    """

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
            current_content = await viking_fs.read_file(file_path, ctx=ctx)
            target_content = await viking_fs.read_file(target_file, ctx=ctx)
            return current_content != target_content
        except Exception:
            return True

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Process dequeued SemanticMsg, recursively process all subdirectories."""
        msg = None
        try:
            import json

            if not data:
                return None

            if "data" in data and isinstance(data["data"], str):
                data = json.loads(data["data"])

            # data is guaranteed to be not None at this point
            assert data is not None
            msg = SemanticMsg.from_dict(data)
            self._current_msg = msg
            self._current_ctx = self._ctx_from_semantic_msg(msg)
            logger.info(f"Processing semantic generation for: {msg})")

            # Check if target_uri exists, auto-detect incremental update
            is_incremental = False
            viking_fs = get_viking_fs()
            if msg.target_uri:
                target_exists = await viking_fs.exists(msg.target_uri, ctx=self._current_ctx)
                if target_exists:
                    is_incremental = True
                    logger.info(f"Target URI exists, using incremental update: {msg.target_uri}")

            tracker = EmbeddingTaskTracker.get_instance()
            on_complete = self._create_sync_diff_callback(
                root_uri=msg.uri,
                target_uri=msg.target_uri,
                ctx=self._current_ctx,
            )
            # Register task with tracker, total_count=1 for root URI
            await tracker.register(
                semantic_msg_id=msg.id,
                total_count=1,
                on_complete=on_complete,
                metadata={
                    "uri": msg.uri,
                },
            )
            executor = SemanticDagExecutor(
                processor=self,
                context_type=msg.context_type,
                max_concurrent_llm=self.max_concurrent_llm,
                ctx=self._current_ctx,
                incremental_update=is_incremental,
                target_uri=msg.target_uri,
                semantic_msg_id=msg.id,
                recursive=msg.recursive,
            )
            self._dag_executor = executor
            await executor.run(msg.uri)
            logger.info(f"Completed semantic generation for: {msg.uri}")
            self.report_success()
            return None

        except Exception as e:
            logger.error(f"Failed to process semantic message: {e}", exc_info=True)
            self.report_error(str(e), data)
            return None
        finally:
            # Decrement task counter for root URI
            if msg is not None:
                tracker = EmbeddingTaskTracker.get_instance()
                await tracker.decrement(
                    semantic_msg_id=msg.id,
                )
            self._current_msg = None
            self._current_ctx = None

    def get_dag_stats(self) -> Optional["DagStats"]:
        if not self._dag_executor:
            return None
        return self._dag_executor.get_stats()

    def _create_sync_diff_callback(
        self,
        root_uri: str,
        target_uri: str,
        ctx: RequestContext,
    ) -> Callable[[], Awaitable[None]]:
        """
        Create a callback function to sync directory differences.

        This callback compares root_uri (new content) with target_uri (old content),
        handles added/updated/deleted files, then cleans up root_uri.

        Args:
            root_uri: Source directory URI (new content)
            target_uri: Target directory URI (old content)
            ctx: Request context (captured at callback creation time)

        Returns:
            Async callback function
        """

        async def sync_diff_callback() -> None:

            try:
                viking_fs = get_viking_fs()

                root_tree = await self._collect_tree_info(root_uri, ctx=ctx)

                target_tree = await self._collect_tree_info(target_uri, ctx=ctx)
                diff = await self._compute_diff(
                    root_tree, target_tree, root_uri, target_uri, ctx=ctx
                )
                logger.info(
                    f"[SyncDiff] Diff computed: "
                    f"added_files={len(diff.added_files)}, "
                    f"deleted_files={len(diff.deleted_files)}, "
                    f"updated_files={len(diff.updated_files)}, "
                    f"added_dirs={len(diff.added_dirs)}, "
                    f"deleted_dirs={len(diff.deleted_dirs)}"
                )
                await self._execute_sync_operations(diff, root_uri, target_uri, ctx=ctx)
                try:
                    await viking_fs.rm(root_uri, recursive=True, ctx=ctx)
                except Exception as e:
                    logger.warning(f"[SyncDiff] Failed to delete root directory {root_uri}: {e}")

            except Exception as e:
                logger.error(
                    f"[SyncDiff] Error in sync_diff_callback: "
                    f"root_uri={root_uri}, target_uri={target_uri} "
                    f"error={e}",
                    exc_info=True,
                )

        return sync_diff_callback

    async def _collect_tree_info(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Tuple[List[str], List[str]]]:
        """
        Recursively collect directory tree information.

        Args:
            uri: Directory URI
            ctx: Request context

        Returns:
            Dictionary: {dir_uri: ([subdir_uris], [file_uris])}
        """
        viking_fs = get_viking_fs()
        result: Dict[str, Tuple[List[str], List[str]]] = {}
        total_dirs = 0
        total_files = 0

        async def collect_recursive(current_uri: str, depth: int = 0) -> None:
            nonlocal total_dirs, total_files
            indent = "  " * depth
            try:
                entries = await viking_fs.ls(current_uri, show_all_hidden=True, ctx=ctx)
            except Exception as e:
                logger.warning(f"[SyncDiff]{indent} Failed to list {current_uri}: {e}")
                return

            sub_dirs: List[str] = []
            files: List[str] = []

            for entry in entries:
                name = entry.get("name", "")
                if not name or name in [".", ".."]:
                    continue
                if name.startswith(".") and name not in [".abstract.md", ".overview.md"]:
                    continue

                item_uri = VikingURI(current_uri).join(name).uri

                if entry.get("isDir", False):
                    sub_dirs.append(item_uri)
                    total_dirs += 1
                    await collect_recursive(item_uri, depth + 1)
                else:
                    files.append(item_uri)
                    total_files += 1

            result[current_uri] = (sub_dirs, files)

        await collect_recursive(uri)
        return result

    async def _compute_diff(
        self,
        root_tree: Dict[str, Tuple[List[str], List[str]]],
        target_tree: Dict[str, Tuple[List[str], List[str]]],
        root_uri: str,
        target_uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> DiffResult:
        """
        Compute differences between two directory trees.

        Args:
            root_tree: Directory tree from root_uri
            target_tree: Directory tree from target_uri
            root_uri: Source directory URI
            target_uri: Target directory URI
            ctx: Request context

        Returns:
            DiffResult with added/deleted/updated files and directories
        """

        def get_relative_path(uri: str, base_uri: str) -> str:
            if uri.startswith(base_uri):
                rel = uri[len(base_uri) :]
                return rel.lstrip("/")
            return uri

        root_files: Set[str] = set()
        root_dirs: Set[str] = set()
        target_files: Set[str] = set()
        target_dirs: Set[str] = set()

        for dir_uri, (sub_dirs, files) in root_tree.items():
            rel_dir = get_relative_path(dir_uri, root_uri)
            if rel_dir:
                root_dirs.add(rel_dir)
            for f in files:
                root_files.add(get_relative_path(f, root_uri))
            for d in sub_dirs:
                root_dirs.add(get_relative_path(d, root_uri))

        for dir_uri, (sub_dirs, files) in target_tree.items():
            rel_dir = get_relative_path(dir_uri, target_uri)
            if rel_dir:
                target_dirs.add(rel_dir)
            for f in files:
                target_files.add(get_relative_path(f, target_uri))
            for d in sub_dirs:
                target_dirs.add(get_relative_path(d, target_uri))

        added_files_rel = root_files - target_files
        deleted_files_rel = target_files - root_files
        common_files = root_files & target_files

        added_dirs_rel = root_dirs - target_dirs
        deleted_dirs_rel = target_dirs - root_dirs

        updated_files: List[str] = []
        for rel_file in common_files:
            root_file = f"{root_uri}/{rel_file}"
            target_file = f"{target_uri}/{rel_file}"
            try:
                if await self._check_file_content_changed(root_file, target_file, ctx=ctx):
                    updated_files.append(root_file)
            except Exception as e:
                logger.warning(
                    f"[SyncDiff] Failed to compare file content for {rel_file}: {e}, "
                    f"treating as unchanged"
                )

        added_files = [f"{root_uri}/{f}" for f in added_files_rel]
        deleted_files = [f"{target_uri}/{f}" for f in deleted_files_rel]
        added_dirs = [f"{root_uri}/{d}" for d in added_dirs_rel]
        deleted_dirs = [f"{target_uri}/{d}" for d in deleted_dirs_rel]

        result = DiffResult(
            added_files=added_files,
            deleted_files=deleted_files,
            updated_files=updated_files,
            added_dirs=added_dirs,
            deleted_dirs=deleted_dirs,
        )

        return result

    async def _execute_sync_operations(
        self,
        diff: DiffResult,
        root_uri: str,
        target_uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """
        Execute sync operations based on diff result.

        Processing order:
        1. Delete files in target that don't exist in root
        2. Move added/updated files from root to target
        3. Delete directories in target that don't exist in root

        Args:
            diff: DiffResult containing operations to perform
            root_uri: Source directory URI
            target_uri: Target directory URI
            ctx: Request context
        """
        viking_fs = get_viking_fs()

        def map_to_target(root_item_uri: str) -> str:
            if root_item_uri.startswith(root_uri):
                rel = root_item_uri[len(root_uri) :]
                return f"{target_uri}{rel}" if rel else target_uri
            return root_item_uri

        total_deleted = 0
        total_moved = 0
        total_failed = 0

        for i, deleted_file in enumerate(diff.deleted_files, 1):
            try:
                await viking_fs.rm(deleted_file, ctx=ctx)
                total_deleted += 1
            except Exception as e:
                total_failed += 1
                logger.warning(
                    f"[SyncDiff] Failed to delete file [{i}/{len(diff.deleted_files)}]: {deleted_file}, error={e}"
                )

        for i, updated_file in enumerate(diff.updated_files, 1):
            target_file = map_to_target(updated_file)
            try:
                await viking_fs.rm(target_file, ctx=ctx)
            except Exception as e:
                logger.warning(
                    f"[SyncDiff] Failed to remove old file [{i}/{len(diff.updated_files)}]: {target_file}, error={e}"
                )

        files_to_move = diff.added_files + diff.updated_files
        for i, root_file in enumerate(files_to_move, 1):
            target_file = map_to_target(root_file)
            try:
                target_parent = VikingURI(target_file).parent
                if target_parent:
                    try:
                        await viking_fs.mkdir(target_parent.uri, exist_ok=True, ctx=ctx)
                    except Exception as mkdir_error:
                        logger.debug(
                            f"[SyncDiff] Parent dir creation skipped (may already exist): {mkdir_error}"
                        )
                await viking_fs.mv(root_file, target_file, ctx=ctx)
                total_moved += 1
            except Exception as e:
                total_failed += 1
                logger.warning(
                    f"[SyncDiff] Failed to move file [{i}/{len(files_to_move)}]: "
                    f"{root_file} -> {target_file}, error={e}"
                )

        for i, deleted_dir in enumerate(
            sorted(diff.deleted_dirs, key=lambda x: x.count("/"), reverse=True), 1
        ):
            try:
                await viking_fs.rm(deleted_dir, recursive=True, ctx=ctx)
            except Exception as e:
                total_failed += 1
                logger.warning(
                    f"[SyncDiff] Failed to delete directory [{i}/{len(diff.deleted_dirs)}]: "
                    f"{deleted_dir}, error={e}"
                )

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

        # Read file content (limit length)
        content = await viking_fs.read_file(file_path, ctx=active_ctx)

        # Limit content length (about 10000 tokens)
        max_chars = 30000
        content = await viking_fs.read_file(file_path, ctx=active_ctx)
        if isinstance(content, bytes):
            # Try to decode with error handling for text files
            try:
                content = content.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning(f"Failed to decode file as UTF-8, skipping: {file_path}")
                return {"name": file_name, "summary": ""}

        # Limit content length (about 10000 tokens)
        max_chars = 30000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n...(truncated)"

        # Generate summary
        if not vlm.is_available():
            logger.warning("VLM not available, using empty summary")
            return {"name": file_name, "summary": ""}

        # Detect file type and select appropriate prompt
        file_type = self._detect_file_type(file_name)

        if file_type == FILE_TYPE_CODE:
            code_mode = get_openviking_config().code.code_summary_mode

            if code_mode in ("ast", "ast_llm") and len(content.splitlines()) >= 100:
                from openviking.parse.parsers.code.ast import extract_skeleton

                verbose = code_mode == "ast_llm"
                skeleton_text = extract_skeleton(file_name, content, verbose=verbose)
                if skeleton_text:
                    if code_mode == "ast":
                        return {"name": file_name, "summary": skeleton_text}
                    else:  # ast_llm
                        prompt = render_prompt(
                            "semantic.code_ast_summary",
                            {"file_name": file_name, "skeleton": skeleton_text},
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
                {"file_name": file_name, "content": content},
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
            {"file_name": file_name, "content": content},
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

    async def _generate_overview(
        self,
        dir_uri: str,
        file_summaries: List[Dict[str, str]],
        children_abstracts: List[Dict[str, str]],
    ) -> str:
        """Generate directory's .overview.md (L1).

        Args:
            dir_uri: Directory URI
            file_summaries: File summary list
            children_abstracts: Subdirectory summary list

        Returns:
            Overview content
        """
        import re

        vlm = get_openviking_config().vlm

        if not vlm.is_available():
            logger.warning("VLM not available, using default overview")
            return f"# {dir_uri.split('/')[-1]}\n\nDirectory overview"

        # Build file index mapping and summary string
        file_index_map = {}
        file_summaries_lines = []
        for idx, item in enumerate(file_summaries, 1):
            file_index_map[idx] = item["name"]
            file_summaries_lines.append(f"[{idx}] {item['name']}: {item['summary']}")
        file_summaries_str = "\n".join(file_summaries_lines) if file_summaries_lines else "None"

        # Build subdirectory summary string
        children_abstracts_str = (
            "\n".join(f"- {item['name']}/: {item['abstract']}" for item in children_abstracts)
            if children_abstracts
            else "None"
        )

        # Generate overview
        try:
            prompt = render_prompt(
                "semantic.overview_generation",
                {
                    "dir_name": dir_uri.split("/")[-1],
                    "file_summaries": file_summaries_str,
                    "children_abstracts": children_abstracts_str,
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
            logger.error(f"Failed to generate overview for {dir_uri}: {e}", exc_info=True)
            return f"# {dir_uri.split('/')[-1]}\n\nDirectory overview"

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

        tracker = EmbeddingTaskTracker.get_instance()
        # Increment task for .abstract.md
        await tracker.increment(semantic_msg_id=semantic_msg_id)
        # Increment task for .overview.md
        await tracker.increment(semantic_msg_id=semantic_msg_id)

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
    ) -> None:
        """Vectorize a single file using its content or summary."""
        from openviking.utils.embedding_utils import vectorize_file

        tracker = EmbeddingTaskTracker.get_instance()
        await tracker.increment(
            semantic_msg_id=semantic_msg_id,
        )
        active_ctx = ctx or self._current_ctx
        await vectorize_file(
            file_path=file_path,
            summary_dict=summary_dict,
            parent_uri=parent_uri,
            context_type=context_type,
            ctx=active_ctx,
            semantic_msg_id=semantic_msg_id,
        )
