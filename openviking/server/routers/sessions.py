# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Sessions endpoints for OpenViking HTTP Server."""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, model_validator

from openviking.message.part import TextPart, part_from_dict
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


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

    @model_validator(mode="after")
    def validate_content_or_parts(self) -> "AddMessageRequest":
        if self.content is None and self.parts is None:
            raise ValueError("Either 'content' or 'parts' must be provided")
        return self


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


@router.post("")
async def create_session(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Create a new session."""
    service = get_service()
    await service.initialize_user_directories(_ctx)
    await service.initialize_agent_directories(_ctx)
    session = await service.sessions.create(_ctx)
    return Response(
        status="ok",
        result={
            "session_id": session.session_id,
            "user": session.user.to_dict(),
        },
    )


@router.get("")
async def list_sessions(
    _ctx: RequestContext = Depends(get_request_context),
):
    """List all sessions."""
    service = get_service()
    result = await service.sessions.sessions(_ctx)
    return Response(status="ok", result=result)


@router.get("/{session_id}")
async def get_session(
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get session details."""
    service = get_service()
    session = await service.sessions.get(session_id, _ctx)
    return Response(
        status="ok",
        result={
            "session_id": session.session_id,
            "user": session.user.to_dict(),
            "message_count": len(session.messages),
        },
    )


@router.delete("/{session_id}")
async def delete_session(
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Delete a session."""
    service = get_service()
    await service.sessions.delete(session_id, _ctx)
    return Response(status="ok", result={"session_id": session_id})


@router.post("/{session_id}/commit")
async def commit_session(
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Commit a session (archive and extract memories)."""
    service = get_service()
    result = await service.sessions.commit(session_id, _ctx)
    return Response(status="ok", result=result)


@router.post("/{session_id}/extract")
async def extract_session(
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Extract memories from a session."""
    service = get_service()
    result = await service.sessions.extract(session_id, _ctx)
    return Response(status="ok", result=_to_jsonable(result))


@router.post("/{session_id}/messages")
async def add_message(
    request: AddMessageRequest,
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
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
    """
    service = get_service()
    session = service.sessions.session(_ctx, session_id)
    await session.load()

    if request.parts is not None:
        parts = [part_from_dict(p) for p in request.parts]
    else:
        parts = [TextPart(text=request.content or "")]

    session.add_message(request.role, parts)
    return Response(
        status="ok",
        result={
            "session_id": session_id,
            "message_count": len(session.messages),
        },
    )
