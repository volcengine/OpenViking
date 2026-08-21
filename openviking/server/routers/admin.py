# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Admin endpoints for OpenViking multi-tenant HTTP Server."""

import asyncio
from typing import Optional

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
from openviking.server.user_config import (
    read_user_config,
    validate_add_targets,
    validate_user_memory_policy,
    write_user_config,
    write_user_memory_policy,
)
from openviking.service.legacy_migration import LegacyDataMigration
from openviking.service.task_store import (
    SYSTEM_TASK_ACCOUNT_ID,
    SYSTEM_TASK_USER_ID,
)
from openviking.service.task_tracker import (
    get_task_tracker,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory_policy import MemoryPolicy
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


class UserSettingsPatch(BaseModel):
    memory_policy: Optional[dict]

    model_config = {"extra": "forbid"}


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
    return bool(_has_add_targets(user_config) or (user_config and user_config.memory_policy))


def _validate_initial_user_config(
    service,
    user_ctx: RequestContext,
    user_config: UserConfig | None,
) -> None:
    if not _has_initial_user_config(user_config):
        return
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    if _has_add_targets(user_config):
        validate_add_targets(
            user_config.add_targets,
            ctx=user_ctx,
            viking_fs=service.viking_fs,
        )
    if user_config is not None:
        validate_user_memory_policy(user_config.memory_policy)


async def _write_initial_user_config(
    service,
    user_ctx: RequestContext,
    user_config: UserConfig | None,
) -> None:
    if not _has_initial_user_config(user_config):
        return
    await write_user_config(service.viking_fs, user_ctx, user_config)


def _check_user_exists(request: Request, account_id: str, user_id: str) -> None:
    manager = _get_api_key_manager(request)
    if not manager.has_user(account_id, user_id):
        raise NotFoundError(user_id, "user")


def _user_settings_result(
    account_id: str,
    user_id: str,
    user_config: UserConfig,
    default_memory_policy: Optional[dict] = None,
) -> dict:
    memory_policy_config = (
        user_config.memory_policy
        if user_config.memory_policy is not None
        else default_memory_policy
    )
    policy = MemoryPolicy.from_dict(memory_policy_config)
    known_memory_types = set(MemoryTypeRegistry().list_names(include_disabled=False))
    policy.validate_memory_types(known_memory_types)
    memory_policy = policy.to_dict()
    if policy.memory_types is None:
        memory_policy["memory_types"] = sorted(known_memory_types)
    return {
        "account_id": account_id,
        "user_id": user_id,
        "memory_policy": memory_policy,
    }


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

    # Cascade: remove AGFS data for the account.
    # Use the raw AGFS path to bypass the VikingFS namespace guard
    # (viking://user is a protected namespace root, not a real directory).
    viking_fs = get_viking_fs()
    try:
        await viking_fs._async_agfs.rm(f"/local/{account_id}", recursive=True)
    except Exception as e:
        logger.warning(f"AGFS cleanup for account {account_id}: {e}")

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


@router.get("/accounts/{account_id}/users/{user_id}/settings")
@require_auth_root_or_admin
async def get_user_settings(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Return the configured and effective memory policy for one User."""
    _check_account_access(ctx, account_id)
    _check_account_exists(request, account_id)
    _check_user_exists(request, account_id, user_id)
    service = get_service()
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    user_ctx = RequestContext(
        user=UserIdentifier(account_id, user_id),
        role=Role.USER,
    )
    user_config = await read_user_config(service.viking_fs, user_ctx)
    return Response(
        status="ok",
        result=_user_settings_result(
            account_id,
            user_id,
            user_config,
            request.app.state.config.user_config_defaults.memory_policy,
        ),
    )


@router.patch("/accounts/{account_id}/users/{user_id}/settings")
@require_auth_root_or_admin
async def patch_user_settings(
    body: UserSettingsPatch,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Update or clear the allowlisted User memory policy without restarting."""
    _check_account_access(ctx, account_id)
    _check_account_exists(request, account_id)
    _check_user_exists(request, account_id, user_id)
    service = get_service()
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    user_ctx = RequestContext(
        user=UserIdentifier(account_id, user_id),
        role=Role.USER,
    )
    await write_user_memory_policy(service.viking_fs, user_ctx, body.memory_policy)
    user_config = await read_user_config(service.viking_fs, user_ctx)
    return Response(
        status="ok",
        result=_user_settings_result(
            account_id,
            user_id,
            user_config,
            request.app.state.config.user_config_defaults.memory_policy,
        ),
    )


@router.delete("/accounts/{account_id}/users/{user_id}", status_code=202)
@require_auth_root_or_admin
async def remove_user(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Revoke a user and start durable cleanup of their owned data."""
    _check_account_access(ctx, account_id)
    deletion_service = request.app.state.user_deletion_service
    if deletion_service is None:
        raise FailedPreconditionError("User deletion service is not initialized.")
    result = await deletion_service.delete_user(account_id, user_id, actor=ctx)
    return Response(status="ok", result=result)


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
