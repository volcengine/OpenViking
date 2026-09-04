"""Compile routes registered on the existing authenticated OpenAPI channel."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from vikingbot.compile.models import (
    CompileAccepted,
    CompileFailure,
    CompileRequest,
    CompileSessionRequest,
)
from vikingbot.compile.service import BotCompileService

_ERROR_HTTP_STATUS = {
    "INVALID_ARGUMENT": status.HTTP_400_BAD_REQUEST,
    "SKILL_INVALID": status.HTTP_400_BAD_REQUEST,
    "SKILL_CAPABILITY_UNAVAILABLE": status.HTTP_400_BAD_REQUEST,
    "UNAUTHENTICATED": status.HTTP_401_UNAUTHORIZED,
    "PERMISSION_DENIED": status.HTTP_403_FORBIDDEN,
    "NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "RESOURCE_EXHAUSTED": status.HTTP_429_TOO_MANY_REQUESTS,
    "DEADLINE_EXCEEDED": status.HTTP_504_GATEWAY_TIMEOUT,
    "UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _raise_http_failure(exc: CompileFailure) -> None:
    raise HTTPException(
        status_code=_ERROR_HTTP_STATUS.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


def register_compile_routes(
    router: APIRouter,
    *,
    channel: Any,
    verify_gateway_request: Callable[..., Awaitable[Any]],
    service: BotCompileService,
) -> None:
    """Attach compile endpoints while reusing OpenAPIChannel's auth dependency."""

    @router.post(
        "/compile",
        response_model=CompileAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_compile(
        compile_request: CompileRequest,
        http_request: Request,
        auth: Any = Depends(verify_gateway_request),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> CompileAccepted:
        await channel._prepare_compile_request(http_request, compile_request, auth)
        try:
            return await service.create_task(
                compile_request,
                principal_scope=compile_request._principal_scope,
                task_id=idempotency_key,
            )
        except CompileFailure as exc:
            _raise_http_failure(exc)

    @router.get("/compile/{task_id}")
    async def get_compile(
        task_id: str,
        http_request: Request,
        auth: Any = Depends(verify_gateway_request),
    ) -> dict[str, Any]:
        principal_scope = await channel._resolve_request_principal(http_request, auth)
        task = await service.get_task(task_id, principal_scope=principal_scope)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Compile task not found"},
            )
        return task

    @router.post("/compile/{task_id}/cancel")
    async def cancel_compile(
        task_id: str,
        http_request: Request,
        auth: Any = Depends(verify_gateway_request),
    ) -> dict[str, Any]:
        principal_scope = await channel._resolve_request_principal(http_request, auth)
        task = await service.cancel_task(task_id, principal_scope=principal_scope)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Compile task not found"},
            )
        return task


def register_compile_control_routes(
    router: APIRouter,
    *,
    channel: Any,
    verify_gateway_request: Callable[..., Awaitable[Any]],
    service: BotCompileService,
) -> None:
    """Attach the root-level status and cancellation session endpoints."""

    async def load_session(
        body: CompileSessionRequest,
        http_request: Request,
        auth: Any,
        *,
        cancel: bool,
    ) -> dict[str, Any]:
        principal_scope = await channel._resolve_request_principal(http_request, auth)
        if cancel:
            task = await service.cancel_task(body.session_id, principal_scope=principal_scope)
        else:
            task = await service.get_task(body.session_id, principal_scope=principal_scope)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Compile session not found"},
            )
        task_status = str(task.get("status") or "running")
        response: dict[str, Any] = {
            "status": "pending" if task_status == "accepted" else task_status,
            "stage": f"compile: {task.get('stage') or task_status}",
            "error": task.get("error"),
            "meta": task.get("meta") or {},
        }
        if task.get("result") is not None:
            response["result"] = task["result"]
        return response

    @router.post("/compile/status")
    async def get_compile_status(
        body: CompileSessionRequest,
        http_request: Request,
        auth: Any = Depends(verify_gateway_request),
    ) -> dict[str, Any]:
        return await load_session(body, http_request, auth, cancel=False)

    @router.post("/compile/cancel")
    async def cancel_compile_session(
        body: CompileSessionRequest,
        http_request: Request,
        auth: Any = Depends(verify_gateway_request),
    ) -> dict[str, Any]:
        return await load_session(body, http_request, auth, cancel=True)


__all__ = ["register_compile_control_routes", "register_compile_routes"]
