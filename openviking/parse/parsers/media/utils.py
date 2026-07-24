# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Media-related utilities for OpenViking."""

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from openviking.core.path_variables import CalendarVariableProvider
from openviking.prompts import render_prompt
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.server.identity import RequestContext

from .constants import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    is_mpeg_ts,
)

logger = get_logger(__name__)

_ARK_UNDERSTANDING_EXTENSIONS = {
    "audio": {".mp3", ".wav", ".aac", ".m4a"},
    "video": {".mp4", ".avi", ".mov"},
}
_ARK_MAX_FILE_BYTES = 512 * 1024 * 1024
_MEDIA_READ_CHUNK_BYTES = 4 * 1024 * 1024


class _InvalidMediaContentError(ValueError):
    pass


class _MediaTooLargeError(ValueError):
    pass


def _exception_metadata(error: BaseException) -> tuple[str, str, str, str]:
    status = code = request_id = "-"
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for name, current_value in (
            ("status", status),
            ("code", code),
            ("request_id", request_id),
        ):
            if current_value != "-":
                continue
            attribute = "status_code" if name == "status" else name
            try:
                value = getattr(current, attribute, None)
            except BaseException:
                value = None
            if value is not None and not isinstance(value, (dict, list, tuple, set)):
                try:
                    rendered = str(value)[:128]
                except BaseException:
                    rendered = "-"
                if name == "status":
                    status = rendered
                elif name == "code":
                    code = rendered
                else:
                    request_id = rendered
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if cause is not None:
            pending.append(cause)
        if context is not None:
            pending.append(context)
    return type(error).__name__, status, code, request_id

_ENGLISH_MEDIA_REFUSAL_RE = re.compile(
    r"^(?:(?:i(?:'m| am)\s+)?sorry[,!.]?(?:\s+but)?\s+)?"
    r"(?:(?:i\s+)?(?:am\s+unable\s+to|unable\s+to|cannot|can't|could\s+not|couldn't|"
    r"failed\s+to)|i'm\s+unable\s+to)\s+"
    r"(?:analy[sz]e|understand|process|transcribe)\s+"
    r"(?:(?:the|this)\s+)?(?:(?:provided|supplied)\s+)?"
    r"(?:audio|video|media|file|content)(?:\s+(?:file|content))?[.!]?$",
    flags=re.IGNORECASE,
)
_ENGLISH_MEDIA_PASSIVE_REFUSAL_RE = re.compile(
    r"^(?:(?:the|this)\s+)?(?:(?:provided|supplied)\s+)?"
    r"(?:audio|video|media|file|content)(?:\s+(?:file|content))?\s+"
    r"(?:cannot|can't|could\s+not|couldn't)\s+be\s+"
    r"(?:analy[sz]ed|understood|processed|transcribed)[.!]?$",
    flags=re.IGNORECASE,
)
_ENGLISH_MEDIA_NOT_UNDERSTOOD_RE = re.compile(
    r"^(?:no|not\s+enough)\s+"
    r"(?:(?:recognizable|understandable|identifiable|usable)\s+)?"
    r"(?:audio|video|media|speech|content|information)"
    r"(?:\s+(?:content|information))?\s+"
    r"(?:(?:could\s+be|was|were)\s+)?"
    r"(?:found|detected|recognized|understood|identified)[.!]?$",
    flags=re.IGNORECASE,
)
_ENGLISH_MEDIA_NO_ACCESS_RE = re.compile(
    r"^(?:(?:i(?:'m| am)\s+)?sorry[,!.]?(?:\s+but)?\s+)?(?:"
    r"i\s+(?:(?:cannot|can't|could\s+not|couldn't)\s+access|"
    r"(?:do\s+not|don't|did\s+not|didn't)\s+have\s+access\s+to)\s+"
    r"(?:(?:the|this)\s+)?(?:(?:provided|supplied)\s+)?"
    r"(?:audio|video|media|file|content)(?:\s+(?:file|content))?"
    r"|(?:(?:the|this)\s+)?(?:(?:provided|supplied)\s+)?"
    r"(?:audio|video|media|file|content)(?:\s+(?:file|content))?\s+"
    r"is\s+not\s+accessible\s+to\s+me)[.!]?$",
    flags=re.IGNORECASE,
)
_CHINESE_MEDIA_REFUSAL_RE = re.compile(
    r"^(?:抱歉[，,。.!！]?\s*)?(?:我\s*)?(?:无法|不能|未能)\s*"
    r"(?:分析|理解|识别|处理|转录)\s*(?:此|该|这个|所提供的|提供的)?\s*"
    r"(?:音频|视频|媒体|文件|内容)(?:文件|内容)?[。.!！]?$"
)
_CHINESE_MEDIA_PASSIVE_REFUSAL_RE = re.compile(
    r"^(?:抱歉[，,。.!！]?\s*)?(?:此|该|这个|所提供的|提供的)?\s*"
    r"(?:音频|视频|媒体|文件|内容)(?:文件|内容)?\s*(?:无法|不能|未能)\s*"
    r"(?:被)?(?:分析|理解|识别|处理|转录)[。.!！]?$"
)
_CHINESE_MEDIA_NOT_UNDERSTOOD_RE = re.compile(
    r"^(?:未|没有)(?:检测|识别|发现)到?"
    r"(?:可识别的|有效的|可理解的)?(?:音频|视频|语音|媒体|内容|信息)"
    r"(?:内容|信息)?[。.!！]?$"
)


def _is_svg(data: bytes) -> bool:
    """Check if the data is an SVG file."""
    return data[:4] == b"<svg" or (data[:5] == b"<?xml" and b"<svg" in data[:100])


def _convert_svg_to_png(svg_data: bytes) -> Optional[bytes]:
    """Convert SVG to PNG using cairosvg or wand.

    Dependencies:
      Ubuntu/Debian: sudo apt-get install libcairo2 && pip install cairosvg
      macOS: brew install cairo && pip install cairosvg
    """
    try:
        import cairosvg
        return cairosvg.svg2png(bytestring=svg_data)
    except ImportError:
        pass
    except OSError:
        pass  # libcairo not installed

    try:
        from wand.image import Image as WandImage
        with WandImage(blob=svg_data, format='svg') as img:
            img.format = 'png'
            return img.make_blob()
    except ImportError:
        pass

    return None


def get_media_type(
    source_path: Optional[str],
    source_format: Optional[str],
    *,
    content: object = None,
) -> Optional[str]:
    """
    Determine media type from source path or format.

    Args:
        source_path: Source file path
        source_format: Source format string (e.g., "image", "audio", "video")

    Returns:
        Media type ("image", "audio", "video") or None if not a media file
    """
    ext = Path(source_path).suffix.lower() if source_path else ""
    if ext == ".ts":
        if content is not None:
            return "video" if is_mpeg_ts(content) else None
        return "video" if source_format == "video" else None

    if source_format:
        if source_format in ["image", "audio", "video"]:
            return source_format

    if source_path:
        if ext in IMAGE_EXTENSIONS:
            return "image"
        elif ext in AUDIO_EXTENSIONS:
            return "audio"
        elif ext in VIDEO_EXTENSIONS:
            return "video"

    return None


def get_media_base_uri(media_type: str) -> str:
    """
    Get base URI for media files.

    Args:
        media_type: Media type ("image", "audio", "video")

    Returns:
        Base URI like "viking://resources/images/2025/02/19"
    """
    # Map singular media types to plural directory names
    media_dir_map = {"image": "images", "audio": "audio", "video": "video"}
    media_dir = media_dir_map.get(media_type, media_type)
    # Use CalendarVariableProvider to get today's date in YYYY/MM/DD format
    date_str = CalendarVariableProvider().get_variables()["today"]
    return f"viking://resources/{media_dir}/{date_str}"


async def generate_image_summary(
    image_uri: str,
    original_filename: str,
    llm_sem: Optional[asyncio.Semaphore] = None,
    ctx: Optional["RequestContext"] = None,
) -> Dict[str, Any]:
    """
    Generate summary for an image file using VLM.

    Args:
        image_uri: URI to the image file in VikingFS
        original_filename: Original filename of the image
        llm_sem: Semaphore to limit concurrent LLM calls
        ctx: Optional request context for tenant-aware file access

    Returns:
        Dictionary with "name" and "summary" keys
    """
    viking_fs = get_viking_fs()
    vlm = get_openviking_config().vlm
    file_name = original_filename

    try:
        # Read image bytes
        image_bytes = await viking_fs.read_file_bytes(image_uri, ctx=ctx)
        if not isinstance(image_bytes, bytes):
            raise ValueError(f"Expected bytes for image file, got {type(image_bytes)}")

        # Check for unsupported formats (SVG, etc.) by detecting magic bytes
        # SVG format is not supported by VolcEngine VLM API, skip VLM analysis
        if _is_svg(image_bytes):
            logger.info(
                f"[MediaUtils.generate_image_summary] SVG format detected, skipping VLM analysis: {image_uri}"
            )
            return {"name": file_name, "summary": "SVG image (format not supported by VLM)"}

        logger.info(
            f"[MediaUtils.generate_image_summary] Generating summary for image: {image_uri}"
        )

        # Render prompt
        prompt = render_prompt(
            "parsing.image_summary",
            {"context": "No additional context"},
        )

        # Call VLM
        async with llm_sem or asyncio.Semaphore(1):
            response = await vlm.get_vision_completion_async(
                prompt=prompt,
                images=[image_bytes],
            )

        logger.info(
            f"[MediaUtils.generate_image_summary] VLM response received, length: {len(response)}"
        )
        return {"name": file_name, "summary": response.strip()}

    except ValueError as e:
        if "SVG format" in str(e) or "not supported" in str(e):
            logger.warning(
                f"[MediaUtils.generate_image_summary] Unsupported image format for {image_uri}: {e}"
            )
            return {"name": file_name, "summary": f"Unsupported image format: {str(e)}"}
        raise
    except Exception as e:
        logger.error(
            f"[MediaUtils.generate_image_summary] Failed to generate image summary: {e}",
            exc_info=True,
        )
        return {"name": file_name, "summary": "Image summary generation failed"}


async def generate_audio_summary(
    audio_uri: str,
    original_filename: str,
    llm_sem: Optional[asyncio.Semaphore] = None,
    ctx: Optional["RequestContext"] = None,
) -> Dict[str, Any]:
    """Generate a normalized semantic summary for an audio file.

    Args:
        audio_uri: URI to the audio file in VikingFS
        original_filename: Original filename of the audio
        llm_sem: Semaphore to limit concurrent LLM calls
        ctx: Optional request context for tenant-aware file access

    Returns:
        Dictionary with "name" and "summary" keys
    """
    return await _generate_media_summary(
        audio_uri,
        original_filename,
        "audio",
        ctx=ctx,
    )


async def generate_video_summary(
    video_uri: str,
    original_filename: str,
    llm_sem: Optional[asyncio.Semaphore] = None,
    ctx: Optional["RequestContext"] = None,
) -> Dict[str, Any]:
    """Generate a normalized semantic summary for a video file.

    Args:
        video_uri: URI to the video file in VikingFS
        original_filename: Original filename of the video
        llm_sem: Semaphore to limit concurrent LLM calls
        ctx: Optional request context for tenant-aware file access

    Returns:
        Dictionary with "name" and "summary" keys
    """
    return await _generate_media_summary(
        video_uri,
        original_filename,
        "video",
        ctx=ctx,
    )


async def _write_media_to_path(
    path: Path,
    media_uri: str,
    viking_fs: Any,
    ctx: Optional["RequestContext"],
) -> None:
    offset = 0
    with path.open("wb") as output:
        while True:
            chunk = await viking_fs.read(
                media_uri,
                offset=offset,
                size=_MEDIA_READ_CHUNK_BYTES,
                ctx=ctx,
            )
            if not isinstance(chunk, bytes):
                raise _InvalidMediaContentError("Media content is non-binary")
            if not chunk:
                break
            offset += len(chunk)
            if offset > _ARK_MAX_FILE_BYTES:
                raise _MediaTooLargeError(
                    "Ark media file exceeds the 512 MB limit"
                )
            output.write(chunk)
    if offset == 0:
        raise _InvalidMediaContentError("Media content is empty")


async def _generate_media_summary(
    media_uri: str,
    original_filename: str,
    media_type: str,
    ctx: Optional["RequestContext"] = None,
) -> Dict[str, Any]:
    config = get_openviking_config()
    media_config = getattr(config.media_understanding, media_type, None)
    result = {"name": original_filename, "summary": ""}
    if media_config is None:
        logger.info("%s understanding is not configured for %s", media_type, media_uri)
        return result

    extension = Path(original_filename).suffix.lower()
    if extension not in _ARK_UNDERSTANDING_EXTENSIONS[media_type]:
        logger.info(
            "Skipping unsupported %s understanding format: %s", media_type, extension
        )
        return result

    viking_fs = get_viking_fs()
    stat = await viking_fs.stat(media_uri, ctx=ctx)
    if int((stat or {}).get("size", 0)) > _ARK_MAX_FILE_BYTES:
        logger.warning(
            "Skipping %s understanding because the file exceeds 512 MB", media_type
        )
        return result

    from openviking.session.memory.utils.language import resolve_output_language

    fallback_language = resolve_output_language("", config=config)
    prompt = render_prompt(
        f"parsing.{media_type}_summary",
        {"filename": original_filename, "fallback_language": fallback_language},
    )

    async def write_content(path: Path) -> None:
        await _write_media_to_path(path, media_uri, viking_fs, ctx)

    try:
        raw = await media_config.get_client_instance().understand_from_writer(
            content_writer=write_content,
            filename=original_filename,
            media_type=media_type,
            prompt=prompt,
        )
    except (_InvalidMediaContentError, _MediaTooLargeError):
        return result
    except Exception as error:
        error_type, status, code, request_id = _exception_metadata(error)
        logger.warning(
            "Media understanding exception: stage=provider_call media_type=%s "
            "error_type=%s status=%s code=%s request_id=%s",
            media_type,
            error_type,
            status,
            code,
            request_id,
        )
        return result

    result["summary"] = _normalize_media_markdown(
        raw,
        filename=original_filename,
        overview_max_chars=config.semantic.overview_max_chars,
        abstract_max_chars=config.semantic.abstract_max_chars,
    )
    return result


def _normalize_media_markdown(
    raw: str,
    *,
    filename: str,
    overview_max_chars: int,
    abstract_max_chars: int,
) -> str:
    text = re.sub(
        r"<think>[\s\S]*?</think>", "", str(raw or ""), flags=re.IGNORECASE
    ).strip()
    fenced = re.fullmatch(
        r"```(?:markdown)?\s*\n?([\s\S]*?)\n?```",
        text,
        flags=re.IGNORECASE,
    )
    if fenced:
        text = fenced.group(1).strip()
    if re.search(r"```|~~~", text):
        return ""
    if not text or overview_max_chars <= 0 or abstract_max_chars <= 0:
        return ""
    if _is_short_media_refusal(text):
        return ""

    title_match = re.search(r"^#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    raw_title = title_match.group(1) if title_match else (Path(filename).stem or filename)
    raw_title = re.sub(r"\s+", " ", raw_title).strip()
    if not raw_title:
        return ""

    without_title = text
    if title_match:
        without_title = text[: title_match.start()] + text[title_match.end() :]
    without_heading = re.sub(
        rf"^###\s+{re.escape(filename)}\s*$",
        "",
        without_title,
        flags=re.MULTILINE,
    )
    blocks = [
        block.strip()
        for block in re.split(r"\n\s*\n", without_heading)
        if block.strip()
    ]
    brief_index = next(
        (
            index
            for index, block in enumerate(blocks)
            if _is_prose_block(block)
        ),
        None,
    )
    if brief_index is None:
        return ""

    raw_brief = re.sub(r"\s+", " ", blocks[brief_index]).strip()
    if not raw_brief:
        return ""
    if _is_short_media_refusal(raw_brief):
        return ""
    body = "\n\n".join(
        block for index, block in enumerate(blocks) if index != brief_index
    ).strip()

    fixed_without_title = f"# \n\nx\n\n### {filename}"
    title_budget = overview_max_chars - len(fixed_without_title)
    if title_budget <= 0:
        return ""
    title = raw_title[: min(200, title_budget)].rstrip()
    fixed_without_briefs = f"# {title}\n\n\n\n### {filename}\n\n"
    brief_budget = (overview_max_chars - len(fixed_without_briefs)) // 2
    if brief_budget <= 0:
        return ""
    brief = raw_brief[: min(abstract_max_chars, brief_budget)].rstrip()
    if not brief:
        return ""

    prefix = f"# {title}\n\n{brief}\n\n### {filename}"
    recoverable_body = brief
    if _has_non_heading_content(body):
        recoverable_body = f"{brief}\n\n{body}"
    body_budget = overview_max_chars - len(prefix) - 2
    if body_budget <= 0:
        return ""
    recoverable_body = recoverable_body[:body_budget].rstrip()
    if not recoverable_body:
        return ""
    return f"{prefix}\n\n{recoverable_body}"


def _is_short_media_refusal(text: str) -> bool:
    candidate = text.strip().replace("’", "'")
    candidate = re.sub(r"^#{1,6}\s+[^\n]+\n+", "", candidate, count=1).strip()
    candidate = re.sub(r"\s+", " ", candidate)
    if not candidate or len(candidate) > 240:
        return False
    return any(
        pattern.fullmatch(candidate)
        for pattern in (
            _ENGLISH_MEDIA_REFUSAL_RE,
            _ENGLISH_MEDIA_PASSIVE_REFUSAL_RE,
            _ENGLISH_MEDIA_NOT_UNDERSTOOD_RE,
            _ENGLISH_MEDIA_NO_ACCESS_RE,
            _CHINESE_MEDIA_REFUSAL_RE,
            _CHINESE_MEDIA_PASSIVE_REFUSAL_RE,
            _CHINESE_MEDIA_NOT_UNDERSTOOD_RE,
        )
    )


def _has_non_heading_content(text: str) -> bool:
    return any(
        line.strip() and not line.lstrip().startswith("#")
        for line in text.splitlines()
    )


def _is_prose_block(block: str) -> bool:
    candidate = block.lstrip()
    if not candidate or not any(character.isalpha() for character in candidate):
        return False
    if re.match(r"^(?:#{1,6}\s|[-*+]\s|>\s?|\d+[.)]\s|`{3,}|~{3,}|\|)", candidate):
        return False

    lines = candidate.splitlines()
    if re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", lines[0]):
        return False
    if len(lines) > 1 and "|" in lines[0] and re.match(
        r"^\s*\|?\s*:?-{3,}:?\s*\|", lines[1]
    ):
        return False
    return True
