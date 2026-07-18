# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
PowerPoint (.pptx) parser for OpenViking.

Converts PowerPoint presentations to Markdown then parses using MarkdownParser.
Inspired by microsoft/markitdown approach.
"""

import asyncio
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class PowerPointParser(BaseParser):
    """
    PowerPoint presentation parser for OpenViking.

    Supports: .pptx

    Converts PowerPoint presentations to Markdown using python-pptx,
    then delegates to MarkdownParser for tree structure creation.
    """

    def __init__(self, config: Optional[ParserConfig] = None, extract_notes: bool = False):
        """
        Initialize PowerPoint parser.

        Args:
            config: Parser configuration
            extract_notes: Whether to extract speaker notes
        """
        from openviking.parse.parsers.markdown import MarkdownParser

        self._md_parser = MarkdownParser(config=config)
        self.config = config or ParserConfig()
        self.extract_notes = extract_notes

    @property
    def supported_extensions(self) -> List[str]:
        return [".pptx"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """Parse PowerPoint presentation from file path."""
        path = Path(source)

        if path.exists():
            import pptx

            from openviking_cli.utils.storage import get_storage

            storage = get_storage()
            resource_name = kwargs.get("resource_name") or kwargs.get("source_name") or path.stem

            markdown_content = await asyncio.to_thread(
                self._convert_to_markdown, path, pptx, resource_name, storage
            )
            result = await self._md_parser.parse_content(
                markdown_content,
                source_path=str(path),
                resource_name=kwargs.get("resource_name"),
                source_name=kwargs.get("source_name"),
                instruction=instruction,
                base_dir=path.parent,
                # Embedded presentation images are extracted through the shared
                # storage helper, so constrain MarkdownParser to that media root.
                allowed_media_dirs=[storage.media_dir],
            )
        else:
            result = await self._md_parser.parse_content(
                str(source),
                instruction=instruction,
                resource_name=kwargs.get("resource_name"),
                source_name=kwargs.get("source_name"),
            )
        result.source_format = "pptx"
        result.parser_name = "PowerPointParser"
        return result

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """Parse content - delegates to MarkdownParser."""
        result = await self._md_parser.parse_content(content, source_path, **kwargs)
        result.source_format = "pptx"
        result.parser_name = "PowerPointParser"
        return result

    def _convert_to_markdown(
        self, path: Path, pptx, resource_name: Optional[str] = None, storage=None
    ) -> str:
        """Convert PowerPoint presentation to Markdown string.

        Embedded picture shapes are persisted through the shared media storage
        helper and referenced inline. MarkdownParser then validates and ingests
        those local files using its existing confined-media path.
        """
        prs = pptx.Presentation(path)
        markdown_parts = []
        slide_count = len(prs.slides)
        image_counter = [0]

        for idx, slide in enumerate(prs.slides, 1):
            slide_parts = []
            slide_parts.append(f"## Slide {idx}/{slide_count}")

            title = self._extract_slide_title(slide)
            if title:
                slide_parts.append(f"### {title}")

            content = self._extract_slide_content(
                slide, resource_name=resource_name, storage=storage, image_counter=image_counter
            )
            if content:
                slide_parts.append(content)

            if self.extract_notes and slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    slide_parts.append(f"**Notes:** {notes}")

            markdown_parts.append("\n\n".join(slide_parts))

        return "\n\n---\n\n".join(markdown_parts)

    def _extract_slide_title(self, slide) -> str:
        """Extract title from a slide."""
        from pptx.enum.shapes import PP_PLACEHOLDER

        for shape in slide.shapes:
            if shape.is_placeholder:
                ph_type = shape.placeholder_format.type
                if ph_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                    return shape.text.strip()
        return ""

    def _extract_slide_content(
        self, slide, resource_name=None, storage=None, image_counter=None
    ) -> str:
        """Extract content from slide shapes."""
        from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

        content_parts = []
        image_counter = image_counter if image_counter is not None else [0]

        for shape in slide.shapes:
            if shape.is_placeholder:
                ph_type = shape.placeholder_format.type
                if ph_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                    continue

            if getattr(shape, "shape_type", None) == MSO_SHAPE_TYPE.PICTURE:
                image_md = self._convert_picture(
                    shape, resource_name=resource_name, storage=storage, image_counter=image_counter
                )
                if image_md:
                    content_parts.append(image_md)
                continue

            if hasattr(shape, "text") and shape.text.strip():
                if shape.has_table:
                    content_parts.append(self._convert_table(shape.table))
                else:
                    text = shape.text.strip()
                    if text:
                        content_parts.append(text)

        return "\n\n".join(content_parts)

    def _convert_picture(self, shape, resource_name, storage, image_counter) -> str:
        """Persist one embedded picture and return its confined Markdown reference."""
        if storage is None:
            return ""

        try:
            image = shape.image
            image_counter[0] += 1
            filename = f"image{image_counter[0]}"
            extension = f".{image.ext}" if image.ext else ".png"
            image_path = storage.save_image(
                resource_name, image.blob, filename=filename, extension=extension
            )
            rel_path = image_path.relative_to(storage.media_dir)
            return f"![{filename}]({rel_path})"
        except Exception as e:
            logger.warning(f"[PowerPointParser] Failed to save embedded picture: {e}")
            return ""

    def _convert_table(self, table) -> str:
        """Convert PowerPoint table to markdown format."""
        if not table.rows:
            return ""

        rows = []
        for row in table.rows:
            row_data = [cell.text.strip() for cell in row.cells]
            rows.append(row_data)

        from openviking.parse.base import format_table_to_markdown

        return format_table_to_markdown(rows, has_header=True)
