# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
PDF parser for OpenViking.

Unified parser that converts PDF to Markdown then parses the result.
Supports dual strategy:
- Local: pdfplumber for direct conversion
- Remote: MinerU API for advanced conversion

This design simplifies PDF handling by delegating structure analysis
to the MarkdownParser after conversion.
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openviking.parse.base import (
    NodeType,
    ParseResult,
    ResourceNode,
    create_parse_result,
    lazy_import,
)
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import PDFConfig

logger = logging.getLogger(__name__)


class PDFParser(BaseParser):
    """
    PDF parser with dual conversion strategy.

    Converts PDF → Markdown → ParseResult using MarkdownParser.

    Strategies:
    - "local": Use pdfplumber for text and table extraction
    - "mineru": Use MinerU API for advanced PDF processing
    - "auto": Try local first, fallback to MinerU if configured

    Examples:
        >>> # Local parsing
        >>> parser = PDFParser(PDFConfig(strategy="local"))
        >>> result = await parser.parse("document.pdf")

        >>> # Remote API parsing
        >>> config = PDFConfig(
        ...     strategy="mineru",
        ...     mineru_endpoint="https://api.example.com/convert",
        ...     mineru_api_key="key"
        ... )
        >>> parser = PDFParser(config)
        >>> result = await parser.parse("document.pdf")
    """

    def __init__(self, config: Optional[PDFConfig] = None):
        """
        Initialize PDF parser.

        Args:
            config: PDFConfig instance (defaults to auto strategy)
        """
        self.config = config or PDFConfig()
        self.config.validate()

        # Lazy import MarkdownParser to avoid circular imports
        self._markdown_parser = None

    def _get_markdown_parser(self):
        """Lazy import and create MarkdownParser."""
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser()
        return self._markdown_parser

    @property
    def supported_extensions(self) -> List[str]:
        """List of supported file extensions."""
        return [".pdf"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse PDF file.

        Args:
            source: Path to PDF file
            **kwargs: Additional options (currently unused)

        Returns:
            ParseResult with document tree

        Raises:
            FileNotFoundError: If PDF file doesn't exist
            ValueError: If conversion fails with all strategies
        """
        start_time = time.time()
        pdf_path = Path(source)

        if not pdf_path.exists():
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT),
                source_path=str(pdf_path),
                source_format="pdf",
                parser_name="PDFParser",
                parse_time=time.time() - start_time,
                warnings=[f"File not found: {pdf_path}"],
            )

        try:
            # Step 1: Convert PDF to Markdown
            markdown_content, conversion_meta = await self._convert_to_markdown(pdf_path)

            # Step 2: Parse Markdown using MarkdownParser
            md_parser = self._get_markdown_parser()
            result = await md_parser.parse_content(markdown_content, source_path=str(pdf_path))

            # Step 3: Update metadata for PDF origin
            result.source_format = "pdf"  # Override markdown format
            result.parser_name = "PDFParser"
            result.parser_version = "2.0"
            result.parse_time = time.time() - start_time
            result.meta.update(conversion_meta)
            result.meta["pdf_strategy"] = self.config.strategy
            result.meta["intermediate_markdown_length"] = len(markdown_content)
            result.meta["intermediate_markdown_preview"] = markdown_content[:500]

            logger.info(
                f"PDF parsed successfully: {pdf_path.name} "
                f"({len(markdown_content)} chars markdown, "
                f"{result.parse_time:.2f}s)"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to parse PDF {pdf_path}: {e}")
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT),
                source_path=str(pdf_path),
                source_format="pdf",
                parser_name="PDFParser",
                parse_time=time.time() - start_time,
                warnings=[f"Failed to parse PDF: {e}"],
            )

    async def _convert_to_markdown(self, pdf_path: Path) -> tuple[str, Dict[str, Any]]:
        """
        Convert PDF to Markdown using configured strategy.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Tuple of (markdown_content, metadata_dict)

        Raises:
            ValueError: If all conversion strategies fail
        """
        if self.config.strategy == "local":
            return await self._convert_local(pdf_path)

        elif self.config.strategy == "mineru":
            return await self._convert_mineru(pdf_path)

        elif self.config.strategy == "auto":
            # Try local first
            try:
                return await self._convert_local(pdf_path)
            except Exception as e:
                logger.warning(f"Local conversion failed: {e}")

                # Fallback to MinerU if configured
                if self.config.mineru_endpoint:
                    logger.info("Falling back to MinerU API")
                    return await self._convert_mineru(pdf_path)
                else:
                    raise ValueError(
                        f"Local conversion failed and no MinerU endpoint configured: {e}"
                    )

        else:
            raise ValueError(f"Unknown strategy: {self.config.strategy}")

    async def _convert_local(
        self, pdf_path: Path, storage=None, resource_name: Optional[str] = None
    ) -> tuple[str, Dict[str, Any]]:
        """
        Convert PDF to Markdown using pdfplumber.

        Args:
            pdf_path: Path to PDF file
            storage: Optional StoragePath for saving images
            resource_name: Resource name for organizing saved images

        Returns:
            Tuple of (markdown_content, metadata)

        Raises:
            ImportError: If pdfplumber not installed
            Exception: If conversion fails
        """
        pdfplumber = lazy_import("pdfplumber")

        # Import storage utilities
        if storage is None:
            from openviking_cli.utils.storage import get_storage

            storage = get_storage()

        if resource_name is None:
            resource_name = pdf_path.stem

        parts = []
        meta = {
            "strategy": "local",
            "library": "pdfplumber",
            "pages_processed": 0,
            "images_extracted": 0,
            "tables_extracted": 0,
        }

        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                meta["total_pages"] = len(pdf.pages)

                for page_num, page in enumerate(pdf.pages, 1):
                    # Extract text
                    text = page.extract_text()
                    if text and text.strip():
                        # Add page marker as HTML comment
                        parts.append(f"<!-- Page {page_num} -->\n{text.strip()}")
                        meta["pages_processed"] += 1

                    # Extract tables
                    tables = page.extract_tables()
                    for table_idx, table in enumerate(tables or []):
                        if table and len(table) > 0:
                            md_table = self._format_table_markdown(table)
                            if md_table:
                                parts.append(
                                    f"<!-- Page {page_num} Table {table_idx + 1} -->\n{md_table}"
                                )
                                meta["tables_extracted"] += 1

                    # Extract images
                    images = page.images
                    for img_idx, img in enumerate(images or []):
                        try:
                            # Extract image using underlying PDF object
                            image_obj = self._extract_image_from_page(page, img)
                            if image_obj:
                                # Save image
                                filename = f"page{page_num}_img{img_idx + 1}"
                                image_path = storage.save_image(
                                    resource_name, image_obj, filename=filename
                                )

                                # Generate relative path for markdown
                                rel_path = image_path.relative_to(Path.cwd())
                                parts.append(
                                    f"<!-- Page {page_num} Image {img_idx + 1} -->\n"
                                    f"![Page {page_num} Image {img_idx + 1}]({rel_path})"
                                )
                                meta["images_extracted"] += 1
                        except Exception as img_err:
                            logger.warning(
                                f"Failed to extract image {img_idx + 1} on page {page_num}: {img_err}"
                            )

            if not parts:
                logger.warning(f"No content extracted from {pdf_path}")
                return "", meta

            markdown_content = "\n\n".join(parts)
            logger.info(
                f"Local conversion: {meta['pages_processed']}/{meta['total_pages']} pages, "
                f"{meta['images_extracted']} images, {meta['tables_extracted']} tables → "
                f"{len(markdown_content)} chars"
            )

            return markdown_content, meta

        except Exception as e:
            logger.error(f"pdfplumber conversion failed: {e}")
            raise

    def _extract_image_from_page(self, page, img_info: dict) -> Optional[bytes]:
        """
        Extract image data from PDF page.

        Args:
            page: pdfplumber page object
            img_info: Image metadata from page.images

        Returns:
            Image bytes or None if extraction fails
        """
        try:
            if hasattr(page, "page_obj") and hasattr(page.page_obj, "resources"):
                resources = page.page_obj.resources
                if resources and "XObject" in resources:
                    xobjects = resources["XObject"]
                    for obj_name in xobjects:
                        obj = xobjects[obj_name]
                        if hasattr(obj, "resolve"):
                            resolved = obj.resolve()
                            if resolved.get("Subtype") and resolved["Subtype"].name == "Image":
                                data = resolved.get("stream")
                                if data:
                                    return data.get_data()

            return None

        except Exception as e:
            logger.debug(f"Image extraction error: {e}")
            return None

    async def _convert_mineru(self, pdf_path: Path) -> tuple[str, Dict[str, Any]]:
        """
        Convert PDF to Markdown using MinerU API.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Tuple of (markdown_content, metadata)

        Raises:
            ImportError: If httpx not installed
            Exception: If API call fails
        """
        httpx = lazy_import("httpx")

        if not self.config.mineru_endpoint:
            raise ValueError("MinerU endpoint not configured")

        meta = {
            "strategy": "mineru",
            "endpoint": self.config.mineru_endpoint,
            "api_version": None,
        }

        try:
            async with httpx.AsyncClient(timeout=self.config.mineru_timeout) as client:
                # Prepare file upload
                with open(pdf_path, "rb") as f:
                    files = {"file": (pdf_path.name, f, "application/pdf")}

                    # Prepare headers
                    headers = {}
                    if self.config.mineru_api_key:
                        headers["Authorization"] = f"Bearer {self.config.mineru_api_key}"

                    # Prepare request params
                    params = self.config.mineru_params or {}

                    # Make API request
                    logger.info(f"Calling MinerU API: {self.config.mineru_endpoint}")
                    response = await client.post(
                        self.config.mineru_endpoint,
                        files=files,
                        headers=headers,
                        params=params,
                    )
                    response.raise_for_status()

                # Parse response
                result = response.json()
                markdown_content = result.get("markdown", "")

                # Extract metadata from response
                meta["api_version"] = result.get("version")
                meta["processing_time"] = result.get("processing_time")
                meta["total_pages"] = result.get("total_pages")

                if not markdown_content:
                    logger.warning(f"MinerU returned empty content for {pdf_path}")

                logger.info(
                    f"MinerU conversion: {meta.get('total_pages', '?')} pages → "
                    f"{len(markdown_content)} chars"
                )

                return markdown_content, meta

        except Exception as e:
            logger.error(f"MinerU API call failed: {e}")
            raise

    def _format_table_markdown(self, table: List[List[Optional[str]]]) -> str:
        """
        Convert table data to Markdown table format.

        Args:
            table: 2D array of table cells

        Returns:
            Markdown table string

        Examples:
            >>> table = [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]
            >>> print(parser._format_table_markdown(table))
            | Name | Age |
            | --- | --- |
            | Alice | 30 |
            | Bob | 25 |
        """
        if not table or not table[0]:
            return ""

        # Clean cells and handle None values
        def clean_cell(cell):
            if cell is None:
                return ""
            return str(cell).strip().replace("|", "\\|")  # Escape pipe characters

        lines = []

        # Header row
        header = table[0]
        header_cells = [clean_cell(cell) for cell in header]
        lines.append("| " + " | ".join(header_cells) + " |")

        # Separator row
        separator = ["---"] * len(header)
        lines.append("| " + " | ".join(separator) + " |")

        # Data rows
        for row in table[1:]:
            # Pad row to match header length
            padded_row = row + [None] * (len(header) - len(row))
            cells = [clean_cell(cell) for cell in padded_row[: len(header)]]
            lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(lines)

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse PDF content string.

        Note: This method is not recommended for PDFParser as it requires
        file path for conversion tools. Use parse() with file path instead.

        Args:
            content: PDF content (not supported)
            source_path: Optional source path
            **kwargs: Additional options

        Raises:
            NotImplementedError: PDFParser requires file path
        """
        raise NotImplementedError(
            "PDFParser does not support parsing content strings. "
            "Use parse() with a file path instead."
        )
