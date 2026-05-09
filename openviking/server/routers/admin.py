# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Admin endpoints for OpenViking multi-tenant HTTP Server."""

from typing import List

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response
from openviking.server.schemas import ExcludeNoneRoute
from openviking.server.schemas.admin import (
    AccountListItem,
    CreateAccountResult,
    DeletedFlagResult,
    RegeneratedKeyResult,
    RegisterUserResult,
    SetUserRoleResult,
    UserListItem,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    route_class=ExcludeNoneRoute,
)

_TRUSTED_MODE_ADMIN_API_MESSAGE = (
    "Admin API is unavailable in trusted mode. In trusted mode, each request is resolved as USER "
    "from X-OpenViking-Account/X-OpenViking-User headers and does not use user-key "
    "registration. Switch to api_key mode with root_api_key for account and user management."
)

_DEV_MODE_ADMIN_API_MESSAGE = (
    "Admin API requires api_key mode with root_api_key configured. Development mode does not "
    "support account or user management."
)


class CreateAccountRequest(BaseModel):
    account_id: str
    admin_user_id: str


class RegisterUserRequest(BaseModel):
    user_id: str
    role: str = "user"


class SetRoleRequest(BaseModel):
    role: str


def _get_api_key_manager(request: Request):
    """Get APIKeyManager from app state."""
    manager = getattr(request.app.state, "api_key_manager", None)
    if manager is None:
        raise PermissionDeniedError(_DEV_MODE_ADMIN_API_MESSAGE)
    return manager


def require_admin_role(*allowed_roles: Role):
    """Dependency factory for Admin API routes with mode-aware errors."""

    async def _check(
        request: Request,
        ctx: RequestContext = Depends(get_request_context),
    ) -> RequestContext:
        config = getattr(request.app.state, "config", None)
        auth_mode = getattr(config, "auth_mode", "api_key")
        if auth_mode == "trusted":
            raise PermissionDeniedError(_TRUSTED_MODE_ADMIN_API_MESSAGE)

        manager = getattr(request.app.state, "api_key_manager", None)
        if manager is None:
            raise PermissionDeniedError(_DEV_MODE_ADMIN_API_MESSAGE)

        if ctx.role not in allowed_roles:
            raise PermissionDeniedError(
                f"Requires role: {', '.join(r.value for r in allowed_roles)}"
            )
        return ctx

    return Depends(_check)


def _check_account_access(ctx: RequestContext, account_id: str) -> None:
    """ADMIN can only operate on their own account."""
    if ctx.role == Role.ADMIN and ctx.account_id != account_id:
        raise PermissionDeniedError(f"ADMIN can only manage account: {ctx.account_id}")


# ---- Account endpoints ----


@router.post("/accounts", response_model=Response[CreateAccountResult])
async def create_account(
    body: CreateAccountRequest,
    request: Request,
    ctx: RequestContext = require_admin_role(Role.ROOT),
) -> Response[CreateAccountResult]:
    """Create a new account (workspace) with its first admin user."""
    manager = _get_api_key_manager(request)
    user_key = await manager.create_account(body.account_id, body.admin_user_id)
    service = get_service()
    account_ctx = RequestContext(
        user=UserIdentifier(body.account_id, body.admin_user_id, "default"),
        role=Role.ADMIN,
    )
    await service.initialize_account_directories(account_ctx)
    await service.initialize_user_directories(account_ctx)
    return Response(
        status="ok",
        result=CreateAccountResult(
            account_id=body.account_id,
            admin_user_id=body.admin_user_id,
            user_key=user_key,
        ),
    )


@router.get("/accounts", response_model=Response[List[AccountListItem]])
async def list_accounts(
    request: Request,
    ctx: RequestContext = require_admin_role(Role.ROOT),
) -> Response[List[AccountListItem]]:
    """List all accounts."""
    manager = _get_api_key_manager(request)
    accounts = manager.get_accounts()
    return Response(
        status="ok",
        result=[AccountListItem.model_validate(item) for item in accounts],
    )


@router.delete("/accounts/{account_id}", response_model=Response[DeletedFlagResult])
async def delete_account(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = require_admin_role(Role.ROOT),
) -> Response[DeletedFlagResult]:
    """Delete an account and cascade-clean its storage (AGFS + VectorDB)."""
    manager = _get_api_key_manager(request)

    # Build a ROOT-level context scoped to the target account for cleanup
    cleanup_ctx = RequestContext(
        user=UserIdentifier(account_id, "system", "system"),
        role=Role.ROOT,
    )

    # Cascade: remove AGFS data for the account
    viking_fs = get_viking_fs()
    account_prefixes = [
        "viking://user/",
        "viking://agent/",
        "viking://session/",
        "viking://resources/",
    ]
    for prefix in account_prefixes:
        try:
            await viking_fs.rm(prefix, recursive=True, ctx=cleanup_ctx)
        except Exception as e:
            logger.warning(f"AGFS cleanup for {prefix} in account {account_id}: {e}")

    # Cascade: remove VectorDB records for the account
    try:
        storage = viking_fs._get_vector_store()
        if storage:
            deleted = await storage.delete_account_data(account_id)
            logger.info(f"VectorDB cascade delete for account {account_id}: {deleted} records")
    except Exception as e:
        logger.warning(f"VectorDB cleanup for account {account_id}: {e}")

    # Finally delete the account metadata
    await manager.delete_account(account_id)
    return Response(status="ok", result=DeletedFlagResult(deleted=True))


# ---- User endpoints ----


@router.post(
    "/accounts/{account_id}/users",
    response_model=Response[RegisterUserResult],
)
async def register_user(
    body: RegisterUserRequest,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = require_admin_role(Role.ROOT, Role.ADMIN),
) -> Response[RegisterUserResult]:
    """Register a new user in an account."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    user_key = await manager.register_user(account_id, body.user_id, body.role)
    service = get_service()
    user_ctx = RequestContext(
        user=UserIdentifier(account_id, body.user_id, "default"),
        role=Role.USER,
    )
    await service.initialize_user_directories(user_ctx)
    return Response(
        status="ok",
        result=RegisterUserResult(
            account_id=account_id,
            user_id=body.user_id,
            user_key=user_key,
        ),
    )


@router.get(
    "/accounts/{account_id}/users",
    response_model=Response[List[UserListItem]],
)
async def list_users(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = require_admin_role(Role.ROOT, Role.ADMIN),
) -> Response[List[UserListItem]]:
    """List all users in an account."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    users = manager.get_users(account_id)
    return Response(
        status="ok",
        result=[UserListItem.model_validate(item) for item in users],
    )


@router.delete(
    "/accounts/{account_id}/users/{user_id}",
    response_model=Response[DeletedFlagResult],
)
async def remove_user(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = require_admin_role(Role.ROOT, Role.ADMIN),
) -> Response[DeletedFlagResult]:
    """Remove a user from an account."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    await manager.remove_user(account_id, user_id)
    return Response(status="ok", result=DeletedFlagResult(deleted=True))


@router.put(
    "/accounts/{account_id}/users/{user_id}/role",
    response_model=Response[SetUserRoleResult],
)
async def set_user_role(
    body: SetRoleRequest,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = require_admin_role(Role.ROOT),
) -> Response[SetUserRoleResult]:
    """Change a user's role (ROOT only)."""
    manager = _get_api_key_manager(request)
    await manager.set_role(account_id, user_id, body.role)
    return Response(
        status="ok",
        result=SetUserRoleResult(
            account_id=account_id,
            user_id=user_id,
            role=body.role,
        ),
    )


@router.post(
    "/accounts/{account_id}/users/{user_id}/key",
    response_model=Response[RegeneratedKeyResult],
)
async def regenerate_key(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = require_admin_role(Role.ROOT, Role.ADMIN),
) -> Response[RegeneratedKeyResult]:
    """Regenerate a user's API key. Old key is immediately invalidated."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    new_key = await manager.regenerate_key(account_id, user_id)
    return Response(status="ok", result=RegeneratedKeyResult(user_key=new_key))
