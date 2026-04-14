# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unified resource processor with strategy-based routing."""

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from openviking.parse import DocumentConverter, parse
from openviking.parse.accessors.base import SourceType
from openviking.parse.base import ParseResult
from openviking.server.local_input_guard import (
    is_remote_resource_source,
    looks_like_local_path,
)
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.parse.vlm import VLMProcessor
    from openviking_cli.utils.storage import StoragePath

logger = get_logger(__name__)


class UnifiedResourceProcessor:
    """Unified resource processing for files, URLs, and raw content.

    Uses two-layer architecture:
    - Phase 1: AccessorRegistry gets LocalResource from source
    - Phase 2: ParserRegistry parses LocalResource to ParseResult
    """

    def __init__(
        self,
        vlm_processor: Optional["VLMProcessor"] = None,
        storage: Optional["StoragePath"] = None,
    ):
        self.storage = storage
        self._vlm_processor = vlm_processor
        self._document_converter = None
        self._accessor_registry = None

    def _get_vlm_processor(self) -> Optional["VLMProcessor"]:
        if self._vlm_processor is None:
            from openviking.parse.vlm import VLMProcessor

            self._vlm_processor = VLMProcessor()
        return self._vlm_processor

    def _get_document_converter(self) -> DocumentConverter:
        if self._document_converter is None:
            self._document_converter = DocumentConverter()
        return self._document_converter

    def _get_accessor_registry(self):
        """Lazy initialize AccessorRegistry for two-layer mode."""
        if self._accessor_registry is None:
            from openviking.parse.accessors import get_accessor_registry

            self._accessor_registry = get_accessor_registry()
        return self._accessor_registry

    async def process(
        self,
        source: str,
        instruction: str = "",
        allow_local_path_resolution: bool = True,
        **kwargs,
    ) -> ParseResult:
        """Process any source (file/URL/content) with two-layer architecture.

        Phase 1: Use AccessorRegistry to get LocalResource
        Phase 2: Use ParserRegistry to parse LocalResource

        Resource Lifecycle:
        - Temporary resources are managed via context manager or temp_dir_path
        - Directories needed for TreeBuilder are preserved via ParseResult.temp_dir_path
        """

        # First check if source is raw content (not URL/path)
        is_potential_path = (
            allow_local_path_resolution and len(source) <= 1024 and "\n" not in source
        )
        if not is_potential_path and not self._is_url(source):
            # Treat as raw content
            return await parse(source, instruction=instruction)

        # Block local paths in HTTP server mode, but allow remote URLs
        if (
            not allow_local_path_resolution
            and not is_remote_resource_source(source)
            and looks_like_local_path(source)
        ):
            raise PermissionDeniedError(
                "HTTP server only accepts remote resource URLs or temp-uploaded files; "
                "direct host filesystem paths are not allowed."
            )

        # Phase 1: Accessor - get local resource
        registry = self._get_accessor_registry()
        local_resource = await registry.access(source, **kwargs)

        # Use context manager for automatic cleanup, but preserve directories for TreeBuilder
        try:
            # Phase 2: Parser - parse the local resource
            parse_kwargs = dict(kwargs)
            parse_kwargs["instruction"] = instruction
            parse_kwargs["vlm_processor"] = self._get_vlm_processor()
            parse_kwargs["storage"] = self.storage
            parse_kwargs["_source_meta"] = local_resource.meta
            parse_kwargs["original_source"] = local_resource.original_source

            # Set resource_name from source_name or path
            source_name = kwargs.get("source_name")
            if source_name:
                parse_kwargs["resource_name"] = Path(source_name).stem
                parse_kwargs.setdefault("source_name", source_name)
            else:
                # For git repositories, use repo_name from meta if available
                repo_name = local_resource.meta.get("repo_name")
                if repo_name and local_resource.source_type == SourceType.GIT:
                    # Use the last part of repo_name as the resource_name (e.g., "OpenViking" from "volcengine/OpenViking")
                    parse_kwargs["resource_name"] = repo_name.split("/")[-1]
                else:
                    # Prefer original_filename from meta for HTTP downloads
                    original_filename = local_resource.meta.get("original_filename")
                    if original_filename:
                        parse_kwargs.setdefault("resource_name", Path(original_filename).stem)
                        parse_kwargs.setdefault("source_name", original_filename)
                    else:
                        parse_kwargs.setdefault("resource_name", local_resource.path.stem)

            # If it's a directory, use DirectoryParser which will delegate to CodeRepositoryParser if it's a git repo
            if local_resource.path.is_dir():
                from openviking.parse.parsers.directory import DirectoryParser

                parser = DirectoryParser()

                result = await parser.parse(str(local_resource.path), **parse_kwargs)
                # Preserve temporary directory for TreeBuilder
                if local_resource.is_temporary and not result.temp_dir_path:
                    result.temp_dir_path = str(local_resource.path)
                    # Mark as non-temporary so context manager doesn't clean it up
                    local_resource.is_temporary = False
                return result

            # For files, use the unified parse function (including .zip files via ZipParser)
            return await parse(str(local_resource.path), **parse_kwargs)
        finally:
            # Clean up temporary resources unless they need to be preserved
            local_resource.cleanup()

    def _is_url(self, source: str) -> bool:
        """Check if source is a URL."""
        return is_remote_resource_source(source)
