# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Synchronize packaged system assets into an account's system namespace."""

import json
import re
from contextlib import asynccontextmanager
from functools import cached_property
from pathlib import Path
from typing import Any, AsyncIterator

from openviking.concurrency import bounded_map
from openviking.core.namespace import uri_parts
from openviking.core.namespace_constants import (
    SYSTEM_ASSET_ROOT_MAPPINGS,
    SYSTEM_VIKINGBOT_SKILLS_ROOT,
)
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
_SYNC_CONCURRENCY = 4


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

    async def _remove_stale_assets(
        self,
        target_root: str,
        expected_uris: set[str],
        ctx: RequestContext,
        *,
        fs_ctx: dict[str, str] | None,
    ) -> int:
        agfs = self._viking_fs._async_agfs
        root_path = self._viking_fs._uri_to_path(target_root, ctx=ctx)
        expected_paths = {self._viking_fs._uri_to_path(uri, ctx=ctx) for uri in expected_uris}
        removed = 0
        pending = [root_path]
        while pending:
            directory = pending.pop()
            for entry in await agfs.ls(directory, fs_ctx=fs_ctx):
                name = str(entry.get("name") or "")
                if not name:
                    continue
                path = f"{directory}/{name}"
                is_dir = entry.get("isDir", entry.get("is_dir", False))
                if is_dir and any(expected.startswith(f"{path}/") for expected in expected_paths):
                    pending.append(path)
                elif is_dir or path not in expected_paths:
                    await agfs.rm(
                        path,
                        recursive=bool(is_dir),
                        fs_ctx=fs_ctx,
                        auto_pathlock=False,
                    )
                    removed += 1
        return removed

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

        managed_assets = list(self._iter_managed_assets())
        expected_vikingbot_uris = {
            target_uri
            for _, target_uri in managed_assets
            if target_uri.startswith(f"{SYSTEM_VIKINGBOT_SKILLS_ROOT}/")
        }

        async def check_asset(asset: tuple[Path, str]) -> tuple[str, bytes] | None:
            source_path, target_uri = asset
            expected = source_path.read_bytes()
            try:
                current = await self._viking_fs.read_file_bytes(target_uri, ctx=ctx)
            except (FileNotFoundError, NotFoundError):
                current = None
            if current == expected:
                return None
            return self._viking_fs._uri_to_path(target_uri, ctx=ctx), expected

        # Finish every check before writing; failed workers settle before the
        # caller releases its manifest or account Tree lease.
        checked = await bounded_map(managed_assets, check_asset, concurrency=_SYNC_CONCURRENCY)
        pending = [asset for asset in checked if asset is not None]

        async def write_asset(asset: tuple[str, bytes]) -> None:
            path, content = asset
            await agfs.ensure_parent_dirs(path, fs_ctx=fs_ctx)
            # This trusted package-copy path is separate from externally authorized
            # VFS writes. Encryption remains in AGFS; the outer lock owns exclusion.
            await agfs.write(path, content, fs_ctx=fs_ctx, auto_pathlock=False)

        await bounded_map(pending, write_asset, concurrency=_SYNC_CONCURRENCY)
        changed = len(pending)
        changed += await self._remove_stale_assets(
            SYSTEM_VIKINGBOT_SKILLS_ROOT,
            expected_vikingbot_uris,
            ctx,
            fs_ctx=fs_ctx,
        )
        return changed
