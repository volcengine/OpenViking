# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
ZIP archive parser for OpenViking.

Extracts ZIP archives and delegates to DirectoryParser for recursive processing.
Supports nested ZIP files via DirectoryParser's recursive parser invocation.
"""

import asyncio
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking.utils.zip_safe import safe_extract_zip
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class ZipParser(BaseParser):
    """
    ZIP archive parser for OpenViking.

    Supports: .zip

    Features:
    - Extracts ZIP archive to temporary directory
    - Delegates to DirectoryParser for recursive content processing
    - Supports nested ZIP files (via DirectoryParser recursion)
    - Preserves temporary directory for TreeBuilder
    """

    @property
    def supported_extensions(self) -> List[str]:
        return [".zip"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """Parse a ZIP file by extracting it and delegating to DirectoryParser.

        Args:
            source: Path to the .zip file
            instruction: Processing instruction (forwarded to DirectoryParser)
            **kwargs: Extra options forwarded to DirectoryParser

        Returns:
            ParseResult from DirectoryParser, with temp_dir_path preserved
            for TreeBuilder to use later.
        """

        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"ZIP file not found: {path}")

        # Check if it's a valid ZIP file (non-blocking)
        def _is_zipfile() -> bool:
            return zipfile.is_zipfile(path)

        if not await asyncio.to_thread(_is_zipfile):
            raise ValueError(f"Not a valid ZIP file: {path}")

        # Extract ZIP to temporary directory (non-blocking)
        temp_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="ov_zip_"))
        try:
            # Extract zip (non-blocking)
            def _extract_zip():
                with zipfile.ZipFile(path, "r") as zipf:
                    safe_extract_zip(zipf, temp_dir)

            await asyncio.to_thread(_extract_zip)

            # Check if extracted content has a single root directory (non-blocking)
            def _list_entries():
                return [p for p in temp_dir.iterdir() if p.name not in {".", ".."}]

            extracted_entries = await asyncio.to_thread(_list_entries)

            # Prepare kwargs for DirectoryParser
            dir_kwargs = dict(kwargs)
            dir_kwargs["instruction"] = instruction

            # Use DirectoryParser to process the extracted content
            from openviking.parse.parsers.directory import DirectoryParser

            parser = DirectoryParser()

            if len(extracted_entries) == 1 and extracted_entries[0].is_dir():
                # Single root directory - parse that directly
                dir_kwargs.pop("source_name", None)
                result = await parser.parse(str(extracted_entries[0]), **dir_kwargs)
            else:
                # Multiple entries at root - parse the temp dir itself
                # Set source_name from zip filename if not provided
                if "source_name" not in dir_kwargs or not dir_kwargs["source_name"]:
                    dir_kwargs["source_name"] = path.stem
                result = await parser.parse(str(temp_dir), **dir_kwargs)

            # Ensure the temporary directory is preserved for TreeBuilder
            if not result.temp_dir_path:
                result.temp_dir_path = str(temp_dir)
            else:
                # If DirectoryParser created its own temp, clean up our extraction dir (non-blocking)
                def _cleanup_temp():
                    try:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                    except Exception:
                        pass

                await asyncio.to_thread(_cleanup_temp)

            result.source_format = "zip"
            result.parser_name = "ZipParser"
            return result

        except Exception:
            # Clean up on error (non-blocking)
            def _cleanup_temp_on_error():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

            await asyncio.to_thread(_cleanup_temp_on_error)
            raise

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """Parse content - not applicable for ZIP files (needs a file path)."""
        raise NotImplementedError(
            "ZipParser does not support parse_content. Please provide a file path to parse()."
        )
