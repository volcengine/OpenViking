# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Sessions endpoints for OpenViking HTTP Server."""

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, model_validator

from openviking.message.part import TextPart, part_from_dict
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import ErrorInfo, Response
from openviking.server.schemas import ExcludeNoneRoute
from openviking.server.schemas.sessions import (
    CommitResult,
    ContextItem,
    MessageAddedResult,
    SessionArchiveDetail,
    SessionContextResult,
    SessionCreatedResult,
    SessionDeletedResult,
    SessionDetail,
    SessionListItem,
    UsageRecordedResult,
    UserInfo,
)

router = APIRouter(
    prefix="/api/v1/sessions",
    tags=["sessions"],
    route_class=ExcludeNoneRoute,
)
logger = logging.getLogger(__name__)


class TextPartRequest(BaseModel):
    """Text part request model."""

    type: Literal["text"] = "text"
    text: str


class ContextPartRequest(BaseModel):
    """Context part request model."""

    type: Literal["context"] = "context"
    uri: str = ""
    context_type: Literal["memory", "resource", "skill"] = "memory"
    abstract: str = ""


class ToolPartRequest(BaseModel):
    """Tool part request model."""

    type: Literal["tool"] = "tool"
    tool_id: str = ""
    tool_name: str = ""
    tool_uri: str = ""
    skill_uri: str = ""
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: str = ""
    tool_status: str = "pending"


PartRequest = TextPartRequest | ContextPartRequest | ToolPartRequest


class AddMessageRequest(BaseModel):
    """Request model for adding a message.

    Supports two modes:
    1. Simple mode: provide `content` string (backward compatible)
    2. Parts mode: provide `parts` array for full Part support

    If both are provided, `parts` takes precedence.
    """

    role: str
    content: Optional[str] = None
    parts: Optional[List[Dict[str, Any]]] = None
    created_at: Optional[str] = None

    @model_validator(mode="after")
    def validate_content_or_parts(self) -> "AddMessageRequest":
        if self.content is None and self.parts is None:
            raise ValueError("Either 'content' or 'parts' must be provided")
        return self


class UsedRequest(BaseModel):
    """Request model for recording usage."""

    contexts: Optional[List[str]] = None
    skill: Optional[Dict[str, Any]] = None


class CreateSessionRequest(BaseModel):
    """Request model for creating a session."""

    session_id: Optional[str] = None


def _to_jsonable(value: Any) -> Any:
    """Convert internal objects (e.g. Context) into JSON-serializable values."""
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


@router.post("", response_model=Response[SessionCreatedResult])
async def create_session(
    request: Optional[CreateSessionRequest] = None,
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[SessionCreatedResult]:
    """Create a new session.

    If session_id is provided, creates a session with the given ID.
    If session_id is None, creates a new session with auto-generated ID.
    """
    service = get_service()
    await service.initialize_user_directories(_ctx)
    await service.initialize_agent_directories(_ctx)
    session_id = request.session_id if request else None
    session = await service.sessions.create(_ctx, session_id)
    return Response(
        status="ok",
        result=SessionCreatedResult(
            session_id=session.session_id,
            user=UserInfo(**session.user.to_dict()),
        ),
    )


@router.get("", response_model=Response[List[SessionListItem]])
async def list_sessions(
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[List[SessionListItem]]:
    """List all sessions."""
    service = get_service()
    items = await service.sessions.sessions(_ctx)
    return Response(
        status="ok",
        result=[SessionListItem(**item) for item in items],
    )


@router.get("/{session_id}", response_model=Response[SessionDetail])
async def get_session(
    session_id: str = Path(..., description="Session ID"),
    auto_create: bool = Query(False, description="Create the session if it does not exist"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[SessionDetail]:
    """Get session details."""
    from openviking_cli.exceptions import NotFoundError

    service = get_service()
    try:
        session = await service.sessions.get(session_id, _ctx, auto_create=auto_create)
    except NotFoundError:
        return Response(
            status="error",
            error=ErrorInfo(code="NOT_FOUND", message=f"Session {session_id} not found"),
        )
    meta_dict = session.meta.to_dict()
    meta_dict["user"] = session.user.to_dict()
    meta_dict["pending_tokens"] = sum(len(m.content) // 4 for m in session.messages)
    return Response(
        status="ok",
        result=SessionDetail.model_validate(meta_dict),
    )


@router.get("/{session_id}/context", response_model=Response[SessionContextResult])
async def get_session_context(
    session_id: str = Path(..., description="Session ID"),
    token_budget: int = Query(128_000, description="Token budget for session context"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[SessionContextResult]:
    """Get assembled session context."""
    service = get_service()
    session = service.sessions.session(_ctx, session_id)
    await session.load()
    result = await session.get_session_context(token_budget=token_budget)
    return Response(
        status="ok",
        result=SessionContextResult.model_validate(_to_jsonable(result)),
    )


@router.get(
    "/{session_id}/archives/{archive_id}",
    response_model=Response[SessionArchiveDetail],
)
async def get_session_archive(
    session_id: str = Path(..., description="Session ID"),
    archive_id: str = Path(..., description="Archive ID"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[SessionArchiveDetail]:
    """Get one completed archive for a session."""
    from openviking_cli.exceptions import NotFoundError

    service = get_service()
    session = service.sessions.session(_ctx, session_id)
    await session.load()
    try:
        result = await session.get_session_archive(archive_id)
    except NotFoundError:
        return Response(
            status="error",
            error=ErrorInfo(code="NOT_FOUND", message=f"Archive {archive_id} not found"),
        )
    return Response(
        status="ok",
        result=SessionArchiveDetail.model_validate(_to_jsonable(result)),
    )


@router.delete("/{session_id}", response_model=Response[SessionDeletedResult])
async def delete_session(
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[SessionDeletedResult]:
    """Delete a session."""
    service = get_service()
    await service.sessions.delete(session_id, _ctx)
    return Response(
        status="ok",
        result=SessionDeletedResult(session_id=session_id),
    )


@router.post("/{session_id}/commit", response_model=Response[CommitResult])
async def commit_session(
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[CommitResult]:
    """Commit a session (archive and extract memories).

    Archive (Phase 1) completes before returning.  Memory extraction
    (Phase 2) runs in the background.  A ``task_id`` is returned for
    polling progress via ``GET /tasks/{task_id}``.
    """
    service = get_service()
    result = await service.sessions.commit_async(session_id, _ctx)
    return Response(
        status="ok",
        result=CommitResult.model_validate(result),
    )


@router.post("/{session_id}/extract", response_model=Response[List[ContextItem]])
async def extract_session(
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[List[ContextItem]]:
    """Extract memories from a session."""
    service = get_service()
    result = await service.sessions.extract(session_id, _ctx)
    items = [ContextItem.model_validate(_to_jsonable(ctx)) for ctx in result]
    return Response(status="ok", result=items)


@router.post("/{session_id}/messages", response_model=Response[MessageAddedResult])
async def add_message(
    request: AddMessageRequest,
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[MessageAddedResult]:
    """Add a message to a session.

    Supports two modes:
    1. Simple mode: provide `content` string (backward compatible)
       Example: {"role": "user", "content": "Hello"}

    2. Parts mode: provide `parts` array for full Part support
       Example: {"role": "assistant", "parts": [
           {"type": "text", "text": "Here's the answer"},
           {"type": "context", "uri": "viking://resources/doc.md", "abstract": "..."}
       ]}

    If both `content` and `parts` are provided, `parts` takes precedence.
    Missing sessions are auto-created on first add.
    """
    service = get_service()
    session = await service.sessions.get(session_id, _ctx, auto_create=True)

    if request.parts is not None:
        parts = [part_from_dict(p) for p in request.parts]
    else:
        parts = [TextPart(text=request.content or "")]

    # created_at 直接传递给 session (ISO string)
    session.add_message(request.role, parts, created_at=request.created_at)
    return Response(
        status="ok",
        result=MessageAddedResult(
            session_id=session_id,
            message_count=len(session.messages),
        ),
    )


@router.post("/{session_id}/used", response_model=Response[UsageRecordedResult])
async def record_used(
    request: UsedRequest,
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response[UsageRecordedResult]:
    """Record actually used contexts and skills in a session."""
    service = get_service()
    session = service.sessions.session(_ctx, session_id)
    await session.load()
    session.used(contexts=request.contexts, skill=request.skill)
    return Response(
        status="ok",
        result=UsageRecordedResult(
            session_id=session_id,
            contexts_used=session.stats.contexts_used,
            skills_used=session.stats.skills_used,
        ),
    )
