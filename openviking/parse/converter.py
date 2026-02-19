# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Document converters for consistent rendering."""

import asyncio
import tempfile
from pathlib import Path
from typing import Optional

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class DocumentConverter:
    """Converts documents to PDF for consistent rendering (DOCX/MD/PPTX -> PDF)."""

    def __init__(self, temp_dir: Optional[Path] = None):
        self.temp_dir = temp_dir

    async def to_pdf(self, file_path: Path) -> Optional[Path]:
        """Convert document to PDF."""
        ext = file_path.suffix.lower()

        if ext == ".pdf":
            return file_path
        elif ext in (".docx", ".pptx"):
            return await self._convert_with_libreoffice(file_path)
        elif ext in (".md", ".markdown"):
            return await self._convert_markdown_to_pdf(file_path)

        logger.warning(f"No converter available for {ext}")
        return None

    async def _convert_with_libreoffice(self, file_path: Path) -> Optional[Path]:
        """Convert using LibreOffice (soffice)."""
        output_dir = self.temp_dir or Path(tempfile.gettempdir())
        output_path = output_dir / f"{file_path.stem}.pdf"

        try:
            cmd = [
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(file_path),
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()

            if process.returncode == 0 and output_path.exists():
                return output_path
            return None
        except FileNotFoundError:
            logger.warning("LibreOffice not found")
            return None
        except Exception as e:
            logger.error(f"Conversion error: {e}")
            return None

    async def _convert_markdown_to_pdf(self, file_path: Path) -> Optional[Path]:
        """Convert Markdown to PDF using pandoc."""
        output_dir = self.temp_dir or Path(tempfile.gettempdir())
        output_path = output_dir / f"{file_path.stem}.pdf"

        try:
            cmd = ["pandoc", str(file_path), "-o", str(output_path), "--pdf-engine=xelatex"]
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()

            if process.returncode == 0 and output_path.exists():
                return output_path
            return await self._convert_with_libreoffice(file_path)
        except FileNotFoundError:
            return await self._convert_with_libreoffice(file_path)
        except Exception:
            return None
