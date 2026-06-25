# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Snapshot (multi-version) namespace for OpenViking clients.

Exposes the snapshot/versioning methods on BaseClient under a
`client.snapshot.*` namespace so the user-facing API reads as
`client.snapshot.commit(...)` rather than the flat `client.git_commit(...)`
underneath.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from openviking_cli.utils import run_async

if TYPE_CHECKING:
    from openviking.async_client import AsyncOpenViking
    from openviking.sync_client import SyncOpenViking


class AsyncSnapshotNamespace:
    """Snapshot version control methods on the async client.

    Forwards to the underlying BaseClient's git_* methods.
    """

    def __init__(self, client: "AsyncOpenViking"):
        self._client = client

    async def commit(
        self,
        *,
        message: str,
        paths: Optional[List[str]] = None,
        branch: str = "main",
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._client._ensure_initialized()
        return await self._client._client.git_commit(
            message=message,
            paths=paths,
            branch=branch,
            author_name=author_name,
            author_email=author_email,
        )

    async def restore(
        self,
        *,
        project_dir: Optional[str] = None,
        source_commit: str,
        branch: str = "main",
        dry_run: bool = False,
        message: Optional[str] = None,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._client._ensure_initialized()
        return await self._client._client.git_restore(
            project_dir=project_dir,
            source_commit=source_commit,
            branch=branch,
            dry_run=dry_run,
            message=message,
            author_name=author_name,
            author_email=author_email,
        )

    async def show(
        self,
        target_ref: str,
        *,
        path: Optional[str] = None,
    ) -> Any:
        await self._client._ensure_initialized()
        return await self._client._client.git_show(target_ref, path=path)

    async def log(
        self,
        *,
        branch: str = "main",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        await self._client._ensure_initialized()
        return await self._client._client.git_log(branch=branch, limit=limit)


class SyncSnapshotNamespace:
    """Synchronous wrapper around AsyncSnapshotNamespace.

    Each method calls into the SyncOpenViking's underlying async client
    via run_async, matching the rest of the SyncOpenViking surface.
    """

    def __init__(self, client: "SyncOpenViking"):
        self._client = client

    def _ns(self) -> AsyncSnapshotNamespace:
        return self._client._async_client.snapshot

    def commit(
        self,
        *,
        message: str,
        paths: Optional[List[str]] = None,
        branch: str = "main",
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._ns().commit(
                message=message,
                paths=paths,
                branch=branch,
                author_name=author_name,
                author_email=author_email,
            )
        )

    def restore(
        self,
        *,
        project_dir: Optional[str] = None,
        source_commit: str,
        branch: str = "main",
        dry_run: bool = False,
        message: Optional[str] = None,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._ns().restore(
                project_dir=project_dir,
                source_commit=source_commit,
                branch=branch,
                dry_run=dry_run,
                message=message,
                author_name=author_name,
                author_email=author_email,
            )
        )

    def show(
        self,
        target_ref: str,
        *,
        path: Optional[str] = None,
    ) -> Any:
        return run_async(self._ns().show(target_ref, path=path))

    def log(
        self,
        *,
        branch: str = "main",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        return run_async(self._ns().log(branch=branch, limit=limit))
