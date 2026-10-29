# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Synchronize packaged system assets into an account's system namespace."""

import base64
import json
import re
from dataclasses import replace
from functools import cached_property
from pathlib import Path

from openviking.core.namespace import uri_parts
from openviking.core.namespace_constants import (
    SYSTEM_ASSET_ROOT_MAPPINGS,
    SYSTEM_VIKINGBOT_SKILLS_ROOT,
)
from openviking.core.namespace_policy import (
    SYSTEM_FIXED_DIRECTORIES,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.viking_fs import LS_ALL_NODES, VikingFS
from openviking.system_assets import SYSTEM_ASSETS_DIR
from openviking_cli.exceptions import NotFoundError

_MANIFEST_FILENAME = "manifest.json"
_MANIFEST_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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
                yield source_path, target_root, f"{target_root}/{relative_path}"

    async def _remove_stale_assets(
        self,
        target_root: str,
        expected_uris: set[str],
        ctx: RequestContext,
    ) -> int:
        removed = 0
        pending = [target_root]
        while pending:
            directory_uri = pending.pop()
            entries = await self._viking_fs.ls(
                directory_uri,
                show_all_hidden=True,
                node_limit=LS_ALL_NODES,
                ctx=ctx,
            )
            for entry in entries:
                uri = entry["uri"]
                if entry.get("isDir"):
                    if any(expected.startswith(f"{uri}/") for expected in expected_uris):
                        pending.append(uri)
                    else:
                        await self._viking_fs.rm(uri, recursive=True, ctx=ctx, no_vector=True)
                        removed += 1
                elif uri not in expected_uris:
                    await self._viking_fs.rm(uri, ctx=ctx, no_vector=True)
                    removed += 1
        return removed

    async def sync(self, ctx: RequestContext) -> int:
        root_ctx = replace(ctx, role=Role.ROOT, actor_peer_id=None)
        for uri in sorted(SYSTEM_FIXED_DIRECTORIES, key=lambda value: len(uri_parts(value))):
            await self._viking_fs.mkdir(uri, exist_ok=True, ctx=root_ctx)

        managed_assets = list(self._iter_managed_assets())
        expected_by_root = {target_root: set() for _, target_root in SYSTEM_ASSET_ROOT_MAPPINGS}
        pending_by_root: dict[str, list[dict[str, str]]] = {}
        for source_path, target_root, target_uri in managed_assets:
            expected_by_root[target_root].add(target_uri)
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
        changed = sum(len(operations) for operations in pending_by_root.values())
        changed += await self._remove_stale_assets(
            SYSTEM_VIKINGBOT_SKILLS_ROOT,
            expected_by_root[SYSTEM_VIKINGBOT_SKILLS_ROOT],
            root_ctx,
        )
        return changed
