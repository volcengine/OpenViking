# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Excel (.xlsx) parser for OpenViking.

Converts Excel spreadsheets to Markdown then parses using MarkdownParser.
Inspired by microsoft/markitdown approach.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


class ExcelParser(BaseParser):
    """
    Excel spreadsheet parser for OpenViking.

    Supports: .xlsx, .xls

    Converts Excel spreadsheets to Markdown using openpyxl,
    then delegates to MarkdownParser for tree structure creation.
    """

    def __init__(self, max_rows_per_sheet: int = 1000):
        """
        Initialize Excel parser.

        Args:
            max_rows_per_sheet: Maximum rows to process per sheet (0 = unlimited)
        """
        self.max_rows_per_sheet = max_rows_per_sheet
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
        return [".xlsx", ".xls"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """Parse Excel spreadsheet from file path."""
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Excel file not found: {path}")

        try:
            import openpyxl
        except ImportError:
            raise ImportError(
                "openpyxl is required for Excel parsing. Install with: pip install openpyxl"
            )

        markdown_content = self._convert_to_markdown(path, openpyxl)
        result = await self._get_markdown_parser().parse_content(
            markdown_content, str(path), instruction, **kwargs
        )
        result.source_format = "xlsx"
        return result

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        """Parse Excel content."""
        if source_path and Path(source_path).exists():
            return await self.parse(source_path, instruction, **kwargs)
        raise ValueError(
            "ExcelParser.parse_content() requires a valid source_path to the .xlsx file"
        )

    def _convert_to_markdown(self, path: Path, openpyxl) -> str:
        """Convert Excel spreadsheet to Markdown string."""
        wb = openpyxl.load_workbook(path, data_only=True)

        markdown_parts = []
        markdown_parts.append(f"# {path.stem}")
        markdown_parts.append(f"**Sheets:** {len(wb.sheetnames)}")

        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            sheet_content = self._convert_sheet(sheet, sheet_name)
            markdown_parts.append(sheet_content)

        return "\n\n".join(markdown_parts)

    def _convert_sheet(self, sheet, sheet_name: str) -> str:
        """Convert a single sheet to markdown."""
        parts = []
        parts.append(f"## Sheet: {sheet_name}")

        max_row = sheet.max_row
        max_col = sheet.max_column

        if max_row == 0 or max_col == 0:
            parts.append("*Empty sheet*")
            return "\n\n".join(parts)

        parts.append(f"**Dimensions:** {max_row} rows × {max_col} columns")

        rows_to_process = max_row
        if self.max_rows_per_sheet > 0:
            rows_to_process = min(max_row, self.max_rows_per_sheet)

        rows = []
        for _row_idx, row in enumerate(
            sheet.iter_rows(min_row=1, max_row=rows_to_process, values_only=True), 1
        ):
            row_data = []
            for cell in row:
                if cell is None:
                    row_data.append("")
                else:
                    row_data.append(str(cell))
            rows.append(row_data)

        if rows:
            from openviking.parse.base import format_table_to_markdown

            table_md = format_table_to_markdown(rows, has_header=True)
            parts.append(table_md)

        if self.max_rows_per_sheet > 0 and max_row > self.max_rows_per_sheet:
            parts.append(f"\n*... {max_row - self.max_rows_per_sheet} more rows truncated ...*")

        return "\n\n".join(parts)
