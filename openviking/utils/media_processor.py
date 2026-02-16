# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unified resource processor with strategy-based routing."""

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from openviking.parse import DocumentConverter, parse
from openviking.parse.base import ParseResult
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.parse.vlm import VLMProcessor
    from openviking_cli.utils.storage import StoragePath

logger = get_logger(__name__)


class UnifiedResourceProcessor:
    """Unified resource processing for files, URLs, and raw content."""

    def __init__(
        self,
        vlm_processor: Optional["VLMProcessor"] = None,
        storage: Optional["StoragePath"] = None,
    ):
        self.storage = storage
        self._vlm_processor = vlm_processor
        self._document_converter = None

    def _get_vlm_processor(self) -> Optional["VLMProcessor"]:
        if self._vlm_processor is None:
            from openviking.parse.vlm import VLMProcessor

            self._vlm_processor = VLMProcessor()
        return self._vlm_processor

    def _get_document_converter(self) -> DocumentConverter:
        if self._document_converter is None:
            self._document_converter = DocumentConverter()
        return self._document_converter

    async def process(
        self,
        source: str,
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        """Process any source (file/URL/content) with appropriate strategy."""
        # Check if URL
        if self._is_url(source):
            return await self._process_url(source, instruction)

        # Check if looks like a file path (short enough and no newlines)
        is_potential_path = len(source) <= 1024 and "\n" not in source
        if is_potential_path:
            path = Path(source)
            if path.exists():
                if path.is_dir():
                    return await self._process_directory(path, instruction, **kwargs)
                return await self._process_file(path, instruction)
            else:
                logger.warning(f"Path {path} does not exist")
                raise FileNotFoundError(f"Path {path} does not exist")

        # Treat as raw content
        return await parse(source, instruction=instruction)

    def _is_url(self, source: str) -> bool:
        """Check if source is a URL."""
        return source.startswith(("http://", "https://"))

    async def _process_url(self, url: str, instruction: str) -> ParseResult:
        """Process URL source."""
        from openviking.parse.parsers.html import HTMLParser

        parser = HTMLParser()
        return await parser.parse(url, instruction=instruction)

    async def _process_directory(
        self,
        dir_path: Path,
        instruction: str,
        **kwargs,
    ) -> ParseResult:
        """Process directory source via DirectoryParser.

        Args:
            dir_path: Path to the directory.
            instruction: Processing instruction.
            **kwargs: Forwarded to ``DirectoryParser.parse()`` →
                ``scan_directory()``: ``strict``, ``ignore_dirs``,
                ``include``, ``exclude``.
        """
        from openviking.parse.parsers.directory import DirectoryParser

        parser = DirectoryParser()
        return await parser.parse(str(dir_path), instruction=instruction, **kwargs)

    async def _process_file(
        self,
        file_path: Path,
        instruction: str,
    ) -> ParseResult:
        """Process file with unified parsing."""
        return await parse(
            str(file_path),
            instruction=instruction,
            vlm_processor=self._get_vlm_processor(),
            storage=self.storage,
            resource_name=file_path.stem,
        )
