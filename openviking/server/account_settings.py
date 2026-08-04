# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistent, account-scoped runtime settings."""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel

from openviking.pyagfs import AGFSAlreadyExistsError, AGFSNotFoundError, AsyncAGFSClient
from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import validate_account_id
from openviking_cli.utils.logger import get_logger

ACCOUNT_SETTINGS_PATH_TEMPLATE = "/local/{account_id}/_system/setting.json"
ACCOUNT_SETTINGS_BACKUP_PATH_TEMPLATE = "/local/{account_id}/_system/setting.backup.json"

logger = get_logger(__name__)


class AccountAgentEvolutionSettings(BaseModel):
    """Account-scoped Agent Evolution override."""

    enabled: bool

    model_config = {"extra": "forbid"}


class AccountSettings(BaseModel):
    """Persisted account overrides.

    Only explicitly allowlisted hot-reloadable settings belong in this model.
    """

    agent_evolution: Optional[AccountAgentEvolutionSettings] = None

    model_config = {"extra": "forbid"}


class AccountSettingsPatch(BaseModel):
    """Allowlisted account settings accepted by the update API."""

    agent_evolution: Optional[AccountAgentEvolutionSettings] = None

    model_config = {"extra": "forbid"}


def account_settings_path(account_id: str) -> str:
    _validate_account_id(account_id)
    return ACCOUNT_SETTINGS_PATH_TEMPLATE.format(account_id=account_id)


def account_settings_backup_path(account_id: str) -> str:
    _validate_account_id(account_id)
    return ACCOUNT_SETTINGS_BACKUP_PATH_TEMPLATE.format(account_id=account_id)


def _validate_account_id(account_id: str) -> None:
    error = validate_account_id(account_id)
    if error:
        raise InvalidArgumentError(error)


def _decode_read_result(result: Any) -> bytes:
    if isinstance(result, bytes):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(result, str):
        return result.encode("utf-8")
    raise InvalidArgumentError("account settings content must be JSON text")


def _parse_account_settings(raw: bytes) -> AccountSettings:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidArgumentError(f"Invalid account settings JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidArgumentError("account settings must be an object")
    try:
        return AccountSettings.model_validate(payload)
    except Exception as exc:
        raise InvalidArgumentError(str(exc)) from exc


async def _read_raw(client: AsyncAGFSClient, path: str) -> Optional[bytes]:
    try:
        return _decode_read_result(await client.read(path))
    except AGFSNotFoundError:
        return None


async def read_account_settings(viking_fs: VikingFS, account_id: str) -> AccountSettings:
    """Read the persisted overrides for one account."""
    path = account_settings_path(account_id)
    raw = await _read_raw(AsyncAGFSClient(viking_fs.agfs), path)
    return AccountSettings() if raw is None else _parse_account_settings(raw)


def effective_agent_evolution_enabled(
    settings: AccountSettings,
    *,
    default_enabled: bool,
) -> bool:
    if settings.agent_evolution is None:
        return bool(default_enabled)
    return settings.agent_evolution.enabled


async def update_account_settings(
    viking_fs: VikingFS,
    account_id: str,
    patch: AccountSettingsPatch,
) -> AccountSettings:
    """Apply a locked, backed-up update to one account's settings."""
    path = account_settings_path(account_id)
    backup_path = account_settings_backup_path(account_id)
    client = AsyncAGFSClient(viking_fs.agfs)
    try:
        lease = await client.pathlock_acquire_exact(path, timeout_secs=10.0)
    except LockAcquisitionError as exc:
        raise ResourceBusyError(
            "Another account settings update is in progress. Please retry.",
            uri=path,
            conflict_type="account_settings_busy",
        ) from exc

    fs_ctx = fs_ctx_from_agfs_path(path)
    lease_ref = getattr(lease, "lease_ref", None) or getattr(lease, "id", None)
    if isinstance(lease, dict):
        lease_ref = lease.get("lease_ref", lease_ref)
    if isinstance(lease_ref, str) and lease_ref:
        fs_ctx["lease_ref"] = lease_ref

    try:
        current_raw = await _read_raw(client, path)
        current = AccountSettings() if current_raw is None else _parse_account_settings(current_raw)
        updated = current.model_copy(deep=True)
        if patch.agent_evolution is not None:
            updated.agent_evolution = patch.agent_evolution.model_copy(deep=True)
        if updated == current:
            return current

        encoded = json.dumps(
            updated.model_dump(exclude_none=True),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        try:
            await client.ensure_parent_dirs(path)
        except AGFSAlreadyExistsError:
            pass

        if current_raw is not None:
            await client.write(backup_path, current_raw)

        try:
            await client.write(path, encoded, fs_ctx=fs_ctx)
        except Exception:
            try:
                if current_raw is not None:
                    await client.write(path, current_raw, fs_ctx=fs_ctx)
                else:
                    await client.rm(path, fs_ctx=fs_ctx)
            except AGFSNotFoundError:
                pass
            except Exception:
                logger.exception(
                    "Failed to roll back account settings for account %s",
                    account_id,
                )
            raise
        return updated
    finally:
        await client.pathlock_release(lease)
