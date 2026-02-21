# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Parser registry for OpenViking.

Provides automatic parser selection based on file type.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.code import CodeRepositoryParser
from openviking.parse.parsers.directory import DirectoryParser
from openviking.parse.parsers.epub import EPubParser
from openviking.parse.parsers.excel import ExcelParser

# Import will be handled dynamically to avoid dependency issues
from openviking.parse.parsers.html import HTMLParser
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.parsers.media import AudioParser, ImageParser, VideoParser
from openviking.parse.parsers.pdf import PDFParser
from openviking.parse.parsers.powerpoint import PowerPointParser
from openviking.parse.parsers.text import TextParser

# Import markitdown-inspired parsers
from openviking.parse.parsers.word import WordParser
from openviking.parse.parsers.zip_parser import ZipParser

if TYPE_CHECKING:
    from openviking.parse.custom import CustomParserProtocol

logger = logging.getLogger(__name__)


class ParserRegistry:
    """
    Registry for document parsers, which is a singleton.

    Automatically selects appropriate parser based on file extension.
    """

    def __init__(self, register_optional: bool = True):
        """
        Initialize registry with default parsers.

        Args:
            register_optional: Whether to register optional parsers
                              that require extra dependencies
            parser_configs: Dictionary of parser configurations (from load_parser_configs_from_dict)
        """
        self._parsers: Dict[str, BaseParser] = {}
        self._extension_map: Dict[str, str] = {}

        # Register core parsers
        self.register("text", TextParser())
        self.register("markdown", MarkdownParser())
        self.register("pdf", PDFParser())
        self.register("html", HTMLParser())  # HTMLParser doesn't accept config yet

        # Register markitdown-inspired parsers (built-in)
        self.register("word", WordParser())
        self.register("powerpoint", PowerPointParser())
        self.register("excel", ExcelParser())
        self.register("epub", EPubParser())
        self.register("zip", ZipParser())
        self.register("code", CodeRepositoryParser())
        self.register("directory", DirectoryParser())

        self.register("image", ImageParser())
        self.register("audio", AudioParser())
        self.register("video", VideoParser())

    def register(self, name: str, parser: BaseParser) -> None:
        """
        Register a parser.

        Args:
            name: Parser name
            parser: Parser instance
        """
        self._parsers[name] = parser

        # Map extensions to parser name
        for ext in parser.supported_extensions:
            self._extension_map[ext.lower()] = name

    def register_custom(
        self,
        handler: "CustomParserProtocol",
        extensions: Optional[List[str]] = None,
        name: Optional[str] = None,
    ) -> None:
        """
        Register a custom parser (Protocol-based).

        Args:
            handler: Object implementing CustomParserProtocol
            extensions: Optional list of extensions (overrides handler's extensions)
            name: Optional parser name (default: "custom_N")

        Example:
            ```python
            class MyParser:
                @property
                def supported_extensions(self) -> List[str]:
                    return [".xyz"]

                def can_handle(self, source) -> bool:
                    return str(source).endswith(".xyz")

                async def parse(self, source, **kwargs) -> ParseResult:
                    ...

            registry.register_custom(MyParser(), name="xyz_parser")
            ```
        """
        from openviking.parse.custom import CustomParserWrapper

        # Generate name if not provided
        if name is None:
            # Count existing custom parsers
            custom_count = sum(1 for n in self._parsers if n.startswith("custom_"))
            name = f"custom_{custom_count}"

        # Wrap and register
        wrapper = CustomParserWrapper(handler, extensions=extensions)
        self.register(name, wrapper)  # type: ignore
        logger.info(f"Registered custom parser '{name}' for {wrapper.supported_extensions}")

    def register_callback(
        self,
        extension: str,
        parse_fn: "Callable[[Union[str, Path]], ParseResult]",
        name: Optional[str] = None,
    ) -> None:
        """
        Register a callback function as a parser.

        Args:
            extension: File extension (e.g., ".xyz")
            parse_fn: Async function that parses and returns ParseResult
            name: Optional parser name (default: "callback_<ext>")

        Example:
            ```python
            async def my_parser(source: Union[str, Path], **kwargs) -> ParseResult:
                content = Path(source).read_text()
                return create_parse_result(
                    root=ResourceNode(type=NodeType.ROOT, content=content),
                    source_path=str(source),
                    source_format="custom",
                    parser_name="my_parser",
                )

            registry.register_callback(".xyz", my_parser)
            ```
        """
        from openviking.parse.custom import CallbackParserWrapper

        # Generate name if not provided
        if name is None:
            name = f"callback{extension}"

        # Wrap and register
        wrapper = CallbackParserWrapper(extension, parse_fn, name=name)
        self.register(name, wrapper)  # type: ignore
        logger.info(f"Registered callback parser '{name}' for {extension}")

    def unregister(self, name: str) -> None:
        """Remove a parser from registry."""
        if name in self._parsers:
            parser = self._parsers[name]
            for ext in parser.supported_extensions:
                if self._extension_map.get(ext.lower()) == name:
                    del self._extension_map[ext.lower()]
            del self._parsers[name]

    def get_parser(self, name: str) -> Optional[BaseParser]:
        """Get parser by name."""
        return self._parsers.get(name)

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
        parser_name = self._extension_map.get(ext)

        if parser_name:
            return self._parsers.get(parser_name)

        return None

    async def parse(self, source: Union[str, Path], **kwargs) -> ParseResult:
        """
        Parse a file or content string.

        Automatically selects parser based on file extension.
        Falls back to text parser for unknown types.

        Args:
            source: File path or content string
            **kwargs: Additional arguments passed to parser

        Returns:
            ParseResult with document tree
        """
        source_str = str(source)

        # First, check if it's a code repository URL
        code_parser = self._parsers.get("code")
        if code_parser:
            # Check if the parser has the is_repository_url method
            try:
                if hasattr(code_parser, "is_repository_url") and code_parser.is_repository_url(
                    source_str
                ):
                    logger.info(f"Detected code repository URL: {source_str}")
                    return await code_parser.parse(source_str, **kwargs)
            except Exception as e:
                logger.warning(f"Error checking if source is repository URL: {e}")
                # Continue with normal parsing flow

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
        _default_registry = ParserRegistry()
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
