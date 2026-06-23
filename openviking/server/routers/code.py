# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Code navigation endpoints for OpenViking HTTP Server."""

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from openviking.parse.parsers.code.ast.code_tools import (
    CODE_SEARCH_CONCURRENCY,
    expand_symbol,
    filter_code_uris,
    outline_file,
    search_symbols,
)
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/code", tags=["code"])

_ERROR_NOT_VIKING = (
    "Error: only viking:// URIs are supported; "
    "use add_resource to ingest local code as a viking:// resource first."
)


class CodeOutlineRequest(BaseModel):
    uri: str


class CodeSearchRequest(BaseModel):
    uri: str
    query: str


class CodeExpandRequest(BaseModel):
    uri: str
    symbol: str


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
        return Response(status="ok", result=f"Error: {request.uri} is not text").model_dump(
            exclude_none=True
        )
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
    entries = await service.fs.ls(request.uri, ctx=_ctx, recursive=True, output="original")
    code_uris, capped = filter_code_uris(entries or [])
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
    result = search_symbols(request.query, files)
    if failed_reads:
        result += f"\n\n(warning: skipped {failed_reads} unreadable source file(s))"
    if capped:
        result += "\n\n(scanning stopped at 200-file cap; narrow uri to search more)"
    return Response(status="ok", result=result).model_dump(exclude_none=True)


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
        return Response(status="ok", result=f"Error: {request.uri} is not text").model_dump(
            exclude_none=True
        )
    return Response(
        status="ok", result=expand_symbol(content, request.uri, request.symbol)
    ).model_dump(exclude_none=True)
