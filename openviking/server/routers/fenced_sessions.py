# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Protocol-v2 PostgreSQL outbox API for Alice fenced session writes."""

from __future__ import annotations

import asyncio
import math
import os
import re
from dataclasses import replace
from datetime import timezone
from typing import Any, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Path, Request
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError, field_validator, model_validator

from openviking.core.path_variables import resolve_path_variables
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.fenced_operation import (
    FencedOperationConflict,
    FencedOperationEnvelope,
    operation_digest,
    stable_message_id,
)
from openviking.server.fenced_postgres import (
    FencedOperationRecord,
    PostgresFencedOperationQueue,
    fencing_database_url,
    fencing_service_token,
    is_valid_alice_service_token,
    validate_postgres_fencing_schema,
    validate_required_fencing_configuration,
)
from openviking.server.fenced_writer import (
    FencedCommitWorkItem,
    FencedOutboxItem,
    PermanentFencedEffectError,
    PostgresFencedWriterPool,
)
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response
from openviking.server.routers.sessions import (
    AddMessageRequest,
    _resolve_message_parts,
    _resolve_message_peer_id,
)
from openviking_cli.exceptions import (
    AlreadyExistsError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    UnauthenticatedError,
    UnavailableError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_ALICE_SESSION_ID_PATTERN = r"^alice_[0-9a-f]{48}$"
_ALICE_SESSION_ID = re.compile(_ALICE_SESSION_ID_PATTERN)


def is_reserved_alice_session_id(session_id: str) -> bool:
    return bool(_ALICE_SESSION_ID.fullmatch(session_id))


async def require_alice_fenced_principal(
    x_openviking_alice_token: Optional[str] = Header(
        None,
        alias="X-OpenViking-Alice-Token",
    ),
) -> None:
    """Authenticate the fixed Alice principal in observe and required modes."""
    if not fencing_service_token():
        raise UnavailableError("Alice fenced writer", "fencing_unconfigured")
    if not is_valid_alice_service_token(x_openviking_alice_token):
        raise UnauthenticatedError("Valid X-OpenViking-Alice-Token is required")


router = APIRouter(
    prefix="/api/v1/fenced",
    tags=["fenced-sessions"],
    dependencies=[Depends(require_alice_fenced_principal)],
)


class FencedCreateSessionRequest(FencedOperationEnvelope):
    session_id: str = Field(
        min_length=54,
        max_length=54,
        pattern=_ALICE_SESSION_ID_PATTERN,
    )
    memory_policy: Optional[dict[str, Any]] = None


class FencedAddMessageRequest(FencedOperationEnvelope):
    role: Literal["user", "assistant"]
    peer_id: Optional[str] = None
    content: Optional[str] = Field(default=None, max_length=2_000_000)
    parts: Optional[list[dict[str, Any]]] = Field(default=None, max_length=100)
    created_at: Optional[str] = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_content_or_parts(self) -> "FencedAddMessageRequest":
        if self.content is None and self.parts is None:
            raise ValueError("Either 'content' or 'parts' must be provided")
        return self


class FencedUsedRequest(FencedOperationEnvelope):
    contexts: Optional[list[str]] = Field(default=None, max_length=1000)
    skill: Optional[dict[str, Any]] = None

    @field_validator("contexts")
    @classmethod
    def validate_contexts(cls, contexts: Optional[list[str]]) -> Optional[list[str]]:
        if contexts is None:
            return None
        for uri in contexts:
            if not isinstance(uri, str) or not uri.strip() or len(uri) > 4096:
                raise ValueError(
                    "context URIs must be non-empty strings of at most 4096 chars"
                )
        return contexts


class FencedCommitRequest(FencedOperationEnvelope):
    keep_recent_count: int = Field(default=0, ge=0, le=10_000)
    wait: bool = False


_writer_pool: Optional[PostgresFencedWriterPool] = None
_writer_schema_validated = False
_writer_draining = False


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


def _float_env(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


def _drain_timeout_seconds(phase2_timeout_seconds: float) -> float:
    name = "OPENVIKING_FENCED_DRAIN_TIMEOUT_SECONDS"
    raw = os.getenv(name)
    if raw is None:
        return phase2_timeout_seconds + 60.0
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    minimum = phase2_timeout_seconds + 60.0
    if not math.isfinite(value) or value < minimum or value > 10_800.0:
        raise RuntimeError(
            f"{name} must be between {minimum:g} and 10800 seconds"
        )
    return value


def fenced_writer_runtime_status() -> dict[str, Any]:
    pool = _writer_pool
    requested = bool(fencing_database_url() or fencing_service_token())
    configured = bool(
        _writer_schema_validated and pool is not None and pool.healthy
    )
    return {
        "requested": requested,
        "configured": configured,
        "schema_validated": _writer_schema_validated,
        "healthy": bool(pool is not None and pool.healthy),
        "draining": _writer_draining,
        "effect_concurrency": pool.effect_concurrency if pool else 0,
        "commit_concurrency": pool.commit_concurrency if pool else 0,
    }


async def start_fenced_writer_runtime() -> bool:
    """Validate the shared schema and start the supervised in-process pool."""
    global _writer_pool, _writer_schema_validated, _writer_draining
    if _writer_pool is not None:
        return fenced_writer_runtime_status()["configured"]
    from openviking.server.fenced_operation import get_alice_fencing_mode

    mode = get_alice_fencing_mode()
    if not fencing_database_url() or not fencing_service_token():
        _writer_schema_validated = False
        if mode == "required":
            validate_required_fencing_configuration()
        return False

    try:
        # The same dedicated API is enabled in both compatibility modes.  A
        # short token, invalid timeout, missing driver, bad DSN, or incomplete
        # migration must therefore never be advertised as configured.
        validate_required_fencing_configuration()
        from openviking.session.session import fenced_phase2_timeout_seconds

        phase2_timeout_seconds = fenced_phase2_timeout_seconds()
        drain_timeout_seconds = _drain_timeout_seconds(
            phase2_timeout_seconds
        )
        await validate_postgres_fencing_schema()
    except Exception:
        _writer_schema_validated = False
        if mode == "required":
            raise
        logger.exception(
            "Alice fenced writer is unavailable in observe mode"
        )
        return False
    _writer_schema_validated = True
    _writer_draining = False
    pool = PostgresFencedWriterPool(
        execute_fenced_outbox_item,
        task_waiter=execute_fenced_commit_work,
        concurrency=_int_env(
            "OPENVIKING_FENCED_EFFECT_CONCURRENCY", 2, minimum=1, maximum=64
        ),
        commit_concurrency=_int_env(
            "OPENVIKING_FENCED_COMMIT_CONCURRENCY", 2, minimum=1, maximum=64
        ),
        poll_seconds=_float_env(
            "OPENVIKING_FENCED_POLL_SECONDS", 0.25, minimum=0.05, maximum=5.0
        ),
        max_idle_seconds=_float_env(
            "OPENVIKING_FENCED_MAX_IDLE_SECONDS", 1.0, minimum=0.1, maximum=30.0
        ),
        # Strict startup validation above guarantees that a normal shutdown
        # outlives the whole Phase 2 wall-clock deadline plus a safety margin.
        drain_timeout_seconds=drain_timeout_seconds,
    )
    pool.start()
    await asyncio.sleep(0)
    if not pool.healthy:
        await pool.stop(drain_timeout_seconds=0)
        _writer_schema_validated = False
        if mode == "required":
            raise RuntimeError("Fenced writer pool failed during startup")
        logger.error("Alice fenced writer pool failed during observe startup")
        return False
    _writer_pool = pool
    return True


async def stop_fenced_writer_runtime() -> None:
    global _writer_pool, _writer_schema_validated, _writer_draining
    pool, _writer_pool = _writer_pool, None
    if pool is None:
        _writer_schema_validated = False
        return
    _writer_draining = True
    try:
        await pool.stop()
    finally:
        _writer_draining = False
        _writer_schema_validated = False


def _ctx_for_outbox(
    account_id: str,
    user_id: str,
    actor_peer_id: Optional[str],
) -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id, user_id),
        role=Role.USER,
        actor_peer_id=actor_peer_id,
    )


def _validated_request(item: FencedOutboxItem) -> FencedOperationEnvelope:
    payload = dict(item.request_payload)
    immutable_identity = {
        "writer": item.writer,
        "session_scope_id": item.session_scope_id,
        "turn_id": item.turn_id,
        "operation_id": item.operation_id,
    }
    if any(payload.get(key) != value for key, value in immutable_identity.items()):
        raise PermanentFencedEffectError(
            "outbox_identity_corrupt",
            details={"operation_id": item.operation_id},
        )
    payload["writer"] = "alice"
    payload["fencing_token"] = item.fencing_token
    request_type: type[FencedOperationEnvelope]
    if item.operation == "create":
        request_type = FencedCreateSessionRequest
    elif item.operation == "message":
        request_type = FencedAddMessageRequest
    elif item.operation == "used":
        request_type = FencedUsedRequest
    elif item.operation == "commit":
        request_type = FencedCommitRequest
    else:
        raise PermanentFencedEffectError(
            "unsupported_fenced_operation",
            details={"operation_id": item.operation_id},
        )
    try:
        request = request_type.model_validate(payload)
    except ValidationError as exc:
        raise PermanentFencedEffectError(
            "outbox_payload_invalid",
            details={"operation_id": item.operation_id},
        ) from exc
    if operation_digest(
        item.operation,
        request,
        resource_id=item.resource_id,
        actor_peer_id=item.actor_peer_id,
    ) != item.digest:
        raise PermanentFencedEffectError(
            "outbox_digest_mismatch",
            details={"operation_id": item.operation_id},
        )
    return request


_SAFE_SESSION_READ_FAILURES = frozenset(
    {
        "session_messages_missing",
        "session_messages_corrupt",
        "session_meta_missing",
        "session_meta_corrupt",
        "usage_journal_corrupt",
    }
)


def _permanent_session_read_error(exc: FailedPreconditionError) -> None:
    reason = str((getattr(exc, "details", None) or {}).get("reason") or "")
    if reason in _SAFE_SESSION_READ_FAILURES:
        raise PermanentFencedEffectError(reason) from exc


async def _get_effect_session(
    service: Any,
    item: FencedOutboxItem,
    ctx: RequestContext,
) -> Any:
    try:
        return await service.sessions.get(
            item.resource_id, ctx, auto_create=False, strict=True
        )
    except NotFoundError as exc:
        raise PermanentFencedEffectError(
            "session_not_found",
            details={"session_id": item.resource_id},
        ) from exc
    except FailedPreconditionError as exc:
        _permanent_session_read_error(exc)
        raise


async def execute_fenced_outbox_item(
    item: FencedOutboxItem,
) -> dict[str, Any]:
    """Execute one claimed effect using only its durable outbox envelope."""
    if not is_reserved_alice_session_id(item.resource_id):
        raise PermanentFencedEffectError(
            "alice_session_id_required",
            details={"reason": "alice_session_id_required"},
        )
    request = _validated_request(item)
    service = get_service()
    ctx = _ctx_for_outbox(item.account_id, item.user_id, item.actor_peer_id)
    await service.initialize_user_directories(ctx)

    if item.operation == "create":
        assert isinstance(request, FencedCreateSessionRequest)
        if request.session_id != item.resource_id:
            raise PermanentFencedEffectError("outbox_resource_identity_corrupt")
        try:
            session = await service.sessions.get(
                item.resource_id, ctx, auto_create=False, strict=True
            )
        except NotFoundError:
            try:
                session = await service.sessions.create(
                    ctx,
                    item.resource_id,
                    memory_policy=request.memory_policy,
                    strict=True,
                )
            except AlreadyExistsError:
                session = await _get_effect_session(service, item, ctx)
        except FailedPreconditionError as exc:
            _permanent_session_read_error(exc)
            raise
        return {
            "session_id": session.session_id,
            "uri": session.uri,
            "user": session.user.to_dict(),
        }

    if item.operation == "message":
        assert isinstance(request, FencedAddMessageRequest)
        session = await _get_effect_session(service, item, ctx)
        compatibility_request = AddMessageRequest.model_validate(
            {
                "role": request.role,
                "peer_id": request.peer_id,
                "content": request.content,
                "parts": request.parts,
                "created_at": request.created_at,
            }
        )
        message_id = stable_message_id(request)
        already_present = any(
            message.id == message_id for message in session.messages
        )
        created_at = request.created_at or item.submitted_at.astimezone(
            timezone.utc
        ).isoformat()
        messages = session.add_messages(
            [
                {
                    "id": message_id,
                    "role": request.role,
                    "parts": _resolve_message_parts(compatibility_request),
                    "peer_id": _resolve_message_peer_id(
                        compatibility_request, ctx
                    ),
                    "created_at": created_at,
                }
            ]
        )
        return {
            "session_id": item.resource_id,
            "message_id": message_id,
            "message_count": len(session.messages),
            "added": 0 if already_present else len(messages),
        }

    if item.operation == "used":
        assert isinstance(request, FencedUsedRequest)
        session = await _get_effect_session(service, item, ctx)
        contexts = (
            [resolve_path_variables(uri) for uri in request.contexts]
            if request.contexts is not None
            else None
        )
        skill = request.skill
        if skill is not None and "uri" in skill:
            skill = dict(skill)
            skill["uri"] = resolve_path_variables(str(skill["uri"]))
        session.used(
            contexts=contexts,
            skill=skill,
            operation_id=request.operation_id,
        )
        await session.persist_usage_records()
        return {
            "session_id": item.resource_id,
            "contexts_used": session.stats.contexts_used,
            "skills_used": session.stats.skills_used,
        }

    if item.operation == "commit":
        assert isinstance(request, FencedCommitRequest)
        await _get_effect_session(service, item, ctx)
        result = await service.sessions.commit_async(
            item.resource_id,
            ctx,
            keep_recent_count=request.keep_recent_count,
            operation_id=request.operation_id,
            operation_sequence_id=item.sequence_id,
        )
        session = await service.sessions.get(
            item.resource_id, ctx, auto_create=False, strict=True
        )
        session.consume_usage_records()
        await session.persist_usage_records()
        return result

    raise PermanentFencedEffectError("unsupported_fenced_operation")


async def execute_fenced_commit_work(item: FencedCommitWorkItem) -> str:
    service = get_service()
    ctx = _ctx_for_outbox(item.account_id, item.user_id, None)
    return await service.sessions.run_fenced_commit_work(
        item.session_id,
        ctx,
        operation_id=item.operation_id,
        task_id=item.task_id,
        archive_uri=item.archive_uri,
    )


def _ensure_runtime_configured() -> None:
    if not fenced_writer_runtime_status()["configured"]:
        raise UnavailableError("Alice fenced writer", "fencing_unconfigured")


def _post_poll_seconds() -> float:
    return _float_env(
        "OPENVIKING_FENCED_POST_POLL_SECONDS",
        0.0,
        minimum=0.0,
        maximum=5.0,
    )


def _record_payload(record: FencedOperationRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "operation_id": record.operation_id,
        "status": record.state,
        "replayed": record.replayed,
        "status_url": (
            "/api/v1/fenced/operations/"
            f"{quote(record.operation_id, safe='')}"
        ),
    }
    if record.state == "completed":
        # Keep the domain result nested.  Alice treats the operation receipt as
        # a protocol object and the effect result as a separate payload.
        payload["result"] = dict(record.result or {})
    return payload


def _record_response(record: FencedOperationRecord) -> JSONResponse:
    if record.state in {"queued", "running"}:
        return JSONResponse(
            status_code=202,
            content=Response(
                status="ok", result=_record_payload(record)
            ).model_dump(mode="json"),
        )
    if record.state == "completed":
        return JSONResponse(
            status_code=200,
            content=Response(
                status="ok", result=_record_payload(record)
            ).model_dump(mode="json"),
        )
    if record.state in {"stale", "conflict"}:
        error = record.error or {}
        details = error.get("details") if isinstance(error, dict) else {}
        reason = (
            details.get("reason")
            if isinstance(details, dict)
            else "fenced_conflict"
        )
        raise FencedOperationConflict(
            "Fenced operation was rejected",
            reason=str(reason or "fenced_conflict"),
            details=details if isinstance(details, dict) else None,
        )
    raise UnavailableError("Fenced session effect", "effect_failed")


async def _submit(
    request: FencedOperationEnvelope,
    operation: str,
    session_id: str,
    ctx: RequestContext,
) -> JSONResponse:
    _ensure_runtime_configured()
    record = await PostgresFencedOperationQueue(ctx, request).submit(
        operation,
        session_id,
    )
    submitted_replayed = record.replayed
    if not record.terminal and _post_poll_seconds() > 0:
        waited = await PostgresFencedOperationQueue.wait(
            ctx,
            request.operation_id,
            timeout_seconds=_post_poll_seconds(),
        )
        if waited is not None:
            # Status polling is not a replay.  Keep the duplicate-submission
            # bit returned by the atomic submit transaction.
            record = replace(waited, replayed=submitted_replayed)
    return _record_response(record)


@router.post("/sessions")
async def create_session(
    request: FencedCreateSessionRequest,
    ctx: RequestContext = Depends(get_request_context),
) -> JSONResponse:
    return await _submit(request, "create", request.session_id, ctx)


@router.post("/sessions/{session_id}/messages")
async def add_message(
    request: FencedAddMessageRequest,
    session_id: str = Path(
        ...,
        min_length=54,
        max_length=54,
        pattern=_ALICE_SESSION_ID_PATTERN,
    ),
    ctx: RequestContext = Depends(get_request_context),
) -> JSONResponse:
    return await _submit(request, "message", session_id, ctx)


@router.post("/sessions/{session_id}/used")
async def record_used(
    request: FencedUsedRequest,
    session_id: str = Path(
        ...,
        min_length=54,
        max_length=54,
        pattern=_ALICE_SESSION_ID_PATTERN,
    ),
    ctx: RequestContext = Depends(get_request_context),
) -> JSONResponse:
    return await _submit(request, "used", session_id, ctx)


@router.post("/sessions/{session_id}/commit")
async def commit_session(
    request: FencedCommitRequest,
    session_id: str = Path(
        ...,
        min_length=54,
        max_length=54,
        pattern=_ALICE_SESSION_ID_PATTERN,
    ),
    ctx: RequestContext = Depends(get_request_context),
) -> JSONResponse:
    return await _submit(request, "commit", session_id, ctx)


@router.get("/operations/{operation_id:path}")
async def get_operation(
    http_request: Request,
    operation_id: str = Path(..., min_length=1, max_length=256),
    ctx: RequestContext = Depends(get_request_context),
) -> JSONResponse:
    _ensure_runtime_configured()
    if http_request.query_params:
        raise InvalidArgumentError(
            "Fenced operation status does not accept query parameters"
        )
    record = await PostgresFencedOperationQueue.get(ctx, operation_id)
    if record is None:
        raise NotFoundError(operation_id, "fenced operation")
    return _record_response(record)
