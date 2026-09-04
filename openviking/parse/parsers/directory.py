# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Directory parser for OpenViking.

Handles local directories containing mixed document types (PDF, Markdown,
Text, code, etc.).  Follows the same three-phase pattern as
CodeRepositoryParser:

1. Scan → classify files with ``scan_directory()``
2. For each file:
   - Files routed to UnderstandingAPI or a dedicated internal parser →
     ``parser.parse()`` handles conversion and VikingFS temp creation; results
     are merged into the main temp.
   - Files WITHOUT a parser (code, config, …) → written directly to VikingFS.
3. Return ``ParseResult`` so that ``TreeBuilder.finalize_from_temp``
   can move the content to AGFS and enqueue semantic processing.
"""

import asyncio
import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union
from weakref import WeakKeyDictionary

from openviking.parse.base import (
    NodeType,
    ParseResult,
    ResourceNode,
    create_parse_result,
)
from openviking.parse.image_rewrite import IMAGE_MAPPINGS_FILENAME
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import MEDIA_EXTENSIONS
from openviking.parse.parsers.upload_utils import is_text_file
from openviking.storage.viking_fs import LS_ALL_NODES
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.parse.directory_scan import ClassifiedFile
    from openviking.parse.parser_router import ParserRouter
    from openviking.parse.registry import ParserRegistry

logger = get_logger(__name__)

# Hidden files a parser's temp tree is allowed to carry through the merge.
# Everything else hidden stays filtered, like a default ls.
_MERGE_SIDECAR_ALLOWLIST = frozenset({IMAGE_MAPPINGS_FILENAME})

# DirectoryParser instances share one limiter in the server event loop so
# concurrent directory imports cannot multiply UnderstandingAPI concurrency.
_UNDERSTANDING_LIMITERS: WeakKeyDictionary[
    asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]
] = WeakKeyDictionary()


def _get_understanding_limiter(max_concurrent: int) -> asyncio.Semaphore:
    """Return the shared directory Understanding limiter for this event loop."""
    loop = asyncio.get_running_loop()
    configured = _UNDERSTANDING_LIMITERS.get(loop)
    if configured is None:
        limiter = asyncio.Semaphore(max_concurrent)
        _UNDERSTANDING_LIMITERS[loop] = (max_concurrent, limiter)
        return limiter

    configured_limit, limiter = configured
    if configured_limit != max_concurrent:
        raise InvalidArgumentError(
            "parsers.directory.max_concurrent changed while the service is running; "
            "restart the service to apply the new global directory limit"
        )
    return limiter


class DirectoryParser(BaseParser):
    """
    Parser for local directories.

    Scans the directory, routes each file through ParserRouter when configured
    for UnderstandingAPI, and otherwise delegates to its registered parser.
    Files without any parser are written directly.

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

        # Check if this is a git repository, delegate to CodeRepositoryParser
        if await self._is_git_repository(source_path):
            logger.debug(
                f"Directory {source_path} is a git repository, delegating to CodeRepositoryParser"
            )
            from openviking.parse.parsers.code.code import CodeRepositoryParser

            # Don't add git metadata if we already have _source_meta from DataAccessor
            # This is crucial:
            #   1. _source_meta already contains repo_name in org/repo format from GitAccessor
            #   2. kwargs also has original_source with the full code-hosting URL
            #   3. Calling _add_git_metadata would overwrite repo_name with just directory name
            #      and lose the org prefix!
            if "_source_meta" not in kwargs:
                await self._add_git_metadata(source_path, kwargs)
            return await CodeRepositoryParser().parse(str(source_path), instruction, **kwargs)

        dir_name = kwargs.get("source_name") or source_path.name
        warnings: List[str] = []

        try:
            # ── Phase 1: scan directory ───────────────────────────────
            from openviking.parse.directory_scan import scan_directory
            from openviking.parse.parser_router import ParserRouter
            from openviking.parse.registry import get_registry
            from openviking_cli.utils.config.open_viking_config import get_openviking_config
            from openviking_cli.utils.config.parser_config import DirectoryConfig

            registry = get_registry()
            parser_router = ParserRouter(registry)
            ov_config = get_openviking_config()
            directory_config = getattr(ov_config, "directory", None) or DirectoryConfig()

            split_content = kwargs.get("split_content", True)
            parser_api_config = getattr(ov_config, "parser_api", None)
            understanding_limits_enabled = bool(
                split_content
                and parser_router.understanding_api_enabled()
                and getattr(parser_api_config, "extensions", None)
            )

            scan_result = scan_directory(
                root=str(source_path),
                registry=registry,
                strict=kwargs.get("strict", False),
                ignore_dirs=kwargs.get("ignore_dirs"),
                include=kwargs.get("include"),
                exclude=kwargs.get("exclude"),
                additional_can_process=parser_router.should_use_understanding_api,
                max_files=directory_config.max_files if understanding_limits_enabled else None,
                max_depth=directory_config.max_depth if understanding_limits_enabled else None,
            )
            directly_upload_media = kwargs.get("directly_upload_media", True)
            preserve_structure = kwargs.get("preserve_structure")
            if preserve_structure is None:
                preserve_structure = directory_config.preserve_structure
            processable_files = scan_result.all_processable_files()
            warnings.extend(scan_result.warnings)
            source_skipped_items = self._source_skipped_items(
                kwargs.get("_source_meta"),
                source_path,
            )
            warnings.extend(
                f"Skipped Feishu Drive item {item['path']}: {item.get('reason', 'unknown error')}"
                for item in source_skipped_items
            )

            file_jobs: List[Dict[str, Any]] = []
            understanding_jobs: List[Dict[str, Any]] = []
            for index, cf in enumerate(processable_files):
                configured_for_understanding = parser_router.should_use_understanding_api(cf.path)
                use_understanding = bool(split_content and configured_for_understanding)
                native_parser = None if use_understanding else self._assign_parser(cf, registry)
                file_parser = parser_router if use_understanding else native_parser
                native_parser_unavailable = bool(
                    not split_content
                    and configured_for_understanding
                    and native_parser is None
                    and not is_text_file(cf.path)
                )
                parser_name = (
                    "UnderstandingAPI"
                    if use_understanding
                    else "native"
                    if native_parser_unavailable
                    else type(file_parser).__name__
                    if file_parser
                    else "direct"
                )

                is_media_parser = file_parser and parser_name in {
                    "ImageParser",
                    "AudioParser",
                    "VideoParser",
                }
                is_media_file = Path(cf.path).suffix.lower() in MEDIA_EXTENSIONS
                job = {
                    "index": index,
                    "classified_file": cf,
                    "file_parser": file_parser,
                    "parser_name": parser_name,
                    "use_understanding": use_understanding,
                    "native_parser_unavailable": native_parser_unavailable,
                    "direct_upload": bool(
                        directly_upload_media
                        and not use_understanding
                        and is_media_parser
                        and is_media_file
                    ),
                }
                file_jobs.append(job)
                if use_understanding:
                    understanding_jobs.append(job)

            viking_fs = self._get_viking_fs()
            temp_uri = self._create_temp_uri()
            target_uri = f"{temp_uri}/{dir_name}"
            await viking_fs.mkdir(temp_uri, exist_ok=True)
            await viking_fs.mkdir(target_uri, exist_ok=True)

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
                result.meta["file_count"] = 0
                result.meta["dir_name"] = dir_name
                result.meta["total_processable"] = 0
                result.meta["processed_files"] = []
                result.meta["failed_files"] = source_skipped_items
                result.meta["unsupported_files"] = []
                result.meta["skipped_files"] = self._parse_skipped(scan_result.skipped)
                return result

            # ── Phase 2: process each file ────────────────────────────
            file_count = 0
            processed_files: List[Dict[str, Any]] = []
            failed_files: List[Dict[str, Any]] = []
            understanding_results: Dict[int, Dict[str, Any]] = {}

            if understanding_jobs:
                parser_api = getattr(ov_config, "parser_api", None)
                job_timeout = self._get_parser_api_job_timeout(parser_api)
                logger.info(
                    "[DirectoryParser] Processing %d Understanding file(s) with "
                    "shared_max_concurrent=%d, job_timeout=%.1fs",
                    len(understanding_jobs),
                    directory_config.max_concurrent,
                    job_timeout,
                )
                understanding_results = await self._parse_understanding_jobs(
                    understanding_jobs,
                    preserve_structure=preserve_structure,
                    import_root=str(source_path),
                    split_content=split_content,
                    max_concurrent=directory_config.max_concurrent,
                    job_timeout=job_timeout,
                )

            for job in file_jobs:
                cf = job["classified_file"]
                file_parser = job["file_parser"]
                parser_name = job["parser_name"]
                detail: Dict[str, Any]

                if job["use_understanding"]:
                    parsed = understanding_results[job["index"]]
                    error = parsed.get("error")
                    sub_result = parsed.get("result")
                    if error is not None:
                        warnings.append(f"Failed to parse {cf.rel_path}: {error}")
                        error_meta = getattr(error, "meta", {})
                        detail = {
                            "ok": False,
                            "meta": error_meta if isinstance(error_meta, dict) else {},
                            "error": str(error),
                        }
                    else:
                        try:
                            await self._merge_parser_result(
                                cf,
                                sub_result,
                                target_uri,
                                viking_fs,
                                preserve_structure=preserve_structure,
                                split_content=split_content,
                            )
                            detail = {
                                "ok": True,
                                "meta": getattr(sub_result, "meta", {}) or {},
                                "error": None,
                            }
                        except Exception as exc:
                            warnings.append(f"Failed to parse {cf.rel_path}: {exc}")
                            detail = {
                                "ok": False,
                                "meta": getattr(sub_result, "meta", {}) or {},
                                "error": str(exc),
                            }
                elif job["native_parser_unavailable"]:
                    error = (
                        "parse_mode='no_split' requires a native parser, but none is "
                        "available for this file type"
                    )
                    warnings.append(f"Failed to parse {cf.rel_path}: {error}")
                    detail = {"ok": False, "meta": {}, "error": error}
                elif job["direct_upload"]:
                    detail = await self._upload_file_directly(
                        cf,
                        target_uri,
                        viking_fs,
                        warnings,
                        preserve_structure=preserve_structure,
                    )
                    parser_name = "direct_upload"
                else:
                    detail = await self._process_single_file(
                        cf,
                        file_parser,
                        target_uri,
                        viking_fs,
                        warnings,
                        preserve_structure=preserve_structure,
                        import_root=str(source_path),
                        split_content=split_content,
                    )

                file_entry = self._file_status_entry(cf, parser_name, detail)
                if detail["ok"]:
                    file_count += 1
                    processed_files.append(file_entry)
                else:
                    failed_files.append(file_entry)
                failed_files.extend(self._nested_failed_files(cf, parser_name, detail))

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
            result.meta["failed_files"] = failed_files + source_skipped_items
            result.meta["unsupported_files"] = unsupported_files
            result.meta["skipped_files"] = skipped_files

            return result

        except InvalidArgumentError:
            raise
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
        Returns a list of ``{"path": ..., "status": ..., "reason": ...}``.
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
            result.append({"path": path, "status": status, "reason": reason})
        return result

    @staticmethod
    def _source_skipped_items(source_meta: Any, source_path: Path) -> List[Dict[str, str]]:
        """Normalize skipped items reported by a remote source accessor."""
        if not isinstance(source_meta, dict):
            return []
        items = source_meta.get("feishu_folder_skipped_items") or []
        if not isinstance(items, list):
            return []

        normalized: List[Dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or item.get("name") or item.get("token") or "unknown")
            display_path = raw_path
            try:
                path = Path(raw_path).resolve(strict=False)
                if path.is_absolute():
                    display_path = str(path.relative_to(source_path.resolve(strict=False)))
            except Exception:
                pass
            display_path = display_path.replace("\\", "/")

            normalized.append(
                {
                    "path": display_path,
                    "parser": "feishu",
                    "status": "failed",
                    "type": str(item.get("type") or ""),
                    "token": str(item.get("token") or ""),
                    "reason": str(item.get("reason") or "unknown error"),
                }
            )
        return normalized

    @staticmethod
    def _nested_failed_files(
        classified_file: "ClassifiedFile",
        parser_name: str,
        detail: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Promote leaf failures from a parsed ZIP into the outer directory result."""
        if parser_name != "ZipParser":
            return []
        meta = detail.get("meta")
        if not isinstance(meta, dict) or not isinstance(meta.get("failed_files"), list):
            return []

        prefix = classified_file.rel_path.replace("\\", "/").rstrip("/")
        promoted: List[Dict[str, Any]] = []
        for item in meta["failed_files"]:
            if not isinstance(item, dict):
                continue
            child = dict(item)
            child_path = str(child.get("path") or "<unknown>").replace("\\", "/").lstrip("/")
            child["path"] = f"{prefix}/{child_path}"
            promoted.append(child)
        return promoted

    @staticmethod
    def _file_status_entry(
        classified_file: "ClassifiedFile",
        parser_name: str,
        detail: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build deterministic per-file status metadata."""
        entry: Dict[str, Any] = {
            "path": classified_file.rel_path,
            "parser": parser_name,
        }
        meta = detail.get("meta")
        if isinstance(meta, dict):
            for key in (
                "doc_name",
                "doc_type",
                "source_name",
                "file_name",
                "file_id",
                "response_id",
            ):
                if meta.get(key):
                    entry[key] = meta[key]
        if detail.get("error"):
            entry["error"] = str(detail["error"])
        return entry

    @staticmethod
    def _get_parser_api_job_timeout(parser_api: Any, default: float = 1800.0) -> float:
        """Return a bounded end-to-end timeout for one Understanding file."""
        try:
            response_timeout = float(getattr(parser_api, "response_timeout_seconds", default))
            http_timeout = float(getattr(parser_api, "http_timeout_seconds", 10.0))
        except (TypeError, ValueError) as exc:
            raise InvalidArgumentError(
                "parser_api response/http timeouts must be positive numbers"
            ) from exc
        if response_timeout <= 0 or http_timeout <= 0:
            raise InvalidArgumentError("parser_api response/http timeouts must be positive numbers")
        return response_timeout + max(60.0, http_timeout * 2.0)

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
    async def _parse_understanding_jobs(
        jobs: List[Dict[str, Any]],
        *,
        preserve_structure: bool,
        import_root: Optional[str],
        split_content: bool,
        max_concurrent: int,
        job_timeout: float,
    ) -> Dict[int, Dict[str, Any]]:
        """Parse jobs with a fixed local pool and a shared service-loop limit."""
        limiter = _get_understanding_limiter(max_concurrent)
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        for job in jobs:
            queue.put_nowait(job)

        results: Dict[int, Dict[str, Any]] = {}

        async def _worker() -> None:
            while True:
                try:
                    job = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

                try:
                    async with limiter:
                        parse_coro = DirectoryParser._parse_file_with_parser(
                            job["classified_file"],
                            job["file_parser"],
                            preserve_structure=preserve_structure,
                            import_root=import_root,
                            split_content=split_content,
                        )
                        sub_result = await asyncio.wait_for(parse_coro, timeout=job_timeout)
                    results[job["index"]] = {"result": sub_result, "error": None}
                except asyncio.TimeoutError:
                    results[job["index"]] = {
                        "result": None,
                        "error": TimeoutError(
                            f"Understanding job timed out after {job_timeout:.1f}s"
                        ),
                    }
                except Exception as exc:
                    results[job["index"]] = {"result": None, "error": exc}
                finally:
                    queue.task_done()

        worker_count = min(max_concurrent, len(jobs))
        workers = [asyncio.create_task(_worker()) for _ in range(worker_count)]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        return results

    @staticmethod
    async def _parse_file_with_parser(
        classified_file: "ClassifiedFile",
        parser: Union[BaseParser, "ParserRouter"],
        *,
        preserve_structure: bool,
        import_root: Optional[str],
        split_content: bool,
    ) -> ParseResult:
        """Run one parser without mutating the directory destination tree."""
        return await parser.parse(
            str(classified_file.path),
            enable_link_rewrite=preserve_structure,
            link_rewrite_root=import_root,
            allowed_media_dirs=[Path(import_root)] if import_root else None,
            split_content=split_content,
            flatten_single_output=bool(not split_content and preserve_structure),
        )

    @staticmethod
    async def _merge_parser_result(
        classified_file: "ClassifiedFile",
        sub_result: ParseResult,
        target_uri: str,
        viking_fs: Any,
        *,
        preserve_structure: bool,
        split_content: bool,
    ) -> None:
        """Merge one completed parser result in source-file order."""
        no_content_error = "Parse failed: no content generated"
        if sub_result.warnings:
            no_content_error += "; " + "; ".join(sub_result.warnings)
        if not sub_result.temp_dir_path:
            raise ValueError(no_content_error)

        if preserve_structure:
            parent = str(PurePosixPath(classified_file.rel_path).parent)
            dest = f"{target_uri}/{parent}" if parent != "." else target_uri
        else:
            dest = target_uri
        merged = await DirectoryParser._merge_temp(
            viking_fs,
            sub_result.temp_dir_path,
            dest,
            flatten_single_output=bool(not split_content and preserve_structure),
        )
        if not merged:
            raise ValueError(no_content_error)

    @staticmethod
    async def _process_single_file(
        classified_file: "ClassifiedFile",
        parser: Optional[Union[BaseParser, "ParserRouter"]],
        target_uri: str,
        viking_fs: Any,
        warnings: List[str],
        preserve_structure: bool = True,
        import_root: Optional[str] = None,
        split_content: bool = True,
    ) -> Dict[str, Any]:
        """Process one file into the VikingFS directory temp.

        - Files WITH a parser → ``parser.parse()`` → merge output into
          *target_uri* at the correct relative location.
        - Files WITHOUT a parser → read and write directly to VikingFS.

        Args:
            preserve_structure: When True, files keep their relative directory
                hierarchy.  When False, all files are placed directly under
                *target_uri* (flat).

        Returns:
            Per-file status detail including the parser error when processing fails.
        """
        rel_path = classified_file.rel_path
        src_file = classified_file.path

        if parser:
            sub_result: Optional[ParseResult] = None
            try:
                sub_result = await DirectoryParser._parse_file_with_parser(
                    classified_file,
                    parser,
                    preserve_structure=preserve_structure,
                    import_root=import_root,
                    split_content=split_content,
                )
                await DirectoryParser._merge_parser_result(
                    classified_file,
                    sub_result,
                    target_uri,
                    viking_fs,
                    preserve_structure=preserve_structure,
                    split_content=split_content,
                )
                return {
                    "ok": True,
                    "meta": getattr(sub_result, "meta", {}) or {},
                    "error": None,
                }
            except Exception as exc:
                warnings.append(f"Failed to parse {rel_path}: {exc}")
                meta = getattr(sub_result, "meta", {}) if sub_result is not None else {}
                return {
                    "ok": False,
                    "meta": meta if isinstance(meta, dict) else {},
                    "error": str(exc),
                }
        else:
            try:
                content = src_file.read_bytes()
                if preserve_structure:
                    dst_uri = f"{target_uri}/{rel_path}"
                else:
                    dst_uri = f"{target_uri}/{PurePosixPath(rel_path).name}"
                await viking_fs.write_file(dst_uri, content)
                return {"ok": True, "meta": {}, "error": None}
            except Exception as exc:
                warnings.append(f"Failed to upload {rel_path}: {exc}")
                return {"ok": False, "meta": {}, "error": str(exc)}

    @staticmethod
    async def _upload_file_directly(
        classified_file: "ClassifiedFile",
        target_uri: str,
        viking_fs: Any,
        warnings: List[str],
        preserve_structure: bool = True,
    ) -> Dict[str, Any]:
        """Directly upload a file without using its parser.

        Used for media files when directly_upload_media=True.

        Args:
            preserve_structure: When True, files keep their relative directory
                hierarchy.  When False, all files are placed directly under
                *target_uri* (flat).

        Returns:
            Per-file status detail including the upload error when processing fails.
        """
        rel_path = classified_file.rel_path
        src_file = classified_file.path

        try:
            content = src_file.read_bytes()
            if preserve_structure:
                dst_uri = f"{target_uri}/{rel_path}"
            else:
                dst_uri = f"{target_uri}/{PurePosixPath(rel_path).name}"
            await viking_fs.write_file(dst_uri, content)
            return {"ok": True, "meta": {}, "error": None}
        except Exception as exc:
            warnings.append(f"Failed to upload {rel_path}: {exc}")
            return {"ok": False, "meta": {}, "error": str(exc)}

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
        *,
        flatten_single_output: bool = False,
    ) -> bool:
        """Move all content from a parser's temp directory into *dest_uri*.

        After the move the source temp is deleted. Hidden files stay filtered,
        except the sidecars in :data:`_MERGE_SIDECAR_ALLOWLIST` that downstream
        steps depend on (e.g. ``.image_mappings.json`` for the post-commit
        image rewrite). In no-split directory imports, a wrapper containing one
        standalone file is promoted into ``dest_uri``; wrappers with additional
        files, directories, sidecars, or destination-name conflicts are retained.
        Returns False for a tree with no visible files, without creating any
        destination directories. Sidecars alone do not count as content.
        """
        entries = await viking_fs.ls(src_temp_uri, show_all_hidden=True, node_limit=LS_ALL_NODES)

        async def has_content(parent_uri: str, children: List[Dict[str, Any]]) -> bool:
            for entry in children:
                name = entry.get("name", "")
                if not name or name in {".", ".."}:
                    continue
                if DirectoryParser._is_dir_entry(entry):
                    child_uri = entry.get("uri", f"{parent_uri.rstrip('/')}/{name}")
                    child_entries = await viking_fs.ls(
                        child_uri, show_all_hidden=True, node_limit=LS_ALL_NODES
                    )
                    if await has_content(child_uri, child_entries):
                        return True
                elif not name.startswith("."):
                    return True
            return False

        if not await has_content(src_temp_uri, entries):
            try:
                await viking_fs.delete_temp(src_temp_uri)
            except Exception:
                pass
            return False

        merge_entries = [
            entry
            for entry in entries
            if entry.get("name") not in ("", ".", "..")
            and (
                DirectoryParser._is_dir_entry(entry)
                or not entry.get("name", "").startswith(".")
                or entry.get("name") in _MERGE_SIDECAR_ALLOWLIST
            )
        ]
        if flatten_single_output and len(merge_entries) == 1:
            wrapper = merge_entries[0]
            if DirectoryParser._is_dir_entry(wrapper):
                wrapper_uri = wrapper.get(
                    "uri",
                    f"{src_temp_uri.rstrip('/')}/{wrapper['name']}",
                )
                wrapper_entries = await viking_fs.ls(
                    wrapper_uri,
                    show_all_hidden=True,
                    node_limit=LS_ALL_NODES,
                )
                payloads = [
                    entry
                    for entry in wrapper_entries
                    if entry.get("name") not in ("", ".", "..")
                    and (
                        not entry.get("name", "").startswith(".")
                        or entry.get("name") in _MERGE_SIDECAR_ALLOWLIST
                    )
                ]
                if len(payloads) == 1 and not DirectoryParser._is_dir_entry(payloads[0]):
                    payload = payloads[0]
                    await viking_fs.mkdir(dest_uri, exist_ok=True)
                    destination_entries = await viking_fs.ls(
                        dest_uri,
                        show_all_hidden=True,
                        node_limit=LS_ALL_NODES,
                    )
                    destination_names = {
                        entry.get("name")
                        for entry in destination_entries
                        if entry.get("name") not in ("", ".", "..")
                    }
                    if payload.get("name") not in destination_names:
                        src = payload.get(
                            "uri",
                            f"{wrapper_uri.rstrip('/')}/{payload['name']}",
                        )
                        await viking_fs.move_file(
                            src,
                            f"{dest_uri.rstrip('/')}/{payload['name']}",
                        )
                        try:
                            await viking_fs.delete_temp(src_temp_uri)
                        except Exception:
                            pass
                        return True
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in (".", ".."):
                continue
            if (
                not DirectoryParser._is_dir_entry(entry)
                and name.startswith(".")
                and name not in _MERGE_SIDECAR_ALLOWLIST
            ):
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
        return True

    @staticmethod
    async def _is_git_repository(source_path: Path) -> bool:
        """Check if the directory contains a git repository (or has our .git_source_repo marker)."""
        try:
            git_dir = source_path / ".git"
            marker_file = source_path / ".git_source_repo"
            return (git_dir.exists() and git_dir.is_dir()) or marker_file.exists()
        except (OSError, PermissionError):
            return False

    @staticmethod
    async def _add_git_metadata(source_path: Path, kwargs: dict) -> None:
        """Add git metadata (branch, commit) from .git directory if available."""
        try:
            from openviking.parse.accessors.git_accessor import GitAccessor

            git_dir = source_path / ".git"
            if not git_dir.exists():
                return  # No .git directory, skip (we already have meta from accessor)

            git_accessor = GitAccessor()

            # Get branch
            try:
                branch = await git_accessor._run_git(
                    ["git", "-C", str(source_path), "rev-parse", "--abbrev-ref", "HEAD"]
                )
                kwargs["repo_ref"] = branch
            except Exception as e:
                logger.debug(f"Failed to get git branch: {e}")

            # Get commit
            try:
                commit = await git_accessor._run_git(
                    ["git", "-C", str(source_path), "rev-parse", "HEAD"]
                )
                kwargs["repo_commit"] = commit
            except Exception as e:
                logger.debug(f"Failed to get git commit: {e}")

            # repo_name and original_source are already set from accessor, no need to get from git

        except Exception as e:
            logger.debug(f"Failed to get git metadata: {e}")

    @staticmethod
    async def _recursive_move(
        viking_fs: Any,
        src_uri: str,
        dst_uri: str,
    ) -> None:
        """Recursively move a VikingFS directory tree (hidden files filtered,
        allowlisted sidecars carried)."""
        await viking_fs.mkdir(dst_uri, exist_ok=True)
        entries = await viking_fs.ls(src_uri, show_all_hidden=True, node_limit=LS_ALL_NODES)
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in (".", ".."):
                continue
            if (
                not DirectoryParser._is_dir_entry(entry)
                and name.startswith(".")
                and name not in _MERGE_SIDECAR_ALLOWLIST
            ):
                continue
            s = f"{src_uri.rstrip('/')}/{name}"
            d = f"{dst_uri.rstrip('/')}/{name}"
            if DirectoryParser._is_dir_entry(entry):
                await DirectoryParser._recursive_move(viking_fs, s, d)
            else:
                await viking_fs.move_file(s, d)
