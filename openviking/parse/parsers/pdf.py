# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PDF parser using pdf-inspector for structure and pdfplumber for images."""

import asyncio
import hashlib
import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openviking.parse.base import NodeType, ParseResult, ResourceNode, create_parse_result
from openviking.parse.image_validation import is_valid_image
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.parser_config import PDFConfig

logger = get_logger(__name__)


@dataclass
class _PdfTextExtraction:
    pages: dict[int, str]
    warnings: list[str]
    meta: dict[str, Any]


@dataclass
class _PdfImageExtraction:
    pages: dict[int, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    images_extracted: int = 0
    images_deduplicated: int = 0


class PDFParser(BaseParser):
    """Convert a PDF to page-ordered Markdown and then build its context tree."""

    def __init__(self, config: Optional[PDFConfig] = None):
        self.config = config or PDFConfig()
        self.config.validate()
        self._markdown_parser = None

    def _get_markdown_parser(self):
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser(config=self.config)
        return self._markdown_parser

    @property
    def supported_extensions(self) -> List[str]:
        return [".pdf"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        started = time.time()
        path = Path(source)
        resource_name = kwargs.get("resource_name") or kwargs.get("source_name")
        if not path.is_file():
            return self._failed_result(path, started, f"File not found: {path}")

        from openviking_cli.utils.storage import get_storage

        storage = get_storage()
        try:
            markdown, conversion_meta, conversion_warnings = await self._convert_to_markdown(
                path,
                storage=storage,
                resource_name=resource_name or path.stem,
            )
            markdown_kwargs = dict(kwargs)
            caller_media_dirs = list(markdown_kwargs.pop("allowed_media_dirs", None) or [])
            caller_media_dirs.append(storage.media_dir)
            result = await self._get_markdown_parser().parse_content(
                markdown,
                source_path=str(path),
                instruction=instruction,
                base_dir=path.parent,
                allowed_media_dirs=caller_media_dirs,
                **markdown_kwargs,
            )
            result.source_format = "pdf"
            result.parser_name = "PDFParser"
            result.parser_version = "3.0"
            result.parse_time = time.time() - started
            result.warnings.extend(conversion_warnings)
            result.meta.update(conversion_meta)
            result.meta["intermediate_markdown_length"] = len(markdown)
            logger.info(
                f"PDF parsed successfully: {path.name} "
                f"({len(markdown)} chars markdown, {result.parse_time:.2f}s)"
            )
            return result
        except Exception as exc:
            logger.error(f"Failed to parse PDF {path}: {exc}", exc_info=True)
            return self._failed_result(path, started, f"Failed to parse PDF: {exc}")

    def _failed_result(self, path: Path, started: float, warning: str) -> ParseResult:
        return create_parse_result(
            root=ResourceNode(type=NodeType.ROOT),
            source_path=str(path),
            source_format="pdf",
            parser_name="PDFParser",
            parse_time=time.time() - started,
            warnings=[warning],
        )

    async def _convert_to_markdown(
        self,
        path: Path,
        *,
        storage: Any,
        resource_name: str,
    ) -> tuple[str, Dict[str, Any], list[str]]:
        text_result, image_result = await asyncio.gather(
            asyncio.to_thread(self._extract_text_sync, path),
            asyncio.to_thread(self._extract_images_sync, path, storage, resource_name),
            return_exceptions=True,
        )
        if isinstance(text_result, BaseException):
            raise text_result
        if isinstance(image_result, BaseException):
            image_result = _PdfImageExtraction(
                warnings=[f"PDF image extraction failed: {image_result}"]
            )

        page_numbers = sorted(set(text_result.pages) | set(image_result.pages))
        parts: list[str] = []
        for page_number in page_numbers:
            page_parts: list[str] = []
            page_markdown = text_result.pages.get(page_number, "").strip()
            if page_markdown:
                page_parts.append(page_markdown)
            page_parts.extend(image_result.pages.get(page_number, []))
            if page_parts:
                parts.append(f"<!-- Page {page_number} -->\n\n" + "\n\n".join(page_parts))
        if not parts:
            raise ValueError("No meaningful text or images extracted from PDF")

        meta = dict(text_result.meta)
        meta.update(
            {
                "library": "pdf-inspector",
                "image_library": "pdfplumber",
                "images_extracted": image_result.images_extracted,
                "images_deduplicated": image_result.images_deduplicated,
            }
        )
        return "\n\n".join(parts), meta, text_result.warnings + image_result.warnings

    @staticmethod
    def _extract_text_sync(path: Path) -> _PdfTextExtraction:
        import pdf_inspector

        extraction = pdf_inspector.extract_pages_markdown(str(path))
        pages = {page.page + 1: page.markdown or "" for page in extraction.pages}
        reason_by_page = {
            reason.page: list(reason.reasons) for reason in extraction.ocr_reasons_by_page
        }
        reasons = [
            {"page": page, "reasons": page_reasons} for page, page_reasons in reason_by_page.items()
        ]
        warnings = [
            f"PDF page {page} requires OCR: {', '.join(reason_by_page.get(page, ['unknown']))}"
            for page in extraction.pages_needing_ocr
        ]
        meta = {
            "total_pages": len(extraction.pages),
            "pages_processed": sum(bool(markdown.strip()) for markdown in pages.values()),
            "pages_needing_ocr": list(extraction.pages_needing_ocr),
            "ocr_reasons_by_page": reasons,
            "pages_with_tables": list(extraction.pages_with_tables),
            "pages_with_columns": list(extraction.pages_with_columns),
            "is_complex_layout": extraction.is_complex,
        }
        return _PdfTextExtraction(pages=pages, warnings=warnings, meta=meta)

    def _extract_images_sync(
        self,
        path: Path,
        storage: Any,
        resource_name: str,
    ) -> _PdfImageExtraction:
        import pdfplumber

        result = _PdfImageExtraction()
        with pdfplumber.open(str(path)) as pdf:
            for page_number, page in enumerate(pdf.pages, 1):
                try:
                    seen_boxes: set[tuple[float, float, float, float]] = set()
                    seen_digests: set[bytes] = set()
                    page_images: list[str] = []
                    for image_index, image in enumerate(page.images or [], 1):
                        try:
                            box = (
                                round(image["x0"], 1),
                                round(image["top"], 1),
                                round(image["x1"], 1),
                                round(image["bottom"], 1),
                            )
                            if box in seen_boxes:
                                result.images_deduplicated += 1
                                continue
                            seen_boxes.add(box)
                            image_bytes = self._extract_image_from_page(page, image)
                            if image_bytes is None:
                                continue
                            filename = f"page{page_number}_img{image_index}"
                            if not is_valid_image(image_bytes, Path(f"{filename}.png")):
                                continue
                            digest = hashlib.md5(image_bytes, usedforsecurity=False).digest()
                            if digest in seen_digests:
                                result.images_deduplicated += 1
                                continue
                            seen_digests.add(digest)
                            image_path = storage.save_image(
                                resource_name,
                                image_bytes,
                                filename=filename,
                                extension=".png",
                            )
                            relative = image_path.relative_to(storage.media_dir).as_posix()
                            page_images.append(
                                f"![Page {page_number} Image {image_index}]({relative})"
                            )
                            result.images_extracted += 1
                        except Exception as exc:
                            warning = (
                                f"Failed to extract image {image_index} on page "
                                f"{page_number}: {exc}"
                            )
                            logger.warning(warning)
                            result.warnings.append(warning)
                    if page_images:
                        result.pages[page_number] = page_images
                finally:
                    self._release_page_cache(page)
        return result

    def _extract_image_from_page(self, page: Any, image: dict[str, Any]) -> Optional[bytes]:
        bbox = (
            max(0, image["x0"]),
            max(0, image["top"]),
            min(page.width, image["x1"]),
            min(page.height, image["bottom"]),
        )
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return None
        cropped = page.crop(bbox)
        rendered = cropped.to_image(resolution=self.config.image_resolution)
        buffer = io.BytesIO()
        rendered.save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def _release_page_cache(page: Any) -> None:
        try:
            close = getattr(page, "close", None)
            if callable(close):
                close()
                return
            flush_cache = getattr(page, "flush_cache", None)
            if callable(flush_cache):
                flush_cache()
        except Exception as exc:
            logger.debug(f"Failed to release pdfplumber page cache: {exc}")

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        raise NotImplementedError(
            "PDFParser requires a PDF file path; string content is not supported"
        )
