# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Directory parser for OpenViking.

Handles local directories containing mixed document types (PDF, Markdown,
Text, code, etc.).  Follows the same three-phase pattern as
CodeRepositoryParser:

1. Scan → classify files with ``scan_directory()``
2. For each file:
   - Files WITH a dedicated parser → ``parser.parse()`` handles conversion
     and VikingFS temp creation; results are merged into the main temp.
   - Files WITHOUT a parser (code, config, …) → written directly to VikingFS.
3. Return ``ParseResult`` so that ``TreeBuilder.finalize_from_temp``
   can move the content to AGFS and enqueue semantic processing.
"""

import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from openviking.parse.base import (
    NodeType,
    ParseResult,
    ResourceNode,
    create_parse_result,
)
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import MEDIA_EXTENSIONS
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.parse.directory_scan import ClassifiedFile
    from openviking.parse.registry import ParserRegistry

logger = get_logger(__name__)


class DirectoryParser(BaseParser):
    """
    Parser for local directories.

    Scans the directory, delegates each file to its registered parser via
    ``parser.parse()``, and merges all results into a single VikingFS temp.
    Files without a dedicated parser are written directly.

    The resulting ``ParseResult.temp_dir_path`` is consumed by
    ``TreeBuilder.finalize_from_temp`` exactly like any other parser.
    """

    @property
    def supported_extensions(self) -> List[str]:
        # Directories have no file extension; routing is handled
        # by ``is_dir()`` checks in the registry / media processor.
        return []

    def can_parse(self, path: Union[str, Path]) -> bool:  # type: ignore[override]
        """Return *True* when *path* is an existing directory."""
        return Path(path).is_dir()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def parse(
        self,
        source: Union[str, Path],
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        """Parse a local directory.

        Args:
            source: Path to the directory.
            instruction: Processing instruction (forwarded where applicable).
            **kwargs: Extra options forwarded to ``scan_directory``:
                ``strict``, ``ignore_dirs``, ``include``, ``exclude``,
                ``directly_upload_media``.

        Returns:
            ``ParseResult`` with ``temp_dir_path`` pointing to VikingFS temp.
        """
        start_time = time.time()
        source_path = Path(source).resolve()

        if not source_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {source_path}")

        dir_name = source_path.name
        warnings: List[str] = []

        try:
            # ── Phase 1: scan directory ───────────────────────────────
            from openviking.parse.directory_scan import scan_directory
            from openviking.parse.registry import get_registry

            registry = get_registry()

            scan_result = scan_directory(
                root=str(source_path),
                registry=registry,
                strict=kwargs.get("strict", False),
                ignore_dirs=kwargs.get("ignore_dirs"),
                include=kwargs.get("include"),
                exclude=kwargs.get("exclude"),
            )
            directly_upload_media = kwargs.get("directly_upload_media", True)
            processable_files = scan_result.all_processable_files()
            warnings.extend(scan_result.warnings)

            viking_fs = self._get_viking_fs()
            temp_uri = self._create_temp_uri()
            target_uri = f"{temp_uri}/{dir_name}"
            await viking_fs.mkdir(temp_uri)
            await viking_fs.mkdir(target_uri)

            if not processable_files:
                root = ResourceNode(
                    type=NodeType.ROOT,
                    title=dir_name,
                    meta={"file_count": 0, "type": "directory"},
                )
                result = create_parse_result(
                    root=root,
                    source_path=str(source_path),
                    source_format="directory",
                    parser_name="DirectoryParser",
                    parse_time=time.time() - start_time,
                    warnings=warnings,
                )
                result.temp_dir_path = temp_uri
                return result

            # ── Phase 2: process each file ────────────────────────────
            file_count = 0
            processed_files: List[Dict[str, str]] = []
            failed_files: List[Dict[str, str]] = []

            for cf in processable_files:
                file_parser = self._assign_parser(cf, registry)
                parser_name = type(file_parser).__name__ if file_parser else "direct"

                # Check if this is a media parser and we should directly upload
                is_media_parser = file_parser and parser_name in [
                    "ImageParser",
                    "AudioParser",
                    "VideoParser",
                ]
                ext = Path(cf.path).suffix.lower()
                is_media_file = ext in MEDIA_EXTENSIONS

                if directly_upload_media and is_media_parser and is_media_file:
                    # Directly upload media file without using media parser
                    ok = await self._upload_file_directly(
                        cf,
                        target_uri,
                        viking_fs,
                        warnings,
                    )
                    parser_name = "direct_upload"
                else:
                    # Normal processing with parser
                    ok = await self._process_single_file(
                        cf,
                        file_parser,
                        target_uri,
                        viking_fs,
                        warnings,
                    )

                if ok:
                    file_count += 1
                    processed_files.append(
                        {
                            "path": cf.rel_path,
                            "parser": parser_name,
                        }
                    )
                else:
                    failed_files.append(
                        {
                            "path": cf.rel_path,
                            "parser": parser_name,
                        }
                    )

            # Collect unsupported files from scan result
            unsupported_files = [
                {
                    "path": uf.rel_path,
                    "status": "unsupported",
                    "reason": uf.classification,
                }
                for uf in scan_result.unsupported
            ]

            # Parse skipped entries: format is "path (reason)"
            skipped_files = self._parse_skipped(scan_result.skipped)

            # ── Phase 3: build ParseResult ────────────────────────────
            root = ResourceNode(
                type=NodeType.ROOT,
                title=dir_name,
                meta={
                    "file_count": file_count,
                    "type": "directory",
                },
            )

            result = create_parse_result(
                root=root,
                source_path=str(source_path),
                source_format="directory",
                parser_name="DirectoryParser",
                parse_time=time.time() - start_time,
                warnings=warnings,
            )
            result.temp_dir_path = temp_uri
            result.meta["file_count"] = file_count
            result.meta["dir_name"] = dir_name
            result.meta["total_processable"] = len(processable_files)
            result.meta["processed_files"] = processed_files
            result.meta["failed_files"] = failed_files
            result.meta["unsupported_files"] = unsupported_files
            result.meta["skipped_files"] = skipped_files

            return result

        except Exception as exc:
            logger.error(
                f"[DirectoryParser] Failed to parse directory {source_path}: {exc}",
                exc_info=True,
            )
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT),
                source_path=str(source_path),
                source_format="directory",
                parser_name="DirectoryParser",
                parse_time=time.time() - start_time,
                warnings=[f"Failed to parse directory: {exc}"],
            )

    # ------------------------------------------------------------------
    # parse_content – not applicable for directories
    # ------------------------------------------------------------------

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        raise NotImplementedError("DirectoryParser does not support parse_content")

    # ------------------------------------------------------------------
    # Skipped entries parsing
    # ------------------------------------------------------------------

    _REASON_TO_STATUS = {
        "dot directory": "ignore",
        "dot file": "ignore",
        "symlink": "ignore",
        "empty file": "ignore",
        "os error": "ignore",
        "IGNORE_DIRS": "ignore",
        "ignore_dirs": "ignore",
        "excluded by include filter": "exclude",
        "excluded by exclude filter": "exclude",
    }

    @staticmethod
    def _parse_skipped(skipped: List[str]) -> List[Dict[str, str]]:
        """Parse skipped entry strings into structured dicts.

        Each entry has the format ``"rel_path (reason)"``.
        Returns a list of ``{"path": ..., "status": ...}``.
        """
        result: List[Dict[str, str]] = []
        for entry in skipped:
            # Extract "path (reason)"
            paren_idx = entry.rfind(" (")
            if paren_idx != -1 and entry.endswith(")"):
                path = entry[:paren_idx]
                reason = entry[paren_idx + 2 : -1]
            else:
                path = entry
                reason = "skip"
            status = DirectoryParser._REASON_TO_STATUS.get(reason, "skip")
            result.append({"path": path, "status": status})
        return result

    # ------------------------------------------------------------------
    # Parser assignment
    # ------------------------------------------------------------------

    @staticmethod
    def _assign_parser(
        classified_file: "ClassifiedFile",
        registry: "ParserRegistry",
    ) -> Optional[BaseParser]:
        """Look up the parser for a file via the registry.

        Returns:
            The ``BaseParser`` instance for the file's extension, or
            ``None`` for text-fallback files with no dedicated parser.
        """
        return registry.get_parser_for_file(classified_file.path)

    # ------------------------------------------------------------------
    # Per-file processing
    # ------------------------------------------------------------------

    @staticmethod
    async def _process_single_file(
        classified_file: "ClassifiedFile",
        parser: Optional[BaseParser],
        target_uri: str,
        viking_fs: Any,
        warnings: List[str],
    ) -> bool:
        """Process one file into the VikingFS directory temp.

        - Files WITH a parser → ``parser.parse()`` → merge output into
          *target_uri* at the correct relative location.
        - Files WITHOUT a parser → read and write directly to VikingFS.

        Returns:
            *True* on success, *False* on failure.
        """
        rel_path = classified_file.rel_path
        src_file = classified_file.path

        if parser:
            try:
                sub_result = await parser.parse(str(src_file))
                if sub_result.temp_dir_path:
                    parent = str(PurePosixPath(rel_path).parent)
                    dest = f"{target_uri}/{parent}" if parent != "." else target_uri
                    await DirectoryParser._merge_temp(
                        viking_fs,
                        sub_result.temp_dir_path,
                        dest,
                    )
                return True
            except Exception as exc:
                warnings.append(f"Failed to parse {rel_path}: {exc}")
                return False
        else:
            try:
                content = src_file.read_bytes()
                dst_uri = f"{target_uri}/{rel_path}"
                await viking_fs.write_file(dst_uri, content)
                return True
            except Exception as exc:
                warnings.append(f"Failed to upload {rel_path}: {exc}")
                return False

    @staticmethod
    async def _upload_file_directly(
        classified_file: "ClassifiedFile",
        target_uri: str,
        viking_fs: Any,
        warnings: List[str],
    ) -> bool:
        """Directly upload a file without using its parser.

        Used for media files when directly_upload_media=True.

        Returns:
            *True* on success, *False* on failure.
        """
        rel_path = classified_file.rel_path
        src_file = classified_file.path

        try:
            content = src_file.read_bytes()
            dst_uri = f"{target_uri}/{rel_path}"
            await viking_fs.write_file(dst_uri, content)
            return True
        except Exception as exc:
            warnings.append(f"Failed to upload {rel_path}: {exc}")
            return False

    # ------------------------------------------------------------------
    # VikingFS merge helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_dir_entry(entry: Dict[str, Any]) -> bool:
        """Check whether an AGFS ``ls`` entry represents a directory."""
        return bool(entry.get("isDir", False)) or entry.get("type") == "directory"

    @staticmethod
    async def _merge_temp(
        viking_fs: Any,
        src_temp_uri: str,
        dest_uri: str,
    ) -> None:
        """Move all content from a parser's temp directory into *dest_uri*.

        After the move the source temp is deleted.
        """
        entries = await viking_fs.ls(src_temp_uri)
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in (".", ".."):
                continue
            src = entry.get("uri", f"{src_temp_uri.rstrip('/')}/{name}")
            dst = f"{dest_uri.rstrip('/')}/{name}"
            if DirectoryParser._is_dir_entry(entry):
                await DirectoryParser._recursive_move(viking_fs, src, dst)
            else:
                await viking_fs.move_file(src, dst)
        try:
            await viking_fs.delete_temp(src_temp_uri)
        except Exception:
            pass

    @staticmethod
    async def _recursive_move(
        viking_fs: Any,
        src_uri: str,
        dst_uri: str,
    ) -> None:
        """Recursively move a VikingFS directory tree."""
        await viking_fs.mkdir(dst_uri, exist_ok=True)
        entries = await viking_fs.ls(src_uri)
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in (".", ".."):
                continue
            s = f"{src_uri.rstrip('/')}/{name}"
            d = f"{dst_uri.rstrip('/')}/{name}"
            if DirectoryParser._is_dir_entry(entry):
                await DirectoryParser._recursive_move(viking_fs, s, d)
            else:
                await viking_fs.move_file(s, d)
