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
    """Reject AGFS moves/copies across encryption-account domains."""
    src_account = encryption_account_id_from_agfs_path(src_path)
    dst_account = encryption_account_id_from_agfs_path(dst_path)
    if src_account != dst_account:
        raise ValueError(
            f"cross-account AGFS move/copy is not supported: {src_account!r} -> {dst_account!r}"
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

    async def stat(
        self, path: str, *, fs_ctx: Dict[str, str] | None = None, bypass_cache: bool = False
    ) -> Dict[str, Any]:
        ctx = dict(_fs_ctx_or_default(path, fs_ctx))
        if bypass_cache:
            # Force plugin-local metadata caches (e.g. S3FS stat cache) to serve
            # fresh backend state so pollers observe writer-side changes.
            ctx["bypass_cache"] = "true"
        return await self.run("stat", path, ctx=ctx)

    async def mv(
        self, old_path: str, new_path: str, *, fs_ctx: Dict[str, str] | None = None
    ) -> Dict[str, Any]:
        ensure_same_encryption_account(old_path, new_path)
        return await self.run("mv", old_path, new_path, ctx=_fs_ctx_or_default(old_path, fs_ctx))

    async def cp(
        self,
        src_path: str,
        dst_path: str,
        recursive: bool = False,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Any:
        """Copy a path within AGFS while preserving the caller's FsContext."""
        from .helpers import cp

        return await asyncio.to_thread(
            cp,
            self._client,
            src_path,
            dst_path,
            recursive=recursive,
            fs_ctx=_fs_ctx_or_default(src_path, fs_ctx),
        )

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

    async def glob_directory(
        self,
        path: str,
        pattern: str,
        show_hidden: bool = False,
        page_size: int | None = None,
        level_limit: int | None = None,
        continuation_token: str | None = None,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        return await self.run(
            "glob_directory",
            path,
            pattern,
            show_hidden=show_hidden,
            page_size=page_size,
            level_limit=level_limit,
            continuation_token=continuation_token,
            ctx=_fs_ctx_or_default(path, fs_ctx),
        )

    async def system_sync_status(
        self, path: str, *, fs_ctx: Dict[str, str] | None = None
    ) -> Dict[str, Any]:
        """Return multi-write sync status for a file or directory path."""
        return await self.run("system_sync_status", path, ctx=_fs_ctx_or_default(path, fs_ctx))

    async def system_sync_retry(
        self, path: str, *, fs_ctx: Dict[str, str] | None = None
    ) -> Dict[str, Any]:
        """Retry pending multi-write sync work for a file or directory path."""
        return await self.run("system_sync_retry", path, ctx=_fs_ctx_or_default(path, fs_ctx))

    # -- pathlock async wrappers ------------------------------------------------

    async def pathlock_acquire_exact(
        self,
        path: str,
        timeout_secs: float = 0.0,
        owner_lease_ref: Dict[str, Any] | None = None,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Acquire an exact lock on a single path."""
        return await self.run(
            "pathlock_acquire_exact",
            _fs_ctx_or_default(path, fs_ctx),
            path,
            timeout_secs,
            owner_lease_ref,
        )

    async def pathlock_acquire_exact_batch(
        self,
        paths: list[str],
        timeout_secs: float = 0.0,
        owner_lease_ref: Dict[str, Any] | None = None,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Acquire exact locks on multiple paths."""
        return await self.run(
            "pathlock_acquire_exact_batch",
            _fs_ctx_or_default(paths[0] if paths else "/", fs_ctx),
            paths,
            timeout_secs,
            owner_lease_ref,
        )

    async def pathlock_acquire_tree(
        self,
        path: str,
        timeout_secs: float = 0.0,
        owner_lease_ref: Dict[str, Any] | None = None,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Acquire a tree lock on a single path."""
        return await self.run(
            "pathlock_acquire_tree",
            _fs_ctx_or_default(path, fs_ctx),
            path,
            timeout_secs,
            owner_lease_ref,
        )

    async def pathlock_acquire_tree_batch(
        self,
        paths: list[str],
        timeout_secs: float = 0.0,
        owner_lease_ref: Dict[str, Any] | None = None,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Acquire tree locks on multiple paths."""
        return await self.run(
            "pathlock_acquire_tree_batch",
            _fs_ctx_or_default(paths[0] if paths else "/", fs_ctx),
            paths,
            timeout_secs,
            owner_lease_ref,
        )

    async def pathlock_acquire_exact_tree_batch(
        self,
        exact_paths: list[str],
        tree_paths: list[str],
        timeout_secs: float = 0.0,
        owner_lease_ref: Dict[str, Any] | None = None,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Acquire a mixed batch of exact and tree locks."""
        first = exact_paths[0] if exact_paths else (tree_paths[0] if tree_paths else "/")
        return await self.run(
            "pathlock_acquire_exact_tree_batch",
            _fs_ctx_or_default(first, fs_ctx),
            exact_paths,
            tree_paths,
            timeout_secs,
            owner_lease_ref,
        )

    async def pathlock_acquire_batch(
        self,
        requests: list[Dict[str, str]],
        timeout_secs: float = 0.0,
        owner_lease_ref: Dict[str, Any] | None = None,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Acquire a batch of locks from a list of request dicts."""
        if not requests:
            raise ValueError("pathlock request batch must not be empty")
        for request in requests:
            path = request.get("path")
            if not path or not path.startswith("/"):
                raise ValueError("pathlock request.path must be an absolute path")
            if request.get("kind") not in {"exact", "tree"}:
                raise ValueError("pathlock request.kind must be 'exact' or 'tree'")
        first = requests[0]["path"]
        return await self.run(
            "pathlock_acquire_batch",
            _fs_ctx_or_default(first, fs_ctx),
            requests,
            timeout_secs,
            owner_lease_ref,
        )

    async def pathlock_as_borrowed(
        self,
        owned_lease_ref: Dict[str, Any],
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Create a borrowed view of an owned lease."""
        return await self.run(
            "pathlock_as_borrowed",
            _fs_ctx_or_default("/", fs_ctx),
            owned_lease_ref,
        )

    async def pathlock_refresh(
        self,
        owned_lease_ref: Dict[str, Any],
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> str:
        """Refresh an owned lease. Returns 'refreshed', 'lost', or 'failed'."""
        return await self.run(
            "pathlock_refresh",
            _fs_ctx_or_default("/", fs_ctx),
            owned_lease_ref,
        )

    async def pathlock_release(
        self,
        owned_lease_ref: Dict[str, Any],
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> None:
        """Release an owned lease."""
        await self.run(
            "pathlock_release",
            _fs_ctx_or_default("/", fs_ctx),
            owned_lease_ref,
        )

    async def pathlock_release_selected(
        self,
        owned_lease_ref: Dict[str, Any],
        lock_paths: list[str],
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> None:
        """Release selected lock paths from an owned lease."""
        await self.run(
            "pathlock_release_selected",
            _fs_ctx_or_default("/", fs_ctx),
            owned_lease_ref,
            lock_paths,
        )

    async def pathlock_to_handoff(
        self,
        owned_lease_ref: Dict[str, Any],
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Export a handoff ref from an owned lease."""
        return await self.run(
            "pathlock_to_handoff",
            _fs_ctx_or_default("/", fs_ctx),
            owned_lease_ref,
        )

    async def pathlock_handoff(
        self,
        owned_lease_ref: Dict[str, Any],
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> None:
        """Mark an owned lease as handed off."""
        await self.run(
            "pathlock_handoff",
            _fs_ctx_or_default("/", fs_ctx),
            owned_lease_ref,
        )

    async def pathlock_adopt(
        self,
        handoff_ref: Dict[str, Any],
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Adopt a handoff ref, returning a new owned lease."""
        return await self.run(
            "pathlock_adopt",
            _fs_ctx_or_default("/", fs_ctx),
            handoff_ref,
        )

    async def pathlock_is_locked(
        self,
        path: str,
        ignore_stale: bool = True,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> bool:
        """Check if a path is locked."""
        return await self.run(
            "pathlock_is_locked",
            _fs_ctx_or_default(path, fs_ctx),
            path,
            ignore_stale,
        )

    async def pathlock_observe(
        self,
        *,
        fs_ctx: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Return an observability snapshot of current lock state."""
        return await self.run(
            "pathlock_observe",
            _fs_ctx_or_default("/", fs_ctx),
        )
