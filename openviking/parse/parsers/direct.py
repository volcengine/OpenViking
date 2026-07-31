# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Stage accessed resources without parsing or splitting their content."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from zipfile import ZipFile

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.base import NodeType, ParseResult, ResourceNode, create_parse_result
from openviking.parse.directory_scan import ClassifiedFile, scan_directory
from openviking.parse.registry import ParserRegistry, get_registry
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils.zip_safe import safe_extract_zip
from openviking_cli.exceptions import InvalidArgumentError

if TYPE_CHECKING:
    from openviking.storage.viking_fs import VikingFS


_SKIPPED_STATUS = {
    "dot directory": "ignore",
    "dot file": "ignore",
    "symlink": "ignore",
    "empty file": "ignore",
    "os error": "ignore",
    "IGNORE_DIRS": "ignore",
    "ignore_dirs": "ignore",
    "gitignore": "ignore",
    "excluded by include filter": "exclude",
    "excluded by exclude filter": "exclude",
}


class DirectResourceStager:
    """Write an accessed file or directory into a parser-compatible temp tree."""

    def __init__(
        self,
        viking_fs: Optional["VikingFS"] = None,
        registry: Optional[ParserRegistry] = None,
    ) -> None:
        self._viking_fs = viking_fs
        self._registry = registry

    @property
    def viking_fs(self) -> "VikingFS":
        return self._viking_fs or get_viking_fs()

    @property
    def registry(self) -> ParserRegistry:
        return self._registry or get_registry()

    async def stage(self, resource: LocalResource, **kwargs: Any) -> ParseResult:
        """Stage a local resource without invoking a content parser."""
        if kwargs.get("preserve_structure") is False:
            raise InvalidArgumentError(
                "preserve_structure=false is not supported when parse_mode='no_parse'."
            )

        if resource.path.is_dir():
            return await self._stage_directory(
                resource.path,
                source_path_for_result=resource.original_source or str(resource.path),
                source_format=(
                    "repository" if resource.source_type == SourceType.GIT else "directory"
                ),
                source_meta=resource.meta,
                **kwargs,
            )
        if self._is_directory_transport_zip(resource.path, kwargs.get("source_name")):
            return await self._stage_transport_zip(resource, **kwargs)
        return await self._stage_file(resource, **kwargs)

    async def _stage_file(self, resource: LocalResource, **kwargs: Any) -> ParseResult:
        start_time = time.time()
        source_path = resource.path
        resource_name = self._resource_name(source_path, kwargs)
        file_name = self._original_file_name(resource, kwargs)
        temp_uri = self.viking_fs.create_temp_uri()
        target_uri = f"{temp_uri}/{resource_name}"

        try:
            await self.viking_fs.mkdir(temp_uri, exist_ok=True)
            await self.viking_fs.mkdir(target_uri, exist_ok=True)
            await self.viking_fs.write_file(
                f"{target_uri}/{file_name}",
                await asyncio.to_thread(source_path.read_bytes),
            )
        except Exception:
            await self._delete_temp(temp_uri)
            raise

        meta = dict(resource.meta)
        meta.update(
            {
                "file_count": 1,
                "dir_name": resource_name,
                "processed_files": [{"path": file_name, "parser": "direct"}],
                "failed_files": [],
                "skipped_files": [],
            }
        )
        result = self._result(
            resource_name=resource_name,
            source_path=resource.original_source or str(source_path),
            source_format=source_path.suffix.lower().lstrip(".") or "file",
            parse_time=time.time() - start_time,
            meta=meta,
            warnings=[],
        )
        result.temp_dir_path = temp_uri
        return result

    async def _stage_directory(
        self,
        source_path: Path,
        *,
        source_path_for_result: Optional[str] = None,
        source_format: str = "directory",
        source_meta: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> ParseResult:
        start_time = time.time()
        resource_name = self._resource_name(source_path, kwargs)
        scan_result = scan_directory(
            root=source_path,
            registry=self.registry,
            strict=False,
            allow_unsupported=True,
            ignore_dirs=kwargs.get("ignore_dirs"),
            include=kwargs.get("include"),
            exclude=kwargs.get("exclude"),
        )
        candidates = sorted(
            [*scan_result.processable, *scan_result.unsupported],
            key=lambda item: item.rel_path,
        )
        temp_uri = self.viking_fs.create_temp_uri()
        target_uri = f"{temp_uri}/{resource_name}"
        warnings: list[str] = []
        processed_files: list[dict[str, str]] = []
        failed_files: list[dict[str, str]] = []

        try:
            await self.viking_fs.mkdir(temp_uri, exist_ok=True)
            await self.viking_fs.mkdir(target_uri, exist_ok=True)
            for candidate in candidates:
                if await self._copy_file(candidate, target_uri, warnings):
                    processed_files.append({"path": candidate.rel_path, "parser": "direct"})
                else:
                    failed_files.append({"path": candidate.rel_path, "parser": "direct"})
            if failed_files and kwargs.get("strict", False):
                failed = ", ".join(item["path"] for item in failed_files)
                raise InvalidArgumentError(f"Failed to stage directory file(s): {failed}")
        except Exception:
            await self._delete_temp(temp_uri)
            raise

        meta = dict(source_meta or {})
        meta.update(
            {
                "file_count": len(processed_files),
                "dir_name": resource_name,
                "total_processable": len(candidates),
                "processed_files": processed_files,
                "failed_files": failed_files,
                "unsupported_files": [],
                "skipped_files": self._parse_skipped(scan_result.skipped),
            }
        )
        result = self._result(
            resource_name=resource_name,
            source_path=source_path_for_result or str(source_path),
            source_format=source_format,
            parse_time=time.time() - start_time,
            meta=meta,
            warnings=warnings,
        )
        result.temp_dir_path = temp_uri
        return result

    async def _stage_transport_zip(
        self,
        resource: LocalResource,
        **kwargs: Any,
    ) -> ParseResult:
        extract_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="ov_no_parse_zip_"))
        try:
            await asyncio.to_thread(self._extract_zip, resource.path, extract_dir)
            source_dir = await asyncio.to_thread(
                self._select_extracted_root,
                extract_dir,
                kwargs.get("source_name"),
            )
            return await self._stage_directory(
                source_dir,
                source_path_for_result=resource.original_source or str(resource.path),
                source_meta=resource.meta,
                **kwargs,
            )
        finally:
            await asyncio.to_thread(shutil.rmtree, extract_dir, True)

    async def _copy_file(
        self,
        candidate: ClassifiedFile,
        target_uri: str,
        warnings: list[str],
    ) -> bool:
        try:
            content = await asyncio.to_thread(candidate.path.read_bytes)
            await self.viking_fs.write_file(f"{target_uri}/{candidate.rel_path}", content)
            return True
        except Exception as exc:
            warnings.append(f"Failed to upload {candidate.rel_path}: {exc}")
            return False

    async def _delete_temp(self, temp_uri: str) -> None:
        try:
            await self.viking_fs.delete_temp(temp_uri)
        except Exception:
            pass

    @staticmethod
    def _extract_zip(archive_path: Path, extract_dir: Path) -> None:
        with ZipFile(archive_path, "r") as archive:
            safe_extract_zip(archive, extract_dir)

    @staticmethod
    def _select_extracted_root(extract_dir: Path, source_name: Optional[str]) -> Path:
        entries = [
            path
            for path in extract_dir.iterdir()
            if path.name not in {".DS_Store", "__MACOSX"} and not path.name.startswith("._")
        ]
        if len(entries) != 1 or not entries[0].is_dir():
            return extract_dir
        source_leaf = Path(source_name).name if source_name else ""
        if not source_leaf or source_leaf in {entries[0].name, f"{entries[0].name}.zip"}:
            return entries[0]
        return extract_dir

    @staticmethod
    def _is_directory_transport_zip(path: Path, source_name: Optional[str]) -> bool:
        if path.suffix.lower() != ".zip" or not source_name:
            return False
        return Path(source_name).suffix.lower() != ".zip"

    @staticmethod
    def _resource_name(source_path: Path, kwargs: dict[str, Any]) -> str:
        explicit = kwargs.get("resource_name") or kwargs.get("source_name")
        if explicit:
            name = Path(str(explicit)).name
        else:
            name = source_path.name if source_path.is_dir() else source_path.stem
        return name or "resource"

    @staticmethod
    def _original_file_name(resource: LocalResource, kwargs: dict[str, Any]) -> str:
        name = (
            kwargs.get("source_name")
            or resource.meta.get("original_filename")
            or resource.path.name
        )
        return Path(str(name)).name or resource.path.name

    @staticmethod
    def _parse_skipped(skipped: list[str]) -> list[dict[str, str]]:
        parsed: list[dict[str, str]] = []
        for entry in skipped:
            marker = entry.rfind(" (")
            if marker != -1 and entry.endswith(")"):
                path = entry[:marker]
                reason = entry[marker + 2 : -1]
            else:
                path = entry
                reason = "skip"
            parsed.append({"path": path, "status": _SKIPPED_STATUS.get(reason, "skip")})
        return parsed

    @staticmethod
    def _result(
        *,
        resource_name: str,
        source_path: str,
        source_format: str,
        parse_time: float,
        meta: dict[str, Any],
        warnings: list[str],
    ) -> ParseResult:
        root = ResourceNode(
            type=NodeType.ROOT,
            title=resource_name,
            meta={"file_count": meta.get("file_count", 0), "type": source_format},
        )
        return create_parse_result(
            root=root,
            source_path=source_path,
            source_format=source_format,
            parser_name="DirectResourceStager",
            parse_time=parse_time,
            meta=meta,
            warnings=warnings,
        )
