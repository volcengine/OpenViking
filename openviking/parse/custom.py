# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Custom parser protocol and wrappers for OpenViking.

Supports two ways to extend parsing:
1. Protocol-based: Implement CustomParserProtocol
2. Callback-based: Pass a simple async function
"""

from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Union

from typing_extensions import Protocol, runtime_checkable

if TYPE_CHECKING:
    from openviking.parse.base import ParseResult


@runtime_checkable
class CustomParserProtocol(Protocol):
    """
    Protocol for custom parsers.

    External parsers must implement this interface to be registered
    with ParserRegistry.

    Example:
        ```python
        class MyCustomParser:
            @property
            def supported_extensions(self) -> List[str]:
                return [".xyz"]

            def can_handle(self, source: Union[str, Path]) -> bool:
                return str(source).endswith(".xyz")

            async def parse(self, source: Union[str, Path], **kwargs) -> ParseResult:
                # Custom parsing logic
                ...
        ```
    """

    def can_handle(self, source: Union[str, Path]) -> bool:
        """
        Check if this parser can handle the given source.

        Args:
            source: File path or content string

        Returns:
            True if this parser can handle the source
        """
        ...

    async def parse(self, source: Union[str, Path], **kwargs) -> "ParseResult":
        """
        Parse the source and return a ParseResult.

        Args:
            source: File path or content string
            **kwargs: Additional parsing options

        Returns:
            ParseResult with document tree
        """
        ...

    @property
    def supported_extensions(self) -> List[str]:
        """
        List of supported file extensions.

        Returns:
            List of extensions (e.g., [".xyz", ".abc"])
        """
        ...


class CustomParserWrapper:
    """
    Wrapper to adapt external CustomParserProtocol to BaseParser interface.

    This allows external parsers to be registered and used seamlessly
    alongside built-in parsers.
    """

    def __init__(
        self,
        custom_parser: CustomParserProtocol,
        extensions: Optional[List[str]] = None,
    ):
        """
        Initialize wrapper.

        Args:
            custom_parser: External parser implementing CustomParserProtocol
            extensions: Override supported extensions (optional)
        """
        if not isinstance(custom_parser, CustomParserProtocol):
            raise TypeError(
                f"custom_parser must implement CustomParserProtocol, "
                f"got {type(custom_parser).__name__}"
            )

        self.custom_parser = custom_parser
        self._extensions = extensions or custom_parser.supported_extensions

    @property
    def supported_extensions(self) -> List[str]:
        """Return supported extensions."""
        return self._extensions

    def can_parse(self, path: Union[str, Path]) -> bool:
        """Check if can parse the given file."""
        return self.custom_parser.can_handle(path)

    async def parse(self, source: Union[str, Path], **kwargs) -> "ParseResult":
        """
        Parse the source using the custom parser.

        Args:
            source: File path or content string
            **kwargs: Additional options

        Returns:
            ParseResult from custom parser

        Raises:
            ValueError: If the custom parser cannot handle this source
        """
        if not self.custom_parser.can_handle(source):
            raise ValueError(
                f"Parser {type(self.custom_parser).__name__} cannot handle source: {source}"
            )

        return await self.custom_parser.parse(source, **kwargs)

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, **kwargs
    ) -> "ParseResult":
        """
        Parse content string.

        Note: Most custom parsers work with file paths, so this may not
        be supported. Override in custom parser if needed.

        Args:
            content: Content string
            source_path: Optional source path for reference
            **kwargs: Additional options

        Returns:
            ParseResult

        Raises:
            NotImplementedError: If custom parser doesn't support content parsing
        """
        raise NotImplementedError(
            f"Parser {type(self.custom_parser).__name__} "
            "does not support content parsing. Use parse() with file path instead."
        )


class CallbackParserWrapper:
    """
    Wrapper for simple callback-based parsers.

    Allows registering a simple async function as a parser without
    implementing the full CustomParserProtocol.

    Example:
        ```python
        async def my_parser(source: Union[str, Path], **kwargs) -> ParseResult:
            root = ResourceNode(type=NodeType.ROOT, title="My Document")
            return create_parse_result(
                root=root,
                source_path=str(source),
                source_format="custom",
                parser_name="my_parser",
            )

        registry.register_callback(".xyz", my_parser)
        ```
    """

    def __init__(
        self,
        extension: str,
        parse_fn: Callable[[Union[str, Path]], "ParseResult"],
        name: Optional[str] = None,
    ):
        """
        Initialize callback wrapper.

        Args:
            extension: File extension (e.g., ".xyz")
            parse_fn: Async function that takes source and returns ParseResult
            name: Optional parser name for identification
        """
        self.extension = extension
        self.parse_fn = parse_fn
        self.name = name or f"callback_{extension}"

    @property
    def supported_extensions(self) -> List[str]:
        """Return supported extension."""
        return [self.extension]

    def can_parse(self, path: Union[str, Path]) -> bool:
        """Check if can parse the given file."""
        return str(path).lower().endswith(self.extension.lower())

    async def parse(self, source: Union[str, Path], **kwargs) -> "ParseResult":
        """
        Parse using the callback function.

        Args:
            source: File path
            **kwargs: Additional options passed to callback

        Returns:
            ParseResult from callback
        """
        return await self.parse_fn(source, **kwargs)

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, **kwargs
    ) -> "ParseResult":
        """
        Parse content - typically not supported for callback parsers.

        Args:
            content: Content string
            source_path: Optional source path
            **kwargs: Additional options

        Raises:
            NotImplementedError: Callback parsers typically work with files
        """
        raise NotImplementedError(
            f"Callback parser {self.name} does not support content parsing. "
            "Use parse() with file path instead."
        )
