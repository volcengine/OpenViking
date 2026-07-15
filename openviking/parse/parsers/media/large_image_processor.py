# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Large image processing utilities for OpenViking.

This module provides functionality to:
1. Detect large images (>10MB or any dimension >4096px)
2. Split into tiles (<=1MB, <=2048px each)
3. Split images into grid tiles with overlap
4. Generate grid overlay images with tile labels
"""

import io
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from openviking_cli.utils.config.parser_config import ImageConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Thresholds for triggering large image processing (fallback defaults when no config)
MAX_FILE_SIZE_MB = 10.0  # 10 MB
LARGE_IMAGE_THRESHOLD_DIMENSION = 4096  # 4096 pixels

# Target limits for individual tiles
MAX_TILE_SIZE_MB = 1.0  # 1 MB
MAX_TILE_DIMENSION_PX = 2048  # 2048 pixels
TILE_OVERLAP_PX = 2  # 2 pixels of overlap on each side

# Quality settings for JPEG compression
PREVIEW_QUALITY_START = 85
MIN_PREVIEW_QUALITY = 20
TILE_QUALITY = 90

# Preview dimension fallback (when no config provides preview_max_dimension)
PREVIEW_MAX_DIMENSION = 2048


@dataclass
class TileInfo:
    """Information about a single image tile."""
    row: int
    col: int
    x1: int
    y1: int
    x2: int
    y2: int
    width: int
    height: int
    filename: str
    bytes_data: Optional[bytes] = None


@dataclass
class LargeImageResult:
    """Result of processing a large image."""
    needs_processing: bool  # True if large image processing was triggered
    preview_bytes: bytes  # Low-resolution preview image
    preview_filename: str
    grid_overlay_bytes: Optional[bytes] = None
    grid_overlay_filename: Optional[str] = None
    tiles: Optional[List[TileInfo]] = None
    total_rows: int = 0
    total_cols: int = 0
    original_width: int = 0
    original_height: int = 0
    original_format: str = ""


def get_image_size_mb(file_path: Path) -> float:
    """Get file size in megabytes."""
    return file_path.stat().st_size / (1024 * 1024)


def save_image_to_bytes(img: Image.Image, format: str = "JPEG", quality: int = 90) -> bytes:
    """Save PIL Image to bytes."""
    buf = io.BytesIO()
    if format.upper() == "PNG":
        img.save(buf, format=format, optimize=True)
    else:
        img.save(buf, format=format, quality=quality, optimize=True)
    return buf.getvalue()


def get_bytes_size_mb(data: bytes) -> float:
    """Get size of bytes in megabytes."""
    return len(data) / (1024 * 1024)


def needs_large_image_processing(
    file_path: Path,
    width: int,
    height: int,
    config: Optional[ImageConfig] = None
) -> bool:
    """
    Determine if an image needs large image processing.

    Args:
        file_path: Path to the image file
        width: Image width in pixels
        height: Image height in pixels
        config: Optional ImageConfig for custom thresholds

    Returns:
        True if large image processing is needed
    """
    max_file_size_mb = config.max_file_size_mb if config else MAX_FILE_SIZE_MB
    max_dimension_px = config.large_image_threshold_dimension if config else LARGE_IMAGE_THRESHOLD_DIMENSION

    file_size_mb = get_image_size_mb(file_path)
    return file_size_mb > max_file_size_mb or width > max_dimension_px or height > max_dimension_px


def create_low_res_preview(
    img: Image.Image,
    target_size_mb: float = MAX_TILE_SIZE_MB,
    config: Optional[ImageConfig] = None
) -> bytes:
    """
    Create a low-resolution preview of an image that's under target size.

    Args:
        img: Original PIL Image
        target_size_mb: Target maximum file size in MB
        config: Optional ImageConfig for custom thresholds

    Returns:
        Preview image bytes in JPEG format
    """
    max_dimension_px = config.preview_max_dimension if config else PREVIEW_MAX_DIMENSION

    # Work on a copy
    img = img.copy()
    original_width, original_height = img.size

    # Convert to RGB if needed (for PNGs with alpha)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Start with reducing dimensions first
    if original_width > max_dimension_px or original_height > max_dimension_px:
        ratio = min(max_dimension_px / original_width, max_dimension_px / original_height)
        new_width = int(original_width * ratio)
        new_height = int(original_height * ratio)
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        logger.debug(f"Resized preview to {new_width}x{new_height}")

    # Adjust quality to meet size constraint
    quality = PREVIEW_QUALITY_START
    img_bytes = None
    while quality >= MIN_PREVIEW_QUALITY:
        img_bytes = save_image_to_bytes(img, format="JPEG", quality=quality)
        if get_bytes_size_mb(img_bytes) <= target_size_mb:
            logger.debug(f"Preview created at quality {quality}, size {get_bytes_size_mb(img_bytes):.2f}MB")
            return img_bytes
        quality -= 5

    # If even lowest quality is too big, resize more aggressively
    current_width, current_height = img.size
    scale_factor = 0.7
    while (img_bytes is None or get_bytes_size_mb(img_bytes) > target_size_mb) and scale_factor > 0.3:
        new_width = int(current_width * scale_factor)
        new_height = int(current_height * scale_factor)
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        img_bytes = save_image_to_bytes(img, format="JPEG", quality=MIN_PREVIEW_QUALITY)
        scale_factor -= 0.1
        logger.debug(f"Further resized to {new_width}x{new_height}")

    # Ensure we always return something
    if img_bytes is None:
        img_bytes = save_image_to_bytes(img, format="JPEG", quality=MIN_PREVIEW_QUALITY)

    return img_bytes


def calculate_grid_dimensions(
    width: int,
    height: int,
    config: Optional[ImageConfig] = None
) -> Tuple[int, int]:
    """
    Calculate optimal grid dimensions (rows x cols) for an image.

    Tries to keep tiles as square as possible and caps total tile count.

    Args:
        width: Original image width
        height: Original image height
        config: Optional ImageConfig for custom thresholds

    Returns:
        Tuple of (num_rows, num_cols)
    """
    max_tile_dimension_px = config.max_tile_dimension_px if config else MAX_TILE_DIMENSION_PX
    tile_overlap_px = config.tile_overlap_px if config else TILE_OVERLAP_PX

    effective_tile = max_tile_dimension_px - tile_overlap_px * 2
    if effective_tile <= 0:
        effective_tile = max_tile_dimension_px

    cols = max(1, ceil(width / effective_tile))
    rows = max(1, ceil(height / effective_tile))

    # Cap total tiles to avoid pathological cases for extreme aspect ratios
    max_total_tiles = 64
    total = rows * cols
    if total > max_total_tiles:
        # Scale down proportionally, preserving aspect ratio as much as possible
        scale = (max_total_tiles / total) ** 0.5
        rows = max(1, ceil(rows * scale))
        cols = max(1, ceil(cols * scale))
        # Re-check and adjust
        while rows * cols > max_total_tiles:
            if cols > rows:
                cols -= 1
            else:
                rows -= 1

    return rows, cols


def calculate_tile_positions(
    width: int,
    height: int,
    rows: int,
    cols: int,
    config: Optional[ImageConfig] = None
) -> List[Tuple[int, int, int, int]]:
    """
    Calculate positions for each tile with overlap.

    Args:
        width: Original image width
        height: Original image height
        rows: Number of grid rows
        cols: Number of grid columns
        config: Optional ImageConfig for custom thresholds

    Returns:
        List of (x1, y1, x2, y2) tuples
    """
    tile_overlap_px = config.tile_overlap_px if config else TILE_OVERLAP_PX

    # Calculate base tile size without overlap
    base_tile_width = ceil(width / cols)
    base_tile_height = ceil(height / rows)

    tiles = []
    for row in range(rows):
        for col in range(cols):
            # Calculate position with overlap
            x1 = max(0, col * base_tile_width - tile_overlap_px)
            y1 = max(0, row * base_tile_height - tile_overlap_px)
            x2 = min(width, (col + 1) * base_tile_width + tile_overlap_px)
            y2 = min(height, (row + 1) * base_tile_height + tile_overlap_px)

            # Adjust first/last to ensure no gaps and no out of bounds
            if col == 0:
                x1 = 0
            if col == cols - 1:
                x2 = width
            if row == 0:
                y1 = 0
            if row == rows - 1:
                y2 = height

            tiles.append((x1, y1, x2, y2))

    return tiles


def split_image_into_tiles(
    img: Image.Image,
    rows: int,
    cols: int,
    filename_prefix: str = "tile",
    ext: str = ".jpg",
    config: Optional[ImageConfig] = None,
    original_format: str = "JPEG",
) -> List[TileInfo]:
    """
    Split an image into grid tiles.

    Args:
        img: Original PIL Image
        rows: Number of grid rows
        cols: Number of grid columns
        filename_prefix: Prefix for tile filenames
        ext: File extension for tiles
        config: Optional ImageConfig for custom thresholds
        original_format: Original image format ("PNG" or "JPEG")

    Returns:
        List of TileInfo objects
    """
    width, height = img.size
    positions = calculate_tile_positions(width, height, rows, cols, config)

    is_png = original_format.upper() == "PNG"
    save_format = "PNG" if is_png else "JPEG"

    tiles = []
    tile_idx = 0
    for row in range(rows):
        for col in range(cols):
            x1, y1, x2, y2 = positions[tile_idx]
            tile_img = img.crop((x1, y1, x2, y2))

            # Convert to RGB if needed (skip for PNG to preserve alpha)
            if not is_png and tile_img.mode in ("RGBA", "P"):
                tile_img = tile_img.convert("RGB")

            tile_bytes = save_image_to_bytes(
                tile_img, format=save_format, quality=TILE_QUALITY
            )

            filename = f"{filename_prefix}_{row+1}_{col+1}{ext}"

            tile_info = TileInfo(
                row=row + 1,
                col=col + 1,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                width=x2 - x1,
                height=y2 - y1,
                filename=filename,
                bytes_data=tile_bytes
            )
            tiles.append(tile_info)
            tile_idx += 1

    return tiles


def create_grid_overlay(
    img: Image.Image,
    rows: int,
    cols: int,
    label_prefix: str = "",
    ext: str = ".jpg",
    config: Optional[ImageConfig] = None
) -> bytes:
    """
    Create a grid overlay image with tile labels.

    Draws on a resized copy of the image to avoid memory issues with very large originals.

    Args:
        img: Original PIL Image
        rows: Number of grid rows
        cols: Number of grid columns
        label_prefix: Prefix for labels (e.g., "3_5_")
        ext: Tile file extension for labels (e.g., ".jpg", ".png")
        config: Optional ImageConfig for custom thresholds

    Returns:
        Grid overlay image bytes
    """
    tile_overlap_px = config.tile_overlap_px if config else TILE_OVERLAP_PX
    preview_max_dimension = config.preview_max_dimension if config else PREVIEW_MAX_DIMENSION

    # Resize to a reasonable size for overlay drawing
    ow, oh = img.size
    ratio = min(preview_max_dimension / ow, preview_max_dimension / oh, 1.0)
    new_w = int(ow * ratio)
    new_h = int(oh * ratio)

    overlay_img = img.copy()
    if overlay_img.mode in ("RGBA", "P"):
        overlay_img = overlay_img.convert("RGB")
    overlay_img = overlay_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    width, height = overlay_img.size
    draw = ImageDraw.Draw(overlay_img)

    # Scale positions to the resized image
    scale_x = new_w / ow
    scale_y = new_h / oh
    positions = calculate_tile_positions(ow, oh, rows, cols, config)

    # Draw grid lines (red, 2 pixels)
    grid_color = (255, 0, 0)
    line_width = 2

    tile_idx = 0
    for row in range(rows):
        for col in range(cols):
            x1, y1, x2, y2 = positions[tile_idx]
            tile_idx += 1

            # Scale positions
            sx1 = int(x1 * scale_x)
            sy1 = int(y1 * scale_y)
            sx2 = int(x2 * scale_x)
            sy2 = int(y2 * scale_y)

            # Draw rectangle around tile
            draw.rectangle([sx1, sy1, sx2, sy2], outline=grid_color, width=line_width)

            # Calculate label (use ext to match tile file extension)
            label = f"./tiles/{label_prefix}_{row+1}_{col+1}{ext}"

            # Try to find a suitable font
            font = ImageFont.load_default()
            try:
                for font_size in [16, 14, 12, 10, 8]:
                    try:
                        font = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", font_size)
                        break
                    except Exception:
                        try:
                            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttf", font_size)
                            break
                        except Exception:
                            continue
            except Exception:
                pass

            # Get text size and center
            bbox = draw.textbbox((0, 0), label, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            # Draw centered text
            center_x = sx1 + (sx2 - sx1) // 2
            center_y = sy1 + (sy2 - sy1) // 2
            text_x = center_x - text_width // 2
            text_y = center_y - text_height // 2

            # Draw background rectangle for text
            padding = 4
            draw.rectangle(
                [text_x - padding, text_y - padding,
                 text_x + text_width + padding, text_y + text_height + padding],
                fill=(255, 255, 255)
            )
            # Draw text
            draw.text((text_x, text_y), label, fill=grid_color, font=font)

    return save_image_to_bytes(overlay_img, format="JPEG", quality=90)


def process_large_image(
    file_path: Path,
    img: Optional[Image.Image] = None,
    filename_prefix: str = "image",
    config: Optional[ImageConfig] = None
) -> LargeImageResult:
    """
    Process a large image: create preview, split into tiles, generate grid overlay.

    Args:
        file_path: Path to original image file
        img: Optional pre-loaded PIL Image
        filename_prefix: Prefix for output filenames
        config: Optional ImageConfig for custom thresholds

    Returns:
        LargeImageResult with processing results
    """
    # Use config values or defaults
    max_file_size_mb = config.max_file_size_mb if config else MAX_FILE_SIZE_MB
    max_tile_size_mb = config.max_tile_size_mb if config else MAX_TILE_SIZE_MB

    # Load image if not provided
    own_img = False
    if img is None:
        img = Image.open(file_path)
        own_img = True

    try:
        width, height = img.size
        format_str = img.format or "JPEG"

        # Check if processing is needed
        if not needs_large_image_processing(file_path, width, height, config):
            # Create simple preview even for small images (for consistency)
            preview_bytes = create_low_res_preview(img, max_tile_size_mb, config)
            return LargeImageResult(
                needs_processing=False,
                preview_bytes=preview_bytes,
                preview_filename=f"{filename_prefix}_preview.jpg",
                original_width=width,
                original_height=height,
                original_format=format_str
            )

        logger.info(f"Processing large image: {width}x{height}, {get_image_size_mb(file_path):.2f}MB")

        # Calculate grid dimensions
        rows, cols = calculate_grid_dimensions(width, height, config)
        label_prefix = f"{rows}_{cols}"
        logger.info(f"Grid dimensions: {rows} rows x {cols} cols")

        # Determine tile format: preserve PNG, default to JPEG for others
        is_png = format_str.upper() == "PNG"
        tile_ext = ".png" if is_png else ".jpg"

        # Create low-res preview
        preview_bytes = create_low_res_preview(img, max_tile_size_mb, config)

        # Split into tiles
        tiles = split_image_into_tiles(
            img, rows, cols,
            filename_prefix=f"{filename_prefix}_{label_prefix}",
            ext=tile_ext,
            config=config,
            original_format=format_str,
        )

        # Create grid overlay (use full tile prefix so labels match tile filenames)
        grid_overlay_bytes = create_grid_overlay(
            img, rows, cols,
            label_prefix=f"{filename_prefix}_{label_prefix}",
            ext=tile_ext,
            config=config
        )

        return LargeImageResult(
            needs_processing=True,
            preview_bytes=preview_bytes,
            preview_filename=f"{filename_prefix}_preview.jpg",
            grid_overlay_bytes=grid_overlay_bytes,
            grid_overlay_filename=f"{filename_prefix}_grid.jpg",
            tiles=tiles,
            total_rows=rows,
            total_cols=cols,
            original_width=width,
            original_height=height,
            original_format=format_str
        )
    finally:
        if own_img:
            img.close()
