# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Async adapter for the synchronous AGFS client."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, BinaryIO, Dict, List, Union


class AsyncAGFSClient:
    """Run blocking AGFS client operations off the event loop.

    This is intentionally a thin adapter over the existing synchronous client.
    If AGFS later provides native async methods, they can be swapped in here
    without changing storage and transaction call sites.
    """

    def __init__(self, client: Any):
        self._client = client

    @property
    def sync_client(self) -> Any:
        return self._client

    async def run(self, method_name: str, /, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(getattr(self._client, method_name), *args, **kwargs)

    async def ls(self, path: str = "/") -> List[Dict[str, Any]]:
        return await self.run("ls", path)

    async def read(self, path: str, offset: int = 0, size: int = -1, stream: bool = False) -> Any:
        kwargs: Dict[str, Any] = {}
        if offset != 0:
            kwargs["offset"] = offset
        if size != -1:
            kwargs["size"] = size
        if stream:
            kwargs["stream"] = stream
        return await self.run("read", path, **kwargs)

    async def cat(self, path: str, offset: int = 0, size: int = -1, stream: bool = False) -> Any:
        kwargs: Dict[str, Any] = {}
        if offset != 0:
            kwargs["offset"] = offset
        if size != -1:
            kwargs["size"] = size
        if stream:
            kwargs["stream"] = stream
        return await self.run("cat", path, **kwargs)

    async def write(
        self, path: str, data: Union[bytes, Iterator[bytes], BinaryIO], max_retries: int = 3
    ) -> str:
        if max_retries == 3:
            return await self.run("write", path, data)
        return await self.run("write", path, data, max_retries=max_retries)

    async def mkdir(self, path: str, mode: str = "755") -> Dict[str, Any]:
        if mode == "755":
            return await self.run("mkdir", path)
        return await self.run("mkdir", path, mode=mode)

    async def ensure_parent_dirs(self, path: str, mode: str = "755") -> Dict[str, Any]:
        if mode == "755":
            return await self.run("ensure_parent_dirs", path)
        return await self.run("ensure_parent_dirs", path, mode=mode)

    async def rm(self, path: str, recursive: bool = False, force: bool = True) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if recursive:
            kwargs["recursive"] = recursive
        if not force:
            kwargs["force"] = force
        return await self.run("rm", path, **kwargs)

    async def stat(self, path: str) -> Dict[str, Any]:
        return await self.run("stat", path)

    async def mv(self, old_path: str, new_path: str) -> Dict[str, Any]:
        return await self.run("mv", old_path, new_path)

    async def cp(self, src_path: str, dst_path: str, recursive: bool = False) -> Any:
        from .helpers import cp

        return await asyncio.to_thread(cp, self._client, src_path, dst_path, recursive=recursive)

    async def grep(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.run("grep", **kwargs)
