# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Embedding utilities for OpenViking.

Common logic for creating Context objects and enqueuing them to EmbeddingQueue.
"""

import os
from datetime import datetime
from typing import Dict, Optional

from openviking.core.context import Context, ContextLevel, ResourceContentType, Vectorize
from openviking.server.identity import RequestContext
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


async def _decrement_embedding_tracker(semantic_msg_id: Optional[str], count: int) -> None:
    if not semantic_msg_id or count <= 0:
        return
    try:
        from openviking.storage.queuefs.embedding_tracker import EmbeddingTaskTracker

        tracker = EmbeddingTaskTracker.get_instance()
        for _ in range(count):
            await tracker.decrement(semantic_msg_id)
    except Exception as e:
        logger.error(
            f"Failed to decrement embedding tracker for semantic_msg_id={semantic_msg_id}: {e}",
            exc_info=True,
        )


def _owner_space_for_uri(uri: str, ctx: RequestContext) -> str:
    """Derive owner_space from a URI."""
    if uri.startswith("viking://agent/"):
        return ctx.user.agent_space_name()
    if uri.startswith("viking://user/") or uri.startswith("viking://session/"):
        return ctx.user.user_space_name()
    return ""


def get_resource_content_type(file_name: str) -> Optional[ResourceContentType]:
    """Determine resource content type based on file extension.

    Returns None if the file type is not recognized.
    """
    file_name = file_name.lower()

    text_extensions = {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".xml",
        ".py",
        ".js",
        ".ts",
        ".java",
        ".cpp",
        ".c",
        ".h",
        ".go",
        ".rs",
        ".lua",
        ".rb",
        ".php",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".sql",
        ".kt",
        ".swift",
        ".scala",
        ".r",
        ".m",
        ".pl",
        ".toml",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".conf",
        ".tsx",
        ".jsx",
        ".cs",
        ".env",
        ".properties",
        ".rst",
        ".tf",
        ".proto",
        ".gradle",
        ".cc",
        ".cxx",
        ".hpp",
        ".hh",
        ".dart",
        ".vue",
        ".groovy",
        ".ps1",
        ".ex",
        ".exs",
        ".erl",
        ".jl",
        ".mm",
    }
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"}
    video_extensions = {".mp4", ".avi", ".mov", ".wmv", ".flv"}
    audio_extensions = {".mp3", ".wav", ".aac", ".flac"}

    if any(file_name.endswith(ext) for ext in text_extensions):
        return ResourceContentType.TEXT
    elif any(file_name.endswith(ext) for ext in image_extensions):
        return ResourceContentType.IMAGE
    elif any(file_name.endswith(ext) for ext in video_extensions):
        return ResourceContentType.VIDEO
    elif any(file_name.endswith(ext) for ext in audio_extensions):
        return ResourceContentType.AUDIO

    return None


async def vectorize_directory_meta(
    uri: str,
    abstract: str,
    overview: str,
    context_type: str = "resource",
    ctx: Optional[RequestContext] = None,
    semantic_msg_id: Optional[str] = None,
) -> None:
    """
    Vectorize directory metadata (.abstract.md and .overview.md).

    Creates Context objects for abstract and overview and enqueues them.
    """
    enqueued = 0
    try:
        if not ctx:
            logger.warning("No context provided for vectorization")
            return

        queue_manager = get_queue_manager()
        embedding_queue = queue_manager.get_queue(queue_manager.EMBEDDING)

        parent_uri = VikingURI(uri).parent.uri
        owner_space = _owner_space_for_uri(uri, ctx)

        # Vectorize L0: .abstract.md (abstract)
        context_abstract = Context(
            uri=uri,
            parent_uri=parent_uri,
            is_leaf=False,
            abstract=abstract,
            context_type=context_type,
            level=ContextLevel.ABSTRACT,
            user=ctx.user,
            account_id=ctx.account_id,
            owner_space=owner_space,
        )
        context_abstract.set_vectorize(Vectorize(text=abstract))
        msg_abstract = EmbeddingMsgConverter.from_context(context_abstract)
        if msg_abstract:
            msg_abstract.semantic_msg_id = semantic_msg_id
            try:
                await embedding_queue.enqueue(msg_abstract)
                enqueued += 1
                logger.debug(f"Enqueued directory L0 (abstract) for vectorization: {uri}")
            except Exception as e:
                logger.error(
                    f"Failed to enqueue directory L0 (abstract) for vectorization: {uri}: {e}",
                    exc_info=True,
                )

        # Vectorize L1: .overview.md (overview)
        context_overview = Context(
            uri=uri,
            parent_uri=parent_uri,
            is_leaf=False,
            abstract=abstract,
            context_type=context_type,
            level=ContextLevel.OVERVIEW,
            user=ctx.user,
            account_id=ctx.account_id,
            owner_space=owner_space,
        )
        context_overview.set_vectorize(Vectorize(text=overview))
        msg_overview = EmbeddingMsgConverter.from_context(context_overview)
        if msg_overview:
            msg_overview.semantic_msg_id = semantic_msg_id
            try:
                await embedding_queue.enqueue(msg_overview)
                enqueued += 1
                logger.debug(f"Enqueued directory L1 (overview) for vectorization: {uri}")
            except Exception as e:
                logger.error(
                    f"Failed to enqueue directory L1 (overview) for vectorization: {uri}: {e}",
                    exc_info=True,
                )
    finally:
        await _decrement_embedding_tracker(semantic_msg_id, 2 - enqueued)


async def vectorize_file(
    file_path: str,
    summary_dict: Dict[str, str],
    parent_uri: str,
    context_type: str = "resource",
    ctx: Optional[RequestContext] = None,
    semantic_msg_id: Optional[str] = None,
    use_summary: bool = False,
) -> None:
    """
    Vectorize a single file.

    Creates Context object for the file and enqueues it.
    The effective vectorization strategy is resolved once from either the explicit
    `use_summary` flag (code path override) or the embedding config.
    """
    enqueued = False

    try:
        if not ctx:
            logger.warning("No context provided for vectorization")
            return

        queue_manager = get_queue_manager()
        embedding_queue = queue_manager.get_queue(queue_manager.EMBEDDING)
        viking_fs = get_viking_fs()

        file_name = summary_dict.get("name") or os.path.basename(file_path)
        summary = summary_dict.get("summary", "")

        context = Context(
            uri=file_path,
            parent_uri=parent_uri,
            is_leaf=True,
            abstract=summary,
            context_type=context_type,
            created_at=datetime.now(),
            user=ctx.user,
            account_id=ctx.account_id,
            owner_space=_owner_space_for_uri(file_path, ctx),
        )

        content_type = get_resource_content_type(file_name)
        embedding_cfg = get_openviking_config().embedding
        configured_text_source = getattr(embedding_cfg, "text_source", "summary_first")
        effective_text_source = "summary_only" if use_summary else configured_text_source
        max_input_chars = int(getattr(embedding_cfg, "max_input_chars", 1000) or 1000)

        def _truncate_text(value: str) -> str:
            if len(value) <= max_input_chars:
                return value
            return value[:max_input_chars] + "\n...(truncated for embedding)"

        if content_type is None:
            # Unsupported file type: fall back to summary if available
            if summary:
                logger.warning(
                    f"Unsupported file type for {file_path}, falling back to summary for vectorization"
                )
                context.set_vectorize(Vectorize(text=summary))
            else:
                logger.warning(
                    f"Unsupported file type for {file_path} and no summary available, skipping vectorization"
                )
                return
        elif content_type == ResourceContentType.TEXT:
            if summary and effective_text_source in {"summary_first", "summary_only"}:
                context.set_vectorize(Vectorize(text=summary))
            else:
                # Read raw file content and apply configured truncation guard.
                try:
                    content = await viking_fs.read_file(file_path, ctx=ctx)
                    if isinstance(content, bytes):
                        content = content.decode("utf-8", errors="replace")
                    content = _truncate_text(content)
                    context.set_vectorize(Vectorize(text=content))
                except Exception as e:
                    logger.warning(
                        f"Failed to read file content for {file_path}, falling back to summary: {e}"
                    )
                    if summary:
                        context.set_vectorize(Vectorize(text=summary))
                    else:
                        logger.warning(
                            f"No summary available for {file_path}, skipping vectorization"
                        )
                        return
        elif summary:
            # For non-text files, use summary
            context.set_vectorize(Vectorize(text=summary))
        else:
            logger.debug(f"Skipping file {file_path} (no text content or summary)")
            return

        embedding_msg = EmbeddingMsgConverter.from_context(context)
        if not embedding_msg:
            return

        embedding_msg.semantic_msg_id = semantic_msg_id
        await embedding_queue.enqueue(embedding_msg)
        enqueued = True
        logger.debug(f"Enqueued file for vectorization: {file_path}")

    except Exception as e:
        logger.error(f"Failed to vectorize file {file_path}: {e}", exc_info=True)
    finally:
        if not enqueued:
            await _decrement_embedding_tracker(semantic_msg_id, 1)


async def index_resource(
    uri: str,
    ctx: RequestContext,
) -> None:
    """
    Build vector index for a resource directory.

    1. Reads .abstract.md and .overview.md and vectorizes them.
    2. Scans files in the directory and vectorizes them.
    """
    viking_fs = get_viking_fs()

    # 1. Index Directory Metadata
    abstract_uri = f"{uri}/.abstract.md"
    overview_uri = f"{uri}/.overview.md"

    abstract = ""
    overview = ""

    if await viking_fs.exists(abstract_uri, ctx=ctx):
        content = await viking_fs.read_file(abstract_uri, ctx=ctx)
        if isinstance(content, bytes):
            abstract = content.decode("utf-8")

    if await viking_fs.exists(overview_uri, ctx=ctx):
        content = await viking_fs.read_file(overview_uri, ctx=ctx)
        if isinstance(content, bytes):
            overview = content.decode("utf-8")

    if abstract or overview:
        await vectorize_directory_meta(uri, abstract, overview, ctx=ctx)

    # 2. Index Files
    try:
        files = await viking_fs.ls(uri, ctx=ctx)
        for file_info in files:
            file_name = file_info["name"]

            # Skip hidden files (like .abstract.md)
            if file_name.startswith("."):
                continue

            if file_info.get("type") == "directory" or file_info.get("isDir"):
                # TODO: Recursive indexing? For now, skip subdirectories to match previous behavior
                continue

            file_uri = file_info.get("uri") or f"{uri}/{file_name}"

            # For direct indexing, we might not have summaries.
            # We pass empty summary_dict, vectorize_file will try to read content for text files.
            await vectorize_file(
                file_path=file_uri, summary_dict={"name": file_name}, parent_uri=uri, ctx=ctx
            )

    except Exception as e:
        logger.error(f"Failed to scan directory {uri} for indexing: {e}")
