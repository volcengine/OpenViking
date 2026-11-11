# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Server-side user configuration helpers."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from openviking.core.namespace import canonical_user_root, resolve_current_user_uri
from openviking.core.uri_validation import validate_content_target_uri
from openviking.server.config import (
    AddTargetsConfig,
    UserConfig,
    UserSettingsPatch,
    validate_config_input,
)
from openviking.storage.acl import AclAction
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError

if TYPE_CHECKING:
    from openviking.server.config import ServerConfig
    from openviking.server.identity import RequestContext
    from openviking.storage.viking_fs import VikingFS


def user_config_uri(ctx: RequestContext) -> str:
    return f"{canonical_user_root(ctx)}/settings/user_config.json"


def user_config_backup_uri(ctx: RequestContext) -> str:
    return f"{canonical_user_root(ctx)}/settings/user_config.backup.json"


@asynccontextmanager
async def _user_config_lock(
    viking_fs: VikingFS,
    uri: str,
    ctx: RequestContext,
    lease_ref: object | None = None,
) -> AsyncIterator[Optional[dict[str, Any]]]:
    if lease_ref is not None:
        yield lease_ref
        return
    uri_to_path = getattr(viking_fs, "_uri_to_path", None)
    agfs = getattr(viking_fs, "_async_agfs", None)
    acquire = getattr(agfs, "pathlock_acquire_exact", None)
    release = getattr(agfs, "pathlock_release", None)
    if not callable(uri_to_path) or not callable(acquire) or not callable(release):
        yield None
        return
    path = uri_to_path(uri, ctx=ctx)
    lease = await acquire(path)
    try:
        yield lease
    finally:
        await release(lease)


async def validate_resource_add_target(
    uri: str,
    *,
    ctx: RequestContext,
    viking_fs: VikingFS,
) -> str:
    resolved = validate_content_target_uri(
        uri,
        ctx,
        kind="resource",
        field_name="resource_uri",
    )
    await viking_fs._ensure_access(resolved, ctx, action=AclAction.WRITE)
    return resolved


async def validate_skill_add_target(
    uri: str,
    *,
    ctx: RequestContext,
    viking_fs: VikingFS,
) -> str:
    resolved = validate_content_target_uri(
        uri,
        ctx,
        kind="skill",
        field_name="skill_uri",
    )
    await viking_fs._ensure_access(resolved, ctx, action=AclAction.WRITE)
    return resolved


def resolve_add_targets(
    settings: AddTargetsConfig,
    *,
    ctx: RequestContext,
) -> AddTargetsConfig:
    """Expand home aliases without revalidating stored configuration."""
    resource_uri = (
        resolve_current_user_uri(settings.resource_uri, ctx)
        if settings.resource_uri and settings.resource_uri.startswith("viking://~/")
        else settings.resource_uri
    )
    skill_uri = (
        resolve_current_user_uri(settings.skill_uri, ctx)
        if settings.skill_uri and settings.skill_uri.startswith("viking://~/")
        else settings.skill_uri
    )
    return AddTargetsConfig.model_construct(
        resource_uri=resource_uri,
        skill_uri=skill_uri,
    )


async def read_user_config(
    viking_fs: VikingFS,
    ctx: RequestContext,
) -> UserConfig:
    uri = user_config_uri(ctx)
    try:
        raw = await viking_fs.read_file(uri, ctx=ctx)
    except (NotFoundError, FileNotFoundError):
        return UserConfig()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidArgumentError(f"Invalid user config JSON: {exc}") from exc
    return UserConfig.from_stored(payload)


async def patch_user_config(
    viking_fs: VikingFS,
    ctx: RequestContext,
    patch: UserSettingsPatch,
) -> UserConfig:
    """Merge a validated sparse patch without revalidating untouched settings."""
    from openviking.config.merge import apply_three_state_patch

    changes = patch.model_dump(by_alias=True, exclude_unset=True)
    if patch.add_targets is not None:
        resolved = resolve_add_targets(patch.add_targets, ctx=ctx)
        changes["add_targets"] = resolved.model_dump(include=patch.add_targets.model_fields_set)
    uri = user_config_uri(ctx)
    async with _user_config_lock(viking_fs, uri, ctx) as handle:
        current = await read_user_config(viking_fs, ctx)
        before = current.model_dump(by_alias=True)
        payload = apply_three_state_patch(
            current.model_dump(by_alias=True, exclude_none=True),
            {field: changes[field] for field in ("add_targets", "ttl") if field in changes},
        )
        if "memory_policy" in changes:
            # Memory policy remains a whole-policy replacement.
            payload["memory_policy"] = changes["memory_policy"]
        updated = UserConfig.from_stored(payload)
        if updated.model_dump(by_alias=True) != before:
            await _prepare_ttl_change(ctx, current, updated)
            before_content = json.dumps(before, ensure_ascii=False, sort_keys=True)
            await viking_fs.write_file(
                user_config_backup_uri(ctx),
                before_content,
                ctx=ctx,
            )
            try:
                await viking_fs.write_file(
                    uri,
                    json.dumps(
                        updated.model_dump(by_alias=True, exclude_none=True),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    ctx=ctx,
                    lease_ref=handle,
                )
            except Exception:
                await viking_fs.write_file(
                    uri,
                    before_content,
                    ctx=ctx,
                    lease_ref=handle,
                )
                raise
            if current.ttl != updated.ttl:
                await _publish_ttl_change(ctx, updated)
        return updated


async def _prepare_ttl_change(ctx: RequestContext, before: UserConfig, after: UserConfig) -> None:
    if before.ttl == after.ttl:
        return
    from openviking.ttl.wiring import get_ttl_adapter

    await get_ttl_adapter().prepare_user_configuration(ctx, before.ttl, after.ttl)


async def _publish_ttl_change(ctx: RequestContext, config: UserConfig) -> None:
    from openviking.ttl.wiring import get_ttl_adapter

    adapter = get_ttl_adapter()
    if adapter is not None:
        await adapter.publish_user_configuration(ctx, config.ttl)


async def write_user_config(
    viking_fs: VikingFS,
    ctx: RequestContext,
    user_config: UserConfig,
    *,
    lease_ref: object | None = None,
) -> None:
    """Persist validated initial settings for a newly created user."""
    runtime = resolve_add_targets(user_config.add_targets, ctx=ctx)
    user_config.add_targets.resource_uri = runtime.resource_uri
    user_config.add_targets.skill_uri = runtime.skill_uri
    uri = user_config_uri(ctx)
    async with _user_config_lock(viking_fs, uri, ctx, lease_ref) as handle:
        await _prepare_ttl_change(ctx, UserConfig(), user_config)
        await viking_fs.write_file(
            uri,
            json.dumps(
                user_config.model_dump(by_alias=True, exclude_none=True),
                ensure_ascii=False,
                sort_keys=True,
            ),
            ctx=ctx,
            lease_ref=handle,
        )
        await _publish_ttl_change(ctx, user_config)


async def delete_user_config(viking_fs: VikingFS, ctx: RequestContext) -> None:
    try:
        await viking_fs.rm(user_config_uri(ctx), ctx=ctx)
    except (NotFoundError, FileNotFoundError):
        pass
    await _publish_ttl_change(ctx, UserConfig())


async def read_user_memory_policy(
    viking_fs: VikingFS,
    ctx: RequestContext,
) -> Optional[dict[str, Any]]:
    return (await read_user_config(viking_fs, ctx)).memory_policy


async def write_user_memory_policy(
    viking_fs: VikingFS,
    ctx: RequestContext,
    memory_policy: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    patch = UserSettingsPatch(memory_policy=memory_policy)
    validate_config_input(patch)
    updated = await patch_user_config(viking_fs, ctx, patch)
    return updated.memory_policy


async def effective_resource_add_target(
    *,
    viking_fs: VikingFS,
    ctx: RequestContext,
    server_config: Optional[ServerConfig],
) -> Optional[str]:
    configured = resolve_user_add_targets(
        await read_user_config(viking_fs, ctx), server_config
    ).resource_uri
    if configured:
        return await validate_resource_add_target(configured, ctx=ctx, viking_fs=viking_fs)
    return None


async def effective_skill_add_target(
    *,
    viking_fs: VikingFS,
    ctx: RequestContext,
    server_config: Optional[ServerConfig],
) -> Optional[str]:
    configured = resolve_user_add_targets(
        await read_user_config(viking_fs, ctx), server_config
    ).skill_uri
    if configured:
        return await validate_skill_add_target(configured, ctx=ctx, viking_fs=viking_fs)
    return None


def resolve_user_add_targets(
    user_config: UserConfig, server_config: Optional[ServerConfig]
) -> AddTargetsConfig:
    """Apply deployment defaults only where the User has no add target."""
    defaults = (
        server_config.user_config_defaults.add_targets if server_config else AddTargetsConfig()
    )
    return AddTargetsConfig.model_construct(
        resource_uri=user_config.add_targets.resource_uri or defaults.resource_uri,
        skill_uri=user_config.add_targets.skill_uri or defaults.skill_uri,
    )
