# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Embedding utilities for OpenViking.

Common logic for creating Context objects and enqueuing them to EmbeddingQueue.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from charset_normalizer import from_bytes

from openviking.core.context import Context, ContextLevel, ResourceContentType, Vectorize
from openviking.core.namespace import context_type_for_uri, is_session_uri, owner_space_for_uri
from openviking.server.identity import RequestContext
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.storage.viking_fs import LS_ALL_NODES, get_viking_fs
from openviking.utils.image_search import image_bytes_to_data_uri
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

# The `abstract` scalar is persisted as a vector-store bytes_row string field,
# which is length-prefixed with a uint16 (STRING_MAX_UINT16_LENGTH = 65535). An
# oversized abstract raises "string field 'abstract' exceeds 65535 bytes" and
# fails embedding enqueue, so the resource is silently never vectorized (and thus
# not retrievable). Cap it with headroom, mirroring
# memory_updater._truncate_memory_abstract introduced for the memory path (#2774).
_ABSTRACT_MAX_BYTES = 50_000


def _truncate_abstract_bytes(abstract: str) -> str:
    """Cap an abstract scalar below the vector-store bytes_row byte limit."""
    encoded = (abstract or "").encode("utf-8")
    if len(encoded) <= _ABSTRACT_MAX_BYTES:
        return abstract or ""
    return encoded[:_ABSTRACT_MAX_BYTES].decode("utf-8", errors="ignore")


_PORTABLE_SCALAR_FIELDS = frozenset(
    {
        "type",
        "level",
        "name",
        "description",
        "tags",
        "abstract",
    }
)


def _apply_scalar_overrides(embedding_msg, overrides: Optional[Dict[str, Any]]) -> None:
    if not embedding_msg or not overrides:
        return
    for field in _PORTABLE_SCALAR_FIELDS:
        value = overrides.get(field)
        if value is not None:
            embedding_msg.context_data[field] = value


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
        ".jsonl",
        ".xml",
        ".svg",
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
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
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


async def _build_image_data_uri(
    file_path: str,
    file_name: str,
    viking_fs,
    ctx: Optional[RequestContext],
) -> Optional[str]:
    """Read an image file and encode it as a base64 ``data:`` URI.

    Returns None if the image cannot be read.
    """
    try:
        content = await viking_fs.read_file_bytes(file_path, ctx=ctx)
        return image_bytes_to_data_uri(content, file_name)
    except Exception as e:
        logger.warning(f"Failed to read image for multimodal vectorization {file_path}: {e}")
        return None


def _coerce_text_file_content(raw: Any) -> str:
    """Coerce known text-file content returned by VikingFS into str."""
    if isinstance(raw, bytes):
        return _decode_text_bytes(raw)
    return raw or ""


def _looks_like_binary_bytes(raw: bytes) -> bool:
    """Conservative binary check for unknown file bytes."""
    if not raw:
        return False
    if b"\x00" in raw[:4096]:
        return True

    allowed_controls = {9, 10, 12, 13}
    sample = raw[:4096]
    control_count = sum(byte < 32 and byte not in allowed_controls for byte in sample)
    return control_count / len(sample) > 0.3


def _decode_text_bytes(raw: bytes) -> str:
    """Decode file bytes for BM25 content.

    Prefer UTF-8. If UTF-8 fails, reject binary-looking bytes, then try charset
    sniffing. Return an empty string when no text encoding can be recognized.
    """
    if not raw:
        return ""

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    if _looks_like_binary_bytes(raw):
        return ""

    best = from_bytes(raw).best()
    if best is None:
        return ""

    return str(best)


def _decode_unknown_file_bytes(raw: bytes) -> str:
    """Decode unknown file bytes with the shared text-byte decoding strategy."""
    return _decode_text_bytes(raw)


async def _read_unknown_file_text_for_fulltext(
    file_path: str,
    viking_fs,
    ctx: Optional[RequestContext],
) -> str:
    """Best-effort raw file text for BM25/full-text indexing.

    This text is intentionally separate from the embedding input. Unknown file
    types may still embed their generated summary, while grep/BM25 should index
    the original text when the file can be read as text-like content.
    """
    try:
        return _decode_unknown_file_bytes(await viking_fs.read_file_bytes(file_path, ctx=ctx))
    except Exception as e:
        logger.debug(f"Failed to read full-text content for {file_path}: {e}")
        return ""


async def vectorize_directory_meta(
    uri: str,
    abstract: str,
    overview: str,
    context_type: str = "resource",
    ctx: Optional[RequestContext] = None,
    semantic_msg_id: Optional[str] = None,
    include_overview: bool = True,
    scalar_overrides: Optional[Dict[int, Dict[str, Any]]] = None,
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
        owner_space = owner_space_for_uri(uri, ctx)

        created_at, updated_at = await _resolve_context_timestamps(uri, ctx)

        # Cap the abstract scalar below the bytes_row 65535-byte limit. #2774
        # added this for the memory path; the resource indexing paths (here and
        # index_resource, which feeds this function) were missed, so an
        # .abstract.md / overview > 65535 UTF-8 bytes still fails embedding enqueue.
        abstract = _truncate_abstract_bytes(abstract)

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
        context_abstract.set_vectorize(Vectorize(text=abstract, full_text=abstract))
        msg_abstract = EmbeddingMsgConverter.from_context(context_abstract)
        _apply_scalar_overrides(
            msg_abstract,
            (scalar_overrides or {}).get(int(ContextLevel.ABSTRACT.value)),
        )
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
            context_overview.set_vectorize(Vectorize(text=overview, full_text=overview))
            msg_overview = EmbeddingMsgConverter.from_context(context_overview)
            _apply_scalar_overrides(
                msg_overview,
                (scalar_overrides or {}).get(int(ContextLevel.OVERVIEW.value)),
            )
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
    except Exception as e:
        logger.error(
            f"Failed to vectorize directory metadata for {uri}: {e}",
            exc_info=True,
        )
        raise
    finally:
        await _decrement_embedding_tracker(semantic_msg_id, expected - enqueued)


async def vectorize_file(
    file_path: str,
    summary_dict: Dict[str, Any],
    parent_uri: str,
    context_type: str = "resource",
    ctx: Optional[RequestContext] = None,
    semantic_msg_id: Optional[str] = None,
    use_summary: bool = False,
    preserve_existing_created_at: bool = False,
    scalar_override: Optional[Dict[str, Any]] = None,
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
        # Cap below the bytes_row 65535-byte abstract-scalar limit (#2774 parity).
        summary = _truncate_abstract_bytes(summary)
        has_reusable_content = "content" in summary_dict
        reusable_content = (
            _coerce_text_file_content(summary_dict.get("content")) if has_reusable_content else ""
        )

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
            owner_space=owner_space_for_uri(file_path, ctx),
        )

        content_type = get_resource_content_type(file_name)
        embedding_cfg = get_openviking_config().embedding
        configured_text_source = getattr(embedding_cfg, "text_source", "content_only")
        effective_text_source = "summary_only" if use_summary else configured_text_source

        if content_type is None:
            # Unsupported file type: fall back to summary if available
            if summary:
                logger.warning(
                    f"Unsupported file type for {file_path}, falling back to summary for vectorization"
                )
                full_content = (
                    reusable_content
                    if has_reusable_content
                    else await _read_unknown_file_text_for_fulltext(file_path, viking_fs, ctx)
                )
                context.set_vectorize(Vectorize(text=summary, full_text=full_content or summary))
            else:
                logger.warning(
                    f"Unsupported file type for {file_path} and no summary available, skipping vectorization"
                )
                return
        elif content_type == ResourceContentType.TEXT:
            # Known text files use VikingFS' text read path once, then reuse that
            # content for BM25 regardless of whether embedding uses summary or raw text.
            try:
                content = (
                    reusable_content
                    if has_reusable_content
                    else _coerce_text_file_content(await viking_fs.read_file(file_path, ctx=ctx))
                )
            except Exception as e:
                logger.warning(
                    f"Failed to read file content for {file_path}, falling back to summary: {e}"
                )
                if summary:
                    context.set_vectorize(Vectorize(text=summary, full_text=summary))
                else:
                    logger.warning(f"No summary available for {file_path}, skipping vectorization")
                    return
            else:
                if summary and effective_text_source in {"summary_first", "summary_only"}:
                    # Use summary for vectorization, but reuse the single raw text read for BM25.
                    context.set_vectorize(Vectorize(text=summary, full_text=content or summary))
                else:
                    # Embedders apply their own input guard.
                    context.set_vectorize(Vectorize(text=content, full_text=content))
        elif content_type == ResourceContentType.IMAGE:
            # Multimodal embedders consume both parts; text-only embedders fall back to summary.
            image_uri = await _build_image_data_uri(file_path, file_name, viking_fs, ctx)
            if image_uri:
                context.set_vectorize(Vectorize(text=summary, images=[image_uri]))
            elif summary:
                context.set_vectorize(Vectorize(text=summary))
            else:
                logger.debug(
                    f"Skipping image {file_path} (image unreadable and no summary available)"
                )
                return
        elif summary:
            # For non-text files, use summary
            context.set_vectorize(Vectorize(text=summary, full_text=summary))
        else:
            logger.debug(f"Skipping file {file_path} (no text content or summary)")
            return

        embedding_msg = EmbeddingMsgConverter.from_context(context)
        if not embedding_msg:
            return

        _apply_scalar_overrides(embedding_msg, scalar_override)
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
    if is_session_uri(uri):
        logger.info("Skipping indexing for session namespace: %s", uri)
        return

    viking_fs = get_viking_fs()
    context_type = context_type_for_uri(uri)

    # 1. Index Directory Metadata
    abstract_uri = f"{uri}/.abstract.md"
    overview_uri = f"{uri}/.overview.md"

    abstract = ""
    overview = ""

    if await viking_fs.exists(abstract_uri, ctx=ctx):
        content = await viking_fs.read_file(abstract_uri, ctx=ctx)
        abstract = content.decode("utf-8") if isinstance(content, bytes) else content

    if await viking_fs.exists(overview_uri, ctx=ctx):
        content = await viking_fs.read_file(overview_uri, ctx=ctx)
        overview = content.decode("utf-8") if isinstance(content, bytes) else content

    if abstract or overview:
        await vectorize_directory_meta(uri, abstract, overview, context_type=context_type, ctx=ctx)

    # 2. Index Files
    try:
        files = await viking_fs.ls(uri, node_limit=LS_ALL_NODES, ctx=ctx)
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
