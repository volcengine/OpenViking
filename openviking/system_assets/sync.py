# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Synchronize packaged system assets into an account's system namespace."""

import json
import re
from contextlib import asynccontextmanager
from functools import cached_property
from pathlib import Path
from typing import Any, AsyncIterator

from openviking.core.namespace import uri_parts
from openviking.core.namespace_constants import SYSTEM_ASSET_ROOT_MAPPINGS
from openviking.core.namespace_policy import (
    SYSTEM_FIXED_DIRECTORIES,
)
from openviking.pyagfs import AGFSAlreadyExistsError, AsyncAGFSClient
from openviking.server.identity import RequestContext
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.viking_fs import VikingFS
from openviking.system_assets import SYSTEM_ASSETS_DIR
from openviking_cli.exceptions import NotFoundError

_MANIFEST_FILENAME = "manifest.json"
_MANIFEST_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SYSTEM_ASSET_MANIFEST_PATH = "/local/_system/system_assets_manifest.json"
_SYNC_LOCK_TIMEOUT_SECONDS = 300.0


@asynccontextmanager
async def system_asset_sync_lock(agfs: AsyncAGFSClient) -> AsyncIterator[Any]:
    """Serialize automatic asset updates and settle ownership before cancellation."""
    guard = PathLockGuard(agfs)
    try:
        lease = await guard.acquire(
            lambda: agfs.pathlock_acquire_exact(
                SYSTEM_ASSET_MANIFEST_PATH, timeout_secs=_SYNC_LOCK_TIMEOUT_SECONDS
            )
        )
        yield lease
    finally:
        await guard.close()


class SystemAssetSynchronizer:
    """Converge one account's builtin assets to the packaged contents."""

    def __init__(
        self,
        viking_fs: VikingFS,
        assets_dir: Path = SYSTEM_ASSETS_DIR,
    ):
        self._viking_fs = viking_fs
        self._assets_dir = assets_dir

    @cached_property
    def manifest(self) -> bytes:
        """Return the validated manifest shipped with the current image."""
        manifest_path = self._assets_dir / _MANIFEST_FILENAME
        raw = manifest_path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid packaged system asset manifest: {manifest_path}") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != _MANIFEST_SCHEMA_VERSION
            or not isinstance(payload.get("sha256"), str)
            or not _SHA256_PATTERN.fullmatch(payload["sha256"])
        ):
            raise ValueError(f"invalid packaged system asset manifest: {manifest_path}")
        return raw

    @property
    def manifest_digest(self) -> str:
        return json.loads(self.manifest)["sha256"]

    def _iter_managed_assets(self):
        for relative_root, target_root in SYSTEM_ASSET_ROOT_MAPPINGS:
            source_root = self._assets_dir / relative_root
            if not source_root.is_dir():
                raise FileNotFoundError(f"packaged system asset directory not found: {source_root}")
            for source_path in sorted(path for path in source_root.rglob("*") if path.is_file()):
                relative_path = source_path.relative_to(source_root).as_posix()
                yield source_path, f"{target_root}/{relative_path}"

    async def sync(self, ctx: RequestContext, *, lease_ref: Any = None) -> int:
        """Use a new-account Tree lease, or serialize an existing-account update."""
        if lease_ref is not None:
            return await self.sync_under_lock(ctx, lease_ref=lease_ref)
        async with system_asset_sync_lock(self._viking_fs._async_agfs):
            return await self.sync_under_lock(ctx)

    async def sync_under_lock(self, ctx: RequestContext, *, lease_ref: Any = None) -> int:
        """Write only packaged assets under the caller's manifest or account Tree lock."""
        agfs = self._viking_fs._async_agfs
        fs_ctx = self._viking_fs._pathlock_fs_ctx(ctx, lease_ref)
        for uri in sorted(SYSTEM_FIXED_DIRECTORIES, key=lambda value: len(uri_parts(value))):
            path = self._viking_fs._uri_to_path(uri, ctx=ctx)
            await agfs.ensure_parent_dirs(path, fs_ctx=fs_ctx)
            try:
                await agfs.mkdir(path, fs_ctx=fs_ctx)
            except AGFSAlreadyExistsError:
                pass

        pending: list[tuple[str, bytes]] = []
        for source_path, target_uri in self._iter_managed_assets():
            expected = source_path.read_bytes()
            try:
                current = await self._viking_fs.read_file_bytes(target_uri, ctx=ctx)
            except (FileNotFoundError, NotFoundError):
                current = None
            if current == expected:
                continue
            pending.append((self._viking_fs._uri_to_path(target_uri, ctx=ctx), expected))

        for path, content in pending:
            await agfs.ensure_parent_dirs(path, fs_ctx=fs_ctx)
            # This trusted package-copy path is separate from externally authorized
            # VFS writes. Encryption remains in AGFS; the outer lock owns exclusion.
            await agfs.write(path, content, fs_ctx=fs_ctx, auto_pathlock=False)
        return len(pending)
