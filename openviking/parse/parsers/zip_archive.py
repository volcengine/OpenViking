# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
ZIP archive parser for OpenViking.

Iterates over ZIP contents and creates a markdown representation.
Inspired by microsoft/markitdown approach.
"""

import logging
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


class ZipParser(BaseParser):
    """
    ZIP archive parser for OpenViking.

    Supports: .zip

    Iterates over ZIP contents and creates a markdown representation
    of the archive structure, then delegates to MarkdownParser.

    Features:
    - File listing with sizes
    - Directory structure visualization
    - Comment extraction
    - Large archive warnings
    """

    def __init__(self, max_list_files: int = 100):
        """
        Initialize ZIP parser.

        Args:
            max_list_files: Maximum number of files to list (0 = unlimited)
        """
        self.max_list_files = max_list_files
        self._markdown_parser = None

    def _get_markdown_parser(self):
        """Lazy import MarkdownParser."""
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser()
        return self._markdown_parser

    @property
    def supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return [".zip"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse ZIP archive from file path.

        Args:
            source: File path to .zip file
            instruction: Processing instruction
            **kwargs: Additional arguments

        Returns:
            ParseResult with document tree
        """
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"ZIP file not found: {path}")

        # Convert to markdown
        markdown_content = self._convert_to_markdown(path)

        # Delegate to MarkdownParser
        result = await self._get_markdown_parser().parse_content(
            markdown_content, str(path), instruction, **kwargs
        )
        result.source_format = "zip"
        return result

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        """
        Parse ZIP content.

        Note: This expects the actual zip binary content, which isn't directly
        usable. Use parse() with a file path instead.

        Args:
            content: Not directly supported for binary zip content
            source_path: Optional source path for reference
            instruction: Processing instruction
            **kwargs: Additional arguments

        Returns:
            ParseResult with document tree
        """
        if source_path and Path(source_path).exists():
            return await self.parse(source_path, instruction, **kwargs)
        raise ValueError("ZipParser.parse_content() requires a valid source_path to the .zip file")

    def _convert_to_markdown(self, path: Path) -> str:
        """
        Convert ZIP archive to Markdown string.

        Args:
            path: Path to .zip file

        Returns:
            Markdown formatted string
        """
        markdown_parts = []
        markdown_parts.append(f"# ZIP Archive: {path.name}")

        try:
            with zipfile.ZipFile(path, "r") as zf:
                # Extract comment if present
                if zf.comment:
                    comment = zf.comment.decode("utf-8", errors="ignore")
                    if comment.strip():
                        markdown_parts.append(f"**Comment:** {comment}")

                # Get file list
                file_list = zf.infolist()
                total_files = len(file_list)
                total_size = sum(info.file_size for info in file_list)

                markdown_parts.append(f"**Total Files:** {total_files}")
                markdown_parts.append(
                    f"**Total Uncompressed Size:** {self._format_size(total_size)}"
                )

                # List files
                markdown_parts.append("## Contents")

                files_to_list = file_list
                if self.max_list_files > 0 and total_files > self.max_list_files:
                    files_to_list = file_list[: self.max_list_files]
                    markdown_parts.append(
                        f"*Showing first {self.max_list_files} of {total_files} files*"
                    )

                # Create table
                rows = [["File Path", "Size", "Compressed", "Modified"]]
                for info in files_to_list:
                    date_str = (
                        f"{info.date_time[0]}-{info.date_time[1]:02d}-{info.date_time[2]:02d}"
                    )
                    rows.append(
                        [
                            info.filename,
                            self._format_size(info.file_size),
                            self._format_size(info.compress_size),
                            date_str,
                        ]
                    )

                from openviking.parse.base import format_table_to_markdown

                table_md = format_table_to_markdown(rows, has_header=True)
                markdown_parts.append(table_md)

                # Show directory summary
                dirs = set()
                for info in file_list:
                    parts = info.filename.split("/")
                    for i in range(1, len(parts)):
                        dirs.add("/".join(parts[:i]))

                if dirs:
                    markdown_parts.append(f"\n**Directories:** {len(dirs)}")

        except zipfile.BadZipFile:
            markdown_parts.append("\n*Error: Invalid or corrupted ZIP file*")
        except Exception as e:
            markdown_parts.append(f"\n*Error reading ZIP file: {e}*")

        return "\n\n".join(markdown_parts)

    def _format_size(self, size_bytes: int) -> str:
        """Format byte size to human readable string."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
