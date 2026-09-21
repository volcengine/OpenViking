# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Built-in file-backed config source.

Paths are specific to this provider. The account override retains the legacy
``setting.json`` location:

============  ==========================================================
override      path
============  ==========================================================
account       ``/local/{account_id}/_system/setting.json``
account backup ``/local/{account_id}/_system/setting.backup.json``
cluster       ``/local/_system/runtime_config/cluster.json``
============  ==========================================================

Only sparse overrides are stored. This source exchanges JSON bytes with AGFS;
encryption at rest follows the mount's configuration. Backups preserve the
previous bytes returned by AGFS so readers can recover if the destination is
left truncated by an interrupted write.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from openviking.config.scope import ConfigScope, ScopeKind
from openviking.config.source.base import Mutate
from openviking.pyagfs import AGFSAlreadyExistsError, AGFSNotFoundError, AsyncAGFSClient
from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

ACCOUNT_SETTINGS_PATH = "/local/{account_id}/_system/setting.json"
ACCOUNT_BACKUP_PATH = "/local/{account_id}/_system/setting.backup.json"
CLUSTER_SETTINGS_PATH = "/local/_system/runtime_config/cluster.json"


class FileConfigSource:
    """Store sparse JSON overrides using AGFS locks, backups and rollback."""

    def __init__(
        self,
        agfs_client: AsyncAGFSClient,
        *,
        lock_timeout_secs: float = 10.0,
    ) -> None:
        self._client = agfs_client
        self._lock_timeout_secs = lock_timeout_secs

    # -- path / account resolution -------------------------------------------

    def _path(self, scope: ConfigScope) -> str:
        if scope.kind is ScopeKind.CLUSTER:
            return CLUSTER_SETTINGS_PATH
        return ACCOUNT_SETTINGS_PATH.format(account_id=scope.key)

    def _backup_path(self, scope: ConfigScope) -> Optional[str]:
        if scope.kind is ScopeKind.ACCOUNT:
            return ACCOUNT_BACKUP_PATH.format(account_id=scope.key)
        return "/local/_system/runtime_config/cluster.backup.json"

    # -- codec ----------------------------------------------------------------

    def _decode(self, raw: bytes) -> dict:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("config override must be a JSON object")
        return payload

    def _encode(self, override: dict) -> bytes:
        return json.dumps(override, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")

    async def _read_raw(self, path: str) -> Optional[bytes]:
        try:
            fs_ctx = fs_ctx_from_agfs_path(path)
            fs_ctx["bypass_cache"] = "true"
            result = await self._client.read(path, fs_ctx=fs_ctx)
        except AGFSNotFoundError:
            return None
        return _decode_read_result(result)

    async def _read_override(self, scope: ConfigScope) -> tuple[Optional[bytes], Optional[dict]]:
        """Read a valid override, recovering from the previous version when needed."""
        path = self._path(scope)
        decode_error: Exception | None = None
        for candidate in (path, self._backup_path(scope)):
            if candidate is None:
                continue
            raw = await self._read_raw(candidate)
            if raw is None:
                continue
            try:
                override = self._decode(raw)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                decode_error = decode_error or exc
                continue
            if candidate != path:
                logger.warning(
                    "Recovered runtime config for %s from %s after the primary was unavailable "
                    "or invalid",
                    scope,
                    candidate,
                )
            return raw, override
        if decode_error is not None:
            logger.error(
                "Runtime config for %s is invalid and no valid backup exists",
                scope,
                exc_info=decode_error,
            )
            raise RuntimeError(
                f"Runtime config for {scope} is invalid and no valid backup exists"
            ) from decode_error
        return None, None

    # -- ConfigSource contract ------------------------------------------------

    async def load(self, scope: ConfigScope) -> Optional[dict]:
        _, override = await self._read_override(scope)
        return override

    async def update(self, scope: ConfigScope, mutate: Mutate) -> dict:
        path = self._path(scope)
        try:
            lease = await self._client.pathlock_acquire_exact(
                path, timeout_secs=self._lock_timeout_secs
            )
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                "Another configuration update is in progress. Please retry.",
                uri=path,
                conflict_type="runtime_config_busy",
            ) from exc
        fs_ctx = _lease_fs_ctx(path, lease)
        try:
            current_raw, current = await self._read_override(scope)
            new_override = mutate(current)
            encoded = self._encode(new_override)

            try:
                await self._client.ensure_parent_dirs(path)
            except AGFSAlreadyExistsError:
                pass

            backup_path = self._backup_path(scope)
            if backup_path is not None and current_raw is not None:
                await self._client.write(backup_path, current_raw)

            try:
                await self._client.write(path, encoded, fs_ctx=fs_ctx)
            except Exception:
                try:
                    if current_raw is None:
                        await self._client.rm(path, fs_ctx=fs_ctx)
                    else:
                        await self._client.write(path, current_raw, fs_ctx=fs_ctx)
                except AGFSNotFoundError:
                    pass
                except Exception:
                    logger.exception("Failed to roll back runtime config at %s", path)
                raise
            return new_override
        finally:
            await self._client.pathlock_release(lease)

    async def delete(self, scope: ConfigScope) -> None:
        path = self._path(scope)
        try:
            lease = await self._client.pathlock_acquire_exact(
                path, timeout_secs=self._lock_timeout_secs
            )
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                "Another configuration update is in progress. Please retry.",
                uri=path,
                conflict_type="runtime_config_busy",
            ) from exc
        fs_ctx = _lease_fs_ctx(path, lease)
        try:
            for target in (path, self._backup_path(scope)):
                if target is None:
                    continue
                try:
                    if target == path:
                        await self._client.rm(target, fs_ctx=fs_ctx)
                    else:
                        await self._client.rm(target)
                except AGFSNotFoundError:
                    pass
        finally:
            await self._client.pathlock_release(lease)


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
    raise ValueError("config override content must be bytes or JSON text")


def _lease_fs_ctx(path: str, lease: Any) -> dict[str, str]:
    fs_ctx = fs_ctx_from_agfs_path(path)
    lease_ref = getattr(lease, "lease_ref", None) or getattr(lease, "id", None)
    if isinstance(lease, dict):
        lease_ref = lease.get("lease_ref", lease_ref)
    if isinstance(lease_ref, str) and lease_ref:
        fs_ctx["lease_ref"] = lease_ref
    return fs_ctx
