# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Sessions endpoints for OpenViking HTTP Server."""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from openviking.core.path_variables import resolve_path_variables
from openviking.core.peer_id import normalize_peer_id
from openviking.message.part import Part, TextPart, part_from_dict
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import ErrorInfo, Response
from openviking.server.responses import error_response
from openviking.server.telemetry import run_operation
from openviking.telemetry import TelemetryRequest
from openviking_cli.utils import get_logger

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
logger = get_logger(__name__)


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
    duration_ms: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    tool_output_ref: str = ""
    tool_output_truncated: bool = False
    tool_output_original_chars: Optional[int] = None
    tool_output_preview_chars: Optional[int] = None
    tool_output_sha256: str = ""
    tool_output_storage_uri: str = ""
    tool_output_mime_type: str = "text/plain"
    tool_output_source_ref: str = ""
    tool_output_source_offset: Optional[int] = None
    tool_output_source_limit: Optional[int] = None
    tool_output_externalization_error: str = ""
    tool_output_group_id: str = ""
    tool_output_externalized_reason: str = ""
    tool_output_group_original_chars: Optional[int] = None
    tool_output_group_budget_chars: Optional[int] = None


PartRequest = TextPartRequest | ContextPartRequest | ToolPartRequest


class AddMessageRequest(BaseModel):
    """Request model for adding a message.

    Supports two modes:
    1. Simple mode: provide `content` string (backward compatible)
    2. Parts mode: provide `parts` array for full Part support

    If both are provided, `parts` takes precedence.
    """

    role: str
    peer_id: Optional[str] = None
    agent_id: Optional[str] = None
    agent_uri: Optional[str] = None
    content: Optional[str] = None
    parts: Optional[List[Dict[str, Any]]] = None
    created_at: Optional[str] = None
    turn_id: Optional[str] = None
    message_kind: Optional[
        Literal["user_query", "assistant_step", "tool_transport", "checkpoint"]
    ] = None
    source_message_ids: Optional[List[str]] = None
    telemetry: TelemetryRequest = False

    @field_validator("peer_id")
    @classmethod
    def validate_peer_id(cls, value: Optional[str]) -> Optional[str]:
        return normalize_peer_id(value)

    @model_validator(mode="after")
    def validate_content_or_parts(self) -> "AddMessageRequest":
        if self.content is None and self.parts is None:
            raise ValueError("Either 'content' or 'parts' must be provided")
        return self


class BatchAddMessageRequest(BaseModel):
    """Request model for adding multiple messages in a single request."""

    messages: List[AddMessageRequest] = Field(..., max_length=100)
    telemetry: TelemetryRequest = False


class UsedRequest(BaseModel):
    """Request model for recording usage."""

    contexts: Optional[List[str]] = None
    skill: Optional[Dict[str, Any]] = None


class CreateSessionRequest(BaseModel):
    """Request model for creating a session."""

    session_id: Optional[str] = None
    memory_policy: Optional[Dict[str, Any]] = None
    telemetry: TelemetryRequest = False


def _resolve_message_parts(msg_request: AddMessageRequest) -> List[Part]:
    """Resolve parts from an AddMessageRequest, handling path variables."""
    if msg_request.parts is not None:
        return [_part_request_to_part(p) for p in msg_request.parts]
    return [TextPart(text=msg_request.content or "")]


def _resolve_message_peer_id(msg_request: AddMessageRequest, ctx: RequestContext) -> Optional[str]:
    if msg_request.peer_id is not None:
        return msg_request.peer_id
    if ctx.legacy_agent_id is not None and msg_request.role == "assistant":
        return ctx.legacy_agent_id
    return None


def _part_request_to_part(raw_part: Dict[str, Any]) -> Part:
    """Convert request part payload into an internal Part."""
    if not isinstance(raw_part, dict):
        return TextPart(text=str(raw_part))

    part_copy = dict(raw_part)
    if part_copy.get("type") == "context" and "uri" in part_copy:
        part_copy["uri"] = resolve_path_variables(part_copy["uri"])
    if part_copy.get("type") == "tool":
        if "tool_uri" in part_copy:
            part_copy["tool_uri"] = resolve_path_variables(part_copy["tool_uri"])
        if "skill_uri" in part_copy:
            part_copy["skill_uri"] = resolve_path_variables(part_copy["skill_uri"])
    if part_copy.get("type") == "image_url":
        image_url = part_copy.get("image_url")
        if isinstance(image_url, dict) and "url" in image_url:
            image_url = dict(image_url)
            image_url["url"] = resolve_path_variables(image_url["url"])
            part_copy["image_url"] = image_url
        elif isinstance(image_url, str):
            part_copy["image_url"] = resolve_path_variables(image_url)
    try:
        return part_from_dict(part_copy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    request: CreateSessionRequest = Body(default_factory=CreateSessionRequest),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Create a new session.

    If session_id is provided, creates a session with the given ID.
    If session_id is None, creates a new session with auto-generated ID.
    """
    service = get_service()

    async def _create() -> dict[str, Any]:
        await service.initialize_user_directories(_ctx)
        session = await service.sessions.create(
            _ctx,
            request.session_id,
            memory_policy=request.memory_policy,
        )
        return {
            "session_id": session.session_id,
            "uri": session.uri,
            "user": session.user.to_dict(),
        }

    execution = await run_operation(
        operation="session.create",
        telemetry=request.telemetry,
        fn=_create,
    )
    return Response(status="ok", result=execution.result, telemetry=execution.telemetry)


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
    auto_create: bool = Query(False, description="Create the session if it does not exist"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get session details."""
    from openviking_cli.exceptions import NotFoundError

    service = get_service()
    try:
        session = await service.sessions.get(session_id, _ctx, auto_create=auto_create)
    except NotFoundError:
        return error_response("NOT_FOUND", f"Session {session_id} not found")
    result = session.meta.to_dict()
    result["uri"] = session.uri
    result["user"] = session.user.to_dict()
    result["pending_tokens"] = int(session.meta.pending_tokens or 0)
    return Response(status="ok", result=result)


@router.get("/{session_id}/tool-results")
async def list_tool_results(
    session_id: str = Path(..., description="Session ID"),
    tool_name: Optional[str] = Query(None, description="Filter by tool name"),
    limit: int = Query(50, ge=1, description="Maximum number of tool results"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """List externalized tool results for a session."""
    service = get_service()
    session = await service.sessions.get(session_id, _ctx, auto_create=False)
    result = await session.list_tool_results(tool_name=tool_name, limit=limit)
    return Response(status="ok", result=_to_jsonable(result))


@router.get("/{session_id}/tool-results/{tool_result_id}")
async def read_tool_result(
    session_id: str = Path(..., description="Session ID"),
    tool_result_id: str = Path(..., description="Tool result ID"),
    offset: int = Query(0, ge=0, description="Unicode character offset"),
    limit: int = Query(20_000, description="Maximum Unicode characters to return"),
    include_metadata: bool = Query(True, description="Include metadata in response"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Read an externalized tool result by Unicode character range."""
    if limit < -1:
        return error_response(
            "INVALID_ARGUMENT",
            "limit must be -1 or greater than or equal to 0",
            details={"field": "limit", "value": limit},
        )
    service = get_service()
    session = await service.sessions.get(session_id, _ctx, auto_create=False)
    result = await session.read_tool_result(
        tool_result_id,
        offset=offset,
        limit=limit,
        include_metadata=include_metadata,
    )
    return Response(status="ok", result=_to_jsonable(result))


@router.get("/{session_id}/tool-results/{tool_result_id}/search")
async def search_tool_result(
    session_id: str = Path(..., description="Session ID"),
    tool_result_id: str = Path(..., description="Tool result ID"),
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, description="Maximum matches"),
    context_chars: int = Query(300, ge=0, description="Context characters around each hit"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Search within an externalized tool result."""
    service = get_service()
    session = await service.sessions.get(session_id, _ctx, auto_create=False)
    result = await session.search_tool_result(
        tool_result_id,
        query=q,
        limit=limit,
        context_chars=context_chars,
    )
    return Response(status="ok", result=_to_jsonable(result))


@router.get("/{session_id}/context")
async def get_session_context(
    session_id: str = Path(..., description="Session ID"),
    token_budget: int = Query(128_000, description="Token budget for session context"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get assembled session context."""
    if token_budget < 0:
        return error_response(
            "INVALID_ARGUMENT",
            "token_budget must be greater than or equal to 0",
            details={"field": "token_budget", "value": token_budget},
        )

    service = get_service()
    session = await service.sessions.get(session_id, _ctx, auto_create=False)
    result = await session.get_session_context(token_budget=token_budget)
    return Response(status="ok", result=_to_jsonable(result))


@router.get("/{session_id}/archives/{archive_id}")
async def get_session_archive(
    session_id: str = Path(..., description="Session ID"),
    archive_id: str = Path(..., description="Archive ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get one completed archive for a session."""
    from openviking_cli.exceptions import NotFoundError

    service = get_service()
    session = await service.sessions.get(session_id, _ctx, auto_create=False)
    try:
        result = await session.get_session_archive(archive_id)
    except NotFoundError:
        return Response(
            status="error",
            error=ErrorInfo(code="NOT_FOUND", message=f"Archive {archive_id} not found"),
        )
    return Response(status="ok", result=_to_jsonable(result))


@router.delete("/{session_id}")
async def delete_session(
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Delete a session."""
    service = get_service()
    await service.sessions.delete(session_id, _ctx)
    return Response(status="ok", result={"session_id": session_id})


class CommitRequest(BaseModel):
    """Commit request body.

    WM v2: ``keep_recent_count`` allows the plugin to retain a tail of recent
    messages in the live session after commit so the next turn still has
    immediate context. Default 0 preserves the pre-v2 "archive everything"
    behavior.
    """

    keep_recent_count: int = Field(
        default=0,
        ge=0,
        le=10_000,
        description=(
            "Number of most-recent messages to keep live after commit. "
            "Plugin's afterTurn path typically passes its configured value "
            "(default 10); compact path passes 0 to archive everything."
        ),
    )
    retention_mode: Optional[Literal["turn_budget"]] = Field(
        default=None,
        description=(
            "Opt in to logical Turn retention. Omit to preserve keep_recent_count semantics."
        ),
    )
    keep_recent_turn_count: Optional[int] = Field(
        default=None,
        ge=0,
        le=10_000,
        description="Maximum number of newest logical user Turns to retain.",
    )
    retained_message_token_budget: Optional[int] = Field(
        default=None,
        ge=1,
        description="Token budget for retained raw messages and checkpoint.",
    )
    min_raw_tail_steps: Optional[int] = Field(
        default=None,
        ge=0,
        le=10_000,
        description="Minimum number of latest atomic assistant Steps kept raw.",
    )
    telemetry: TelemetryRequest = False

    @model_validator(mode="after")
    def validate_turn_retention_opt_in(self) -> "CommitRequest":
        if self.retention_mode is None and any(
            value is not None
            for value in (
                self.keep_recent_turn_count,
                self.retained_message_token_budget,
                self.min_raw_tail_steps,
            )
        ):
            raise ValueError(
                "retention_mode='turn_budget' is required when Turn retention fields are set"
            )
        return self


@router.post("/{session_id}/commit")
async def commit_session(
    session_id: str = Path(..., description="Session ID"),
    body: CommitRequest = Body(default_factory=CommitRequest),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Commit a session (archive and extract memories).

    Archive (Phase 1) completes before returning.  Memory extraction
    (Phase 2) runs in the background.  A ``task_id`` is returned for
    polling progress via ``GET /tasks/{task_id}``.
    """
    service = get_service()
    commit_kwargs: Dict[str, Any] = {"keep_recent_count": body.keep_recent_count}
    optional_retention = {
        "retention_mode": body.retention_mode,
        "keep_recent_turn_count": body.keep_recent_turn_count,
        "retained_message_token_budget": body.retained_message_token_budget,
        "min_raw_tail_steps": body.min_raw_tail_steps,
    }
    commit_kwargs.update(
        {key: value for key, value in optional_retention.items() if value is not None}
    )
    execution = await run_operation(
        operation="session.commit",
        telemetry=body.telemetry,
        fn=lambda: service.sessions.commit_async(
            session_id,
            _ctx,
            **commit_kwargs,
        ),
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


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
    Missing sessions are auto-created on first add.
    """
    service = get_service()

    async def _add() -> dict[str, Any]:
        session = await service.sessions.get(session_id, _ctx, auto_create=True)
        parts = _resolve_message_parts(request)

        specs = [
            {
                "role": request.role,
                "parts": parts,
                "peer_id": _resolve_message_peer_id(request, _ctx),
                "created_at": request.created_at,
                "turn_id": request.turn_id,
                "message_kind": request.message_kind,
                "source_message_ids": request.source_message_ids,
            }
        ]
        add_many_async = getattr(session, "add_messages_async", None)
        if callable(add_many_async):
            await add_many_async(specs)
        else:
            session.add_messages(specs)
        return {
            "session_id": session_id,
            "message_count": len(session.messages),
        }

    execution = await run_operation(
        operation="session.add_message",
        telemetry=request.telemetry,
        fn=_add,
    )
    return Response(status="ok", result=execution.result, telemetry=execution.telemetry)


@router.post("/{session_id}/messages/batch")
async def batch_add_messages(
    request: BatchAddMessageRequest,
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Add multiple messages to a session in a single request.

    Accepts a list of messages, each following the same format as AddMessageRequest.
    Missing sessions are auto-created on first add.
    """
    service = get_service()

    async def _batch_add() -> dict[str, Any]:
        session = await service.sessions.get(session_id, _ctx, auto_create=True)
        specs = []
        for msg_request in request.messages:
            parts = _resolve_message_parts(msg_request)
            specs.append(
                {
                    "role": msg_request.role,
                    "parts": parts,
                    "peer_id": _resolve_message_peer_id(msg_request, _ctx),
                    "created_at": msg_request.created_at,
                    "turn_id": msg_request.turn_id,
                    "message_kind": msg_request.message_kind,
                    "source_message_ids": msg_request.source_message_ids,
                }
            )
        add_many_async = getattr(session, "add_messages_async", None)
        if callable(add_many_async):
            msgs = await add_many_async(specs)
        else:
            msgs = session.add_messages(specs)
        return {
            "session_id": session_id,
            "message_count": len(session.messages),
            "added": len(msgs),
        }

    execution = await run_operation(
        operation="session.batch_add_messages",
        telemetry=request.telemetry,
        fn=_batch_add,
    )
    return Response(status="ok", result=execution.result, telemetry=execution.telemetry)


@router.post("/{session_id}/used")
async def record_used(
    request: UsedRequest,
    session_id: str = Path(..., description="Session ID"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Record actually used contexts and skills in a session."""
    service = get_service()
    session = await service.sessions.get(session_id, _ctx, auto_create=False)

    # Resolve path variables in contexts
    resolved_contexts = None
    if request.contexts is not None:
        resolved_contexts = [resolve_path_variables(uri) for uri in request.contexts]

    # Resolve path variables in skill URI if present
    resolved_skill = request.skill
    if resolved_skill is not None and "uri" in resolved_skill:
        resolved_skill = dict(resolved_skill)
        resolved_skill["uri"] = resolve_path_variables(resolved_skill["uri"])

    session.used(contexts=resolved_contexts, skill=resolved_skill)
    return Response(
        status="ok",
        result={
            "session_id": session_id,
            "contexts_used": session.stats.contexts_used,
            "skills_used": session.stats.skills_used,
        },
    )
