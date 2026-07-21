# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Media parser interfaces for OpenViking - Future expansion.

This module defines parser interfaces for media types (image, audio, video).
These are placeholder implementations that raise NotImplementedError.
They serve as a design reference for future media parsing capabilities.

For current document parsing (PDF, Markdown, HTML, Text), see other parser modules.
"""

import io
from pathlib import Path
from typing import List, Optional, Union

from PIL import Image

from openviking.parse.base import NodeType, ParseResult, ResourceNode
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import IMAGE_EXTENSIONS
from openviking.parse.parsers.media.large_image_processor import (
    LargeImageResult,
    process_large_image,
    save_image_to_bytes,
)
from openviking.parse.parsers.media.naming import resolve_media_names
from openviking.parse.parsers.media.utils import _convert_svg_to_png
from openviking.prompts import render_prompt
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.config.parser_config import ImageConfig
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)

# =============================================================================
# Configuration Classes
# =============================================================================


# =============================================================================
# Parser Classes
# =============================================================================


class ImageParser(BaseParser):
    """
    Image parser - Future implementation.

    Planned Features:
    1. Visual content understanding using VLM (Vision Language Model)
    2. OCR text extraction for images containing text
    3. Metadata extraction (dimensions, format, EXIF data)
    4. Generate semantic description and structured ResourceNode

    Example workflow:
        1. Load image file
        2. (Optional) Perform OCR to extract text
        3. (Optional) Use VLM to generate visual description
        4. Create ResourceNode with image metadata and descriptions
        5. Return ParseResult

    Supported formats: PNG, JPG, JPEG, GIF, BMP, WEBP, SVG, TIFF, ICO, DIB, ICNS, SGI, JP2
    """

    def __init__(self, config: Optional[ImageConfig] = None, **kwargs):
        """
        Initialize ImageParser.

        Args:
            config: Image parsing configuration
            **kwargs: Additional configuration parameters
        """
        self.config = config or ImageConfig()

    @property
    def supported_extensions(self) -> List[str]:
        """Return supported image file extensions."""
        return IMAGE_EXTENSIONS

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse image file:
        - For large images (>10MB or any dimension >4096px):
          - Create low-res preview (<=1MB) as the "original"
          - Split into tiles (<=2048px each)
          - Generate grid overlay image
        - For small images: just save as-is with metadata

        Args:
            source: Image file path
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with image content

        Raises:
            FileNotFoundError: If source file doesn't exist
            ValueError: If image processing fails
        """
        # Convert to Path object
        file_path = Path(source) if isinstance(source, str) else source
        if not file_path.exists():
            raise FileNotFoundError(f"Image file not found: {source}")

        viking_fs = get_viking_fs()
        temp_uri = viking_fs.create_temp_uri()

        # Load image (SVG is converted to PNG first since PIL doesn't support it)
        # For other formats not natively supported by VLM/embedding, convert to PNG
        # to ensure end-to-end compatibility with image understanding and vectorization
        try:
            file_bytes = file_path.read_bytes()
            if file_path.suffix.lower() == ".svg" or file_bytes[:4] == b"<svg" or (file_bytes[:5] == b"<?xml" and b"<svg" in file_bytes[:100]):
                png_bytes = _convert_svg_to_png(file_bytes)
                if png_bytes is None:
                    raise ValueError(
                        f"SVG files require cairosvg. Install it: pip install cairosvg"
                    )
                img = Image.open(io.BytesIO(png_bytes))
                img.verify()
                img.close()
                img = Image.open(io.BytesIO(png_bytes))
                # Save as PNG in output
                needs_png_conversion = True
                converted_png_bytes = png_bytes  # Save for small-image branch
            else:
                # Check if extension is in VLM-natively supported list
                suffix_lower = file_path.suffix.lower()
                supported_by_vlm = suffix_lower in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
                if supported_by_vlm:
                    img = Image.open(file_path)
                    img.verify()  # Verify that it's a valid image
                    img.close()  # Close and reopen to reset after verify()
                    img = Image.open(file_path)
                    needs_png_conversion = False
                else:
                    # Convert unsupported formats to PNG for VLM compatibility
                    img = Image.open(file_path)
                    img.verify()
                    img.close()
                    img = Image.open(file_path)
                    needs_png_conversion = True
            width, height = img.size
            format_str = img.format or file_path.suffix[1:].upper() if file_path.suffix else "JPEG"
        except Exception as e:
            raise ValueError(f"Invalid image file: {file_path}. Error: {e}") from e

        # Resolve names
        display_stem, stem, original_filename = resolve_media_names(file_path, file_path.suffix, **kwargs)
        ext_no_dot = file_path.suffix[1:] if file_path.suffix else "jpg"
        if needs_png_conversion:
            # Convert to PNG and change extension for VLM/embedding compatibility
            ext_no_dot = "png"
            original_filename = f"{stem}.png"
            format_str = "png"

        root_dir_name = VikingURI.sanitize_segment(f"{stem}_{ext_no_dot}")
        root_dir_uri = f"{temp_uri}/{root_dir_name}"
        await viking_fs.mkdir(root_dir_uri, exist_ok=True)

        # Process the image (check if large)
        large_image_result = process_large_image(file_path, img, filename_prefix=stem, config=self.config)
        img.close()

        # Create root node with metadata
        root_node = ResourceNode(
            type=NodeType.ROOT,
            title=display_stem,
            level=0,
            detail_file=None,
            content_path=None,
            children=[],
            meta={
                "width": width,
                "height": height,
                "format": format_str.lower(),
                "content_type": "image",
                "source_title": display_stem,
                "semantic_name": display_stem,
                "original_filename": original_filename,
                "is_large_image": large_image_result.needs_processing,
            },
        )

        if large_image_result.needs_processing:
            # Large image processing mode: save preview, grid, tiles
            # NOTE: The original file is intentionally NOT saved. Large images can be
            # hundreds of MB; storing the full-resolution original would exceed storage
            # budgets. The preview + tiles are the canonical representation. The
            # `original_filename` metadata field records the original name for reference
            # but the corresponding file is not persisted.
            logger.info(f"Processing large image {original_filename}: {width}x{height}")

            # Save low-res preview (original is too large to store)
            await viking_fs.write_file_bytes(
                f"{root_dir_uri}/{large_image_result.preview_filename}",
                large_image_result.preview_bytes
            )
            root_node.meta["preview_filename"] = large_image_result.preview_filename

            # Save grid overlay
            if large_image_result.grid_overlay_bytes:
                grid_filename = large_image_result.grid_overlay_filename or f"{stem}_grid.jpg"
                await viking_fs.write_file_bytes(
                    f"{root_dir_uri}/{grid_filename}",
                    large_image_result.grid_overlay_bytes
                )
                root_node.meta["grid_overlay"] = grid_filename

            # Create tiles directory
            if large_image_result.tiles:
                tiles_dir_name = "tiles"
                tiles_dir_uri = f"{root_dir_uri}/{tiles_dir_name}"
                await viking_fs.mkdir(tiles_dir_uri, exist_ok=True)

                # Save all tiles
                for tile in large_image_result.tiles:
                    if tile.bytes_data:
                        await viking_fs.write_file_bytes(
                            f"{tiles_dir_uri}/{tile.filename}",
                            tile.bytes_data
                        )

                # Update metadata
                root_node.meta["tiles_dir"] = tiles_dir_name
                root_node.meta["num_tiles"] = len(large_image_result.tiles)
                root_node.meta["grid_rows"] = large_image_result.total_rows
                root_node.meta["grid_cols"] = large_image_result.total_cols
                root_node.meta["original_width"] = width
                root_node.meta["original_height"] = height

        else:
            # Small image: save as PNG if format is not VLM-supported, else as-is
            if needs_png_conversion:
                # SVG was already converted to PNG bytes during loading
                if file_path.suffix.lower() == ".svg":
                    image_bytes = converted_png_bytes
                else:
                    with Image.open(file_path) as converted_img:
                        image_bytes = save_image_to_bytes(converted_img, format="PNG")
            else:
                image_bytes = file_path.read_bytes()
            await viking_fs.write_file_bytes(f"{root_dir_uri}/{original_filename}", image_bytes)

        # Phase 3: Build directory structure (handled by TreeBuilder)
        return ParseResult(
            root=root_node,
            source_path=str(file_path),
            temp_dir_path=temp_uri,
            source_format="image",
            parser_name="ImageParser",
            meta={
                "content_type": "image",
                "format": format_str.lower(),
                "is_large_image": large_image_result.needs_processing,
            },
        )

        # Write to files in temp directory
        # await viking_fs.write_file(f"{root_dir_uri}/.abstract.md", abstract)
        # await viking_fs.write_file(f"{root_dir_uri}/.overview.md", overview)

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse image from content string - Not yet implemented.

        Args:
            content: Image content (base64 or binary string)
            source_path: Optional source path for metadata
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with image content

        Raises:
            NotImplementedError: This feature is not yet implemented
        """
        raise NotImplementedError("Image parsing not yet implemented")
