# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Embedding utilities for OpenViking.

Common logic for creating Context objects and enqueuing them to EmbeddingQueue.
"""

import math
import os
from datetime import datetime, timezone
from typing import Dict, Optional

from openviking.core.context import Context, ContextLevel, ResourceContentType, Vectorize
from openviking.core.directories import get_context_type_for_uri
from openviking.core.namespace import agent_space_fragment, user_space_fragment
from openviking.server.identity import RequestContext
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)
_EMBEDDING_TRUNCATION_SUFFIX = "\n...(truncated for embedding)"


def _estimate_embedding_input_tokens(text: str) -> int:
    """Estimate tokens for the raw text embedding input guard."""
    if not text:
        return 0
    cjk_chars = sum(
        1
        for char in text
        if "\u4e00" <= char <= "\u9fff"
        or "\u3040" <= char <= "\u30ff"
        or "\uac00" <= char <= "\ud7af"
    )
    other_chars = len(text) - cjk_chars
    return max(1, cjk_chars + math.ceil(other_chars / 4))


def _truncate_embedding_input(
    text: str,
    max_tokens: int,
    suffix: str = _EMBEDDING_TRUNCATION_SUFFIX,
) -> str:
    """Trim raw text before embedding using the local estimate above."""
    if not text:
        return text
    if max_tokens <= 0:
        return suffix.lstrip()
    if _estimate_embedding_input_tokens(text) <= max_tokens:
        return text

    low = 0
    high = len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _estimate_embedding_input_tokens(text[:mid]) <= max_tokens:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + suffix


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
        return agent_space_fragment(ctx)
    if uri.startswith("viking://user/") or uri.startswith("viking://session/"):
        return user_space_fragment(ctx)
    return ""


def _coerce_datetime(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return parse_iso_datetime(value)
        except Exception:
            return None
    return None


async def _get_existing_created_at(
    uri: str,
    ctx: Optional[RequestContext],
) -> Optional[datetime]:
    if ctx is None:
        return None
    try:
        from openviking.server.dependencies import get_service

        service = get_service()
        if not service or not service.vikingdb_manager:
            return None
        record = await service.vikingdb_manager.fetch_by_uri(uri, ctx=ctx)
        if not record:
            return None
        return _coerce_datetime(record.get("created_at"))
    except Exception:
        return None


async def _resolve_context_timestamps(
    uri: str,
    ctx: Optional[RequestContext],
    *,
    preserve_existing_created_at: bool = False,
) -> tuple[datetime, datetime]:
    updated_at = datetime.now(timezone.utc)
    try:
        stat_result = await get_viking_fs().stat(uri, ctx=ctx)
        stat_mod_time = _coerce_datetime((stat_result or {}).get("modTime"))
        if stat_mod_time is not None:
            updated_at = stat_mod_time
    except Exception:
        pass

    created_at = updated_at
    if preserve_existing_created_at:
        existing_created_at = await _get_existing_created_at(uri, ctx)
        if existing_created_at is not None:
            created_at = existing_created_at

    return created_at, updated_at


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
    include_overview: bool = True,
) -> None:
    """
    Vectorize directory metadata (.abstract.md and .overview.md).

    Creates Context objects for abstract and overview and enqueues them.
    """
    enqueued = 0
    expected = 2 if include_overview else 1
    try:
        if not ctx:
            logger.warning("No context provided for vectorization")
            return

        queue_manager = get_queue_manager()
        embedding_queue = queue_manager.get_queue(queue_manager.EMBEDDING)

        parent_uri = VikingURI(uri).parent.uri
        owner_space = _owner_space_for_uri(uri, ctx)

        created_at, updated_at = await _resolve_context_timestamps(uri, ctx)

        # Vectorize L0: .abstract.md (abstract)
        context_abstract = Context(
            uri=uri,
            parent_uri=parent_uri,
            is_leaf=False,
            abstract=abstract,
            context_type=context_type,
            level=ContextLevel.ABSTRACT,
            created_at=created_at,
            updated_at=updated_at,
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

        if include_overview:
            # Vectorize L1: .overview.md (overview)
            context_overview = Context(
                uri=uri,
                parent_uri=parent_uri,
                is_leaf=False,
                abstract=abstract,
                context_type=context_type,
                level=ContextLevel.OVERVIEW,
                created_at=created_at,
                updated_at=updated_at,
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
        await _decrement_embedding_tracker(semantic_msg_id, expected - enqueued)


async def vectorize_file(
    file_path: str,
    summary_dict: Dict[str, str],
    parent_uri: str,
    context_type: str = "resource",
    ctx: Optional[RequestContext] = None,
    semantic_msg_id: Optional[str] = None,
    use_summary: bool = False,
    preserve_existing_created_at: bool = False,
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

        created_at, updated_at = await _resolve_context_timestamps(
            file_path,
            ctx,
            preserve_existing_created_at=preserve_existing_created_at,
        )

        context = Context(
            uri=file_path,
            parent_uri=parent_uri,
            is_leaf=True,
            abstract=summary,
            context_type=context_type,
            created_at=created_at,
            updated_at=updated_at,
            user=ctx.user,
            account_id=ctx.account_id,
            owner_space=_owner_space_for_uri(file_path, ctx),
        )

        content_type = get_resource_content_type(file_name)
        embedding_cfg = get_openviking_config().embedding
        configured_text_source = getattr(embedding_cfg, "text_source", "content_only")
        effective_text_source = "summary_only" if use_summary else configured_text_source
        max_input_tokens = int(getattr(embedding_cfg, "max_input_tokens", 4096) or 4096)

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
                    content = _truncate_embedding_input(content, max_input_tokens)
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

    The context_type is derived from the URI so that memory directories
    (``/memories/``) are indexed as ``"memory"`` rather than the default
    ``"resource"``.
    """
    viking_fs = get_viking_fs()
    context_type = get_context_type_for_uri(uri)

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
        await vectorize_directory_meta(uri, abstract, overview, context_type=context_type, ctx=ctx)

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
                file_path=file_uri,
                summary_dict={"name": file_name},
                parent_uri=uri,
                context_type=context_type,
                ctx=ctx,
            )

    except Exception as e:
        logger.error(f"Failed to scan directory {uri} for indexing: {e}")
