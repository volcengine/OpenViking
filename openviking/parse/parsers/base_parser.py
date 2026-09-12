# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

from openviking.parse.base import ParseResult

if TYPE_CHECKING:
    from openviking.parse.output import ParseOutputStore


class BaseParser(ABC):
    """
    Abstract base class for document parsers.

    Parsers convert documents into tree structures that preserve
    natural document hierarchy (sections, paragraphs, etc.).

    All parsers use async interface for parsing operations.
    """

    #: Optional artifact store. When unset, parsers fall back to the global
    #: VikingFS singleton (AGFS temp), i.e. the pre-abstraction behaviour.
    _output_store: "Optional[ParseOutputStore]" = None

    def set_output_store(self, store: "Optional[ParseOutputStore]") -> None:
        """Attach an artifact store for this parse. ``None`` restores the default."""
        self._output_store = store

    @abstractmethod
    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse a document from file path or content string.

        Args:
            source: File path or content string
            instruction: Processing instruction, guides LLM how to understand the resource
            **kwargs: Additional parameters (e.g., vlm_processor, etc.)

        Returns:
            ParseResult with document tree (including temp_dir_path in v4.0)
        """
        pass

    @abstractmethod
    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse document content directly.

        Args:
            content: Document content string
            source_path: Optional source path for reference
            instruction: Processing instruction, guides LLM how to understand the resource
            **kwargs: Additional parameters

        Returns:
            ParseResult with document tree (including temp_dir_path in v4.0)
        """
        pass

    @property
    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """List of supported file extensions."""
        pass

    def can_parse(self, path: Union[str, Path]) -> bool:
        """
        Check if this parser can handle the given file.

        Args:
            path: File path

        Returns:
            True if this parser supports the file type
        """
        path = Path(path)
        return path.suffix.lower() in self.supported_extensions

    def _read_file(self, path: Union[str, Path]) -> str:
        """
        Read file content with encoding detection.

        Args:
            path: File path

        Returns:
            File content as string
        """
        path = Path(path)
        from openviking.parse.parsers.upload_utils import detect_and_convert_encoding

        raw = path.read_bytes()
        normalized = detect_and_convert_encoding(raw, path)
        try:
            return normalized.decode("utf-8")
        except UnicodeDecodeError:
            pass

        # Preserve the previous permissive fallback for files whose encoding is
        # unknown or rejected by text detection as likely binary/control-heavy.
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue

        return raw.decode("latin-1")

    def _get_viking_fs(self):
        """
        Get the VikingFS singleton instance.

        Returns:
            VikingFS instance
        """
        from openviking.storage.viking_fs import get_viking_fs

        return get_viking_fs()

    def _create_temp_uri(self) -> str:
        """
        Create a temporary URI for storing intermediate files during parsing.

        This is a common utility method for all parsers that follow the
        three-phase parsing architecture.

        Returns:
            Temporary URI string (e.g., "viking://temp/abc12345")
        """
        return self._get_viking_fs().create_temp_uri()

    def _artifact_ref_for(self, temp_uri: str):
        """Build a serializable artifact ref for a temp root this parser wrote.

        Step 2a always uses the AGFS backend; the ref is a thin view over the
        temp URI so downstream persistence can migrate off bare strings.
        """
        from openviking.parse.output import ParseArtifactRef

        return ParseArtifactRef(backend="agfs", root=temp_uri, root_type="dir")
