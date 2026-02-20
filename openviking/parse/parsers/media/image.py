# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Media parser interfaces for OpenViking - Future expansion.

This module defines parser interfaces for media types (image, audio, video).
These are placeholder implementations that raise NotImplementedError.
They serve as a design reference for future media parsing capabilities.

For current document parsing (PDF, Markdown, HTML, Text), see other parser modules.
"""

from pathlib import Path
from typing import List, Optional, Union

from PIL import Image

from openviking.parse.base import NodeType, ParseResult, ResourceNode
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import IMAGE_EXTENSIONS
from openviking.prompts import render_prompt
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.config import get_openviking_config
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

    Supported formats: PNG, JPG, JPEG, GIF, BMP, WEBP, SVG
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
        Parse image file - only copy original file and extract basic metadata, no content understanding.

        Args:
            source: Image file path
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with image content

        Raises:
            FileNotFoundError: If source file does not exist
            IOError: If image processing fails
        """
        # Convert to Path object
        file_path = Path(source) if isinstance(source, str) else source
        if not file_path.exists():
            raise FileNotFoundError(f"Image file not found: {source}")

        viking_fs = get_viking_fs()
        temp_uri = viking_fs.create_temp_uri()

        # Phase 1: Generate temporary files
        image_bytes = file_path.read_bytes()
        ext = file_path.suffix

        # Sanitize original filename (replace spaces with underscores)
        original_filename = file_path.name.replace(" ", "_")
        # Root directory name: filename stem + _ + extension (without dot)
        stem = file_path.stem.replace(" ", "_")
        ext_no_dot = ext[1:] if ext else ""
        root_dir_name = VikingURI.sanitize_segment(f"{stem}_{ext_no_dot}")
        root_dir_uri = f"{temp_uri}/{root_dir_name}"
        await viking_fs.mkdir(root_dir_uri)

        # 1.1 Save original image with original filename (sanitized)
        await viking_fs.write_file_bytes(f"{root_dir_uri}/{original_filename}", image_bytes)

        # 1.2 Validate and extract image metadata
        try:
            img = Image.open(file_path)
            img.verify()  # Verify that it's a valid image
            img.close()  # Close and reopen to reset after verify()
            img = Image.open(file_path)
            width, height = img.size
            format_str = img.format or ext[1:].upper()
        except Exception as e:
            raise ValueError(f"Invalid image file: {file_path}. Error: {e}") from e

        # Create ResourceNode - metadata only, no content understanding yet
        root_node = ResourceNode(
            type=NodeType.ROOT,
            title=file_path.stem,
            level=0,
            detail_file=None,
            content_path=None,
            children=[],
            meta={
                "width": width,
                "height": height,
                "format": format_str.lower(),
                "content_type": "image",
                "source_title": file_path.stem,
                "semantic_name": file_path.stem,
                "original_filename": original_filename,
            },
        )

        # Phase 3: Build directory structure (handled by TreeBuilder)
        return ParseResult(
            root=root_node,
            source_path=str(file_path),
            temp_dir_path=temp_uri,
            source_format="image",
            parser_name="ImageParser",
            meta={"content_type": "image", "format": format_str.lower()},
        )

    async def _vlm_describe(self, image_bytes: bytes, model: Optional[str]) -> str:
        """
        Generate image description using VLM.

        Args:
            image_bytes: Image binary data
            model: VLM model name

        Returns:
            Image description in markdown format
        """
        try:
            vlm = get_openviking_config().vlm

            # Render prompt
            prompt = render_prompt(
                "parsing.image_summary",
                {
                    "context": "No additional context",
                },
            )
            response = await vlm.get_vision_completion_async(
                prompt=prompt,
                images=[image_bytes],
            )
            logger.info(
                f"[ImageParser._vlm_describe] VLM response received, length: {len(response)}, content: {response[:256]}"
            )

            return response.strip()

        except Exception as e:
            logger.error(
                f"[ImageParser._vlm_describe] Error in VLM image description: {e}", exc_info=True
            )
            # Fallback to basic description
            return "Image description (VLM integration failed)\n\nThis is an image file."

    async def _ocr_extract(self, image_bytes: bytes, lang: str) -> Optional[str]:
        """
        Extract text from image using OCR.

        Args:
            image_bytes: Image binary data
            lang: OCR language code

        Returns:
            Extracted text in markdown format, or None if no text found

        TODO: Integrate with OCR API (Tesseract, PaddleOCR, etc.)
        """
        # Not implemented - return None
        return None

    async def _generate_semantic_info(
        self, node: ResourceNode, description: str, viking_fs, has_ocr: bool, root_dir_uri: str
    ):
        """
        Phase 2: Generate abstract and overview and write to .abstract.md and .overview.md.

        Args:
            node: ResourceNode to update
            description: Image description
            viking_fs: VikingFS instance
            has_ocr: Whether OCR file exists
            root_dir_uri: Root directory URI to write semantic files
        """
        # Generate abstract (short summary, < 100 tokens)
        abstract = description[:253] + "..." if len(description) > 256 else description

        # Generate overview (content summary + file list + usage instructions)
        overview_parts = [
            "## Content Summary\n",
            description,
            "\n\n## Available Files\n",
            f"- {node.meta['original_filename']}: Original image file ({node.meta['width']}x{node.meta['height']}, {node.meta['format'].upper()} format)\n",
        ]

        if has_ocr:
            overview_parts.append("- ocr.md: OCR text recognition result from the image\n")

        overview_parts.append("\n## Usage\n")
        overview_parts.append("### View Image\n")
        overview_parts.append("```python\n")
        overview_parts.append("image_bytes = await image_resource.view()\n")
        overview_parts.append("# Returns: PNG/JPG format image binary data\n")
        overview_parts.append("# Purpose: Display or save the image\n")
        overview_parts.append("```\n\n")

        if has_ocr:
            overview_parts.append("### Get OCR-recognized Text\n")
            overview_parts.append("```python\n")
            overview_parts.append("ocr_text = await image_resource.ocr()\n")
            overview_parts.append("# Returns: FileContent object or None\n")
            overview_parts.append("# Purpose: Extract text information from the image\n")
            overview_parts.append("```\n\n")

        overview_parts.append("### Get Image Metadata\n")
        overview_parts.append("```python\n")
        overview_parts.append(
            f"size = image_resource.get_size()  # ({node.meta['width']}, {node.meta['height']})\n"
        )
        overview_parts.append(f'format = image_resource.get_format()  # "{node.meta["format"]}"\n')
        overview_parts.append("```\n")

        overview = "".join(overview_parts)

        # Store in node meta
        node.meta["abstract"] = abstract
        node.meta["overview"] = overview

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
