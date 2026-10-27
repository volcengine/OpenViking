# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Synchronize packaged system assets into an account's system namespace."""

import base64
import hashlib
from dataclasses import replace
from functools import cached_property
from pathlib import Path

from openviking.core.namespace import uri_parts
from openviking.core.namespace_policy import (
    SYSTEM_EXAMPLE_SKILLS_ROOT,
    SYSTEM_FIXED_DIRECTORIES,
    SYSTEM_MEMORY_BUILTIN_ROOT,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.viking_fs import VikingFS
from openviking.system_assets import SYSTEM_ASSETS_DIR
from openviking_cli.exceptions import NotFoundError

_MANAGED_ASSET_ROOTS = (
    (Path("compile/example-skills"), SYSTEM_EXAMPLE_SKILLS_ROOT),
    (Path("memory/memory_templates/builtin"), SYSTEM_MEMORY_BUILTIN_ROOT),
)


class SystemAssetSynchronizer:
    """Converge one account's builtin assets to the packaged contents."""

    def __init__(
        self,
        viking_fs: VikingFS,
        assets_dir: Path = SYSTEM_ASSETS_DIR,
    ):
        self._viking_fs = viking_fs
        self._coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
        self._assets_dir = assets_dir

    @cached_property
    def manifest_digest(self) -> str:
        """Return a stable digest of all managed packaged assets."""
        digest = hashlib.sha256()
        for uri in sorted(SYSTEM_FIXED_DIRECTORIES):
            self._update_digest(digest, uri.encode("utf-8"), b"")
        for source_path, _, target_uri in self._iter_managed_assets():
            self._update_digest(
                digest,
                target_uri.encode("utf-8"),
                source_path.read_bytes(),
            )
        return digest.hexdigest()

    @staticmethod
    def _update_digest(digest, path: bytes, content: bytes) -> None:
        digest.update(len(path).to_bytes(8, "big"))
        digest.update(path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)

    def _iter_managed_assets(self):
        for relative_root, target_root in _MANAGED_ASSET_ROOTS:
            source_root = self._assets_dir / relative_root
            if not source_root.is_dir():
                raise FileNotFoundError(f"packaged system asset directory not found: {source_root}")
            for source_path in sorted(path for path in source_root.rglob("*") if path.is_file()):
                relative_path = source_path.relative_to(source_root).as_posix()
                yield source_path, target_root, f"{target_root}/{relative_path}"

    async def sync(self, ctx: RequestContext) -> int:
        root_ctx = replace(ctx, role=Role.ROOT, actor_peer_id=None)
        for uri in sorted(SYSTEM_FIXED_DIRECTORIES, key=lambda value: len(uri_parts(value))):
            await self._viking_fs.mkdir(uri, exist_ok=True, ctx=root_ctx)

        pending_by_root: dict[str, list[dict[str, str]]] = {}
        for source_path, target_root, target_uri in self._iter_managed_assets():
            expected = source_path.read_bytes()
            try:
                current = await self._viking_fs.read_file_bytes(target_uri, ctx=root_ctx)
            except (FileNotFoundError, NotFoundError):
                current = None
            if current == expected:
                continue
            pending_by_root.setdefault(target_root, []).append(
                {
                    "uri": target_uri,
                    "content_base64": base64.b64encode(expected).decode("ascii"),
                    "mode": "replace",
                }
            )

        for root_uri, operations in pending_by_root.items():
            await self._coordinator.batch_write(
                root_uri=root_uri,
                operations=operations,
                ctx=root_ctx,
            )
        return sum(len(operations) for operations in pending_by_root.values())
