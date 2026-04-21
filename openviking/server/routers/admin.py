# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Admin endpoints for OpenViking multi-tenant HTTP Server."""

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel

from openviking.server.auth import (
    get_api_key_manager_or_raise,
    get_request_context,
    require_auth_root,
    require_auth_root_or_admin,
)
from openviking.server.config import ServerConfig
from openviking.server.dependencies import get_service
from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking.server.models import Response
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class CreateAccountRequest(BaseModel):
    account_id: str
    admin_user_id: str
    isolate_user_scope_by_agent: bool = False
    isolate_agent_scope_by_user: bool = False


class RegisterUserRequest(BaseModel):
    user_id: str
    role: str = "user"


class SetRoleRequest(BaseModel):
    role: str


def _get_api_key_manager(request: Request):
    """Get APIKeyManager from app state."""
    return get_api_key_manager_or_raise(request)


def _should_expose_user_key(request: Request) -> bool:
    config = getattr(request.app.state, "config", None)
    if not isinstance(config, ServerConfig):
        return True
    return config.get_effective_auth_mode() != "trusted"


def _check_account_access(ctx: RequestContext, account_id: str) -> None:
    """ADMIN can only operate on their own account."""
    if ctx.role == Role.ADMIN and ctx.account_id != account_id:
        raise PermissionDeniedError(f"ADMIN can only manage account: {ctx.account_id}")


# ---- Account endpoints ----


@router.post("/accounts")
@require_auth_root
async def create_account(
    body: CreateAccountRequest,
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
):
    """Create a new account (workspace) with its first admin user."""
    manager = _get_api_key_manager(request)
    policy = AccountNamespacePolicy(
        isolate_user_scope_by_agent=body.isolate_user_scope_by_agent,
        isolate_agent_scope_by_user=body.isolate_agent_scope_by_user,
    )
    user_key = await manager.create_account(
        body.account_id,
        body.admin_user_id,
        namespace_policy=policy,
    )
    service = get_service()
    account_ctx = RequestContext(
        user=UserIdentifier(body.account_id, body.admin_user_id, "default"),
        role=Role.ADMIN,
        namespace_policy=policy,
    )
    await service.initialize_account_directories(account_ctx)
    await service.initialize_user_directories(account_ctx)
    result = {
        "account_id": body.account_id,
        "admin_user_id": body.admin_user_id,
        **policy.to_dict(),
    }
    if _should_expose_user_key(request):
        result["user_key"] = user_key
    return Response(status="ok", result=result)


@router.get("/accounts")
@require_auth_root
async def list_accounts(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
):
    """List all accounts."""
    manager = _get_api_key_manager(request)
    accounts = manager.get_accounts()
    return Response(status="ok", result=accounts)


@router.delete("/accounts/{account_id}")
@require_auth_root
async def delete_account(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
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
    return Response(status="ok", result={"deleted": True})


# ---- User endpoints ----


@router.post("/accounts/{account_id}/users")
@require_auth_root_or_admin
async def register_user(
    body: RegisterUserRequest,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Register a new user in an account."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    user_key = await manager.register_user(account_id, body.user_id, body.role)
    service = get_service()
    user_ctx = RequestContext(
        user=UserIdentifier(account_id, body.user_id, "default"),
        role=Role.USER,
        namespace_policy=manager.get_account_policy(account_id),
    )
    await service.initialize_user_directories(user_ctx)
    result = {
        "account_id": account_id,
        "user_id": body.user_id,
    }
    if _should_expose_user_key(request):
        result["user_key"] = user_key
    return Response(status="ok", result=result)


@router.get("/accounts/{account_id}/users")
@require_auth_root_or_admin
async def list_users(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """List all users in an account."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    users = manager.get_users(account_id)
    return Response(status="ok", result=users)


@router.delete("/accounts/{account_id}/users/{user_id}")
@require_auth_root_or_admin
async def remove_user(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Remove a user from an account."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    await manager.remove_user(account_id, user_id)
    return Response(status="ok", result={"deleted": True})


@router.put("/accounts/{account_id}/users/{user_id}/role")
@require_auth_root
async def set_user_role(
    body: SetRoleRequest,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
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
@require_auth_root_or_admin
async def regenerate_key(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Regenerate a user's API key. Old key is immediately invalidated."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    new_key = await manager.regenerate_key(account_id, user_id)
    return Response(status="ok", result={"user_key": new_key})
