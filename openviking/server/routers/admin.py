# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Admin endpoints for OpenViking multi-tenant HTTP Server."""

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel

from openviking.server.auth import require_role
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response
from openviking_cli.exceptions import PermissionDeniedError

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


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
        raise PermissionDeniedError("Admin API requires root_api_key to be configured")
    return manager


def _check_account_access(ctx: RequestContext, account_id: str) -> None:
    """ADMIN can only operate on their own account."""
    if ctx.role == Role.ADMIN and ctx.account_id != account_id:
        raise PermissionDeniedError(f"ADMIN can only manage account: {ctx.account_id}")


# ---- Account endpoints ----


@router.post("/accounts")
async def create_account(
    body: CreateAccountRequest,
    request: Request,
    ctx: RequestContext = require_role(Role.ROOT),
):
    """Create a new account (workspace) with its first admin user."""
    manager = _get_api_key_manager(request)
    user_key = await manager.create_account(body.account_id, body.admin_user_id)
    return Response(
        status="ok",
        result={
            "account_id": body.account_id,
            "admin_user_id": body.admin_user_id,
            "user_key": user_key,
        },
    )


@router.get("/accounts")
async def list_accounts(
    request: Request,
    ctx: RequestContext = require_role(Role.ROOT),
):
    """List all accounts."""
    manager = _get_api_key_manager(request)
    accounts = manager.get_accounts()
    return Response(status="ok", result=accounts)


@router.delete("/accounts/{account_id}")
async def delete_account(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = require_role(Role.ROOT),
):
    """Delete an account."""
    manager = _get_api_key_manager(request)
    await manager.delete_account(account_id)
    return Response(status="ok", result={"deleted": True})


# ---- User endpoints ----


@router.post("/accounts/{account_id}/users")
async def register_user(
    body: RegisterUserRequest,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Register a new user in an account."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    user_key = await manager.register_user(account_id, body.user_id, body.role)
    return Response(
        status="ok",
        result={
            "account_id": account_id,
            "user_id": body.user_id,
            "user_key": user_key,
        },
    )


@router.get("/accounts/{account_id}/users")
async def list_users(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """List all users in an account."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    users = manager.get_users(account_id)
    return Response(status="ok", result=users)


@router.delete("/accounts/{account_id}/users/{user_id}")
async def remove_user(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Remove a user from an account."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    await manager.remove_user(account_id, user_id)
    return Response(status="ok", result={"deleted": True})


@router.put("/accounts/{account_id}/users/{user_id}/role")
async def set_user_role(
    body: SetRoleRequest,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = require_role(Role.ROOT),
):
    """Change a user's role (ROOT only)."""
    manager = _get_api_key_manager(request)
    await manager.set_role(account_id, user_id, body.role)
    return Response(
        status="ok",
        result={
            "account_id": account_id,
            "user_id": user_id,
            "role": body.role,
        },
    )


@router.post("/accounts/{account_id}/users/{user_id}/key")
async def regenerate_key(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Regenerate a user's API key. Old key is immediately invalidated."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    new_key = await manager.regenerate_key(account_id, user_id)
    return Response(status="ok", result={"user_key": new_key})
