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
from openviking_cli.utils.config.parser_config import ImageConfig

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
        return [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse image file using three-phase architecture.

        Phase 1: Generate temporary files
        - Copy original image to temp_uri/content.{ext}
        - Generate description.md using VLM
        - (Optional) Generate ocr.md using OCR

        Phase 2: Generate semantic info
        - Generate abstract and overview based on description.md
        - Overview includes file list and usage instructions

        Phase 3: Build directory structure
        - Move all files to final URI
        - Generate .abstract.md, .overview.md

        Args:
            source: Image file path
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with image content

        Raises:
            FileNotFoundError: If source file does not exist
            IOError: If image processing fails
        """
        from openviking.storage.viking_fs import get_viking_fs

        # Convert to Path object
        file_path = Path(source) if isinstance(source, str) else source
        if not file_path.exists():
            raise FileNotFoundError(f"Image file not found: {source}")

        viking_fs = get_viking_fs()
        temp_uri = viking_fs.create_temp_uri()

        # Phase 1: Generate temporary files
        image_bytes = file_path.read_bytes()
        ext = file_path.suffix

        from openviking_cli.utils.uri import VikingURI

        root_dir_name = VikingURI.sanitize_segment(file_path.stem)
        root_dir_uri = f"{temp_uri}/{root_dir_name}"
        await viking_fs.mkdir(root_dir_uri)

        # 1.1 Save original image
        await viking_fs.write_file_bytes(f"{root_dir_uri}/content{ext}", image_bytes)

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

        # 1.3 Generate VLM description
        description = ""
        if self.config.enable_vlm:
            description = await self._vlm_describe(image_bytes, self.config.vlm_model)
        else:
            # Fallback: basic description
            description = f"Image file: {file_path.name} ({format_str}, {width}x{height})"

        await viking_fs.write_file(f"{root_dir_uri}/description.md", description)

        # 1.4 OCR (optional)
        ocr_text = None
        if self.config.enable_ocr:
            ocr_text = await self._ocr_extract(image_bytes, self.config.ocr_lang)
            if ocr_text:
                await viking_fs.write_file(f"{root_dir_uri}/ocr.md", ocr_text)

        # Create ResourceNode
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
            },
        )

        # Phase 2: Generate semantic info
        await self._generate_semantic_info(root_node, root_dir_uri, viking_fs, ocr_text is not None)

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

        TODO: Integrate with actual VLM API (OpenAI GPT-4V, Claude Vision, etc.)
        """
        # Fallback implementation - returns basic placeholder
        return "Image description (VLM integration pending)\n\nThis is an image. VLM description feature has not yet integrated external API."

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
        self, node: ResourceNode, temp_uri: str, viking_fs, has_ocr: bool
    ):
        """
        Phase 2: Generate abstract and overview.

        Args:
            node: ResourceNode to update
            temp_uri: Temporary URI
            viking_fs: VikingFS instance
            has_ocr: Whether OCR file exists
        """
        # Read description.md
        description = await viking_fs.read_file(f"{temp_uri}/description.md")

        # Generate abstract (short summary, < 100 tokens)
        abstract = description[:200] if len(description) > 200 else description

        # Generate overview (content summary + file list + usage instructions)
        overview_parts = [
            "## Content Summary\n",
            description,
            "\n\n## Available Files\n",
            f"- content.{node.meta['format']}: Original image file ({node.meta['width']}x{node.meta['height']}, {node.meta['format'].upper()} format)\n",
            "- description.md: Detailed image description generated by VLM\n",
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

        overview_parts.append("### Get VLM-generated Image Description\n")
        overview_parts.append("```python\n")
        overview_parts.append("description = await image_resource.description()\n")
        overview_parts.append("# Returns: FileContent object for further processing\n")
        overview_parts.append("# Purpose: Understand image content\n")
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
