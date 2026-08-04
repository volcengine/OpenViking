# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Admin endpoints for OpenViking multi-tenant HTTP Server."""

import asyncio

from fastapi import APIRouter, Body, Depends, Path, Request
from pydantic import BaseModel

from openviking.server.account_settings import (
    AccountAgentEvolutionSettings,
    AccountSettings,
    AccountSettingsPatch,
    read_account_settings,
    update_account_settings,
)
from openviking.server.api_keys.models import validate_account_user_role
from openviking.server.auth import (
    get_api_key_manager_or_raise,
    get_request_context,
    require_auth_root,
    require_auth_root_or_admin,
)
from openviking.server.config import ServerConfig, UserConfig
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response
from openviking.server.user_config import validate_add_targets, write_user_config
from openviking.service.legacy_migration import LegacyDataMigration
from openviking.service.task_store import (
    SYSTEM_TASK_ACCOUNT_ID,
    SYSTEM_TASK_USER_ID,
)
from openviking.service.task_tracker import (
    get_task_tracker,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class CreateAccountRequest(BaseModel):
    account_id: str
    admin_user_id: str
    seed: str | None = None
    user_config: UserConfig | None = None


class RegisterUserRequest(BaseModel):
    user_id: str
    role: str = "user"
    seed: str | None = None
    user_config: UserConfig | None = None


class SetRoleRequest(BaseModel):
    role: str


class RegenerateKeyRequest(BaseModel):
    seed: str | None = None


class MigrateLegacyDataRequest(BaseModel):
    action: str = "migrate"


class SetAgentEvolutionRequest(BaseModel):
    enabled: bool


def _agent_evolution_account_id(ctx: RequestContext) -> str:
    if ctx.role == Role.ROOT:
        return get_openviking_config().default_account
    return ctx.account_id


@router.get("/agent-evolution")
@require_auth_root_or_admin
async def get_agent_evolution_status(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
):
    """Return the effective Agent Evolution switch for the caller's account."""
    account_id = _agent_evolution_account_id(ctx)
    _check_account_exists(request, account_id)
    enabled = await get_service().sessions.get_agent_evolution_enabled(account_id)
    return Response(
        status="ok",
        result={"enabled": enabled, "account_id": account_id},
    )


@router.put("/agent-evolution")
@require_auth_root_or_admin
async def set_agent_evolution_status(
    body: SetAgentEvolutionRequest,
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
):
    """Persist and hot-reload Agent Evolution for the caller's account."""
    account_id = _agent_evolution_account_id(ctx)
    _check_account_exists(request, account_id)
    service = get_service()
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    await update_account_settings(
        service.viking_fs,
        account_id,
        AccountSettingsPatch(agent_evolution=AccountAgentEvolutionSettings(enabled=body.enabled)),
    )
    enabled = await service.sessions.get_agent_evolution_enabled(account_id)
    return Response(
        status="ok",
        result={"enabled": enabled, "account_id": account_id},
    )


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


def _check_account_exists(request: Request, account_id: str) -> None:
    manager = getattr(request.app.state, "api_key_manager", None)
    if manager is None:
        return
    accounts = manager.get_accounts()
    if not any(item.get("account_id") == account_id for item in accounts):
        raise NotFoundError(account_id, "account")


async def _account_settings_result(
    account_id: str,
    settings: AccountSettings,
) -> dict:
    enabled = await get_service().sessions.get_agent_evolution_enabled(account_id)
    return {
        "account_id": account_id,
        "settings": {
            "agent_evolution": {
                "enabled": enabled,
            }
        },
        "overrides": settings.model_dump(exclude_none=True),
    }


def _has_add_targets(user_config: UserConfig | None) -> bool:
    return bool(
        user_config and (user_config.add_targets.resource_uri or user_config.add_targets.skill_uri)
    )


def _has_initial_user_config(user_config: UserConfig | None) -> bool:
    return _has_add_targets(user_config)


def _validate_initial_user_config(
    service,
    user_ctx: RequestContext,
    user_config: UserConfig | None,
) -> None:
    if not _has_add_targets(user_config):
        return
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    validate_add_targets(
        user_config.add_targets,
        ctx=user_ctx,
        viking_fs=service.viking_fs,
    )


async def _write_initial_user_config(
    service,
    user_ctx: RequestContext,
    user_config: UserConfig | None,
) -> None:
    if not _has_initial_user_config(user_config):
        return
    await write_user_config(service.viking_fs, user_ctx, user_config)


async def _run_legacy_migration_task(
    task_id: str,
    migration: LegacyDataMigration,
    *,
    action: str,
    account_id: str,
    user_id: str,
) -> None:
    tracker = get_task_tracker()
    await tracker.start(task_id, account_id=account_id, user_id=user_id, stage="running")
    try:
        if action == "cleanup":
            result = await migration.cleanup()
        else:
            result = await migration.run()
    except Exception as exc:
        await tracker.fail(task_id, str(exc), account_id=account_id, user_id=user_id)
        logger.exception("Legacy %s task %s failed", action, task_id)
        return
    await tracker.complete(task_id, result, account_id=account_id, user_id=user_id)


# ---- Account endpoints ----


@router.post("/accounts")
@require_auth_root
async def create_account(
    body: CreateAccountRequest,
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
):
    """Create a new account (workspace) with its first admin user."""
    service = get_service()
    account_ctx = RequestContext(
        user=UserIdentifier(body.account_id, body.admin_user_id),
        role=Role.ADMIN,
    )
    _validate_initial_user_config(service, account_ctx, body.user_config)
    manager = _get_api_key_manager(request)
    user_key = await manager.create_account(
        body.account_id,
        body.admin_user_id,
        seed=body.seed,
    )
    await service.initialize_account_directories(account_ctx)
    await service.initialize_user_directories(account_ctx)
    await _write_initial_user_config(service, account_ctx, body.user_config)
    result = {
        "account_id": body.account_id,
        "admin_user_id": body.admin_user_id,
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


@router.post("/migrate")
@require_auth_root
async def migrate_legacy_data(
    request: Request,
    body: MigrateLegacyDataRequest | None = None,
    ctx: RequestContext = Depends(get_request_context),
):
    """Preflight and enqueue legacy agent/session data migration or cleanup."""
    manager = _get_api_key_manager(request)
    service = get_service()
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    action = (body.action if body else "migrate").strip().lower()
    if action not in {"migrate", "cleanup"}:
        raise InvalidArgumentError("Migration action must be 'migrate' or 'cleanup'.")

    migration = LegacyDataMigration(
        viking_fs=service.viking_fs,
        api_key_manager=manager,
        service=service,
    )
    if action == "migrate":
        plan = await migration.preflight()
        if plan.errors:
            raise FailedPreconditionError(
                "Legacy migration preflight failed.",
                details=plan.to_preflight_result(),
            )

    tracker = get_task_tracker()
    task_type = "legacy_cleanup" if action == "cleanup" else "legacy_migration"
    resource_id = "legacy-data-cleanup" if action == "cleanup" else "legacy-data"
    task = await tracker.create(
        task_type,
        resource_id=resource_id,
        account_id=SYSTEM_TASK_ACCOUNT_ID,
        user_id=SYSTEM_TASK_USER_ID,
    )
    asyncio.create_task(
        _run_legacy_migration_task(
            task.task_id,
            migration,
            action=action,
            account_id=SYSTEM_TASK_ACCOUNT_ID,
            user_id=SYSTEM_TASK_USER_ID,
        )
    )
    return Response(status="ok", result={"task_id": task.task_id})


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
        user=UserIdentifier(account_id, "system"),
        role=Role.ROOT,
    )

    # Cascade: remove AGFS data for the account
    viking_fs = get_viking_fs()
    account_prefixes = [
        "viking://user/",
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


@router.get("/accounts/{account_id}/settings")
@require_auth_root_or_admin
async def get_account_settings(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Return effective and explicitly overridden settings for one account."""
    _check_account_access(ctx, account_id)
    _check_account_exists(request, account_id)
    service = get_service()
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    settings = await read_account_settings(service.viking_fs, account_id)
    return Response(
        status="ok",
        result=await _account_settings_result(account_id, settings),
    )


@router.patch("/accounts/{account_id}/settings")
@require_auth_root_or_admin
async def patch_account_settings(
    body: AccountSettingsPatch,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Update allowlisted hot-reloadable settings for one account."""
    _check_account_access(ctx, account_id)
    _check_account_exists(request, account_id)
    service = get_service()
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    settings = await update_account_settings(service.viking_fs, account_id, body)
    return Response(
        status="ok",
        result=await _account_settings_result(account_id, settings),
    )


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
    resolved_role = validate_account_user_role(body.role)
    service = get_service()
    user_ctx = RequestContext(
        user=UserIdentifier(account_id, body.user_id),
        role=resolved_role,
    )
    _validate_initial_user_config(service, user_ctx, body.user_config)
    manager = _get_api_key_manager(request)
    user_key = await manager.register_user(
        account_id,
        body.user_id,
        str(resolved_role),
        seed=body.seed,
    )
    await service.initialize_user_directories(user_ctx)
    await _write_initial_user_config(service, user_ctx, body.user_config)
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
    limit: int = 100,
    name: str | None = None,
    role: str | None = None,
    ctx: RequestContext = Depends(get_request_context),
):
    """List all users in an account."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    expose_key = _should_expose_user_key(request)
    users = manager.get_users(
        account_id, limit=limit, name_filter=name, role_filter=role, expose_key=expose_key
    )
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
@require_auth_root_or_admin
async def set_user_role(
    body: SetRoleRequest,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Promote an account user to ADMIN."""
    _check_account_access(ctx, account_id)
    if body.role != Role.ADMIN:
        raise InvalidArgumentError("set_user_role only supports promotion to admin.")
    manager = _get_api_key_manager(request)
    await manager.set_role(account_id, user_id, Role.ADMIN)
    return Response(
        status="ok",
        result={
            "account_id": account_id,
            "user_id": user_id,
            "role": Role.ADMIN,
        },
    )


@router.post("/accounts/{account_id}/users/{user_id}/key")
@require_auth_root_or_admin
async def regenerate_key(
    request: Request,
    body: RegenerateKeyRequest | None = Body(default=None),
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Regenerate a user's API key. Old key is immediately invalidated."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    new_key = await manager.regenerate_key(
        account_id,
        user_id,
        seed=body.seed if body is not None else None,
    )
    return Response(status="ok", result={"user_key": new_key})
