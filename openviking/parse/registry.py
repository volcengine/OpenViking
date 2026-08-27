# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Parser registry for OpenViking.

Provides automatic parser selection based on file type.
"""

from pathlib import Path
from typing import Dict, List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.anydoc import AnyDocParser
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.constants import TYPESCRIPT_MPEG_TS_EXTENSION
from openviking.parse.parsers.directory import DirectoryParser

# Import will be handled dynamically to avoid dependency issues
from openviking.parse.parsers.html import HTMLParser
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.parsers.media import AudioParser, ImageParser, VideoParser
from openviking.parse.parsers.media.utils import is_mpeg_ts, read_mpeg_ts_probe
from openviking.parse.parsers.pdf import PDFParser
from openviking.parse.parsers.text import TextParser
from openviking.parse.parsers.zip_parser import ZipParser
from openviking_cli.utils.config.parser_config import ParserConfig


class ParserRegistry:
    """
    Registry for document parsers, which is a singleton.

    Automatically selects appropriate parser based on file extension.
    """

    def __init__(
        self,
        parser_configs: Optional[Dict[str, ParserConfig]] = None,
    ):
        """
        Initialize registry with default parsers.

        Args:
            parser_configs: Dictionary of parser configurations (from load_parser_configs_from_dict)
        """
        self._parsers: Dict[str, BaseParser] = {}
        self._extension_map: Dict[str, str] = {}
        self._parser_configs = parser_configs or {}

        # Register core parsers
        self._register("text", TextParser(config=self._parser_configs.get("text")))
        self._register("markdown", MarkdownParser(config=self._parser_configs.get("markdown")))
        self._register("pdf", PDFParser(config=self._parser_configs.get("pdf")))
        self._register("html", HTMLParser(config=self._parser_configs.get("html")))

        # Register Office/EPUB through the unified AnyDoc parser.
        self._register(
            "anydoc",
            AnyDocParser(
                config=self._parser_configs.get("markdown"),
                anydoc_config=self._parser_configs.get("anydoc"),
            ),
        )
        self._register("zip", ZipParser())
        self._register("directory", DirectoryParser())

        self._register("image", ImageParser(config=self._parser_configs.get("image")))
        self._register("audio", AudioParser())
        self._register("video", VideoParser())

    def _register(self, name: str, parser: BaseParser) -> None:
        self._parsers[name] = parser

        # Map extensions to parser name
        for ext in parser.supported_extensions:
            self._extension_map[ext.lower()] = name

    def get_parser_for_file(self, path: Union[str, Path]) -> Optional[BaseParser]:
        """
        Get appropriate parser for a file.

        Args:
            path: File path

        Returns:
            Parser instance or None if no suitable parser found
        """
        path = Path(path)
        ext = path.suffix.lower()
        if ext == TYPESCRIPT_MPEG_TS_EXTENSION:
            if not path.is_file():
                return None
            if not is_mpeg_ts(read_mpeg_ts_probe(path)):
                return None

        parser_name = self._extension_map.get(ext)

        if parser_name:
            return self._parsers.get(parser_name)

        return None

    async def parse(self, source: Union[str, Path], **kwargs) -> ParseResult:
        """
        Parse a local file or content string.

        Automatically selects parser based on file extension.
        Falls back to text parser for unknown types.

        NOTE: For URL handling, use AccessorRegistry in the two-layer architecture.
        This registry only handles local files and raw content.

        Args:
            source: Local file path or content string
            **kwargs: Additional arguments passed to parser

        Returns:
            ParseResult with document tree
        """
        source_str = str(source)

        # Check if source looks like a file path (short enough and no newlines)
        is_potential_path = len(source_str) <= 1024 and "\n" not in source_str

        if is_potential_path:
            path = Path(source)
            if path.exists():
                # Directory → route to DirectoryParser
                if path.is_dir():
                    dir_parser = self._parsers.get("directory")
                    if dir_parser:
                        return await dir_parser.parse(path, **kwargs)
                    raise ValueError(
                        f"Source is a directory but DirectoryParser is not registered: {path}"
                    )

                parser = self.get_parser_for_file(path)
                if parser:
                    return await parser.parse(path, **kwargs)
                else:
                    return await self._parsers["text"].parse(path, **kwargs)

        # Content string - use text parser
        return await self._parsers["text"].parse_content(source_str, **kwargs)

    def list_parsers(self) -> List[str]:
        """List registered parser names."""
        return list(self._parsers.keys())

    def list_supported_extensions(self) -> List[str]:
        """List all supported file extensions."""
        return list(self._extension_map.keys())


# Global registry instance
_default_registry: Optional[ParserRegistry] = None


def get_registry() -> ParserRegistry:
    """Get the default parser registry."""
    global _default_registry
    if _default_registry is None:
        parser_configs = None
        try:
            from openviking_cli.utils.config import get_openviking_config

            config = get_openviking_config()
            parser_configs = {
                "text": config.text,
                "markdown": config.markdown,
                "pdf": config.pdf,
                "html": config.html,
                "anydoc": getattr(config, "anydoc", None),
                "image": config.image,
            }
        except Exception:
            parser_configs = None
        _default_registry = ParserRegistry(parser_configs=parser_configs)
    return _default_registry


async def parse(source: Union[str, Path], **kwargs) -> ParseResult:
    """
    Parse a document using the default registry.

    Args:
        source: File path or content string
        **kwargs: Additional arguments passed to parser

    Returns:
        ParseResult with document tree
    """
    return await get_registry().parse(source, **kwargs)
