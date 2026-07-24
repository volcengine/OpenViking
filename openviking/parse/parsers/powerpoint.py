# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
PowerPoint (.pptx) parser for OpenViking.

Converts PowerPoint presentations to Markdown then parses using MarkdownParser.
Inspired by microsoft/markitdown approach.
"""

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Union

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
        parse_kwargs = dict(kwargs)
        parse_kwargs.pop("instruction", None)

        if path.exists():
            import pptx

            from openviking_cli.utils.storage import get_storage

            storage = get_storage()
            caller_media_dirs = parse_kwargs.pop("allowed_media_dirs", None) or []
            caller_preferred_media = parse_kwargs.pop("preferred_media_paths", None) or {}
            allowed_media_dirs = []
            for media_dir in caller_media_dirs:
                media_path = Path(media_dir)
                if media_path not in allowed_media_dirs:
                    allowed_media_dirs.append(media_path)

            # A unique namespace prevents caller-controlled resource names, same
            # stems, and StoragePath sanitization collisions from sharing files.
            # MarkdownParser gets an exact reference-to-file capability map; the
            # namespace itself is never authorized as a readable directory.
            media_resource_name = self._media_resource_name(path)
            generated_media_paths: Dict[str, Path] = {}
            try:
                conversion_task = asyncio.create_task(
                    asyncio.to_thread(
                        self._convert_to_markdown,
                        path,
                        pptx,
                        media_resource_name,
                        storage,
                        generated_media_paths,
                    )
                )
                try:
                    markdown_content = await asyncio.shield(conversion_task)
                except asyncio.CancelledError:
                    # ``to_thread`` keeps running after its awaiter is cancelled.
                    # Drain it before cleanup so it cannot recreate the staging
                    # namespace after this parse has returned.
                    try:
                        await conversion_task
                    except Exception:
                        pass
                    raise

                preferred_media_paths = {
                    str(reference): Path(media_path)
                    for reference, media_path in caller_preferred_media.items()
                }
                # Generated references take precedence over caller-provided
                # mappings with the same spelling.
                preferred_media_paths.update(generated_media_paths)

                # The parser owns the converted content's source and base directory,
                # while caller-provided link-rewrite and identity options remain intact.
                parse_kwargs.pop("source_path", None)
                parse_kwargs["base_dir"] = path.parent
                parse_kwargs["allowed_media_dirs"] = allowed_media_dirs
                parse_kwargs["preferred_media_paths"] = preferred_media_paths
                result = await self._md_parser.parse_content(
                    markdown_content,
                    source_path=str(path),
                    instruction=instruction,
                    **parse_kwargs,
                )
            finally:
                # MarkdownParser has copied every accepted image into VikingFS by
                # this point. Remove the per-parse staging namespace on success,
                # failure, or cancellation so it cannot leak into a later parse.
                try:
                    storage.cleanup_resource_media(media_resource_name)
                except Exception as e:
                    logger.warning(
                        "[PowerPointParser] Failed to clean generated media namespace %s: %s",
                        media_resource_name,
                        e,
                    )
        else:
            result = await self._md_parser.parse_content(
                str(source), instruction=instruction, **parse_kwargs
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
        self,
        path: Path,
        pptx,
        resource_name: Optional[str] = None,
        storage=None,
        generated_media_paths: Optional[Dict[str, Path]] = None,
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
                slide,
                resource_name=resource_name,
                storage=storage,
                image_counter=image_counter,
                generated_media_paths=generated_media_paths,
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

    @staticmethod
    def _media_resource_name(path: Path) -> str:
        """Return a collision-resistant staging namespace for one source parse."""
        source_digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
        return f"pptx-{source_digest}-{uuid.uuid4().hex}"

    def _extract_slide_content(
        self,
        slide,
        resource_name=None,
        storage=None,
        image_counter=None,
        generated_media_paths=None,
    ) -> str:
        """Extract content from slide shapes."""
        return self._extract_shapes_content(
            slide.shapes,
            resource_name=resource_name,
            storage=storage,
            image_counter=image_counter,
            generated_media_paths=generated_media_paths,
        )

    def _extract_shapes_content(
        self,
        shapes,
        resource_name=None,
        storage=None,
        image_counter=None,
        generated_media_paths=None,
    ) -> str:
        """Extract text, tables, and pictures from a shape collection.

        Picture placeholders retain ``PLACEHOLDER`` as their shape type after
        an image is inserted. Group shapes are containers, so recurse into
        their children while preserving document order.
        """
        from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

        content_parts = []
        image_counter = image_counter if image_counter is not None else [0]

        for shape in shapes:
            if shape.is_placeholder:
                ph_type = shape.placeholder_format.type
                if ph_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                    continue

            shape_type = getattr(shape, "shape_type", None)
            if shape_type == MSO_SHAPE_TYPE.GROUP:
                grouped_content = self._extract_shapes_content(
                    shape.shapes,
                    resource_name=resource_name,
                    storage=storage,
                    image_counter=image_counter,
                    generated_media_paths=generated_media_paths,
                )
                if grouped_content:
                    content_parts.append(grouped_content)
                continue

            if shape_type == MSO_SHAPE_TYPE.PICTURE or (
                shape_type == MSO_SHAPE_TYPE.PLACEHOLDER and hasattr(shape, "image")
            ):
                image_md = self._convert_picture(
                    shape,
                    resource_name=resource_name,
                    storage=storage,
                    image_counter=image_counter,
                    generated_media_paths=generated_media_paths,
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

    def _convert_picture(
        self, shape, resource_name, storage, image_counter, generated_media_paths=None
    ) -> str:
        """Persist one embedded picture and return its confined Markdown reference."""
        if storage is None:
            return ""

        try:
            image = shape.image
            image_bytes = image.blob
            image_counter[0] += 1
            content_digest = hashlib.sha256(image_bytes).hexdigest()[:12]
            filename = f"image{image_counter[0]}_{content_digest}"
            extension = f".{image.ext}" if image.ext else ".png"
            image_path = storage.save_image(
                resource_name, image_bytes, filename=filename, extension=extension
            )
            # Keep generated references independent of the resource directory
            # name. Besides avoiding Markdown delimiter characters in that
            # name, this lets parse() authorize only this resource's media root
            # instead of the global media directory shared by sibling inputs.
            resource_media_root = storage.get_resource_media_dir(resource_name, "images").parent
            rel_path = image_path.relative_to(resource_media_root)
            reference = rel_path.as_posix()
            if generated_media_paths is not None:
                generated_media_paths[reference] = image_path.resolve()
            return f"![{filename}]({reference})"
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
