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


def _is_zip_metadata_entry(path: Path) -> bool:
    """Return true for archive metadata that should not define a resource root."""
    name = path.name
    return name == "__MACOSX" or name == ".DS_Store" or name.startswith("._")


async def _extract_zip_to_temp(path: Path) -> Path:
    """Validate and safely extract one ZIP archive into a temporary directory."""
    if not path.exists():
        raise FileNotFoundError(f"ZIP file not found: {path}")

    if not await asyncio.to_thread(zipfile.is_zipfile, path):
        raise ValueError(f"Not a valid ZIP file: {path}")

    temp_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="ov_zip_"))
    try:

        def _extract_zip() -> None:
            with zipfile.ZipFile(path, "r") as zipf:
                safe_extract_zip(zipf, temp_dir)

        await asyncio.to_thread(_extract_zip)
        return temp_dir
    except Exception:
        await asyncio.to_thread(shutil.rmtree, temp_dir, True)
        raise


def _select_extracted_directory(
    temp_dir: Path,
    archive_path: Path,
    source_name: Optional[str],
) -> tuple[Path, Optional[str]]:
    """Return the DirectoryParser root and effective source name for a ZIP."""
    extracted_entries = [
        path
        for path in temp_dir.iterdir()
        if path.name not in {".", ".."} and not _is_zip_metadata_entry(path)
    ]
    if len(extracted_entries) == 1 and extracted_entries[0].is_dir():
        source_leaf = Path(source_name).name if source_name else None
        source_stem = Path(source_leaf).stem if source_leaf else None
        root_name = extracted_entries[0].name
        if not source_name or source_leaf == root_name or source_stem == root_name:
            return extracted_entries[0], None

    return temp_dir, source_name or archive_path.stem


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
        temp_dir = await _extract_zip_to_temp(path)
        try:
            # Prepare kwargs for DirectoryParser
            dir_kwargs = dict(kwargs)
            dir_kwargs["instruction"] = instruction

            # Use DirectoryParser to process the extracted content
            from openviking.parse.parsers.directory import DirectoryParser

            parser = DirectoryParser()

            directory_source, effective_source_name = _select_extracted_directory(
                temp_dir,
                path,
                dir_kwargs.get("source_name"),
            )
            if effective_source_name is None:
                dir_kwargs.pop("source_name", None)
            else:
                dir_kwargs["source_name"] = effective_source_name
            result = await parser.parse(str(directory_source), **dir_kwargs)

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
