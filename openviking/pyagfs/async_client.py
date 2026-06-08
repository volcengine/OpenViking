# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Async adapter for the synchronous AGFS client."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, BinaryIO, Dict, List, Union

from .protocols import AGFSSyncClientProtocol

_SYSTEM_ACCOUNT_ID = "_system"


def fs_ctx_from_agfs_path(path: str) -> Dict[str, str]:
    """Derive a stable FsContext from an absolute AGFS path.

    `/local/{account_id}/...` paths use their path-scoped account to match
    VikingFS URI conversion. Plugin/system paths do not encode a tenant, so
    they use the reserved system account key instead of running without ctx.
    """
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "local" and parts[1]:
        return {"account_id": parts[1]}
    return {"account_id": _SYSTEM_ACCOUNT_ID}


def _fs_ctx_or_default(path: str, fs_ctx: Dict[str, str] | None) -> Dict[str, str]:
    """Return explicit FsContext when present, otherwise derive it from path."""
    return fs_ctx if fs_ctx is not None else fs_ctx_from_agfs_path(path)


def local_account_id_from_agfs_path(path: str) -> str | None:
    """Extract the account_id from `/local/{account_id}/...`, or None for non-local paths."""
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "local" and parts[1]:
        return parts[1]
    return None


def encryption_account_id_from_agfs_path(path: str) -> str:
    """Return the account_id used by the encryption layer for this AGFS path."""
    return fs_ctx_from_agfs_path(path)["account_id"]


def ensure_same_encryption_account(src_path: str, dst_path: str) -> None:
    """Reject raw moves/copies that would preserve ciphertext under a different account key."""
    src_account = encryption_account_id_from_agfs_path(src_path)
    dst_account = encryption_account_id_from_agfs_path(dst_path)
    if src_account != dst_account:
        raise ValueError(
            "cross-account AGFS move/copy is not supported by raw path operations: "
            f"{src_account!r} -> {dst_account!r}"
        )


class AsyncAGFSClient:
    """Run blocking AGFS binding client operations off the event loop.

    This is intentionally a thin adapter over the synchronous RAGFS binding
    client (``RAGFSBindingClient``). If the binding later provides native async
    methods, they can be swapped in here without changing storage and
    transaction call sites.
    """

    def __init__(self, client: AGFSSyncClientProtocol):
        self._client = client

    @property
    def sync_client(self) -> AGFSSyncClientProtocol:
        return self._client

    async def run(self, method_name: str, /, *args: Any, **kwargs: Any) -> Any:
        """Run a sync client method in a worker thread, preserving ctx when supported."""
        try:
            return await asyncio.to_thread(getattr(self._client, method_name), *args, **kwargs)
        except TypeError as exc:
            message = str(exc)
            if "ctx" not in kwargs or "unexpected keyword argument 'ctx'" not in message:
                raise
            legacy_kwargs = dict(kwargs)
            legacy_kwargs.pop("ctx", None)
            return await asyncio.to_thread(
                getattr(self._client, method_name), *args, **legacy_kwargs
            )

    async def ls(
        self, path: str = "/", *, fs_ctx: Dict[str, str] | None = None
    ) -> List[Dict[str, Any]]:
        return await self.run("ls", path, ctx=_fs_ctx_or_default(path, fs_ctx))

    async def read(
        self,
        path: str,
        offset: int = 0,
        size: int = -1,
        stream: bool = False,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Any:
        kwargs: Dict[str, Any] = {}
        if offset != 0:
            kwargs["offset"] = offset
        if size != -1:
            kwargs["size"] = size
        if stream:
            kwargs["stream"] = stream
        return await self.run("read", path, **kwargs, ctx=_fs_ctx_or_default(path, fs_ctx))

    async def cat(
        self,
        path: str,
        offset: int = 0,
        size: int = -1,
        stream: bool = False,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Any:
        kwargs: Dict[str, Any] = {}
        if offset != 0:
            kwargs["offset"] = offset
        if size != -1:
            kwargs["size"] = size
        if stream:
            kwargs["stream"] = stream
        return await self.run("cat", path, **kwargs, ctx=_fs_ctx_or_default(path, fs_ctx))

    async def read_raw(self, path: str, offset: int = 0, size: int = -1) -> Any:
        """Read raw bytes, bypassing the encryption layer (cp/persist whole-blob copy)."""
        kwargs: Dict[str, Any] = {}
        if offset != 0:
            kwargs["offset"] = offset
        if size != -1:
            kwargs["size"] = size
        return await self.run("read_raw", path, **kwargs)

    async def write_raw(self, path: str, data: bytes) -> str:
        """Write raw bytes, bypassing the encryption layer (cp/persist whole-blob copy)."""
        return await self.run("write_raw", path, data)

    async def write(
        self,
        path: str,
        data: Union[bytes, Iterator[bytes], BinaryIO],
        max_retries: int = 3,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> str:
        if max_retries == 3:
            return await self.run("write", path, data, ctx=_fs_ctx_or_default(path, fs_ctx))
        return await self.run(
            "write", path, data, max_retries=max_retries, ctx=_fs_ctx_or_default(path, fs_ctx)
        )

    async def mkdir(
        self, path: str, mode: str = "755", *, fs_ctx: Dict[str, str] | None = None
    ) -> Dict[str, Any]:
        if mode == "755":
            return await self.run("mkdir", path, ctx=_fs_ctx_or_default(path, fs_ctx))
        return await self.run("mkdir", path, mode=mode, ctx=_fs_ctx_or_default(path, fs_ctx))

    async def ensure_parent_dirs(
        self, path: str, mode: str = "755", *, fs_ctx: Dict[str, str] | None = None
    ) -> Dict[str, Any]:
        if mode == "755":
            return await self.run("ensure_parent_dirs", path, ctx=_fs_ctx_or_default(path, fs_ctx))
        return await self.run(
            "ensure_parent_dirs", path, mode=mode, ctx=_fs_ctx_or_default(path, fs_ctx)
        )

    async def rm(
        self,
        path: str,
        recursive: bool = False,
        force: bool = True,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if recursive:
            kwargs["recursive"] = recursive
        if not force:
            kwargs["force"] = force
        return await self.run("rm", path, **kwargs, ctx=_fs_ctx_or_default(path, fs_ctx))

    async def stat(self, path: str, *, fs_ctx: Dict[str, str] | None = None) -> Dict[str, Any]:
        return await self.run("stat", path, ctx=_fs_ctx_or_default(path, fs_ctx))

    async def mv(
        self, old_path: str, new_path: str, *, fs_ctx: Dict[str, str] | None = None
    ) -> Dict[str, Any]:
        ensure_same_encryption_account(old_path, new_path)
        return await self.run("mv", old_path, new_path, ctx=_fs_ctx_or_default(old_path, fs_ctx))

    async def cp(self, src_path: str, dst_path: str, recursive: bool = False) -> Any:
        from .helpers import cp

        return await asyncio.to_thread(cp, self._client, src_path, dst_path, recursive=recursive)

    async def grep(self, **kwargs: Any) -> Dict[str, Any]:
        if "ctx" not in kwargs or kwargs["ctx"] is None:
            path = kwargs.get("path")
            if isinstance(path, str):
                kwargs["ctx"] = fs_ctx_from_agfs_path(path)
        return await self.run("grep", **kwargs)

    async def tree_directory(
        self,
        path: str,
        show_hidden: bool = False,
        node_limit: int | None = None,
        level_limit: int | None = None,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> list[Dict[str, Any]]:
        return await self.run(
            "tree_directory",
            path,
            show_hidden=show_hidden,
            node_limit=node_limit,
            level_limit=level_limit,
            ctx=_fs_ctx_or_default(path, fs_ctx),
        )
