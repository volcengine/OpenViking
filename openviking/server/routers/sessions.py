# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Sessions endpoints for OpenViking HTTP Server."""

from typing import Optional

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel

from openviking.server.auth import verify_api_key
from openviking.server.dependencies import get_service
from openviking.server.models import Response

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    """Request model for creating a session."""

    user: Optional[str] = None


class AddMessageRequest(BaseModel):
    """Request model for adding a message."""

    role: str
    content: str


@router.post("")
async def create_session(
    request: CreateSessionRequest,
    _: bool = Depends(verify_api_key),
):
    """Create a new session."""
    service = get_service()
    session = service.sessions.session()
    return Response(
        status="ok",
        result={
            "session_id": session.session_id,
            "user": session.user,
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
            "user": session.user,
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


@router.post("/{session_id}/compress")
async def compress_session(
    session_id: str = Path(..., description="Session ID"),
    _: bool = Depends(verify_api_key),
):
    """Compress a session."""
    service = get_service()
    result = await service.sessions.compress(session_id)
    return Response(status="ok", result=result)


@router.post("/{session_id}/extract")
async def extract_session(
    session_id: str = Path(..., description="Session ID"),
    _: bool = Depends(verify_api_key),
):
    """Extract memories from a session."""
    service = get_service()
    result = await service.sessions.extract(session_id)
    return Response(status="ok", result=result)


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
    session.add_message(request.role, request.content)
    return Response(
        status="ok",
        result={
            "session_id": session_id,
            "message_count": len(session.messages),
        },
    )
