# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Media-related utilities for OpenViking."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from openviking.prompts import render_prompt
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.server.identity import RequestContext

from .constants import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

logger = get_logger(__name__)


def _is_svg(data: bytes) -> bool:
    """Check if the data is an SVG file."""
    return data[:4] == b"<svg" or (data[:5] == b"<?xml" and b"<svg" in data[:100])


# SVG to PNG conversion (disabled by default)
# Uncomment and install dependencies if you need SVG support:
#   Ubuntu/Debian: sudo apt-get install libcairo2 && pip install cairosvg
#   macOS: brew install cairo && pip install cairosvg
#   Or use ImageMagick: sudo apt-get install libmagickwand-dev && pip install Wand
#
# def _convert_svg_to_png(svg_data: bytes) -> Optional[bytes]:
#     """Convert SVG to PNG using cairosvg or wand."""
#     try:
#         import cairosvg
#         return cairosvg.svg2png(bytestring=svg_data)
#     except ImportError:
#         pass
#     except OSError:
#         pass  # libcairo not installed
#
#     try:
#         from wand.image import Image as WandImage
#         with WandImage(blob=svg_data, format='svg') as img:
#             img.format = 'png'
#             return img.make_blob()
#     except ImportError:
#         pass
#
#     return None


def get_media_type(source_path: Optional[str], source_format: Optional[str]) -> Optional[str]:
    """
    Determine media type from source path or format.

    Args:
        source_path: Source file path
        source_format: Source format string (e.g., "image", "audio", "video")

    Returns:
        Media type ("image", "audio", "video") or None if not a media file
    """
    if source_format:
        if source_format in ["image", "audio", "video"]:
            return source_format

    if source_path:
        ext = Path(source_path).suffix.lower()
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
        Base URI like "viking://resources/images/20250219"
    """
    # Map singular media types to plural directory names
    media_dir_map = {"image": "images", "audio": "audio", "video": "video"}
    media_dir = media_dir_map.get(media_type, media_type)
    # Get current date in YYYYMMDD format
    date_str = datetime.now().strftime("%Y%m%d")
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
    """
    Generate summary for an audio file (placeholder).

    Args:
        audio_uri: URI to the audio file in VikingFS
        original_filename: Original filename of the audio
        llm_sem: Semaphore to limit concurrent LLM calls
        ctx: Optional request context for tenant-aware file access

    Returns:
        Dictionary with "name" and "summary" keys
    """
    logger.info(
        f"[MediaUtils.generate_audio_summary] Audio summary generation not yet implemented for: {audio_uri}"
    )
    return {"name": original_filename, "summary": "Audio summary generation not yet implemented"}


async def generate_video_summary(
    video_uri: str,
    original_filename: str,
    llm_sem: Optional[asyncio.Semaphore] = None,
    ctx: Optional["RequestContext"] = None,
) -> Dict[str, Any]:
    """
    Generate summary for a video file (placeholder).

    Args:
        video_uri: URI to the video file in VikingFS
        original_filename: Original filename of the video
        llm_sem: Semaphore to limit concurrent LLM calls
        ctx: Optional request context for tenant-aware file access

    Returns:
        Dictionary with "name" and "summary" keys
    """
    logger.info(
        f"[MediaUtils.generate_video_summary] Video summary generation not yet implemented for: {video_uri}"
    )
    return {"name": original_filename, "summary": "Video summary generation not yet implemented"}
