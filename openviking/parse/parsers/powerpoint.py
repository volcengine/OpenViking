# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
PowerPoint (.pptx) parser for OpenViking.

Converts PowerPoint presentations to Markdown then parses using MarkdownParser.
Inspired by microsoft/markitdown approach.
"""

import asyncio
import json
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
                # pptx images are extracted into storage.media_dir, mirroring pdf/word.
                allowed_media_dirs=[storage.media_dir],
            )
        else:
            result = await self._md_parser.parse_content(
                str(source), instruction=instruction, **kwargs
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
        """Convert PowerPoint presentation to Markdown string."""
        prs = pptx.Presentation(path)
        markdown_parts = []
        slide_count = len(prs.slides)

        for idx, slide in enumerate(prs.slides, 1):
            slide_parts = []
            slide_parts.append(f"## Slide {idx}/{slide_count}")

            title = self._extract_slide_title(slide)
            if title:
                slide_parts.append(f"### {title}")

            content = self._extract_slide_content(
                slide, slide_idx=idx, slide_title=title, resource_name=resource_name, storage=storage
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
        self,
        slide,
        slide_idx: int,
        slide_title: str = "",
        resource_name: Optional[str] = None,
        storage=None,
    ) -> str:
        """Extract content from slide shapes."""
        from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

        content_parts = []
        image_counter = 0

        for shape in slide.shapes:
            if shape.is_placeholder:
                ph_type = shape.placeholder_format.type
                if ph_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                    continue

            # Tables: emit JSON block + plain markdown so structured retrieval and
            # keyword search both succeed.
            if getattr(shape, "has_table", False):
                table_md = self._convert_table_with_json(
                    shape.table, slide_idx=slide_idx, slide_title=slide_title
                )
                if table_md:
                    content_parts.append(table_md)
                continue

            # Pictures: persist via the shared storage helper (#2429 pattern) and
            # reference by relative media path so MarkdownParser can rewrite to viking://.
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE and storage is not None:
                image_counter += 1
                img_md = self._save_picture_shape(
                    shape,
                    slide_idx=slide_idx,
                    image_idx=image_counter,
                    resource_name=resource_name,
                    storage=storage,
                )
                if img_md:
                    content_parts.append(img_md)
                continue

            if hasattr(shape, "text") and shape.text.strip():
                content_parts.append(shape.text.strip())

        return "\n\n".join(content_parts)

    def _save_picture_shape(
        self, shape, slide_idx: int, image_idx: int, resource_name: Optional[str], storage
    ) -> str:
        """Persist a Picture shape's blob and return a markdown image reference."""
        try:
            image = shape.image
            image_bytes = image.blob
            extension = f".{image.ext}" if image.ext else ".png"
        except Exception as e:
            logger.warning(
                f"[PowerPointParser] Failed to read picture on slide {slide_idx} idx {image_idx}: {e}"
            )
            return ""

        try:
            filename = f"slide{slide_idx}_image{image_idx}"
            image_path = storage.save_image(
                resource_name, image_bytes, filename=filename, extension=extension
            )
            rel_path = image_path.relative_to(storage.media_dir)
            alt = f"slide{slide_idx}_image{image_idx}"
            return f"![{alt}]({rel_path})"
        except Exception as e:
            logger.warning(
                f"[PowerPointParser] Failed to save picture on slide {slide_idx} idx {image_idx}: {e}"
            )
            return ""

    def _convert_table_with_json(
        self, table, slide_idx: int, slide_title: str = ""
    ) -> str:
        """Emit fenced JSON block + plain markdown table for a slide table."""
        if not table.rows:
            return ""

        rows = []
        for row in table.rows:
            row_data = [cell.text.strip() for cell in row.cells]
            rows.append(row_data)

        if not rows:
            return ""

        caption = (
            f"Table from slide {slide_idx}: {slide_title}".strip()
            if slide_title
            else f"Table from slide {slide_idx}"
        )
        json_payload = json.dumps({"rows": rows}, ensure_ascii=False)
        json_block = f"{caption}\n\n```json\n{json_payload}\n```"

        from openviking.parse.base import format_table_to_markdown

        md_table = format_table_to_markdown(rows, has_header=True)
        return f"{json_block}\n\n{md_table}" if md_table else json_block
