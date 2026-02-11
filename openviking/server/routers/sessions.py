# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Sessions endpoints for OpenViking HTTP Server."""

from typing import Any, Optional
from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel

from openviking.message.part import TextPart
from openviking.server.auth import verify_api_key
from openviking.server.dependencies import get_service
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class AddMessageRequest(BaseModel):
    """Request model for adding a message."""

    role: str
    content: str


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
    _: bool = Depends(verify_api_key),
):
    """Create a new session."""
    service = get_service()
    session = service.sessions.session()
    return Response(
        status="ok",
        result={
            "session_id": session.session_id,
            "user": session.user.to_dict(),
        },
    )


@router.get("")
async def list_sessions(
    _: bool = Depends(verify_api_key),
):
    """List all sessions."""
    service = get_service()
    result = await service.sessions.sessions()
    return Response(status="ok", result=result)


@router.get("/{session_id}")
async def get_session(
    session_id: str = Path(..., description="Session ID"),
    _: bool = Depends(verify_api_key),
):
    """Get session details."""
    service = get_service()
    session = service.sessions.session(session_id)
    session.load()
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
    _: bool = Depends(verify_api_key),
):
    """Delete a session."""
    service = get_service()
    await service.sessions.delete(session_id)
    return Response(status="ok", result={"session_id": session_id})


@router.post("/{session_id}/commit")
async def commit_session(
    session_id: str = Path(..., description="Session ID"),
    _: bool = Depends(verify_api_key),
):
    """Commit a session (archive and extract memories)."""
    service = get_service()
    result = await service.sessions.commit(session_id)
    return Response(status="ok", result=result)


@router.post("/{session_id}/extract")
async def extract_session(
    session_id: str = Path(..., description="Session ID"),
    _: bool = Depends(verify_api_key),
):
    """Extract memories from a session."""
    service = get_service()
    result = await service.sessions.extract(session_id)
    return Response(status="ok", result=_to_jsonable(result))


@router.post("/{session_id}/messages")
async def add_message(
    request: AddMessageRequest,
    session_id: str = Path(..., description="Session ID"),
    _: bool = Depends(verify_api_key),
):
    """Add a message to a session."""
    service = get_service()
    session = service.sessions.session(session_id)
    session.load()
    session.add_message(request.role, [TextPart(text=request.content)])
    return Response(
        status="ok",
        result={
            "session_id": session_id,
            "message_count": len(session.messages),
        },
    )
