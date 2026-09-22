# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Admin endpoints for OpenViking multi-tenant HTTP Server."""

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from pydantic import BaseModel, Field

from openviking.config.scope import ConfigScope
from openviking.config.validate import ConfigPatchError
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
from openviking.session.memory.account_templates import (
    EDITABLE_MEMORY_TEMPLATE_FIELDS,
    default_memory_template,
    memory_template_result,
    read_account_memory_template,
    update_account_memory_template,
)
from openviking.session.memory.memory_type_registry import get_default_registry
from openviking.session.memory_policy import MemoryPolicy
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
    # Optional initial AccountConfig override, validated against the active
    # account-level runtime field surface before the account is created.
    settings: dict[str, Any] | None = None


class RegisterUserRequest(BaseModel):
    user_id: str
    role: str = "user"
    seed: str | None = None
    user_config: UserConfig | None = None


class SetRoleRequest(BaseModel):
    role: str


class RegenerateKeyRequest(BaseModel):
    seed: str | None = None


class CreateGroupRequest(BaseModel):
    model_config = {"extra": "forbid"}

    group_id: str


class MigrateLegacyDataRequest(BaseModel):
    action: str = "migrate"


class SetAgentEvolutionRequest(BaseModel):
    enabled: bool


class LegacyAccountAgentEvolutionSettings(BaseModel):
    """Legacy Agent Evolution request shape; enabled was required."""

    enabled: bool

    model_config = {"extra": "forbid"}


class LegacyAccountAclSettings(BaseModel):
    """Legacy ACL request shape; an empty object meant disabled."""

    enabled: bool = False

    model_config = {"extra": "forbid"}


class LegacyAccountSettingsPatch(BaseModel):
    """Compatibility parser for the original account settings endpoint."""

    agent_evolution: Optional[LegacyAccountAgentEvolutionSettings] = None
    acl: Optional[LegacyAccountAclSettings] = None

    model_config = {"extra": "forbid"}


class ConfigPatchRequest(BaseModel):
    """A three-state sparse PATCH over a config scope's ``RuntimeField`` surface.

    The body is an arbitrary nested object; structural validity (which paths may
    be touched, create-only gating) is enforced by the runtime config manager's
    ``validate_request`` hook, and semantic validity by re-parsing the merged
    config. ``extra`` is intentionally permissive here because the allowlist is
    the model surface, not this envelope.
    """

    settings: dict[str, Any] = Field(default_factory=dict)


class UserSettingsPatch(BaseModel):
    memory_policy: Optional[dict]

    model_config = {"extra": "forbid"}


def _agent_evolution_account_id(ctx: RequestContext) -> str:
    if ctx.role == Role.ROOT:
        return get_openviking_config().default_account
    return ctx.account_id


@router.get("/agent-evolution", deprecated=True)
@require_auth_root_or_admin
async def get_agent_evolution_status(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
):
    """Return the effective Agent Evolution switch for the caller's account."""
    account_id = _agent_evolution_account_id(ctx)
    await _check_account_exists(request, account_id)
    enabled = await get_service().sessions.get_agent_evolution_enabled(account_id)
    return Response(
        status="ok",
        result={"enabled": enabled, "account_id": account_id},
    )


@router.put("/agent-evolution", deprecated=True)
@require_auth_root_or_admin
async def set_agent_evolution_status(
    body: SetAgentEvolutionRequest,
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
):
    """Persist and hot-reload Agent Evolution for the caller's account."""
    account_id = _agent_evolution_account_id(ctx)
    await _check_account_exists(request, account_id)
    service = get_service()
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    runtime_config = _get_runtime_config_manager()
    try:
        await runtime_config.patch_account(
            account_id, {"agent_evolution": {"enabled": body.enabled}}
        )
    except (ConfigPatchError, ValueError) as exc:
        raise InvalidArgumentError(str(exc)) from exc
    enabled = await service.sessions.get_agent_evolution_enabled(account_id)
    return Response(
        status="ok",
        result={"enabled": enabled, "account_id": account_id},
    )


def _get_api_key_manager(request: Request):
    """Get APIKeyManager from app state."""
    return get_api_key_manager_or_raise(request)


def _get_runtime_config_manager():
    """Return the live runtime config manager, or fail with a clear precondition."""
    service = get_service()
    manager = service.runtime_config_manager
    if manager is None:
        raise FailedPreconditionError("Runtime config manager is not initialized.")
    return manager


def _should_expose_user_key(request: Request) -> bool:
    config = getattr(request.app.state, "config", None)
    if not isinstance(config, ServerConfig):
        return True
    return config.get_effective_auth_mode() != "trusted"


def _registry_watcher_running(request: Request) -> bool:
    plugin = getattr(request.app.state, "auth_plugin", None)
    watch_task = getattr(plugin, "_watch_task", None)
    return watch_task is not None and not watch_task.done()


def _check_account_access(ctx: RequestContext, account_id: str) -> None:
    """ADMIN can only operate on their own account."""
    if ctx.role == Role.ADMIN and ctx.account_id != account_id:
        raise PermissionDeniedError(f"ADMIN can only manage account: {ctx.account_id}")


async def _check_account_exists(
    request: Request, account_id: str, *, refresh_scope: str | None = None
):
    manager = getattr(request.app.state, "api_key_manager", None)
    if manager is None:
        return None
    watcher_running = _registry_watcher_running(request)
    if not watcher_running:
        await manager.refresh_accounts_from_store()
    accounts = manager.get_accounts()
    if not any(item.get("account_id") == account_id for item in accounts):
        raise NotFoundError(account_id, "account")
    manager.ensure_account_active(account_id)
    if refresh_scope is not None and not watcher_running:
        await manager.refresh_account_users_from_store(refresh_scope)
    return manager


async def _account_settings_result(
    account_id: str,
    acl_setting: Any,
    overrides: dict,
) -> dict:
    """Build the account settings response.

    ``settings`` is the effective view of the two legacy switches; ``overrides``
    projects only their explicit account values to preserve the original response.
    """
    enabled = await get_service().sessions.get_agent_evolution_enabled(account_id)
    legacy_overrides = {
        key: overrides[key]
        for key in ("agent_evolution", "acl")
        if key in overrides
    }
    return {
        "account_id": account_id,
        "settings": {
            "agent_evolution": {
                "enabled": enabled,
            },
            "acl": {
                "enabled": acl_setting.enabled if acl_setting is not None else False,
            },
        },
        "overrides": legacy_overrides,
    }


def _has_add_targets(user_config: UserConfig | None) -> bool:
    return bool(
        user_config and (user_config.add_targets.resource_uri or user_config.add_targets.skill_uri)
    )


def _has_initial_user_config(user_config: UserConfig | None) -> bool:
    return bool(_has_add_targets(user_config) or (user_config and user_config.memory_policy))


async def _validate_initial_user_config(
    service,
    user_ctx: RequestContext,
    user_config: UserConfig | None,
) -> None:
    if not _has_initial_user_config(user_config):
        return
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    if _has_add_targets(user_config):
        await validate_add_targets(
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


async def _rollback_account_creation(
    service,
    manager,
    account_id: str,
    runtime_config,
    deletion_service=None,
    actor: RequestContext | None = None,
) -> None:
    """Compensate resources created after the account registry entry.

    ``APIKeyManager.create_account`` already rolls back its own registry writes.
    This covers the later workspace, user-config and runtime-config steps when
    one of them fails.
    """
    if deletion_service is not None and actor is not None:
        try:
            await deletion_service.delete_now(account_id, actor=actor)
            return
        except Exception:
            logger.exception(
                "Synchronous account cleanup failed for %s; durable deletion remains queued",
                account_id,
            )
            return

    rollback_errors: list[Exception] = []
    if runtime_config is not None:
        try:
            await runtime_config.delete_account(account_id)
        except Exception as exc:
            rollback_errors.append(exc)
            logger.exception("Failed to roll back runtime config for account %s", account_id)

    viking_fs = getattr(service, "viking_fs", None)
    agfs = getattr(viking_fs, "_async_agfs", None)
    if agfs is not None:
        try:
            await agfs.rm(f"/local/{account_id}", recursive=True)
        except Exception as exc:
            rollback_errors.append(exc)
            logger.exception("Failed to roll back workspace for account %s", account_id)

    try:
        await manager.delete_account(account_id)
    except Exception as exc:
        rollback_errors.append(exc)
        logger.exception("Failed to roll back account registry entry for %s", account_id)

    if rollback_errors:
        logger.error(
            "Account creation rollback for %s completed with %d error(s)",
            account_id,
            len(rollback_errors),
        )


async def _check_user_exists(
    request: Request, account_id: str, user_id: str, manager=None
) -> None:
    manager = manager or _get_api_key_manager(request)
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
    known_memory_types = set(get_default_registry().list_names(include_disabled=False))
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
    await _validate_initial_user_config(service, account_ctx, body.user_config)
    # Reject bad initial config before any storage is created, so a failed
    # create leaves nothing behind. This validates the active account runtime
    # field surface without persisting. Only needed when initial settings exist.
    runtime_config = None
    if body.settings:
        runtime_config = _get_runtime_config_manager()
        try:
            runtime_config.validate_initial_settings(body.account_id, body.settings)
        except (ConfigPatchError, ValueError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
    manager = _get_api_key_manager(request)
    user_key = await manager.create_account(
        body.account_id,
        body.admin_user_id,
        seed=body.seed,
    )
    try:
        await service.initialize_account_workspace(account_ctx)
        await _write_initial_user_config(service, account_ctx, body.user_config)
        if body.settings and runtime_config is not None:
            # Persist the pre-validated override.
            await runtime_config.patch_account(
                body.account_id,
                body.settings,
                creating=True,
            )
    except BaseException:
        await _rollback_account_creation(
            service,
            manager,
            body.account_id,
            runtime_config,
            deletion_service=getattr(request.app.state, "deletion_service", None),
            actor=ctx,
        )
        raise
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
    name: str | None = None,
    limit: int | None = Query(None, ge=1, description="Page size; omit to return all"),
    page: int = Query(1, ge=1, description="1-based page number (requires limit)"),
    query: str | None = Query(None, description="Case-insensitive account id substring"),
    ctx: RequestContext = Depends(get_request_context),
):
    """List accounts in creation order. `name` supports wildcard (* and ?) matching."""
    manager = _get_api_key_manager(request)
    if not _registry_watcher_running(request):
        await manager.refresh_accounts_from_store()
    accounts = manager.get_accounts(name_filter=name, limit=limit, page=page, query_filter=query)
    return Response(status="ok", result=accounts)


@router.post("/migrate")
@require_auth_root
async def migrate_legacy_data(
    request: Request,
    body: MigrateLegacyDataRequest | None = None,
    ctx: RequestContext = Depends(get_request_context),
):
    """Preflight and enqueue legacy session data migration or cleanup."""
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


@router.delete("/accounts/{account_id}", status_code=202)
@require_auth_root
async def delete_account(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Revoke an account and submit durable cleanup of its data."""
    deletion_service = request.app.state.deletion_service
    if deletion_service is None:
        raise FailedPreconditionError("Deletion service is not initialized.")
    result = await deletion_service.delete(account_id, actor=ctx)
    return Response(status="ok", result=result)


@router.get("/accounts/{account_id}/settings", deprecated=True)
@require_auth_root_or_admin
async def get_account_settings(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Return the original effective-switch and override response shape."""
    _check_account_access(ctx, account_id)
    await _check_account_exists(request, account_id)
    runtime_config = _get_runtime_config_manager()
    acl_setting = await runtime_config.get_account(account_id, "acl")
    overrides = await runtime_config.get_settings(ConfigScope.account(account_id))
    return Response(
        status="ok",
        result=await _account_settings_result(account_id, acl_setting, overrides),
    )


@router.patch("/accounts/{account_id}/settings", deprecated=True)
@require_auth_root_or_admin
async def patch_account_settings(
    request: Request,
    body: LegacyAccountSettingsPatch,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Apply the original account settings semantics through the new manager.

    Missing and null fields are both no-ops. Present objects replace the legacy
    section after model validation; notably an empty ACL object means disabled.
    """
    _check_account_access(ctx, account_id)
    await _check_account_exists(request, account_id)
    service = get_service()
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    runtime_config = _get_runtime_config_manager()
    patch = body.model_dump(exclude_none=True)
    try:
        if patch:
            await runtime_config.patch_account(account_id, patch)
    except (ConfigPatchError, ValueError) as exc:
        raise InvalidArgumentError(str(exc)) from exc
    acl_setting = await runtime_config.get_account(account_id, "acl")
    overrides = await runtime_config.get_settings(ConfigScope.account(account_id))
    return Response(
        status="ok",
        result=await _account_settings_result(account_id, acl_setting, overrides),
    )


# ---- Runtime configuration -------------------------------------------------


@router.get("/accounts/{account_id}/configuration")
@require_auth_root_or_admin
async def get_account_configuration(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Return this account layer's explicit runtime configuration."""
    _check_account_access(ctx, account_id)
    await _check_account_exists(request, account_id)
    settings = await _get_runtime_config_manager().get_settings(
        ConfigScope.account(account_id)
    )
    return Response(
        status="ok",
        result={"account_id": account_id, "settings": settings},
    )


# ---- Account memory templates ----


async def _memory_template_service(request: Request, ctx: RequestContext, account_id: str):
    _check_account_access(ctx, account_id)
    await _check_account_exists(request, account_id)
    service = get_service()
    if service.viking_fs is None:
        raise FailedPreconditionError("OpenViking service is not initialized.")
    return service


@router.get("/accounts/{account_id}/memory-templates")
@require_auth_root_or_admin
async def list_memory_templates(
    request: Request,
    account_id: str,
    ctx: RequestContext = Depends(get_request_context),
):
    """List full defaults and account overrides for the six editable memory templates."""
    service = await _memory_template_service(request, ctx, account_id)
    registry = get_default_registry()
    names = list(EDITABLE_MEMORY_TEMPLATE_FIELDS)
    templates = await asyncio.gather(
        *(read_account_memory_template(service.viking_fs, account_id, name) for name in names)
    )
    return Response(
        status="ok",
        result={
            "account_id": account_id,
            "templates": [
                memory_template_result(registry, template, name)
                for name, template in zip(names, templates, strict=True)
            ],
        },
    )


@router.get("/accounts/{account_id}/memory-templates/{memory_type}")
@require_auth_root_or_admin
async def get_memory_template(
    request: Request,
    account_id: str,
    memory_type: str,
    ctx: RequestContext = Depends(get_request_context),
):
    """Read one template's full defaults and effective account configuration."""
    service = await _memory_template_service(request, ctx, account_id)
    registry = get_default_registry()
    default_memory_template(registry, memory_type)
    config = await read_account_memory_template(service.viking_fs, account_id, memory_type)
    return Response(
        status="ok",
        result={
            "account_id": account_id,
            **memory_template_result(registry, config, memory_type),
        },
    )


@router.put("/accounts/{account_id}/memory-templates/{memory_type}")
@require_auth_root_or_admin
async def put_memory_template(
    request: Request,
    account_id: str,
    memory_type: str,
    body: dict = Body(...),
    ctx: RequestContext = Depends(get_request_context),
):
    """Fill omitted values from deployment defaults and publish a complete YAML template."""
    service = await _memory_template_service(request, ctx, account_id)
    registry = get_default_registry()
    config = await update_account_memory_template(
        service.viking_fs, account_id, memory_type, body, registry
    )
    return Response(
        status="ok",
        result={
            "account_id": account_id,
            **memory_template_result(registry, config, memory_type),
        },
    )


@router.delete("/accounts/{account_id}/memory-templates/{memory_type}")
@require_auth_root_or_admin
async def reset_memory_template(
    request: Request,
    account_id: str,
    memory_type: str,
    ctx: RequestContext = Depends(get_request_context),
):
    """Remove one template override without rewriting existing memories."""
    service = await _memory_template_service(request, ctx, account_id)
    registry = get_default_registry()
    config = await update_account_memory_template(
        service.viking_fs, account_id, memory_type, None, registry
    )
    return Response(
        status="ok",
        result={
            "account_id": account_id,
            **memory_template_result(registry, config, memory_type),
        },
    )


@router.patch("/accounts/{account_id}/configuration")
@require_auth_root_or_admin
async def patch_account_configuration(
    body: ConfigPatchRequest,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    """Apply a three-state PATCH to the account configuration layer."""
    _check_account_access(ctx, account_id)
    await _check_account_exists(request, account_id)
    try:
        await _get_runtime_config_manager().patch_account(account_id, body.settings)
    except (ConfigPatchError, ValueError) as exc:
        raise InvalidArgumentError(str(exc)) from exc
    settings = await _get_runtime_config_manager().get_settings(
        ConfigScope.account(account_id)
    )
    return Response(
        status="ok",
        result={"account_id": account_id, "settings": settings},
    )


@router.get("/configuration")
@require_auth_root
async def get_cluster_configuration(
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
):
    """Return the cluster layer's explicit runtime configuration."""
    runtime_config = _get_runtime_config_manager()
    settings = await runtime_config.get_settings(ConfigScope.cluster())
    return Response(status="ok", result={"settings": settings})


@router.patch("/configuration")
@require_auth_root
async def patch_cluster_configuration(
    body: ConfigPatchRequest,
    request: Request,
    ctx: RequestContext = Depends(get_request_context),
):
    """Apply a three-state PATCH to the cluster configuration layer."""
    runtime_config = _get_runtime_config_manager()
    try:
        await runtime_config.patch_cluster(body.settings)
    except (ConfigPatchError, ValueError) as exc:
        raise InvalidArgumentError(str(exc)) from exc
    settings = await runtime_config.get_settings(ConfigScope.cluster())
    return Response(status="ok", result={"settings": settings})


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
    await _validate_initial_user_config(service, user_ctx, body.user_config)
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
    limit: int | None = Query(None, ge=1, description="Page size; omit to return all"),
    include_credentials: bool = Query(
        True,
        description="Include credentials when permitted; false returns only credential availability",
    ),
    name: str | None = None,
    role: str | None = None,
    page: int = Query(1, ge=1, description="1-based page number (requires limit)"),
    query: str | None = Query(None, description="Case-insensitive username substring"),
    include_summary: bool = Query(
        False, description="Return users, matching total and account statistics"
    ),
    ctx: RequestContext = Depends(get_request_context),
):
    """List users in an account, in creation order. `name` supports wildcard (* and ?) matching."""
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    if not _registry_watcher_running(request):
        await manager.refresh_account_users_from_store(account_id)
    expose_key = _should_expose_user_key(request)
    users = manager.get_users_page(
        account_id,
        limit=limit,
        name_filter=name,
        role_filter=role,
        expose_key=expose_key or not include_credentials,
        page=page,
        query_filter=query,
    )
    if not include_credentials:
        users["users"] = [
            {
                "user_id": user["user_id"],
                "role": user["role"],
                "api_key_available": bool(user.get("api_key")),
            }
            for user in users["users"]
        ]
    return Response(status="ok", result=users if include_summary else users["users"])


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
    manager = await _check_account_exists(request, account_id, refresh_scope=account_id)
    await _check_user_exists(request, account_id, user_id, manager)
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
    manager = await _check_account_exists(request, account_id, refresh_scope=account_id)
    await _check_user_exists(request, account_id, user_id, manager)
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
    deletion_service = request.app.state.deletion_service
    if deletion_service is None:
        raise FailedPreconditionError("Deletion service is not initialized.")
    result = await deletion_service.delete(account_id, user_id, actor=ctx)
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


# ---- Group endpoints ----


@router.post("/accounts/{account_id}/groups")
@require_auth_root_or_admin
async def create_group(
    body: CreateGroupRequest,
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    _check_account_access(ctx, account_id)
    result = await _get_api_key_manager(request).create_group(account_id, body.group_id)
    return Response(status="ok", result=result)


@router.get("/accounts/{account_id}/groups")
@require_auth_root_or_admin
async def list_groups(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    await manager.ensure_account_groups_loaded(account_id)
    result = manager.get_groups(account_id)
    return Response(status="ok", result=result)


@router.delete("/accounts/{account_id}/groups/{group_id}")
@require_auth_root_or_admin
async def delete_group(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    group_id: str = Path(..., description="Group ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    _check_account_access(ctx, account_id)
    await _get_api_key_manager(request).delete_group(account_id, group_id)
    return Response(status="ok", result={"deleted": True})


@router.get("/accounts/{account_id}/groups/{group_id}/members")
@require_auth_root_or_admin
async def list_group_members(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    group_id: str = Path(..., description="Group ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    _check_account_access(ctx, account_id)
    manager = _get_api_key_manager(request)
    await manager.ensure_account_groups_loaded(account_id)
    members = manager.get_group_members(account_id, group_id)
    return Response(status="ok", result={"group_id": group_id, "members": members})


@router.put("/accounts/{account_id}/groups/{group_id}/members/{user_id}")
@require_auth_root_or_admin
async def add_group_member(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    group_id: str = Path(..., description="Group ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    _check_account_access(ctx, account_id)
    added = await _get_api_key_manager(request).add_group_member(account_id, group_id, user_id)
    return Response(status="ok", result={"added": added})


@router.delete("/accounts/{account_id}/groups/{group_id}/members/{user_id}")
@require_auth_root_or_admin
async def remove_group_member(
    request: Request,
    account_id: str = Path(..., description="Account ID"),
    group_id: str = Path(..., description="Group ID"),
    user_id: str = Path(..., description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
):
    _check_account_access(ctx, account_id)
    removed = await _get_api_key_manager(request).remove_group_member(account_id, group_id, user_id)
    return Response(status="ok", result={"removed": removed})
