# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""User privacy config storage service."""

import json
from typing import Any, Optional

from openviking_cli.exceptions import NotFoundError

from openviking.core.namespace import canonical_user_root
from openviking.privacy.helpers import (
    canonicalize_values,
    config_root_uri,
    current_uri,
    history_dir_uri,
    meta_uri,
    parse_version_filename,
    version_uri,
)
from openviking.privacy.models import UserPrivacyConfigMeta, UserPrivacyConfigVersion
from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import VikingFS
from openviking.utils.time_utils import get_current_timestamp


class UserPrivacyConfigService:
    """Manage user-scoped privacy configs with version history."""

    def __init__(self, viking_fs: VikingFS):
        self._viking_fs = viking_fs

    def _user_root(self, ctx: RequestContext) -> str:
        return canonical_user_root(ctx)

    def _user_space(self, ctx: RequestContext) -> str:
        return self._user_root(ctx)[len("viking://user/") :]

    def get_config_root(self, ctx: RequestContext, category: str, target_key: str) -> str:
        return config_root_uri(self._user_space(ctx), category, target_key)

    async def exists(self, ctx: RequestContext, category: str, target_key: str) -> bool:
        try:
            await self._viking_fs.stat(self.get_config_root(ctx, category, target_key), ctx=ctx)
            return True
        except Exception:
            return False

    async def _ensure_root(self, ctx: RequestContext, category: str, target_key: str) -> None:
        root_uri = self.get_config_root(ctx, category, target_key)
        await self._viking_fs.mkdir(root_uri, exist_ok=True, ctx=ctx)
        await self._viking_fs.mkdir(history_dir_uri(self._user_space(ctx), category, target_key), exist_ok=True, ctx=ctx)

    async def get_meta(
        self, ctx: RequestContext, category: str, target_key: str
    ) -> Optional[UserPrivacyConfigMeta]:
        try:
            content = await self._viking_fs.read_file(
                meta_uri(self._user_space(ctx), category, target_key), ctx=ctx
            )
        except Exception:
            return None
        return UserPrivacyConfigMeta.from_dict(json.loads(content))

    async def _save_meta(
        self, ctx: RequestContext, category: str, target_key: str, meta: UserPrivacyConfigMeta
    ) -> None:
        meta.updated_at = get_current_timestamp()
        await self._viking_fs.write_file(
            meta_uri(self._user_space(ctx), category, target_key),
            json.dumps(meta.to_dict(), ensure_ascii=False),
            ctx=ctx,
        )

    async def get_current(
        self, ctx: RequestContext, category: str, target_key: str
    ) -> Optional[UserPrivacyConfigVersion]:
        try:
            content = await self._viking_fs.read_file(
                current_uri(self._user_space(ctx), category, target_key), ctx=ctx
            )
        except Exception:
            return None
        return UserPrivacyConfigVersion.from_dict(json.loads(content))

    async def get_version(
        self, ctx: RequestContext, category: str, target_key: str, version: int
    ) -> Optional[UserPrivacyConfigVersion]:
        try:
            content = await self._viking_fs.read_file(
                version_uri(self._user_space(ctx), category, target_key, version), ctx=ctx
            )
        except Exception:
            return None
        return UserPrivacyConfigVersion.from_dict(json.loads(content))

    async def list_categories(self, ctx: RequestContext) -> list[str]:
        uri = f"{self._user_root(ctx)}/privacy"
        try:
            entries = await self._viking_fs.ls(uri, ctx=ctx)
        except Exception:
            return []
        return sorted(entry["name"] for entry in entries if entry.get("name"))

    async def list_targets(self, ctx: RequestContext, category: str) -> list[str]:
        uri = f"{self._user_root(ctx)}/privacy/{category}"
        try:
            entries = await self._viking_fs.ls(uri, ctx=ctx)
        except Exception:
            return []
        return sorted(entry["name"] for entry in entries if entry.get("name"))

    async def list_versions(self, ctx: RequestContext, category: str, target_key: str) -> list[int]:
        try:
            entries = await self._viking_fs.ls(
                history_dir_uri(self._user_space(ctx), category, target_key), ctx=ctx
            )
        except Exception:
            return []
        versions = []
        for entry in entries:
            version = parse_version_filename(entry.get("name", ""))
            if version is not None:
                versions.append(version)
        return sorted(versions)

    async def upsert(
        self,
        ctx: RequestContext,
        category: str,
        target_key: str,
        values: dict[str, Any],
        updated_by: str = "",
        change_reason: str = "",
        labels: Optional[dict[str, Any]] = None,
    ) -> UserPrivacyConfigVersion:
        await self._ensure_root(ctx, category, target_key)
        now = get_current_timestamp()
        meta = await self.get_meta(ctx, category, target_key)
        current = await self.get_current(ctx, category, target_key)

        if current and canonicalize_values(current.values) == canonicalize_values(values):
            if meta is None:
                meta = UserPrivacyConfigMeta(
                    category=category,
                    target_key=target_key,
                    active_version=current.version,
                    latest_version=current.version,
                    created_at=now,
                )
            meta.updated_by = updated_by
            meta.last_accessed_at = now
            if labels is not None:
                meta.labels = dict(labels)
            await self._save_meta(ctx, category, target_key, meta)
            return current

        version_number = 1 if meta is None else meta.latest_version + 1
        version = UserPrivacyConfigVersion(
            version=version_number,
            category=category,
            target_key=target_key,
            values=dict(values),
            created_at=now,
            created_by=updated_by,
            change_reason=change_reason,
        )

        await self._viking_fs.write_file(
            version_uri(self._user_space(ctx), category, target_key, version_number),
            json.dumps(version.to_dict(), ensure_ascii=False),
            ctx=ctx,
        )
        await self._viking_fs.write_file(
            current_uri(self._user_space(ctx), category, target_key),
            json.dumps(version.to_dict(), ensure_ascii=False),
            ctx=ctx,
        )

        if meta is None:
            meta = UserPrivacyConfigMeta(
                category=category,
                target_key=target_key,
                created_at=now,
            )
        meta.active_version = version_number
        meta.latest_version = version_number
        meta.updated_by = updated_by
        meta.last_accessed_at = now
        if labels is not None:
            meta.labels = dict(labels)
        await self._save_meta(ctx, category, target_key, meta)
        return version

    async def activate_version(
        self,
        ctx: RequestContext,
        category: str,
        target_key: str,
        version: int,
        updated_by: str = "",
    ) -> UserPrivacyConfigVersion:
        meta = await self.get_meta(ctx, category, target_key)
        snapshot = await self.get_version(ctx, category, target_key, version)
        if meta is None or snapshot is None:
            raise NotFoundError(f"{category}/{target_key}/versions/{version}", "privacy config")

        await self._viking_fs.write_file(
            current_uri(self._user_space(ctx), category, target_key),
            json.dumps(snapshot.to_dict(), ensure_ascii=False),
            ctx=ctx,
        )
        meta.active_version = version
        meta.updated_by = updated_by
        meta.last_accessed_at = get_current_timestamp()
        await self._save_meta(ctx, category, target_key, meta)
        return snapshot
