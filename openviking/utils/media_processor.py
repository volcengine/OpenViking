# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unified resource processor with strategy-based routing."""

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from openviking.parse import DocumentConverter, parse
from openviking.parse.accessors.base import SourceType
from openviking.parse.accessors.mime_types import IANA_MEDIA_TYPE_TO_EXTENSION
from openviking.parse.base import ParseResult
from openviking.parse.parsers.constants import (
    CODE_EXTENSIONS,
    DOCUMENTATION_EXTENSIONS,
    IGNORE_EXTENSIONS,
)
from openviking.server.local_input_guard import (
    is_remote_resource_source,
    looks_like_local_path,
)
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.utils.logger import get_logger

# All known valid extensions - only these should be stripped when getting stem
# Build from multiple sources:
# 1. IANA media type mappings (comprehensive)
# 2. Code/documentation/ignore extensions (parser-specific)
KNOWN_EXTENSIONS: set[str] = set()
for extensions in IANA_MEDIA_TYPE_TO_EXTENSION.values():
    KNOWN_EXTENSIONS.update(extensions)
KNOWN_EXTENSIONS.update(CODE_EXTENSIONS)
KNOWN_EXTENSIONS.update(DOCUMENTATION_EXTENSIONS)
KNOWN_EXTENSIONS.update(IGNORE_EXTENSIONS)


def _smart_stem(path_or_name: str | Path) -> str:
    """Get the stem of a filename, but only strip known valid extensions.

    For filenames like "2601.00014" where ".00014" is not a valid extension,
    returns the full name instead of just "2601".

    Args:
        path_or_name: Path object or string filename

    Returns:
        Stem with only known extensions stripped
    """
    path = Path(path_or_name)
    suffix = path.suffix.lower()

    if suffix in KNOWN_EXTENSIONS:
        return path.stem

    # If the suffix is not a known extension, treat the whole name as the stem
    return path.name


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
            # CRITICAL: Pass along the original_source!
            # This is the full URL the user provided (e.g. "https://github.com/volcengine/OpenViking")
            # CodeRepositoryParser and TreeBuilder need this to extract the org/repo format
            # Without it, we'd only get the repo name without the org prefix!
            parse_kwargs["original_source"] = local_resource.original_source

            # Set resource_name from source_name or path
            source_name = kwargs.get("source_name")
            if source_name:
                parse_kwargs["resource_name"] = _smart_stem(source_name)
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
                        parse_kwargs.setdefault("resource_name", _smart_stem(original_filename))
                        parse_kwargs.setdefault("source_name", original_filename)
                    else:
                        parse_kwargs.setdefault("resource_name", _smart_stem(local_resource.path))

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

    @staticmethod
    def _is_feishu_url(source: str) -> bool:
        """Backward-compatible Feishu URL detector used by legacy tests/callers."""
        try:
            from openviking.parse.accessors.feishu_accessor import FeishuAccessor

            return FeishuAccessor._is_feishu_url(source)
        except Exception:
            return False
