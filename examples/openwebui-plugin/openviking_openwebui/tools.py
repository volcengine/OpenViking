# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""FastAPI route handlers, one per OpenWebUI tool."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .client import OVClient, OVError
from .config import Settings


def get_client(request: Request) -> OVClient:
    """Resolve the shared OVClient placed on the FastAPI app state."""
    return request.app.state.ov_client


def get_settings(request: Request) -> Settings:
    return request.app.state.ov_settings


router = APIRouter(tags=["openviking"])


def _forward(exc: OVError) -> HTTPException:
    """Surface the OV server's error status + body verbatim."""
    return HTTPException(status_code=exc.status, detail=exc.payload)


# --- Schemas -----------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(..., description="Natural-language query")
    limit: int = Field(10, ge=1, le=100)
    target_uri: Optional[str] = Field(
        None,
        description="Optional viking:// prefix to scope the search",
    )
    score_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)


class SearchHit(BaseModel):
    uri: str
    score: float
    snippet: Optional[str] = None


class SearchResponse(BaseModel):
    hits: List[SearchHit]
    raw: Dict[str, Any] = Field(default_factory=dict)


class RecallRequest(BaseModel):
    query: str
    limit: int = Field(6, ge=1, le=50)


class AddMemoryRequest(BaseModel):
    name: str = Field(..., description="Filename under viking://user/memories/, e.g. 'profile.md'")
    content: str = Field(..., description="Memory body, plain text or Markdown")
    mode: Literal["replace", "append", "create"] = "replace"
    wait: bool = Field(False, description="Block until semantic indexing completes")


class AddMemoryResponse(BaseModel):
    uri: str
    raw: Dict[str, Any] = Field(default_factory=dict)


class ListMemoriesRequest(BaseModel):
    recursive: bool = False
    limit: int = Field(200, ge=1, le=1000)


class ReadResourceRequest(BaseModel):
    uri: str = Field(..., description="Full viking:// URI")
    offset: int = 0
    limit: int = -1


class AddResourceRequest(BaseModel):
    path: str = Field(..., description="Remote URL or local path the OV server can reach")
    to: Optional[str] = None
    parent: Optional[str] = None
    reason: str = ""
    instruction: str = ""
    wait: bool = False


class SessionStatusRequest(BaseModel):
    session_id: str


# --- Helpers -----------------------------------------------------------------


def _hits_from_find(payload: Dict[str, Any]) -> List[SearchHit]:
    """Flatten an OpenViking /search/find response into typed hits."""
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return []
    bucket_keys = ("memories", "resources", "skills", "results")
    seen: List[SearchHit] = []
    for key in bucket_keys:
        bucket = result.get(key)
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if not isinstance(item, dict):
                continue
            uri = item.get("uri") or item.get("target_uri")
            if not isinstance(uri, str):
                continue
            score = float(item.get("score") or 0.0)
            snippet = item.get("snippet") or item.get("abstract") or item.get("preview")
            seen.append(
                SearchHit(
                    uri=uri,
                    score=score,
                    snippet=snippet if isinstance(snippet, str) else None,
                )
            )
    return seen


def _memory_uri(settings: Settings, name: str) -> str:
    name = name.strip().lstrip("/")
    if not name:
        raise HTTPException(status_code=400, detail="name must not be empty")
    return settings.memories_uri + name


# --- Routes ------------------------------------------------------------------


@router.post(
    "/tools/ov_search",
    response_model=SearchResponse,
    operation_id="ov_search",
    summary="Semantic search across OpenViking memories, resources, and skills.",
)
async def ov_search(
    body: SearchRequest,
    client: OVClient = Depends(get_client),
) -> SearchResponse:
    """Run a semantic find over OpenViking and return the top hits."""
    payload: Dict[str, Any] = {"query": body.query, "limit": body.limit}
    if body.target_uri:
        payload["target_uri"] = body.target_uri
    if body.score_threshold is not None:
        payload["score_threshold"] = body.score_threshold
    try:
        raw = await client.post("/api/v1/search/find", json=payload)
    except OVError as exc:
        raise _forward(exc) from exc
    return SearchResponse(hits=_hits_from_find(raw), raw=raw if isinstance(raw, dict) else {})


@router.post(
    "/tools/ov_recall_memories",
    response_model=SearchResponse,
    operation_id="ov_recall_memories",
    summary="Recall personal memories relevant to a query.",
)
async def ov_recall_memories(
    body: RecallRequest,
    client: OVClient = Depends(get_client),
    settings: Settings = Depends(get_settings),
) -> SearchResponse:
    """Search scoped to viking://user/memories/ for personal memory recall."""
    payload = {
        "query": body.query,
        "limit": body.limit,
        "target_uri": settings.memories_uri,
    }
    try:
        raw = await client.post("/api/v1/search/find", json=payload)
    except OVError as exc:
        raise _forward(exc) from exc
    return SearchResponse(hits=_hits_from_find(raw), raw=raw if isinstance(raw, dict) else {})


@router.post(
    "/tools/ov_add_memory",
    response_model=AddMemoryResponse,
    operation_id="ov_add_memory",
    summary="Persist a new memory under viking://user/memories/.",
)
async def ov_add_memory(
    body: AddMemoryRequest,
    client: OVClient = Depends(get_client),
    settings: Settings = Depends(get_settings),
) -> AddMemoryResponse:
    """Write a memory file via /api/v1/content/write."""
    uri = _memory_uri(settings, body.name)
    payload = {
        "uri": uri,
        "content": body.content,
        "mode": body.mode,
        "wait": body.wait,
    }
    try:
        raw = await client.post("/api/v1/content/write", json=payload)
    except OVError as exc:
        raise _forward(exc) from exc
    return AddMemoryResponse(uri=uri, raw=raw if isinstance(raw, dict) else {})


@router.post(
    "/tools/ov_list_memories",
    operation_id="ov_list_memories",
    summary="List entries under viking://user/memories/.",
)
async def ov_list_memories(
    body: ListMemoriesRequest,
    client: OVClient = Depends(get_client),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Browse the memories directory via /api/v1/fs/ls."""
    params = {
        "uri": settings.memories_uri,
        "recursive": str(body.recursive).lower(),
        "node_limit": body.limit,
    }
    try:
        return await client.get("/api/v1/fs/ls", params=params)
    except OVError as exc:
        raise _forward(exc) from exc


@router.post(
    "/tools/ov_read_resource",
    operation_id="ov_read_resource",
    summary="Read text content of any viking:// URI.",
)
async def ov_read_resource(
    body: ReadResourceRequest,
    client: OVClient = Depends(get_client),
) -> Dict[str, Any]:
    """Read full text content via /api/v1/content/read."""
    params: Dict[str, Any] = {
        "uri": body.uri,
        "offset": body.offset,
        "limit": body.limit,
    }
    try:
        return await client.get("/api/v1/content/read", params=params)
    except OVError as exc:
        raise _forward(exc) from exc


@router.post(
    "/tools/ov_add_resource",
    operation_id="ov_add_resource",
    summary="Ingest a remote URL or path-reachable file as an OpenViking resource.",
)
async def ov_add_resource(
    body: AddResourceRequest,
    client: OVClient = Depends(get_client),
) -> Dict[str, Any]:
    """POST /api/v1/resources to start ingestion."""
    payload = {
        "path": body.path,
        "reason": body.reason,
        "instruction": body.instruction,
        "wait": body.wait,
    }
    if body.to:
        payload["to"] = body.to
    if body.parent:
        payload["parent"] = body.parent
    try:
        return await client.post("/api/v1/resources", json=payload)
    except OVError as exc:
        raise _forward(exc) from exc


@router.post(
    "/tools/ov_session_status",
    operation_id="ov_session_status",
    summary="Get OpenViking session metadata (counts, archive state, pending tokens).",
)
async def ov_session_status(
    body: SessionStatusRequest,
    client: OVClient = Depends(get_client),
) -> Dict[str, Any]:
    """GET /api/v1/sessions/{session_id}."""
    try:
        return await client.get(f"/api/v1/sessions/{body.session_id}")
    except OVError as exc:
        raise _forward(exc) from exc
