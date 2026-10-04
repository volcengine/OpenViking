# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read-only TTL projection for public listings and object details."""

import asyncio

from openviking.config.ttl import resolve_ttl_config
from openviking.core.ttl import hidden_by_ttl, ttl_object_for_uri, ttl_scope_for_uri
from openviking.storage.directory_ttl import read_directory_fields
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.ttl_config import TTLPolicy


def lifetime_fields(fields):
    return {"expires_at": fields.get("expires_at") or None, "ttl_days": fields.get("ttl_days")}


class TTLView:
    """Expose the effective owner deadline without modifying stored metadata."""

    def __init__(self, fs, ctx):
        self.fs, self.ctx = fs, ctx
        self._config = None
        self._owners = {}

    async def fields(self, uri, *, is_dir=False):
        scope = ttl_scope_for_uri(uri)
        if scope is None:
            return lifetime_fields({})
        target = ttl_object_for_uri(uri)
        if target:
            root = target[1]
            if root not in self._owners:
                self._owners[root] = asyncio.create_task(self._read_owner(root))
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
                "ttl_days": policy.ttl_days,
                "policy": policy.model_dump(exclude_none=True),
                "effective_policy": effective.model_dump(exclude_none=True),
            }
        return lifetime_fields({})

    async def attach(self, entry):
        uri = entry.get("uri")
        return (
            {**entry, **await self.fields(uri, is_dir=entry.get("isDir", False))} if uri else entry
        )

    async def _read_owner(self, root):
        fields = await read_directory_fields(self.fs, root, ctx=self.ctx)
        if not fields:
            record = await self.fs.ttl_registry.get(self.ctx.account_id, root)
            if record:
                return {"expires_at": record.expires_at}
        return fields

    async def visible(self, uri):
        return not hidden_by_ttl((await self.fields(uri)).get("expires_at"))

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
