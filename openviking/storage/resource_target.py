# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Concrete AGFS write side for planned content-tree actions.

This is the real AGFS implementation: it joins artifact-relative paths onto the
resource root and writes the already-normalized artifact bytes unchanged so the
stored bytes and their manifest md5 stay identical. Content mutation stays behind
this adapter so the planner remains independent from VikingFS URI and lease details.

An initial import is simply "the plan is all added", so the same target serves
both first-import (full upload) and incremental (diff subset) via one channel.
"""

from __future__ import annotations

from typing import Any, Optional

from openviking.utils.path_safety import safe_join_viking_uri


class AgfsResourceTarget:
    """Write/delete side of a diff apply against an AGFS resource tree."""

    def __init__(
        self,
        *,
        viking_fs: Any,
        root_uri: str,
        ctx: Any,
        lease_ref: Optional[dict] = None,
        resource_ttl: Optional[dict] = None,
    ) -> None:
        self._viking_fs = viking_fs
        self._root_uri = root_uri.rstrip("/")
        self._ctx = ctx
        self._lease_ref = lease_ref
        self._resource_ttl = None if resource_ttl is None else dict(resource_ttl)

    def _resolve(self, rel_path: str) -> str:
        # safe_join_viking_uri rejects absolute paths, drive prefixes and ..
        # traversal, keeping every write inside the resource root.
        return self._root_uri if not rel_path else safe_join_viking_uri(self._root_uri, rel_path)

    async def write_file(self, rel_path: str, data: bytes) -> bytes:
        """Write the artifact bytes unchanged; normalization happened at creation."""
        uri = self._resolve(rel_path)
        exists = await self._file_exists(uri) if self._resource_ttl is not None else False
        if self._resource_ttl is not None and not exists:
            from openviking.storage.resource_ttl import prepare_resource_ttl

            await prepare_resource_ttl(
                self._viking_fs,
                uri,
                is_dir=False,
                existing=False,
                ctx=self._ctx,
                lease_ref=self._lease_ref,
                resource_ttl=self._resource_ttl,
            )
        await self._viking_fs.write_file_bytes(uri, data, ctx=self._ctx, lease_ref=self._lease_ref)
        if self._resource_ttl is not None:
            from openviking.storage.resource_ttl import prepare_resource_ttl
            from openviking.utils.content_hash import content_md5

            # The relative deadline is based on the successful content write,
            # including the first write.  New files pre-publish their fence so
            # a crash cannot expose managed bytes without a cleanup record, then
            # renew that snapshot once the bytes are durable.
            await prepare_resource_ttl(
                self._viking_fs,
                uri,
                is_dir=False,
                existing=True,
                ctx=self._ctx,
                lease_ref=self._lease_ref,
                content_md5=content_md5(data),
            )
        return data

    async def _file_exists(self, uri: str) -> bool:
        try:
            stat = await self._viking_fs.stat(uri, ctx=self._ctx, skip_count=True)
        except Exception:
            return False
        if bool(stat.get("isDir", False)):
            return False
        from openviking.storage.resource_ttl import resource_ttl_visible

        return await resource_ttl_visible(self._viking_fs, uri, ctx=self._ctx)

    async def mkdir(self, rel_path: str) -> None:
        await self._viking_fs.mkdir(
            self._resolve(rel_path),
            exist_ok=True,
            ctx=self._ctx,
            lease_ref=self._lease_ref,
        )

    async def read_file(self, rel_path: str) -> bytes:
        return await self._viking_fs.read_file_bytes(self._resolve(rel_path), ctx=self._ctx)

    async def delete_path(self, rel_path: str, *, is_dir: bool) -> None:
        """Delete one planned path under the already-held resource tree lease."""
        uri = self._resolve(rel_path)
        await self._viking_fs.remove_files(
            uri,
            recursive=is_dir,
            ctx=self._ctx,
            lease_ref=self._lease_ref,
        )
        if not is_dir and self._resource_ttl is not None:
            # Content-tree plans deliberately bypass vector deletion here, but
            # an exact resource file's lifecycle sidecar/projection must leave
            # with the source or a later cleanup could target a replacement.
            await self._viking_fs._remove_resource_file_metadata(
                uri, ctx=self._ctx, lease_ref=self._lease_ref
            )


__all__ = ["AgfsResourceTarget"]
