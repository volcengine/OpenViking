# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-only TTL projection for public listings and object details."""

import asyncio

from openviking.config.ttl import resolve_ttl_config
from openviking.core.ttl import hidden_by_ttl, ttl_object_for_uri, ttl_scope_for_uri
from openviking.server.error_mapping import is_storage_not_found
from openviking.storage.directory_ttl import read_directory_fields
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.ttl_config import TTLPolicy


def lifetime_fields(fields):
    return {"expires_at": fields.get("expires_at") or None}


class TTLView:
    """Expose the effective owner deadline without modifying stored metadata."""

    def __init__(self, fs, ctx):
        self.fs, self.ctx = fs, ctx
        self._config = None
        self._owners = {}
        self._owner_exists = {}

    async def fields(self, uri, *, is_dir=False):
        scope = ttl_scope_for_uri(uri)
        if scope is None:
            return lifetime_fields({})
        target = ttl_object_for_uri(uri)
        if target:
            root = target[1]
            if root not in self._owners:
                self._owners[root] = asyncio.create_task(
                    read_directory_fields(self.fs, root, ctx=self.ctx)
                )
            return lifetime_fields(await self._owners[root])
        if is_dir and uri.rstrip("/").endswith(("/events", "/sessions")):
            if self._config is None:
                self._config = (
                    await resolve_ttl_config(self.fs, self.ctx.account_id)
                    or get_openviking_config().ttl
                )
            policy = self._config.directories.get(uri.rstrip("/"), TTLPolicy())
            effective = self._config.resolve_uri_policy(uri, scope)
            return {
                **lifetime_fields({}),
                "policy": policy.model_dump(exclude_none=True),
                "effective_policy": effective.model_dump(exclude_none=True),
            }
        return lifetime_fields({})

    async def attach(self, entry):
        uri = entry.get("uri")
        return (
            {**entry, **await self.fields(uri, is_dir=entry.get("isDir", False))} if uri else entry
        )

    async def visible(self, uri, *, require_owner=False):
        if hidden_by_ttl((await self.fields(uri)).get("expires_at")):
            return False
        target = ttl_object_for_uri(uri)
        if require_owner and target:
            root = target[1]
            # Persisted metadata already establishes the owner. Legacy objects
            # without metadata need one directory check, never one per hit.
            if not await self._owners[root]:
                if root not in self._owner_exists:
                    self._owner_exists[root] = asyncio.create_task(self._directory_exists(root))
                return await self._owner_exists[root]
        return True

    async def _directory_exists(self, root):
        try:
            await self.fs._async_agfs.stat(
                self.fs._uri_to_path(root, ctx=self.ctx), bypass_cache=True
            )
        except Exception as exc:
            if is_storage_not_found(exc):
                return False
            raise
        return True

    async def attach_many(self, entries):
        # Bound concurrency while sharing each owner read across sibling files.
        result = []
        for start in range(0, len(entries), 16):
            result.extend(
                await asyncio.gather(
                    *(
                        self.attach(entry)
                        if isinstance(entry, dict)
                        else asyncio.sleep(0, result=entry)
                        for entry in entries[start : start + 16]
                    )
                )
            )
        return result
