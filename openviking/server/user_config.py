# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Server-side user configuration helpers."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Optional, TypeVar

from openviking.core.namespace import canonical_user_root
from openviking.core.uri_validation import validate_content_target_uri
from openviking.server.config import AddTargetsConfig, UserConfig
from openviking.storage.transaction import LockContext, get_lock_manager
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError

if TYPE_CHECKING:
    from openviking.server.config import ServerConfig
    from openviking.server.identity import RequestContext
    from openviking.storage.transaction import LockHandle
    from openviking.storage.viking_fs import VikingFS


@dataclass(frozen=True)
class ResolvedAddTargets:
    resource_uri: Optional[str] = None
    skill_uri: Optional[str] = None


@dataclass(frozen=True)
class ResolvedMemorySettings:
    agent_evolution_enabled: bool


_UpdateResult = TypeVar("_UpdateResult")


def user_config_uri(ctx: RequestContext) -> str:
    return f"{canonical_user_root(ctx)}/settings/user_config.json"


@asynccontextmanager
async def _user_config_lock(
    viking_fs: VikingFS,
    uri: str,
    ctx: RequestContext,
) -> AsyncIterator[Optional[LockHandle]]:
    uri_to_path = getattr(viking_fs, "_uri_to_path", None)
    if not callable(uri_to_path):
        yield None
        return
    path = uri_to_path(uri, ctx=ctx)
    async with LockContext(get_lock_manager(), [path], lock_mode="exact") as handle:
        yield handle


def _user_config_from_payload(payload: Any) -> UserConfig:
    if not isinstance(payload, dict):
        raise InvalidArgumentError("user config must be an object")
    try:
        return UserConfig.model_validate(payload)
    except Exception as exc:
        raise InvalidArgumentError(str(exc)) from exc


def _ensure_mutable(viking_fs: VikingFS, uri: str, ctx: RequestContext) -> None:
    ensure = getattr(viking_fs, "_ensure_mutable_access", None)
    if callable(ensure):
        ensure(uri, ctx)


def validate_resource_add_target(
    uri: str,
    *,
    ctx: RequestContext,
    viking_fs: VikingFS,
) -> str:
    resolved = validate_content_target_uri(uri, ctx, kind="resource", field_name="resource_uri")
    _ensure_mutable(viking_fs, resolved, ctx)
    return resolved


def validate_skill_add_target(
    uri: str,
    *,
    ctx: RequestContext,
    viking_fs: VikingFS,
) -> str:
    normalized = uri.strip().rstrip("/")
    if normalized == "viking://user/skills":
        resolved = f"{canonical_user_root(ctx)}/skills"
    elif normalized == "viking://agent/skills":
        resolved = "viking://agent/skills"
    else:
        raise InvalidArgumentError(
            "skill_uri must be viking://user/skills or viking://agent/skills",
            details={"field": "skill_uri"},
        )
    _ensure_mutable(viking_fs, resolved, ctx)
    return resolved


def validate_add_targets(
    settings: AddTargetsConfig,
    *,
    ctx: RequestContext,
    viking_fs: VikingFS,
) -> ResolvedAddTargets:
    return ResolvedAddTargets(
        resource_uri=(
            validate_resource_add_target(settings.resource_uri, ctx=ctx, viking_fs=viking_fs)
            if settings.resource_uri
            else None
        ),
        skill_uri=(
            validate_skill_add_target(settings.skill_uri, ctx=ctx, viking_fs=viking_fs)
            if settings.skill_uri
            else None
        ),
    )


async def read_user_config(
    viking_fs: VikingFS,
    ctx: RequestContext,
) -> UserConfig:
    read_file = getattr(viking_fs, "read_file", None)
    if not callable(read_file):
        return UserConfig()
    uri = user_config_uri(ctx)
    try:
        raw = await read_file(uri, ctx=ctx)
    except (NotFoundError, FileNotFoundError):
        return UserConfig()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidArgumentError(f"Invalid user config JSON: {exc}") from exc
    return _user_config_from_payload(payload)


async def update_user_config(
    viking_fs: VikingFS,
    ctx: RequestContext,
    updater: Callable[[UserConfig], _UpdateResult],
) -> _UpdateResult:
    """Apply a locked read-modify-write update to the current user's config."""
    uri = user_config_uri(ctx)
    async with _user_config_lock(viking_fs, uri, ctx) as handle:
        current = await read_user_config(viking_fs, ctx)
        before = current.model_dump()
        result = updater(current)
        validate_add_targets(current.add_targets, ctx=ctx, viking_fs=viking_fs)
        if current.model_dump() != before:
            await viking_fs.write_file(
                uri,
                json.dumps(
                    current.model_dump(exclude_none=True),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                ctx=ctx,
                lock_handle=handle,
            )
        return result


async def write_user_config(
    viking_fs: VikingFS,
    ctx: RequestContext,
    user_config: UserConfig,
) -> ResolvedAddTargets:
    runtime = validate_add_targets(user_config.add_targets, ctx=ctx, viking_fs=viking_fs)
    uri = user_config_uri(ctx)
    async with _user_config_lock(viking_fs, uri, ctx) as handle:
        await viking_fs.write_file(
            uri,
            json.dumps(
                user_config.model_dump(exclude_none=True),
                ensure_ascii=False,
                sort_keys=True,
            ),
            ctx=ctx,
            lock_handle=handle,
        )
    return runtime


async def delete_user_config(viking_fs: VikingFS, ctx: RequestContext) -> None:
    try:
        await viking_fs.rm(user_config_uri(ctx), ctx=ctx)
    except (NotFoundError, FileNotFoundError):
        return


async def read_user_add_targets(
    viking_fs: VikingFS,
    ctx: RequestContext,
) -> AddTargetsConfig:
    return (await read_user_config(viking_fs, ctx)).add_targets


async def write_user_add_targets(
    viking_fs: VikingFS,
    ctx: RequestContext,
    settings: AddTargetsConfig,
) -> ResolvedAddTargets:
    runtime = validate_add_targets(settings, ctx=ctx, viking_fs=viking_fs)

    def _set(user_config: UserConfig) -> None:
        user_config.add_targets = settings

    await update_user_config(viking_fs, ctx, _set)
    return runtime


async def delete_user_add_targets(viking_fs: VikingFS, ctx: RequestContext) -> None:
    def _clear(user_config: UserConfig) -> None:
        user_config.add_targets = AddTargetsConfig()

    await update_user_config(viking_fs, ctx, _clear)


async def write_user_memory_settings(
    viking_fs: VikingFS,
    ctx: RequestContext,
    *,
    agent_evolution_enabled: Any,
    agent_evolution_enabled_set: bool,
) -> None:
    def _set(user_config: UserConfig) -> None:
        if agent_evolution_enabled_set:
            user_config.agent_evolution.enabled = agent_evolution_enabled

    await update_user_config(viking_fs, ctx, _set)


async def resolve_memory_settings(
    *,
    viking_fs: VikingFS,
    ctx: RequestContext,
    user_config_defaults: Optional[UserConfig] = None,
    user_config: Optional[UserConfig] = None,
) -> ResolvedMemorySettings:
    current = user_config or await read_user_config(viking_fs, ctx)
    defaults = user_config_defaults or UserConfig()
    enabled = current.agent_evolution.enabled
    if enabled is None:
        enabled = defaults.agent_evolution.enabled
    effective_agent_evolution_enabled = bool(enabled) if enabled is not None else False
    return ResolvedMemorySettings(
        agent_evolution_enabled=effective_agent_evolution_enabled,
    )


def public_memory_settings(user_config: UserConfig) -> dict[str, Any]:
    return {"agent_evolution_enabled": user_config.agent_evolution.enabled}


async def effective_resource_add_target(
    *,
    viking_fs: VikingFS,
    ctx: RequestContext,
    server_config: Optional[ServerConfig],
) -> Optional[str]:
    user_settings = await read_user_add_targets(viking_fs, ctx)
    if user_settings.resource_uri:
        return validate_resource_add_target(
            user_settings.resource_uri, ctx=ctx, viking_fs=viking_fs
        )
    configured = getattr(
        getattr(getattr(server_config, "user_config_defaults", None), "add_targets", None),
        "resource_uri",
        None,
    )
    if configured:
        return validate_resource_add_target(configured, ctx=ctx, viking_fs=viking_fs)
    return None


async def effective_skill_add_target(
    *,
    viking_fs: VikingFS,
    ctx: RequestContext,
    server_config: Optional[ServerConfig],
) -> Optional[str]:
    user_settings = await read_user_add_targets(viking_fs, ctx)
    if user_settings.skill_uri:
        return validate_skill_add_target(user_settings.skill_uri, ctx=ctx, viking_fs=viking_fs)
    configured = getattr(
        getattr(getattr(server_config, "user_config_defaults", None), "add_targets", None),
        "skill_uri",
        None,
    )
    if configured:
        return validate_skill_add_target(configured, ctx=ctx, viking_fs=viking_fs)
    return None


def public_add_targets(settings: AddTargetsConfig) -> dict[str, Optional[str]]:
    return {
        "resource_uri": settings.resource_uri,
        "skill_uri": settings.skill_uri,
    }
