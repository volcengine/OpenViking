# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""SemanticProcessor: Processes messages from SemanticQueue, generates .abstract.md and .overview.md."""

import asyncio
from typing import Any, Dict, List, Tuple

from openviking.core.context import Context, ResourceContentType, Vectorize
from openviking.prompts import render_prompt
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils import VikingURI
from openviking.utils.config import get_openviking_config
from openviking.utils.logger import get_logger

logger = get_logger(__name__)


class SemanticProcessor(DequeueHandlerBase):
    """
    Semantic processor, generates .abstract.md and .overview.md bottom-up.

    Processing flow:
    1. Concurrently generate summaries for files in directory
    2. Collect .abstract.md from subdirectories
    3. Generate .abstract.md and .overview.md for this directory
    4. Enqueue to EmbeddingQueue for vectorization
    """

    def __init__(self, max_concurrent_llm: int = 10):
        """
        Initialize SemanticProcessor.

        Args:
            max_concurrent_llm: Maximum concurrent LLM calls
        """
        self.max_concurrent_llm = max_concurrent_llm

    async def _collect_directory_info(
        self,
        uri: str,
        result: List[Tuple[str, List[str], List[str]]],
    ) -> None:
        """Recursively collect directory info, post-order traversal ensures bottom-up order."""
        viking_fs = get_viking_fs()

        try:
            entries = await viking_fs.ls(uri)
        except Exception as e:
            logger.warning(f"Failed to list directory {uri}: {e}")
            return

        children_uris = []
        file_paths = []

        for entry in entries:
            name = entry.get("name", "")
            if not name or name.startswith(".") or name in [".", ".."]:
                continue

            item_uri = VikingURI(uri).join(name).uri

            if entry.get("isDir", False):
                # Child directory
                children_uris.append(item_uri)
                # Recursively collect children
                await self._collect_directory_info(item_uri, result)
            else:
                # File (not starting with .)
                file_paths.append(item_uri)

        # Add current directory info
        result.append((uri, children_uris, file_paths))

    async def on_dequeue(self, data: Dict[str, Any]) -> None:
        """Process dequeued SemanticMsg, recursively process all subdirectories."""
        try:
            import json

            if "data" in data and isinstance(data["data"], str):
                data = json.loads(data["data"])

            msg = SemanticMsg.from_dict(data)
            logger.info(f"Processing semantic generation for: {msg.uri}")

            # Collect all directory info (bottom-up order)
            dir_info_list: List[Tuple[str, List[str], List[str]]] = []
            await self._collect_directory_info(msg.uri, dir_info_list)

            # Process each directory in order (leaves first)
            for uri, children_uris, file_paths in dir_info_list:
                await self._process_single_directory(
                    uri=uri,
                    context_type=msg.context_type,
                    children_uris=children_uris,
                    file_paths=file_paths,
                )

            logger.info(f"Completed semantic generation for: {msg.uri}")
            self.report_success()
            return None

        except Exception as e:
            logger.error(f"Failed to process semantic message: {e}", exc_info=True)
            self.report_error(str(e), data)
            return None

    async def _process_single_directory(
        self,
        uri: str,
        context_type: str,
        children_uris: List[str],
        file_paths: List[str],
    ) -> None:
        """Process single directory, generate .abstract.md and .overview.md."""
        viking_fs = get_viking_fs()

        # 1. Collect .abstract.md from subdirectories (already processed earlier)
        children_abstracts = await self._collect_children_abstracts(children_uris)

        # 2. Concurrently generate summaries for files in directory
        file_summaries = await self._generate_file_summaries(file_paths)

        # 3. Generate .overview.md (contains brief description)
        overview = await self._generate_overview(uri, file_summaries, children_abstracts)

        # 4. Extract abstract from overview
        abstract = self._extract_abstract_from_overview(overview)

        # 5. Write files
        await viking_fs.write_file(f"{uri}/.overview.md", overview)
        await viking_fs.write_file(f"{uri}/.abstract.md", abstract)

        logger.debug(f"Generated overview and abstract for {uri}")

        # 6. Vectorize directory
        try:
            await self._vectorize_directory_simple(uri, context_type, abstract, overview)
        except Exception as e:
            logger.error(f"Failed to vectorize directory {uri}: {e}", exc_info=True)

        # 7. Vectorize files
        # Requires retrieval refactoring before enabling
        try:
            await self._vectorize_files(uri, context_type, file_paths, file_summaries)
        except Exception as e:
            logger.error(f"Failed to vectorize files in {uri}: {e}", exc_info=True)

    async def _collect_children_abstracts(self, children_uris: List[str]) -> List[Dict[str, str]]:
        """Collect .abstract.md from subdirectories."""
        viking_fs = get_viking_fs()
        results = []

        for child_uri in children_uris:
            abstract = await viking_fs.abstract(child_uri)
            dir_name = child_uri.split("/")[-1]
            results.append({"name": dir_name, "abstract": abstract})
        return results

    async def _generate_file_summaries(self, file_paths: List[str]) -> List[Dict[str, str]]:
        """Concurrently generate file summaries."""
        if not file_paths:
            return []

        sem = asyncio.Semaphore(self.max_concurrent_llm)

        async def generate_one_summary(file_path: str) -> Dict[str, str]:
            async with sem:
                return await self._generate_single_file_summary(file_path)

        tasks = [generate_one_summary(fp) for fp in file_paths]
        return await asyncio.gather(*tasks)

    async def _generate_single_file_summary(self, file_path: str) -> Dict[str, str]:
        """Generate summary for a single file.

        Args:
            file_path: File path

        Returns:
            {"name": file_name, "summary": summary_content}
        """
        viking_fs = get_viking_fs()
        vlm = get_openviking_config().vlm
        file_name = file_path.split("/")[-1]

        try:
            # Read file content (limit length)
            content = await viking_fs.read_file(file_path)
            if isinstance(content, bytes):
                content = content.decode("utf-8")

            # Limit content length (about 10000 tokens)
            max_chars = 30000
            if len(content) > max_chars:
                content = content[:max_chars] + "\n...(truncated)"

            # Generate summary
            if not vlm.is_available():
                logger.warning("VLM not available, using empty summary")
                return {"name": file_name, "summary": ""}

            prompt = render_prompt(
                "semantic.file_summary",
                {"file_name": file_name, "content": content},
            )

            summary = await vlm.get_completion_async(prompt)
            return {"name": file_name, "summary": summary.strip()}

        except Exception as e:
            logger.warning(f"Failed to generate summary for {file_path}: {e}")
            return {"name": file_name, "summary": ""}

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

    async def _vectorize_directory_simple(
        self, uri: str, context_type: str, abstract: str, overview: str
    ) -> None:
        """Create directory Context and enqueue to EmbeddingQueue."""

        from openviking.storage.queuefs import get_queue_manager
        from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter

        parent_uri = VikingURI(uri).parent.uri
        context = Context(
            uri=uri,
            parent_uri=parent_uri,
            is_leaf=False,
            abstract=abstract,
            context_type=context_type,
        )
        context.set_vectorize(Vectorize(text=overview))

        embedding_msg = EmbeddingMsgConverter.from_context(context)
        queue_manager = get_queue_manager()
        embedding_queue = queue_manager.get_queue(queue_manager.EMBEDDING)
        await embedding_queue.enqueue(embedding_msg)
        logger.debug(f"Enqueued directory for vectorization: {uri}")

    async def _vectorize_files(
        self,
        uri: str,
        context_type: str,
        file_paths: List[str],
        file_summaries: List[Dict[str, str]],
    ) -> None:
        """Vectorize files in directory."""
        from datetime import datetime

        from openviking.core.context import Context
        from openviking.storage.queuefs import get_queue_manager
        from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter

        queue_manager = get_queue_manager()
        embedding_queue = queue_manager.get_queue(queue_manager.EMBEDDING)

        for file_path, file_summary_dict in zip(file_paths, file_summaries):
            file_name = file_summary_dict["name"]
            summary = file_summary_dict["summary"]

            context = Context(
                uri=file_path,
                parent_uri=uri,
                is_leaf=True,
                abstract=summary,
                context_type=context_type,
                created_at=datetime.now(),
            )
            if self.get_resource_content_type(file_name) == ResourceContentType.TEXT:
                content = await get_viking_fs().read_file(file_path)
                context.set_vectorize(Vectorize(text=content))
            else:
                context.set_vectorize(Vectorize(text=summary))
            embedding_msg = EmbeddingMsgConverter.from_context(context)
            await embedding_queue.enqueue(embedding_msg)
            logger.debug(f"Enqueued file for vectorization: {file_path}")

    def get_resource_content_type(self, file_name: str) -> ResourceContentType:
        def _is_image_file(file_name: str) -> bool:
            image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"}
            return any(file_name.endswith(ext) for ext in image_extensions)

        def _is_video_file(file_name: str) -> bool:
            video_extensions = {".mp4", ".avi", ".mov", ".wmv", ".flv"}
            return any(file_name.endswith(ext) for ext in video_extensions)

        def _is_text_file(file_name: str) -> bool:
            text_extensions = {".txt", ".md", ".csv", ".json", ".xml"}
            return any(file_name.endswith(ext) for ext in text_extensions)

        def _is_audio_file(file_name: str) -> bool:
            audio_extensions = {".mp3", ".wav", ".aac", ".flac"}
            return any(file_name.endswith(ext) for ext in audio_extensions)

        if _is_text_file(file_name):
            return ResourceContentType.TEXT
        elif _is_image_file(file_name):
            return ResourceContentType.IMAGE
        elif _is_video_file(file_name):
            return ResourceContentType.VIDEO
        elif _is_audio_file(file_name):
            return ResourceContentType.AUDIO

        return ResourceContentType.TEXT
