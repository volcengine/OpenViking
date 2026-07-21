"""Compile routes registered on the existing authenticated OpenAPI channel."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status

from vikingbot.compile.models import CompileAccepted, CompileFailure, CompileRequest
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
    ) -> CompileAccepted:
        await channel._prepare_compile_request(http_request, compile_request, auth)
        try:
            return await service.create_task(
                compile_request,
                principal_scope=compile_request._principal_scope,
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


__all__ = ["register_compile_routes"]
