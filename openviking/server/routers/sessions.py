# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Sessions endpoints for OpenViking HTTP Server."""

import asyncio
import logging
import time
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import BaseModel, model_validator

from openviking.message.part import TextPart, part_from_dict
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import ErrorInfo, Response
from openviking.server.telemetry import resolve_selection, run_operation
from openviking.storage import get_queue_manager
from openviking.telemetry.resource_summary import record_resource_wait_metrics, register_wait_telemetry
from openviking.telemetry.resource_summary import unregister_wait_telemetry
from openviking.service.task_tracker import get_task_tracker
from openviking.telemetry import TelemetryRequest
from openviking_cli.exceptions import InvalidArgumentError

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
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

    @model_validator(mode="after")
    def validate_content_or_parts(self) -> "AddMessageRequest":
        if self.content is None and self.parts is None:
            raise ValueError("Either 'content' or 'parts' must be provided")
        return self


class CommitSessionRequest(BaseModel):
    """Request model for session commit."""

    telemetry: TelemetryRequest = False


class ExtractSessionRequest(BaseModel):
    """Request model for session extract."""

    telemetry: TelemetryRequest = False
    wait: bool = False
    timeout: Optional[float] = None


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
    request: CommitSessionRequest = Body(default_factory=CommitSessionRequest),
    session_id: str = Path(..., description="Session ID"),
    wait: bool = Query(
        True,
        description="If False, commit runs in background and returns immediately",
    ),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Commit a session (archive and extract memories).

    When wait=False, the commit is processed in the background and a
    ``task_id`` is returned.  Use ``GET /tasks/{task_id}`` to poll for
    completion status, results, or errors.

    When wait=True (default), the commit blocks until complete and
    returns the full result inline.
    """
    service = get_service()
    tracker = get_task_tracker()

    if wait:
        # Reject if same session already has a background commit running
        if tracker.has_running("session_commit", session_id):
            return Response(
                status="error",
                error=ErrorInfo(
                    code="CONFLICT",
                    message=f"Session {session_id} already has a commit in progress",
                ),
            )
        execution = await run_operation(
            operation="session.commit",
            telemetry=request.telemetry,
            fn=lambda: service.sessions.commit_async(session_id, _ctx),
        )
        return Response(
            status="ok",
            result=execution.result,
            telemetry=execution.telemetry,
        ).model_dump(exclude_none=True)

    selection = resolve_selection(request.telemetry)
    if selection.include_payload:
        raise InvalidArgumentError("telemetry is not supported when wait=false for session.commit")

    # Atomically check + create to prevent race conditions
    task = tracker.create_if_no_running("session_commit", session_id)
    if task is None:
        return Response(
            status="error",
            error=ErrorInfo(
                code="CONFLICT",
                message=f"Session {session_id} already has a commit in progress",
            ),
        )
    asyncio.create_task(_background_commit_tracked(service, session_id, _ctx, task.task_id))

    return Response(
        status="ok",
        result={
            "session_id": session_id,
            "status": "accepted",
            "task_id": task.task_id,
            "message": "Commit is processing in the background",
        },
    )


async def _background_commit_tracked(
    service, session_id: str, ctx: RequestContext, task_id: str
) -> None:
    """Run session commit in background with task tracking."""
    tracker = get_task_tracker()
    tracker.start(task_id)
    try:
        result = await service.sessions.commit_async(session_id, ctx)
        tracker.complete(
            task_id,
            {
                "session_id": session_id,
                "memories_extracted": result.get("memories_extracted", 0),
                "archived": result.get("archived", False),
            },
        )
        logger.info(
            "Background commit completed: session=%s task=%s memories=%d",
            session_id,
            task_id,
            result.get("memories_extracted", 0),
        )
    except Exception as exc:
        tracker.fail(task_id, str(exc))
        logger.exception("Background commit failed: session=%s task=%s", session_id, task_id)


@router.post("/{session_id}/extract")
async def extract_session(
    request: ExtractSessionRequest = Body(default_factory=ExtractSessionRequest),
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Extract memories from a session."""
    service = get_service()

    async def _extract():
        request_started_at = time.perf_counter()
        telemetry_id = register_wait_telemetry(request.wait)
        try:
            result = await service.sessions.extract(session_id, _ctx)
            if request.wait:
                qm = get_queue_manager()
                wait_started_at = time.perf_counter()
                status = await qm.wait_complete(timeout=request.timeout)
                record_resource_wait_metrics(
                    telemetry_id=telemetry_id,
                    queue_status=status,
                    root_uri=None,
                )
                from openviking.telemetry import get_current_telemetry

                telemetry = get_current_telemetry()
                telemetry.set(
                    "queue.wait.duration_ms",
                    round((time.perf_counter() - wait_started_at) * 1000, 3),
                )
                telemetry.set(
                    "session.extract.request.duration_ms",
                    round((time.perf_counter() - request_started_at) * 1000, 3),
                )
            return _to_jsonable(result)
        finally:
            unregister_wait_telemetry(telemetry_id)

    execution = await run_operation(
        operation="session.extract",
        telemetry=request.telemetry,
        fn=_extract,
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


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
