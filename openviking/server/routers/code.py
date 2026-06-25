# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Code navigation endpoints for OpenViking HTTP Server."""

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openviking.parse.parsers.code.ast.code_tools import (
    CODE_LOCATE_FILE_CAP,
    CODE_SCAN_LS_LEVEL_LIMIT,
    CODE_SCAN_LS_NODE_LIMIT,
    CODE_SEARCH_CONCURRENCY,
    CODE_SEARCH_FILE_CAP,
    CodeLocateFile,
    CodeLocateResult,
    expand_symbol,
    format_locate_text,
    locate_code_structured,
    outline_file,
    search_code,
    select_code_paths,
    select_code_uris,
)
from openviking.parse.parsers.code.ast.extractor import get_extractor
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_server_config, get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/code", tags=["code"])

_ERROR_NOT_VIKING = (
    "Error: only viking:// URIs are supported; "
    "use add_resource to ingest local code as a viking:// resource first."
)
_ERROR_LOCAL_SOURCE_DISABLED = (
    "Error: local code source paths are disabled; "
    "set server.allow_local_code_source_paths=true to enable."
)


class CodeOutlineRequest(BaseModel):
    uri: str


class CodeSearchRequest(BaseModel):
    uri: str
    query: str


class CodeLocateSource(BaseModel):
    type: Literal["local", "viking"]
    path: str | None = None
    uri: str | None = None


class CodeLocateRequest(BaseModel):
    source: CodeLocateSource
    query: str
    failing_tests: list[str] = []
    output_format: Literal["text", "json", "both"] = "text"
    debug: bool = False
    max_edit: int = 5
    max_references: int = 3


class CodeExpandRequest(BaseModel):
    uri: str
    symbol: str


_LOCAL_SKIP_DIRS = {
    ".git",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
}


def _format_locate_response(result: CodeLocateResult, output_format: str):
    if output_format == "text":
        return format_locate_text(result)
    if output_format == "json":
        return result.to_dict()
    payload = result.to_dict()
    payload["summary_text"] = format_locate_text(result)
    return payload


def _allow_local_code_source_paths() -> bool:
    config = get_server_config()
    return bool(getattr(config, "allow_local_code_source_paths", False))


def _error_locate_result(request: CodeLocateRequest, code: str, message: str) -> CodeLocateResult:
    return CodeLocateResult(
        schema_version="code-locate/v1",
        source={"type": request.source.type, "root": request.source.path or request.source.uri or ""},
        query={"text": request.query, "failing_tests": request.failing_tests},
        edit_candidates=[],
        behavior_references=[],
        verification=[],
        warnings=[{"code": code, "message": message}],
        summary_text=message,
    )


def _local_source_root(path: Path) -> Path:
    return path if path.is_dir() else path.parent


def _select_local_code_files(root: Path, query: str) -> tuple[list[Path], bool, list[str]]:
    extractor = get_extractor()
    paths: list[Path] = []
    skipped_dirs: set[str] = set()
    if root.is_file():
        return ([root] if extractor.supports(str(root)) else []), False, []

    for path in root.rglob("*"):
        if any(part in _LOCAL_SKIP_DIRS for part in path.parts):
            skipped_dirs.update(part for part in path.parts if part in _LOCAL_SKIP_DIRS)
            continue
        if not path.is_file():
            continue
        if extractor.supports(str(path)):
            paths.append(path)

    paths, capped = select_code_paths(paths, query, cap=CODE_LOCATE_FILE_CAP)
    return paths, capped, sorted(skipped_dirs)


def _read_local_code_source(path_value: str, query: str) -> tuple[list[CodeLocateFile], bool, list[dict], dict]:
    path = Path(path_value).expanduser().resolve()
    if not path.exists():
        return (
            [],
            False,
            [{"code": "path_not_found", "message": f"Local source path not found: {path}"}],
            {"source_type": "local", "root": str(path), "candidate_files": 0, "scanned_files": 0},
        )
    if not path.is_file() and not path.is_dir():
        return (
            [],
            False,
            [
                {
                    "code": "path_not_file_or_directory",
                    "message": f"Local source path is not a file or directory: {path}",
                }
            ],
            {"source_type": "local", "root": str(path), "candidate_files": 0, "scanned_files": 0},
        )

    root = _local_source_root(path)
    code_paths, capped, skipped_dirs = _select_local_code_files(path, query)
    if not code_paths:
        return (
            [],
            capped,
            [{"code": "no_supported_source_files", "message": f"No supported source files found under {path}"}],
            {
                "source_type": "local",
                "root": str(root),
                "candidate_files": 0,
                "scanned_files": 0,
                "skipped_dirs": skipped_dirs,
                "capped": capped,
            },
        )

    files: list[CodeLocateFile] = []
    failed_reads = 0
    for code_path in code_paths:
        try:
            content = code_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = code_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                failed_reads += 1
                continue
        except OSError:
            failed_reads += 1
            continue
        files.append(
            CodeLocateFile(
                content=content,
                file_name=str(code_path),
                location_type="local",
                relative_path=code_path.relative_to(root).as_posix(),
            )
        )
    warnings = []
    if capped:
        warnings.append(
            {
                "code": "scan_capped",
                "message": (
                    f"Scanning stopped at {CODE_LOCATE_FILE_CAP}-file cap; "
                    "narrow source path to search more."
                ),
            }
        )
    if failed_reads:
        warnings.append(
            {
                "code": "skipped_unreadable_files",
                "message": f"Skipped {failed_reads} unreadable source file(s).",
            }
        )
    scan = {
        "source_type": "local",
        "root": str(root),
        "candidate_files": len(code_paths),
        "scanned_files": len(code_paths),
        "read_files": len(files),
        "failed_reads": failed_reads,
        "skipped_dirs": skipped_dirs,
        "capped": capped,
    }
    return files, capped, warnings, scan


@router.post("/outline")
async def code_outline_endpoint(
    request: CodeOutlineRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    if not request.uri.startswith("viking://"):
        return Response(status="ok", result=_ERROR_NOT_VIKING).model_dump(exclude_none=True)
    service = get_service()
    content = await service.fs.read(request.uri, ctx=_ctx)
    if not isinstance(content, str):
        return Response(
            status="ok", result=f"Error: {request.uri} is not text"
        ).model_dump(exclude_none=True)
    return Response(status="ok", result=outline_file(content, request.uri)).model_dump(
        exclude_none=True
    )


@router.post("/search")
async def code_search_endpoint(
    request: CodeSearchRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    if not request.uri.startswith("viking://"):
        return Response(status="ok", result=_ERROR_NOT_VIKING).model_dump(exclude_none=True)
    if not request.query:
        return Response(status="ok", result="Error: empty query").model_dump(exclude_none=True)
    service = get_service()
    entries = await service.fs.ls(
        request.uri,
        ctx=_ctx,
        recursive=True,
        output="original",
        node_limit=CODE_SCAN_LS_NODE_LIMIT,
        level_limit=CODE_SCAN_LS_LEVEL_LIMIT,
    )
    code_uris, capped = select_code_uris(entries or [], request.query)
    if not code_uris:
        return Response(
            status="ok",
            result=f"No supported source files found under {request.uri}",
        ).model_dump(exclude_none=True)

    semaphore = asyncio.Semaphore(CODE_SEARCH_CONCURRENCY)

    async def _read_one(uri: str):
        async with semaphore:
            try:
                body = await service.fs.read(uri, ctx=_ctx)
            except Exception as exc:
                logger.warning("code_search: read failed for %s: %s", uri, exc)
                return None, uri
            return ((body, uri) if isinstance(body, str) else None), uri

    fetched = await asyncio.gather(*[_read_one(u) for u in code_uris])
    files = [pair for pair, _uri in fetched if pair is not None]
    failed_reads = len(fetched) - len(files)
    if failed_reads == len(code_uris):
        return Response(
            status="ok",
            result=f"Error: failed to read all {len(code_uris)} source files under {request.uri}",
        ).model_dump(exclude_none=True)
    result = search_code(request.query, files)
    if failed_reads:
        result += f"\n\n(warning: skipped {failed_reads} unreadable source file(s))"
    if capped:
        result += f"\n\n(scanning stopped at {CODE_SEARCH_FILE_CAP}-file cap; narrow uri to search more)"
    return Response(status="ok", result=result).model_dump(exclude_none=True)


@router.post("/locate")
async def code_locate_endpoint(
    request: CodeLocateRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    if not request.query:
        result = _error_locate_result(request, "empty_query", "Error: empty query")
        return Response(
            status="ok",
            result=_format_locate_response(result, request.output_format),
        ).model_dump(exclude_none=True)

    warnings: list[dict] = []
    scan_debug: dict | None = None
    source_root = ""
    if request.source.type == "local":
        if not request.source.path or request.source.uri:
            result = _error_locate_result(
                request,
                "invalid_source",
                "local source requires path and must not include uri",
            )
            return Response(
                status="ok",
                result=_format_locate_response(result, request.output_format),
            ).model_dump(exclude_none=True)
        if not _allow_local_code_source_paths():
            result = _error_locate_result(
                request,
                "local_source_disabled",
                _ERROR_LOCAL_SOURCE_DISABLED,
            )
            return Response(
                status="ok",
                result=_format_locate_response(result, request.output_format),
            ).model_dump(exclude_none=True)
        files, _capped, warnings, scan_debug = _read_local_code_source(
            request.source.path, request.query
        )
        source_root = scan_debug.get("root", request.source.path)
    else:
        if not request.source.uri or request.source.path:
            result = _error_locate_result(
                request,
                "invalid_source",
                "viking source requires uri and must not include path",
            )
            return Response(
                status="ok",
                result=_format_locate_response(result, request.output_format),
            ).model_dump(exclude_none=True)
        if not request.source.uri.startswith("viking://"):
            result = _error_locate_result(request, "invalid_source", _ERROR_NOT_VIKING)
            return Response(
                status="ok",
                result=_format_locate_response(result, request.output_format),
            ).model_dump(exclude_none=True)

        service = get_service()
        entries = await service.fs.ls(
            request.source.uri,
            ctx=_ctx,
            recursive=True,
            output="original",
            node_limit=CODE_SCAN_LS_NODE_LIMIT,
            level_limit=CODE_SCAN_LS_LEVEL_LIMIT,
        )
        code_uris, capped = select_code_uris(
            entries or [], request.query, cap=CODE_LOCATE_FILE_CAP
        )
        if not code_uris:
            result = _error_locate_result(
                request,
                "no_supported_source_files",
                f"No supported source files found under {request.source.uri}",
            )
            return Response(
                status="ok",
                result=_format_locate_response(result, request.output_format),
            ).model_dump(exclude_none=True)

        semaphore = asyncio.Semaphore(CODE_SEARCH_CONCURRENCY)

        async def _read_one(uri: str):
            async with semaphore:
                try:
                    body = await service.fs.read(uri, ctx=_ctx)
                except Exception as exc:
                    logger.warning("code_locate: read failed for %s: %s", uri, exc)
                    return None, uri
                return (
                    (
                        CodeLocateFile(
                            content=body,
                            file_name=uri,
                            location_type="viking",
                            relative_path=uri.removeprefix(request.source.uri.rstrip("/") + "/"),
                        ),
                        uri,
                    )
                    if isinstance(body, str)
                    else (None, uri)
                )

        fetched = await asyncio.gather(*[_read_one(u) for u in code_uris])
        files = [pair for pair, _uri in fetched if pair is not None]
        failed_reads = len(fetched) - len(files)
        if failed_reads == len(code_uris):
            result = _error_locate_result(
                request,
                "skipped_unreadable_files",
                f"Error: failed to read all {len(code_uris)} source files under {request.source.uri}",
            )
            return Response(
                status="ok",
                result=_format_locate_response(result, request.output_format),
            ).model_dump(exclude_none=True)
        if failed_reads:
            warnings.append(
                {
                    "code": "skipped_unreadable_files",
                    "message": f"Skipped {failed_reads} unreadable source file(s).",
                }
            )
        if capped:
            warnings.append(
                {
                    "code": "scan_capped",
                    "message": (
                        f"Scanning stopped at {CODE_LOCATE_FILE_CAP}-file cap; "
                        "narrow source path to search more."
                    ),
                }
            )
        source_root = request.source.uri
        scan_debug = {
            "source_type": "viking",
            "root": request.source.uri,
            "candidate_files": len(code_uris),
            "scanned_files": len(code_uris),
            "read_files": len(files),
            "failed_reads": failed_reads,
            "capped": capped,
        }

    result = locate_code_structured(
        request.query,
        files,
        request.failing_tests,
        max_edit=request.max_edit,
        max_references=request.max_references,
        debug=request.debug,
        source_root=source_root,
    )
    result.warnings.extend(warnings)
    if request.debug:
        result.debug = result.debug or {}
        result.debug["scan"] = scan_debug
    return Response(
        status="ok",
        result=_format_locate_response(result, request.output_format),
    ).model_dump(exclude_none=True)


@router.post("/expand")
async def code_expand_endpoint(
    request: CodeExpandRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    if not request.uri.startswith("viking://"):
        return Response(status="ok", result=_ERROR_NOT_VIKING).model_dump(exclude_none=True)
    if not request.symbol:
        return Response(status="ok", result="Error: empty symbol").model_dump(exclude_none=True)
    service = get_service()
    content = await service.fs.read(request.uri, ctx=_ctx)
    if not isinstance(content, str):
        return Response(
            status="ok", result=f"Error: {request.uri} is not text"
        ).model_dump(exclude_none=True)
    return Response(
        status="ok", result=expand_symbol(content, request.uri, request.symbol)
    ).model_dump(exclude_none=True)
