# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Concrete AGFS write side for :func:`apply_diff_plan`.

``apply_diff_plan`` is backend-agnostic over a duck-typed ``target``. This is the
real AGFS implementation: it joins artifact-relative paths onto the resource
root, normalizes text encoding exactly like directory uploads so a file's final
stored bytes (and therefore its md5) match between AGFS and local artifact modes,
and deletes files / L2 vector records.

An initial import is simply "the plan is all added", so the same target serves
both first-import (full upload) and incremental (diff subset) via one channel.
"""

from __future__ import annotations

from typing import Any, Optional

from openviking.parse.parsers.upload_utils import detect_and_convert_encoding
from openviking.utils.path_safety import safe_join_viking_uri


class AgfsResourceTarget:
    """Write/delete side of a diff apply against an AGFS resource tree."""

    def __init__(
        self,
        *,
        viking_fs: Any,
        vikingdb: Any,
        root_uri: str,
        ctx: Any,
        lease_ref: Optional[dict] = None,
    ) -> None:
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb
        self._root_uri = root_uri.rstrip("/")
        self._ctx = ctx
        self._lease_ref = lease_ref

    def _resolve(self, rel_path: str) -> str:
        # safe_join_viking_uri rejects absolute paths, drive prefixes and ..
        # traversal, keeping every write inside the resource root.
        return safe_join_viking_uri(self._root_uri, rel_path)

    async def write_file(self, rel_path: str, data: bytes) -> bytes:
        """Write ``data`` under the resource root, returning final stored bytes.

        Encoding normalization mirrors the directory upload path so the bytes
        that land in AGFS are identical regardless of artifact backend; the apply
        executor hashes the returned bytes so md5 tracks the final content.
        """
        final_bytes = detect_and_convert_encoding(data, rel_path)
        uri = self._resolve(rel_path)
        await self._viking_fs.write_file_bytes(
            uri, final_bytes, ctx=self._ctx, lease_ref=self._lease_ref
        )
        return final_bytes

    async def read_file(self, rel_path: str) -> bytes:
        return await self._viking_fs.read_file_bytes(self._resolve(rel_path), ctx=self._ctx)

    async def delete_file(self, rel_path: str) -> None:
        await self._viking_fs.rm(
            self._resolve(rel_path),
            recursive=True,
            ctx=self._ctx,
            lease_ref=self._lease_ref,
        )

    async def delete_vector(self, rel_path: str) -> None:
        await self._vikingdb.delete_uris(self._ctx, [self._resolve(rel_path)])


__all__ = ["AgfsResourceTarget"]
