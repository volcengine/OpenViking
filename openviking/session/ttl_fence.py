# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Write fence for asynchronous work derived from a TTL session."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from openviking.core.ttl import hidden_by_ttl
from openviking.server.error_mapping import is_storage_not_found
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime


class StaleSessionGenerationError(RuntimeError):
    """Raised when delayed work no longer belongs to the live session incarnation."""


async def reconcile_session_ttl(
    viking_fs: Any,
    ctx: Any,
    *,
    session_uri: str,
    generation: str,
    lease_ref: Any,
    archive_uri: str = "",
) -> dict[str, Any] | None:
    """Repair a durable Phase 2 completion before deciding that a session expired.

    The caller holds the session tree lock. Completion is recorded in the
    archive before root metadata, so both recovery and deletion must replay
    that record. Never renew from enqueue/Phase 1 timestamps or retry time.
    """
    meta_uri = f"{session_uri}/.meta.json"
    try:
        metadata = json.loads(await viking_fs.read_file(meta_uri, ctx=ctx, include_expired=True))
    except Exception as exc:
        if is_storage_not_found(exc):
            return None
        raise
    if not isinstance(metadata, dict):
        raise ValueError(f"Invalid session metadata: {session_uri}")
    if metadata.get("ttl_generation") != generation or not metadata.get("ttl_days"):
        return metadata
    if not archive_uri and not hidden_by_ttl(metadata.get("expires_at")):
        return metadata

    from datetime import timedelta

    expiry = parse_iso_datetime(metadata["expires_at"])
    received = parse_iso_datetime(metadata["received_at"])
    renewed = expiry

    async def read_completion(uri: str) -> None:
        nonlocal renewed
        for name in (".meta.json", ".done"):
            try:
                marker = json.loads(
                    await viking_fs.read_file(f"{uri}/{name}", ctx=ctx, include_expired=True)
                )
            except Exception as exc:
                if is_storage_not_found(exc):
                    continue
                raise
            if not isinstance(marker, dict):
                raise ValueError(f"Invalid session completion marker: {uri}/{name}")
            completed_at = marker.get("phase2_completed_at")
            if not completed_at or marker.get("ttl_generation", generation) != generation:
                continue
            completed = parse_iso_datetime(completed_at)
            # Generation-less markers are from the pre-fence format. They can
            # only authorize renewal within this incarnation's lifetime.
            if completed < received or (not marker.get("ttl_generation") and completed >= expiry):
                continue
            renewed = max(renewed, completed + timedelta(days=metadata["ttl_days"]))

    if archive_uri:
        await read_completion(archive_uri)
    else:
        # Cleanup only scans this expired session's own history, in bounded
        # pages. Public ls intentionally hides expired sessions.
        path = viking_fs._uri_to_path(f"{session_uri}/history", ctx=ctx)
        offset = 0
        while True:
            try:
                entries = await viking_fs._async_agfs.ls(
                    path, offset=offset, limit=128, sort_by="name"
                )
            except Exception as exc:
                if is_storage_not_found(exc):
                    break
                raise
            for entry in entries:
                name = str(entry.get("name", ""))
                if re.fullmatch(r"archive_\d+", name):
                    await read_completion(f"{session_uri}/history/{name}")
            if len(entries) < 128:
                break
            offset += len(entries)
    if renewed > expiry:
        metadata["expires_at"] = format_iso8601(renewed)
        await viking_fs.write_file(meta_uri, json.dumps(metadata), ctx=ctx, lease_ref=lease_ref)
    return metadata


@dataclass(frozen=True, slots=True)
class SessionGenerationFence:
    """Validate one frozen session generation while holding its tree lock.

    Empty generations preserve the legacy behavior and do not acquire an
    additional lock. TTL-aware callers use :meth:`lock` only around their
    final persistence step, after any expensive LLM or embedding work.
    """

    viking_fs: Any
    ctx: Any
    session_uri: str = ""
    generation: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.viking_fs is not None and self.session_uri and self.generation)

    @property
    def key(self) -> tuple[str, str]:
        return (self.session_uri.rstrip("/"), self.generation) if self.enabled else ("", "")

    def lock_path(self) -> str:
        if not self.enabled:
            return ""
        return self.viking_fs._uri_to_path(self.session_uri.rstrip("/"), ctx=self.ctx)

    async def is_current(self, *, allow_expired: bool = False) -> bool:
        """Return whether the source session still has this live generation."""
        if not self.enabled:
            return True
        try:
            raw = await self.viking_fs.read_file(
                f"{self.session_uri.rstrip('/')}/.meta.json",
                ctx=self.ctx,
                include_expired=True,
            )
        except Exception as exc:
            if is_storage_not_found(exc):
                return False
            # An unavailable source is not proof that this generation is stale.
            # Let the normal failure/retry path handle storage errors.
            raise
        metadata = json.loads(raw)
        if not isinstance(metadata, dict):
            raise ValueError(f"Invalid session metadata: {self.session_uri}")
        return str(metadata.get("ttl_generation") or "") == self.generation and (
            allow_expired or not hidden_by_ttl(metadata.get("expires_at"))
        )

    async def require_current(self, *, allow_expired: bool = False) -> None:
        if not await self.is_current(allow_expired=allow_expired):
            raise StaleSessionGenerationError(f"stale TTL session generation: {self.session_uri}")

    @asynccontextmanager
    async def lock(self) -> AsyncIterator[Any | None]:
        """Hold the source session tree lock and validate before yielding."""
        if not self.enabled:
            yield None
            return
        lease = await self.viking_fs._async_agfs.pathlock_acquire_tree(
            self.lock_path(), timeout_secs=300.0
        )
        try:
            await self.require_current()
            yield lease
        finally:
            await self.viking_fs._async_agfs.pathlock_release(lease)

    @asynccontextmanager
    async def lock_policy_set(self, policy_set: Any) -> AsyncIterator[Any]:
        """Atomically lock a policy root and this session before apply."""
        if not self.enabled:
            async with policy_set.lock() as lease:
                yield lease
            return
        if policy_set.viking_fs is not self.viking_fs:
            raise RuntimeError("TTL fence and policy set must use the same VikingFS")
        policy_path = self.viking_fs._uri_to_path(
            policy_set.root_uri, ctx=policy_set.request_context
        )
        lease = await self.viking_fs._async_agfs.pathlock_acquire_batch(
            [
                {"path": policy_path, "kind": "tree"},
                {"path": self.lock_path(), "kind": "tree"},
            ],
            timeout_secs=300.0,
        )
        try:
            await self.require_current()
            yield lease
        finally:
            await self.viking_fs._async_agfs.pathlock_release(lease)


def session_generation_fence(
    viking_fs: Any,
    ctx: Any,
    *,
    session_uri: str = "",
    generation: str = "",
) -> SessionGenerationFence:
    return SessionGenerationFence(
        viking_fs=viking_fs,
        ctx=ctx,
        session_uri=str(session_uri or "").rstrip("/"),
        generation=str(generation or ""),
    )
