# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Embedding utilities for OpenViking.

Common logic for creating Context objects and enqueuing them to EmbeddingQueue.
"""

import base64
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from openviking.core.context import Context, ContextLevel, ResourceContentType, Vectorize
from openviking.core.namespace import context_type_for_uri, owner_space_for_uri
from openviking.server.identity import RequestContext
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.storage.viking_fs import LS_ALL_NODES, get_viking_fs
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)
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

# Maximum bytes to read for content sniffing.
_SNIFF_READ_SIZE = 1024
# If more than this ratio of null bytes in the sample, treat as binary.
_SNIFF_NULL_BYTE_RATIO = 0.05
# If more than this ratio of suspicious control bytes in the sample, treat as binary.
_SNIFF_CONTROL_CHAR_RATIO = 0.02
_TEXT_CONTROL_BYTES = frozenset({0x09, 0x0A, 0x0C, 0x0D})

# Magic bytes for content-based file type detection.
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n",),
    (b"\xff\xd8\xff",),
    (b"GIF87a", b"GIF89a"),
    (b"BM",),
    (b"RIFF",),  # WebP: RIFF....WEBP — checked below
)
_VIDEO_MAGIC = (
    (b"\x00\x00\x00",),  # MP4 ftyp box — checked via 'ftyp' substring
    (b"RIFF",),  # AVI: RIFF....AVI — checked below
    (b"\x30\x26\xb2\x75",),  # WMV
    (b"FLV",),
)
_AUDIO_MAGIC = (
    (b"ID3",),  # MP3 with ID3 tag
    (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),  # MP3 frame sync
    (b"RIFF",),  # WAV: RIFF....WAVE — checked below
    (b"\xff\xf1", b"\xff\xf9"),  # AAC
    (b"fLaC",),  # FLAC
)
_BINARY_MAGIC = (
    b"%PDF-",  # PDF
    b"PK\x03\x04",  # ZIP local file header
    b"PK\x05\x06",  # ZIP end of central directory record
    b"PK\x07\x08",  # ZIP data descriptor / spanning marker
    b"\x1f\x8b",  # gzip
    b"\xfd7zXZ\x00",  # xz
    b"7z\xbc\xaf\x27\x1c",  # 7z
    b"Rar!\x1a\x07\x00",  # RAR v1-v4
    b"Rar!\x1a\x07\x01\x00",  # RAR v5+
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


async def get_resource_content_type(
    file_name: str,
    file_path: Optional[str] = None,
    ctx: Optional[RequestContext] = None,
) -> Optional[ResourceContentType]:
    """Determine resource content type based on file extension and content sniffing.

    When extension matching fails and *file_path* is provided, reads the file
    and falls back to magic-byte / null-byte-ratio content sniffing.

    Returns None if the file type cannot be determined.
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

    # Fall back to content sniffing when file_path is available.
    if file_path:
        try:
            raw = await get_viking_fs().read(file_path, offset=0, size=_SNIFF_READ_SIZE, ctx=ctx)
            return _sniff_content_type(raw)
        except Exception as e:
            logger.debug(
                f"Content sniffing failed for {file_path}, falling back to extension-only detection: {e}"
            )

    return None


def _sniff_content_type(content: bytes) -> Optional[ResourceContentType]:
    """Detect file type from raw content bytes using magic bytes and null-byte ratio.

    Returns ResourceContentType or None if undetermined.
    """
    if not content:
        return None

    sample = content

    if _has_binary_magic(sample):
        return None

    media_type = _sniff_known_media_type(sample)
    if media_type is not None:
        return media_type

    return _sniff_text_type(sample)


def _has_binary_magic(sample: bytes) -> bool:
    """Return True when the sample matches known generic binary signatures."""
    for magic in _BINARY_MAGIC:
        if sample.startswith(magic):
            return True
    return False


def _sniff_known_media_type(sample: bytes) -> Optional[ResourceContentType]:
    """Detect known media types by magic bytes."""
    # Image: PNG, JPEG, GIF, BMP, WebP(RIFF)
    for candidates in _IMAGE_MAGIC:
        for magic in candidates:
            if sample.startswith(magic):
                if magic == b"RIFF":
                    riff_type = _sniff_riff_container_type(sample)
                    if riff_type is not None:
                        return riff_type
                    continue
                return ResourceContentType.IMAGE

    # Video: MP4 (ftyp box), WMV, FLV
    for candidates in _VIDEO_MAGIC:
        for magic in candidates:
            if magic == b"\x00\x00\x00":
                if len(sample) > 7 and sample[4:8] == b"ftyp":
                    return ResourceContentType.VIDEO
                continue
            if magic == b"RIFF":
                continue
            if sample.startswith(magic):
                return ResourceContentType.VIDEO

    # Audio: MP3 (ID3 / frame sync), AAC, FLAC
    for candidates in _AUDIO_MAGIC:
        for magic in candidates:
            if magic == b"RIFF":
                continue
            if sample.startswith(magic):
                return ResourceContentType.AUDIO

    return None


def _sniff_riff_container_type(sample: bytes) -> Optional[ResourceContentType]:
    """Detect content type for RIFF-based containers using bytes 8-11."""
    if len(sample) <= 11:
        return None

    riff_type = sample[8:12]
    if riff_type == b"WEBP":
        return ResourceContentType.IMAGE
    if riff_type == b"AVI ":
        return ResourceContentType.VIDEO
    if riff_type == b"WAVE":
        return ResourceContentType.AUDIO
    return None


def _sniff_text_type(sample: bytes) -> Optional[ResourceContentType]:
    """Detect whether the sample should be classified as text."""
    if _is_valid_utf16_text(sample):
        return ResourceContentType.TEXT

    text_sample = _extract_utf8_text_sample(sample)
    if text_sample is None:
        return None

    if _has_too_many_null_bytes(sample):
        return None

    if _has_suspicious_control_bytes(text_sample):
        return None

    return ResourceContentType.TEXT


def _extract_utf8_text_sample(sample: bytes) -> Optional[bytes]:
    """Return a UTF-8-compatible sample for text validation.

    - UTF-8 BOM: strip BOM and continue validating as UTF-8 text.
    - No BOM: require UTF-8 decodability.
    - When the bounded sniff sample ends in the middle of a UTF-8 code point,
      tolerate the incomplete trailing bytes and validate the decodable prefix.
    """
    if sample.startswith(b"\xef\xbb\xbf"):
        text_sample = sample[3:]
        return _trim_incomplete_utf8_suffix(text_sample)

    return _trim_incomplete_utf8_suffix(sample)


def _trim_incomplete_utf8_suffix(sample: bytes) -> Optional[bytes]:
    """Return a decodable UTF-8 prefix, tolerating a truncated tail only for bounded sniff samples."""
    try:
        sample.decode("utf-8")
        return sample
    except UnicodeDecodeError as exc:
        if not _is_incomplete_suffix_error(exc, sample):
            return None

        trimmed = sample[: exc.start]
        try:
            trimmed.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return trimmed


def _is_valid_utf16_text(sample: bytes) -> bool:
    """Return True when the sample looks like valid UTF-16 text with BOM."""
    if not sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False

    try:
        sample.decode("utf-16")
    except UnicodeDecodeError as exc:
        return _has_valid_utf16_prefix(sample, exc)
    return True


def _has_valid_utf16_prefix(sample: bytes, exc: UnicodeDecodeError) -> bool:
    """Return True when a bounded sniff sample has only a truncated UTF-16 tail."""
    if not _is_incomplete_suffix_error(exc, sample):
        return False

    for trim_size in (2, 4):
        if len(sample) <= trim_size:
            continue
        trimmed = sample[:-trim_size]
        try:
            trimmed.decode("utf-16")
        except UnicodeDecodeError:
            continue
        return True
    return False


def _is_incomplete_suffix_error(exc: UnicodeDecodeError, sample: bytes) -> bool:
    """Return True for decode errors caused only by a truncated tail at sniff boundary."""
    return (
        len(sample) >= _SNIFF_READ_SIZE
        and exc.reason == "unexpected end of data"
        and exc.end == len(sample)
    )


def _has_too_many_null_bytes(sample: bytes) -> bool:
    """Return True when null-byte density suggests binary content."""
    null_count = sample.count(b"\x00")
    return (null_count / len(sample)) > _SNIFF_NULL_BYTE_RATIO


def _has_suspicious_control_bytes(text_sample: bytes) -> bool:
    """Return True when control-byte density is too high for normal text."""
    control_count = sum(
        1 for byte in text_sample if byte < 0x20 and byte not in _TEXT_CONTROL_BYTES
    )
    return bool(text_sample) and (control_count / len(text_sample)) > _SNIFF_CONTROL_CHAR_RATIO


_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def _image_mime_type(file_name: str) -> str:
    """Resolve the MIME type for an image file based on its extension."""
    _, ext = os.path.splitext(file_name.lower())
    return _IMAGE_MIME_TYPES.get(ext, "image/png")


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
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{_image_mime_type(file_name)};base64,{encoded}"
    except Exception as e:
        logger.warning(f"Failed to read image for multimodal vectorization {file_path}: {e}")
        return None


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
            context_overview.set_vectorize(Vectorize(text=overview))
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
    summary_dict: Dict[str, str],
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

        content_type = await get_resource_content_type(file_name, file_path=file_path, ctx=ctx)
        embedding_cfg = get_openviking_config().embedding
        configured_text_source = getattr(embedding_cfg, "text_source", "content_only")
        effective_text_source = "summary_only" if use_summary else configured_text_source
        image_vectorization = getattr(embedding_cfg, "image_vectorization", "summary_only")

        if content_type is None:
            # Still unknown: fall back to summary if available.
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
                # Read raw file content; embedders apply their own input guard.
                try:
                    content = await viking_fs.read_file(file_path, ctx=ctx)
                    if isinstance(content, bytes):
                        content = content.decode("utf-8", errors="replace")
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
        elif content_type == ResourceContentType.IMAGE and image_vectorization in {
            "image_only",
            "image_and_summary",
        }:
            # Multimodal: embed the image itself (optionally with its text summary).
            image_uri = await _build_image_data_uri(file_path, file_name, viking_fs, ctx)
            if image_uri:
                text = summary if image_vectorization == "image_and_summary" else ""
                context.set_vectorize(Vectorize(text=text, images=[image_uri]))
            elif summary:
                # Could not load image; fall back to summary text.
                context.set_vectorize(Vectorize(text=summary))
            else:
                logger.debug(
                    f"Skipping image {file_path} (image unreadable and no summary available)"
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
    if uri.startswith("viking://session/") or uri == "viking://session":
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
