# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unified resource processor with strategy-based routing."""

import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from openviking.parse import DocumentConverter, parse
from openviking.parse.base import ParseResult
from openviking.server.local_input_guard import (
    is_remote_resource_source,
    looks_like_local_path,
)
from openviking.utils.zip_safe import safe_extract_zip
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.parse.vlm import VLMProcessor
    from openviking_cli.utils.storage import StoragePath

logger = get_logger(__name__)


class UnifiedResourceProcessor:
    """Unified resource processing for files, URLs, and raw content."""

    def __init__(
        self,
        vlm_processor: Optional["VLMProcessor"] = None,
        storage: Optional["StoragePath"] = None,
    ):
        self.storage = storage
        self._vlm_processor = vlm_processor
        self._document_converter = None

    def _get_vlm_processor(self) -> Optional["VLMProcessor"]:
        if self._vlm_processor is None:
            from openviking.parse.vlm import VLMProcessor

            self._vlm_processor = VLMProcessor()
        return self._vlm_processor

    def _get_document_converter(self) -> DocumentConverter:
        if self._document_converter is None:
            self._document_converter = DocumentConverter()
        return self._document_converter

    async def process(
        self,
        source: str,
        instruction: str = "",
        allow_local_path_resolution: bool = True,
        **kwargs,
    ) -> ParseResult:
        """Process any source (file/URL/content) with appropriate strategy."""
        # Check if URL
        if self._is_url(source):
            return await self._process_url(source, instruction)

        # Check if looks like a file path (short enough and no newlines)
        is_potential_path = (
            allow_local_path_resolution and len(source) <= 1024 and "\n" not in source
        )
        if is_potential_path:
            path = Path(source)
            if path.exists():
                if path.is_dir():
                    return await self._process_directory(path, instruction, **kwargs)
                return await self._process_file(path, instruction, **kwargs)
            else:
                logger.warning(f"Path {path} does not exist")
                raise FileNotFoundError(f"Path {path} does not exist")

        if not allow_local_path_resolution and looks_like_local_path(source):
            raise PermissionDeniedError(
                "HTTP server only accepts remote resource URLs or temp-uploaded files; "
                "direct host filesystem paths are not allowed."
            )

        # Treat as raw content
        return await parse(source, instruction=instruction)

    def _is_url(self, source: str) -> bool:
        """Check if source is a URL."""
        return is_remote_resource_source(source)

    async def _process_url(self, url: str, instruction: str, **kwargs) -> ParseResult:
        """Process URL source."""
        from openviking.utils.code_hosting_utils import is_git_repo_url, validate_git_ssh_uri

        # Validate git@ SSH URIs early
        if url.startswith("git@"):
            validate_git_ssh_uri(url)

        # Route Feishu/Lark cloud document URLs to FeishuParser
        if self._is_feishu_url(url):
            from openviking.parse.registry import get_registry

            parser = get_registry().get_parser("feishu")
            if parser is None:
                raise ImportError(
                    "FeishuParser not available. "
                    "Install lark-oapi: pip install 'openviking[bot-feishu]'"
                )
            return await parser.parse(url, instruction=instruction, **kwargs)

        # Route git protocols and repo URLs to CodeRepositoryParser
        if url.startswith(("git@", "git://", "ssh://")) or is_git_repo_url(url):
            from openviking.parse.parsers.code.code import CodeRepositoryParser

            parser = CodeRepositoryParser()
            return await parser.parse(url, instruction=instruction, **kwargs)

        from openviking.parse.parsers.html import HTMLParser

        parser = HTMLParser()
        return await parser.parse(url, instruction=instruction, **kwargs)

    @staticmethod
    def _is_feishu_url(url: str) -> bool:
        """Check if URL is a Feishu/Lark cloud document."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path
        is_feishu_domain = host.endswith(".feishu.cn") or host.endswith(".larksuite.com")
        has_doc_path = any(
            path == f"/{t}" or path.startswith(f"/{t}/") for t in ("docx", "wiki", "sheets", "base")
        )
        return is_feishu_domain and has_doc_path

    async def _process_directory(
        self,
        dir_path: Path,
        instruction: str,
        **kwargs,
    ) -> ParseResult:
        """Process directory source via DirectoryParser.

        Args:
            dir_path: Path to the directory.
            instruction: Processing instruction.
            **kwargs: Forwarded to ``DirectoryParser.parse()`` →
                ``scan_directory()``: ``strict``, ``ignore_dirs``,
                ``include``, ``exclude``.
        """
        from openviking.parse.parsers.directory import DirectoryParser

        parser = DirectoryParser()
        return await parser.parse(str(dir_path), instruction=instruction, **kwargs)

    async def _process_file(
        self,
        file_path: Path,
        instruction: str,
        **kwargs,
    ) -> ParseResult:
        """Process file with unified parsing."""
        ext = file_path.suffix.lower()
        # Only treat .zip files as archives to extract.
        if ext == ".zip" and zipfile.is_zipfile(file_path):
            temp_dir = Path(tempfile.mkdtemp())
            try:
                with zipfile.ZipFile(file_path, "r") as zipf:
                    safe_extract_zip(zipf, temp_dir)
                visible = [
                    p
                    for p in temp_dir.iterdir()
                    if p.name not in {".", ".."} and not p.name.startswith(".")
                ]
                dirs = [p for p in visible if p.is_dir()]
                files = [p for p in visible if p.is_file()]

                if len(dirs) == 1 and not files:
                    inferred_root = dirs[0]
                    kwargs_for_dir = dict(kwargs)
                    kwargs_for_dir.pop("source_name", None)
                    return await self._process_directory(inferred_root, instruction, **kwargs_for_dir)
                if len(files) == 1 and not dirs:
                    return await self._process_file(files[0], instruction, **kwargs)

                kwargs_for_dir = dict(kwargs)
                kwargs_for_dir["source_name"] = kwargs_for_dir.get("source_name") or file_path.stem
                return await self._process_directory(temp_dir, instruction, **kwargs_for_dir)
            finally:
                pass  # Don't delete temp_dir yet, it will be used by TreeBuilder
        return await parse(
            str(file_path),
            instruction=instruction,
            vlm_processor=self._get_vlm_processor(),
            storage=self.storage,
            resource_name=kwargs.get("source_name") or kwargs.get("resource_name") or file_path.stem,
        )
