# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
ZIP archive parser for OpenViking.

Lists and describes contents of ZIP files.
Converts to markdown and delegates to MarkdownParser.
"""

import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class ZipParser(BaseParser):
    """
    ZIP archive parser for OpenViking.

    Supports: .zip

    Features:
    - Lists all files in the archive
    - Shows file sizes and modification dates
    - Groups files by type/extension
    """

    def __init__(self, config: Optional[ParserConfig] = None):
        """Initialize ZIP parser."""
        from openviking.parse.parsers.markdown import MarkdownParser

        self._md_parser = MarkdownParser(config=config)
        self.config = config or ParserConfig()

    @property
    def supported_extensions(self) -> List[str]:
        return [".zip"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """Parse from file path."""
        path = Path(source)

        if path.exists():
            markdown_content = self._convert_zip_to_markdown(path)
            result = await self._md_parser.parse_content(
                markdown_content,
                source_path=str(path),
                instruction=instruction,
                **kwargs,
            )
        else:
            # Treat as raw content string
            result = await self._md_parser.parse_content(
                str(source), instruction=instruction, **kwargs
            )
        result.source_format = "zip"
        result.parser_name = "ZipParser"
        return result

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """Parse content - for zip, content should be a file path."""
        result = await self._md_parser.parse_content(content, source_path, **kwargs)
        result.source_format = "zip"
        result.parser_name = "ZipParser"
        return result

    def _convert_zip_to_markdown(self, path: Path) -> str:
        """
        Convert ZIP file information to markdown format.

        Args:
            path: Path to .zip file

        Returns:
            Markdown formatted string
        """
        try:
            with zipfile.ZipFile(path, "r") as zf:
                return self._process_zip_contents(zf, path)
        except zipfile.BadZipFile:
            raise ValueError(f"Invalid or corrupted ZIP file: {path}")
        except Exception as e:
            raise ValueError(f"Error reading ZIP file: {e}")

    def _process_zip_contents(self, zf: zipfile.ZipFile, path: Path) -> str:
        """Process ZIP file contents and return markdown."""
        md_parts = []

        # Title
        md_parts.append(f"# ZIP Archive: {path.name}")
        md_parts.append("")

        # Archive info
        md_parts.append("## Archive Information")
        md_parts.append("")
        md_parts.append(f"- **File:** {path.name}")
        md_parts.append(f"- **Total files:** {len(zf.namelist())}")
        md_parts.append(
            f"- **Comment:** {zf.comment.decode('utf-8', errors='ignore') if zf.comment else 'None'}"
        )
        md_parts.append("")

        # Group files by extension
        files_by_ext = self._group_files_by_extension(zf.namelist())

        # File listing by category
        md_parts.append("## Contents")
        md_parts.append("")

        # Summary table
        if files_by_ext:
            md_parts.append("### File Types Summary")
            md_parts.append("")
            md_parts.append("| Extension | Count |")
            md_parts.append("|-----------|-------|")
            for ext, files in sorted(files_by_ext.items(), key=lambda x: -len(x[1])):
                display_ext = ext if ext else "(no extension)"
                md_parts.append(f"| {display_ext} | {len(files)} |")
            md_parts.append("")

        # Detailed listing
        md_parts.append("### File List")
        md_parts.append("")

        # Create a table with file info
        md_parts.append("| File | Size | Modified |")
        md_parts.append("|------|------|----------|")

        for info in zf.infolist():
            # Skip directories
            if info.is_dir():
                continue

            filename = info.filename
            size = self._format_size(info.file_size)
            modified = self._format_datetime(info.date_time)

            # Escape pipe characters
            filename = filename.replace("|", "\\|")

            md_parts.append(f"| {filename} | {size} | {modified} |")

        md_parts.append("")

        # Directory structure
        md_parts.append("## Directory Structure")
        md_parts.append("")
        md_parts.append("```")
        md_parts.append(self._generate_tree_view(zf.namelist()))
        md_parts.append("```")

        return "\n".join(md_parts)

    def _group_files_by_extension(self, filenames: List[str]) -> dict:
        """Group files by their extension."""
        groups = {}
        for name in filenames:
            if name.endswith("/"):  # Skip directories
                continue
            ext = Path(name).suffix.lower()
            if ext not in groups:
                groups[ext] = []
            groups[ext].append(name)
        return groups

    def _format_size(self, size: int) -> str:
        """Format file size in human-readable format."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"

    def _format_datetime(self, dt_tuple) -> str:
        """Format datetime tuple from ZIP info."""
        try:
            dt = datetime(*dt_tuple)
            return dt.strftime("%Y-%m-%d %H:%M")
        except:
            return "Unknown"

    def _generate_tree_view(self, filenames: List[str]) -> str:
        """Generate a tree-like view of the archive contents."""
        # Build a simple tree structure
        lines = []

        # Get unique directories
        dirs = set()
        for name in filenames:
            parts = name.split("/")
            for i in range(len(parts) - 1):
                dirs.add("/".join(parts[: i + 1]) + "/")

        # Sort all items
        all_items = sorted(set(filenames) | dirs)

        for item in all_items:
            # Calculate depth
            depth = item.count("/")
            if item.endswith("/"):
                depth -= 1

            # Create indentation
            indent = "    " * depth

            # Get just the name part
            name = item.rstrip("/").split("/")[-1] if "/" in item else item

            # Add prefix for directories vs files
            if item.endswith("/"):
                prefix = "[dir] "
            else:
                prefix = ""

            lines.append(f"{indent}{prefix}{name}")

        return "\n".join(lines)
