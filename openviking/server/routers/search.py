# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Search endpoints for OpenViking HTTP Server."""

import asyncio
import math
from typing import Any, Dict, List, Literal, Optional, Sequence, Union

from fastapi import APIRouter, Depends
from fastapi import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.pyagfs.exceptions import AGFSClientError, AGFSNotFoundError
from openviking.retrieve.context_assembler import (
    CATEGORY_KEYS,
    DEFAULT_LIMIT,
    DEFAULT_MAX_TOKENS,
    MAX_EXCLUDE_URIS,
    REPORTED_CATEGORY_KEYS,
    AssembleParams,
    DetailRequest,
    assemble_context,
)
from openviking.retrieve.context_assembler.recall_preset import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MIN_SCORE,
    deprecation_stats,
    fold_recall_request,
)
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.error_mapping import map_exception
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.server.telemetry import run_operation
from openviking.telemetry import TelemetryRequest
from openviking.utils.image_search import is_viking_uri
from openviking.utils.search_filters import (
    SearchContextTypeInput,
    _resolve_levels,
    merge_search_filter,
)
from openviking.utils.tags import build_search_tags_filter
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError


def _sanitize_floats(obj: Any) -> Any:
    """Recursively replace inf/nan with 0.0 to ensure JSON compliance."""
    if isinstance(obj, float):
        if math.isinf(obj) or math.isnan(obj):
            return 0.0
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


router = APIRouter(prefix="/api/v1/search", tags=["search"])
TimeField = Literal["updated_at", "created_at"]


def _resolve_search_limit(limit: int, node_limit: Optional[int]) -> int:
    return node_limit if node_limit is not None else limit


def _resolve_search_filter(
    request_filter: Optional[Dict[str, Any]],
    context_type: Optional[SearchContextTypeInput],
    since: Optional[str],
    until: Optional[str],
    time_field: Optional[TimeField],
    tags: Optional[List[str]],
) -> Optional[Dict[str, Any]]:
    try:
        merged = merge_search_filter(
            request_filter,
            context_type=context_type,
            since=since,
            until=until,
            time_field=time_field,
        )
        tag_filter = build_search_tags_filter(tags)
        if not tag_filter:
            return merged
        if merged:
            if tag_filter.get("op") == "and" and isinstance(tag_filter.get("conds"), list):
                return {"op": "and", "conds": [merged, *tag_filter["conds"]]}
            return {"op": "and", "conds": [merged, tag_filter]}
        return tag_filter
    except ValueError as exc:
        raise InvalidArgumentError(str(exc)) from exc


def _resolve_uri_or_uris(
    uri: Union[str, List[str]], ctx: RequestContext
) -> Union[str, List[str]]:
    """Resolve path variables in a single URI or list of URIs."""
    if isinstance(uri, list):
        return [validate_request_viking_uri(resolve_path_variables(u), ctx) for u in uri]
    if not uri:
        return ""
    return validate_request_viking_uri(resolve_path_variables(uri), ctx)


def _resolve_uri_list(uris: Sequence[str], ctx: RequestContext) -> list[str]:
    return [validate_request_viking_uri(resolve_path_variables(uri), ctx) for uri in uris]


def _resolve_image_url(image_url: Optional[str], ctx: RequestContext) -> Optional[str]:
    if not image_url:
        return image_url
    resolved = resolve_path_variables(image_url)
    if is_viking_uri(resolved):
        return validate_request_viking_uri(resolved, ctx, field_name="image_url")
    return resolved


class FindRequest(BaseModel):
    """Request model for find."""

    model_config = ConfigDict(extra="forbid")

    query: str = ""
    image_url: Optional[str] = None
    target_uri: Union[str, List[str]] = ""
    context_type: Optional[Union[str, List[str]]] = None
    limit: int = DEFAULT_LIMIT
    node_limit: Optional[int] = None
    score_threshold: Optional[float] = None
    filter: Optional[Dict[str, Any]] = None
    include_provenance: bool = False
    tags: Optional[List[str]] = None
    since: Optional[str] = None
    until: Optional[str] = None
    time_field: Optional[TimeField] = None
    level: Optional[Union[int, str, List[int]]] = None
    read_content: bool = False
    telemetry: TelemetryRequest = False


def _reject_unknown_categories(value: Any, label: str, allowed: Sequence[str]) -> None:
    if not isinstance(value, dict):
        return
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(
            f"unknown {label} keys: {', '.join(unknown)}; allowed: {', '.join(allowed)}"
        )


def _reject_unknown_quota_and_detail(quotas: Any, detail: Any) -> None:
    # Quotas name a retrieval bucket, `detail` names a reported category: the
    # catch-all memory category is reportable but owns no bucket to size.
    _reject_unknown_categories(quotas, "quota", CATEGORY_KEYS)
    _reject_unknown_categories(detail, "detail", REPORTED_CATEGORY_KEYS)


CONTEXT_ONLY_FIELDS = (
    "query_expansion",
    "max_tokens",
    "quotas",
    "purpose",
    "detail",
    "dedup_turns",
    "exclude_uris",
    "peer_scope",
    "other_peer_penalty",
    "rewrite",
    "rewrite_max_bullets",
)


class SearchRequest(BaseModel):
    """Request model for search with session.

    ``mode="list"`` is the historical ranked-hit response. ``mode="context"``
    turns the endpoint into the context assembly face: budgeting, detail tiers,
    cross-turn dedup and the optional digest all live behind it.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = ""
    image_url: Optional[str] = None
    target_uri: Union[str, List[str]] = ""
    context_type: Optional[Union[str, List[str]]] = None
    session_id: Optional[str] = None
    limit: int = DEFAULT_LIMIT
    node_limit: Optional[int] = None
    score_threshold: Optional[float] = None
    filter: Optional[Dict[str, Any]] = None
    include_provenance: bool = False
    tags: Optional[List[str]] = None

    since: Optional[str] = None
    until: Optional[str] = None
    time_field: Optional[TimeField] = None
    level: Optional[Union[int, str, List[int]]] = None
    read_content: bool = False
    telemetry: TelemetryRequest = False

    mode: Literal["list", "context"] = "list"

    query_expansion: Literal["off", "auto"] = "auto"
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, ge=64, le=32000)
    quotas: Optional[Dict[str, int]] = None
    purpose: Optional[Literal["chat", "coding"]] = None
    detail: Optional[DetailRequest] = None
    dedup_turns: int = Field(default=0, ge=0, le=100)
    exclude_uris: List[str] = Field(default_factory=list, max_length=MAX_EXCLUDE_URIS)
    peer_scope: Literal["actor", "all"] = "all"
    other_peer_penalty: Optional[Union[float, Dict[str, float]]] = None
    rewrite: Union[bool, Literal["auto"]] = False
    rewrite_max_bullets: int = Field(default=6, ge=1, le=20)

    @model_validator(mode="after")
    def _validate_mode(self) -> "SearchRequest":
        if self.mode == "list":
            used = sorted(set(CONTEXT_ONLY_FIELDS) & self.model_fields_set)
            if used:
                raise ValueError(
                    f"{', '.join(used)} require mode='context'; "
                    "set mode='context' or drop these fields"
                )
            return self

        if self.read_content:
            raise ValueError("read_content is only supported in mode='list'")
        if self.target_uri:
            raise ValueError("target_uri is not supported in mode='context'")
        _reject_unknown_quota_and_detail(self.quotas, self.detail)
        return self


async def _inline_read_content(
    result: Any,
    *,
    service: Any,
    ctx: RequestContext,
) -> Any:
    """Attach visible file content to ranked hits when it can be read."""
    if not isinstance(result, dict):
        return result

    hits = [
        hit
        for category in ("memories", "resources", "skills")
        for hit in result.get(category, [])
        if isinstance(hit, dict) and isinstance(hit.get("uri"), str)
    ]
    semaphore = asyncio.Semaphore(10)

    async def _read(hit: Dict[str, Any]) -> None:
        async with semaphore:
            try:
                hit["content"] = await service.fs.read_visible(hit["uri"], ctx=ctx)
            except Exception:
                pass

    await asyncio.gather(*(_read(hit) for hit in hits))
    return result


class RecallRequest(BaseModel):
    """Request model for the recall preset over context assembly.

    Deprecated in favour of ``POST /search`` with ``mode="context"``. The v1
    fields (``max_chars``, ``min_score``, ``render``) are accepted as aliases
    here only, and are folded onto the context contract.
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    quotas: Optional[Dict[str, int]] = None
    max_chars: int = DEFAULT_MAX_CHARS
    min_score: float = DEFAULT_MIN_SCORE
    peer_scope: Literal["actor", "all"] = "all"
    other_peer_penalty: Optional[Union[float, Dict[str, float]]] = None
    render: Union[bool, Literal["full", "compact"]] = True
    telemetry: TelemetryRequest = False

    session_id: Optional[str] = None
    query_expansion: Optional[Literal["off", "auto"]] = None
    max_tokens: Optional[int] = Field(default=None, ge=64, le=32000)
    purpose: Optional[Literal["chat", "coding"]] = None
    detail: Optional[DetailRequest] = None
    score_threshold: Optional[float] = None
    dedup_turns: Optional[int] = Field(default=None, ge=0, le=100)
    exclude_uris: List[str] = Field(default_factory=list, max_length=MAX_EXCLUDE_URIS)
    rewrite: Union[bool, Literal["auto"]] = False
    rewrite_max_bullets: int = Field(default=6, ge=1, le=20)

    @model_validator(mode="after")
    def _validate_quotas(self) -> "RecallRequest":
        _reject_unknown_quota_and_detail(self.quotas, self.detail)
        return self


class GrepRequest(BaseModel):
    """Request model for grep."""

    uri: str
    exclude_uri: Optional[str] = None
    pattern: str
    case_insensitive: bool = False
    node_limit: Optional[int] = 256
    level_limit: int = 10


class GlobRequest(BaseModel):
    """Request model for glob."""

    pattern: str
    uri: str = "viking://"
    node_limit: Optional[int] = 256


@router.post("/find")
async def find(
    request: FindRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Semantic search without session context."""
    service = get_service()
    actual_limit = _resolve_search_limit(request.limit, request.node_limit)
    effective_filter = _resolve_search_filter(
        request.filter,
        request.context_type,
        request.since,
        request.until,
        request.time_field,
        request.tags,
    )
    resolved_target_uri = _resolve_uri_or_uris(request.target_uri, _ctx)
    resolved_image_url = _resolve_image_url(request.image_url, _ctx)
    execution = await run_operation(
        operation="search.find",
        telemetry=request.telemetry,
        fn=lambda: service.search.find(
            query=request.query,
            ctx=_ctx,
            target_uri=resolved_target_uri,
            limit=actual_limit,
            score_threshold=request.score_threshold,
            filter=effective_filter,
            level=_resolve_levels(request.level) or None,
            image_url=resolved_image_url,
        ),
    )
    result = execution.result
    if hasattr(result, "to_dict"):
        result = result.to_dict(include_provenance=request.include_provenance)
    if request.read_content:
        result = await _inline_read_content(result, service=service, ctx=_ctx)
    result = _sanitize_floats(result)
    return Response(
        status="ok",
        result=result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


def _context_ignored_fields(request: SearchRequest) -> List[str]:
    """Fields accepted but inert in context mode, surfaced through stats."""
    ignored: List[str] = []
    if request.level is not None:
        ignored.append("level")
    if (request.quotas is not None or request.purpose is not None) and (
        "limit" in request.model_fields_set or request.node_limit is not None
    ):
        ignored.append("limit")
    return ignored


async def _search_context(
    *,
    service: Any,
    ctx: RequestContext,
    request: SearchRequest,
    effective_filter: Optional[Dict[str, Any]],
    actual_limit: int,
):
    """Assemble an injection-ready context block for one request."""
    params = AssembleParams(
        query=request.query,
        image_url=_resolve_image_url(request.image_url, ctx),
        limit=actual_limit,
        score_threshold=request.score_threshold,
        filter=effective_filter,
        session_id=request.session_id,
        query_expansion=request.query_expansion,
        max_tokens=request.max_tokens,
        quotas=request.quotas,
        purpose=request.purpose,
        detail=request.detail,
        dedup_turns=request.dedup_turns,
        exclude_uris=_resolve_uri_list(request.exclude_uris, ctx),
        peer_scope=request.peer_scope,
        other_peer_penalty=request.other_peer_penalty,
        rewrite=request.rewrite,
        rewrite_max_bullets=request.rewrite_max_bullets,
    )
    execution = await run_operation(
        operation="search.context",
        telemetry=request.telemetry,
        fn=lambda: assemble_context(service=service, ctx=ctx, params=params),
    )
    result = execution.result
    ignored = _context_ignored_fields(request)
    if ignored:
        result.stats["ignored"] = ignored
    return Response(
        status="ok",
        result=_sanitize_floats(result.to_dict()),
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.post("/search")
async def search(
    request: SearchRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Semantic search with optional session context."""
    service = get_service()
    actual_limit = _resolve_search_limit(request.limit, request.node_limit)
    effective_filter = _resolve_search_filter(
        request.filter,
        request.context_type,
        request.since,
        request.until,
        request.time_field,
        request.tags,
    )
    if request.mode == "context":
        return await _search_context(
            service=service,
            ctx=_ctx,
            request=request,
            effective_filter=effective_filter,
            actual_limit=actual_limit,
        )
    resolved_target_uri = _resolve_uri_or_uris(request.target_uri, _ctx)
    resolved_image_url = _resolve_image_url(request.image_url, _ctx)

    async def _search():
        session = None
        # Intent off: skip session.load — SearchService will not scan session either.
        if request.session_id and service.search.is_intent_enabled():
            session = service.sessions.session(_ctx, request.session_id)
            await session.load()
        return await service.search.search(
            query=request.query,
            ctx=_ctx,
            target_uri=resolved_target_uri,
            session=session,
            limit=actual_limit,
            score_threshold=request.score_threshold,
            filter=effective_filter,
            level=_resolve_levels(request.level) or None,
            image_url=resolved_image_url,
        )

    execution = await run_operation(
        operation="search.search",
        telemetry=request.telemetry,
        fn=_search,
    )
    result = execution.result
    if hasattr(result, "to_dict"):
        result = result.to_dict(include_provenance=request.include_provenance)
    if request.read_content:
        result = await _inline_read_content(result, service=service, ctx=_ctx)
    result = _sanitize_floats(result)
    return Response(
        status="ok",
        result=result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.post("/recall", deprecated=True)
async def recall(
    request: RecallRequest,
    response: FastAPIResponse,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Deprecated preset over context assembly; use /search with mode="context"."""
    service = get_service()
    params, aliases = fold_recall_request(request.model_dump(), request.model_fields_set)
    params.image_url = _resolve_image_url(params.image_url, _ctx)
    params.exclude_uris = _resolve_uri_list(params.exclude_uris, _ctx)
    execution = await run_operation(
        operation="search.recall",
        telemetry=request.telemetry,
        fn=lambda: assemble_context(service=service, ctx=_ctx, params=params),
    )
    result = execution.result
    result.stats["deprecated"] = deprecation_stats(aliases)
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</api/v1/search/search>; rel="successor-version"'
    return Response(
        status="ok",
        result=_sanitize_floats(result.to_dict()),
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.post("/grep")
async def grep(
    request: GrepRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Content search with pattern."""
    service = get_service()
    resolved_uri = validate_request_viking_uri(resolve_path_variables(request.uri), _ctx)
    resolved_exclude_uri = None
    if request.exclude_uri:
        resolved_exclude_uri = validate_request_viking_uri(
            resolve_path_variables(request.exclude_uri), _ctx, field_name="exclude_uri"
        )
    try:
        result = await service.fs.grep(
            resolved_uri,
            request.pattern,
            ctx=_ctx,
            exclude_uri=resolved_exclude_uri,
            case_insensitive=request.case_insensitive,
            node_limit=request.node_limit,
            level_limit=request.level_limit,
        )
    except AGFSNotFoundError:
        raise NotFoundError(resolved_uri, "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=resolved_uri, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise
    except Exception as exc:
        mapped = map_exception(exc, resource=resolved_uri, resource_type="file")
        if mapped is not None:
            raise mapped from exc
        raise
    return Response(status="ok", result=result)


@router.post("/glob")
async def glob(
    request: GlobRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """File pattern matching."""
    service = get_service()
    resolved_uri = validate_request_viking_uri(resolve_path_variables(request.uri), _ctx)
    try:
        result = await service.fs.glob(
            request.pattern, ctx=_ctx, uri=resolved_uri, node_limit=request.node_limit
        )
    except AGFSNotFoundError:
        raise NotFoundError(resolved_uri or request.pattern, "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=resolved_uri or request.pattern, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise
    except Exception as exc:
        mapped = map_exception(exc, resource=request.uri, resource_type="file")
        if mapped is not None:
            raise mapped from exc
        raise
    return Response(status="ok", result=result)
