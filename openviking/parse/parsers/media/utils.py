# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Media-related utilities for OpenViking."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from openviking.prompts import render_prompt
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

from .constants import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

logger = get_logger(__name__)


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
    image_uri: str, original_filename: str, llm_sem: Optional[asyncio.Semaphore] = None
) -> Dict[str, Any]:
    """
    Generate summary for an image file using VLM.

    Args:
        image_uri: URI to the image file in VikingFS
        original_filename: Original filename of the image

    Returns:
        Dictionary with "name" and "summary" keys
    """
    viking_fs = get_viking_fs()
    vlm = get_openviking_config().vlm
    file_name = original_filename

    try:
        # Read image bytes
        image_bytes = await viking_fs.read_file_bytes(image_uri)
        if not isinstance(image_bytes, bytes):
            raise ValueError(f"Expected bytes for image file, got {type(image_bytes)}")

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

    except Exception as e:
        logger.error(
            f"[MediaUtils.generate_image_summary] Failed to generate image summary: {e}",
            exc_info=True,
        )
        return {"name": file_name, "summary": "Image summary generation failed"}


async def generate_audio_summary(
    audio_uri: str, original_filename: str, llm_sem: Optional[asyncio.Semaphore] = None
) -> Dict[str, Any]:
    """
    Generate summary for an audio file (placeholder).

    Args:
        audio_uri: URI to the audio file in VikingFS
        original_filename: Original filename of the audio

    Returns:
        Dictionary with "name" and "summary" keys
    """
    logger.info(
        f"[MediaUtils.generate_audio_summary] Audio summary generation not yet implemented for: {audio_uri}"
    )
    return {"name": original_filename, "summary": "Audio summary generation not yet implemented"}


async def generate_video_summary(
    video_uri: str, original_filename: str, llm_sem: Optional[asyncio.Semaphore] = None
) -> Dict[str, Any]:
    """
    Generate summary for a video file (placeholder).

    Args:
        video_uri: URI to the video file in VikingFS
        original_filename: Original filename of the video

    Returns:
        Dictionary with "name" and "summary" keys
    """
    logger.info(
        f"[MediaUtils.generate_video_summary] Video summary generation not yet implemented for: {video_uri}"
    )
    return {"name": original_filename, "summary": "Video summary generation not yet implemented"}
