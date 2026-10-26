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
    ) -> None:
        self._viking_fs = viking_fs
        self._root_uri = root_uri.rstrip("/")
        self._ctx = ctx
        self._lease_ref = lease_ref

    def _resolve(self, rel_path: str) -> str:
        # safe_join_viking_uri rejects absolute paths, drive prefixes and ..
        # traversal, keeping every write inside the resource root.
        return self._root_uri if not rel_path else safe_join_viking_uri(self._root_uri, rel_path)

    async def write_file(self, rel_path: str, data: bytes) -> bytes:
        """Write the artifact bytes unchanged; normalization happened at creation."""
        uri = self._resolve(rel_path)
        await self._viking_fs.write_file_bytes(uri, data, ctx=self._ctx, lease_ref=self._lease_ref)
        return data

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
        await self._viking_fs.remove_files(
            self._resolve(rel_path),
            recursive=is_dir,
            ctx=self._ctx,
            lease_ref=self._lease_ref,
        )


__all__ = ["AgfsResourceTarget"]
