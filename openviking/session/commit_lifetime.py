# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Validate asynchronous writes against the original persisted commit marker."""

import inspect
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps

from openviking.core.ttl import hidden_by_ttl
from openviking.server.error_mapping import is_storage_not_found
from openviking_cli.exceptions import NotFoundError

_commit = ContextVar("session_commit_lifetime", default=None)
_write_lease = ContextVar("session_commit_write_lease", default=None)


class StaleSessionCommit(NotFoundError):
    def __init__(self, uri):
        super().__init__(uri, "session commit")


async def validate_commit(fs, ctx, session_uri, archive_uri, task_id):
    """The Phase 1 task ID survives task-history eviction but not session deletion."""

    async def read(uri):
        return json.loads(await fs.read_file(uri, ctx=ctx, include_expired=True))

    try:
        meta = await read(session_uri + "/.meta.json")
        archive = await read(archive_uri + "/.meta.json")
    except Exception as exc:
        if is_storage_not_found(exc):
            raise StaleSessionCommit(session_uri) from exc
        raise
    saved_id = archive.get("phase1", {}).get("queue_message", {}).get("task_id")
    if not task_id or saved_id != task_id or hidden_by_ttl(meta.get("expires_at")):
        raise StaleSessionCommit(session_uri)
    return meta


@asynccontextmanager
async def commit_write(fs, ctx, lease_ref=None):
    """Hold a session exact lock only across validation and persistence, never LLM work."""
    identity = _commit.get()
    if identity is None:
        yield lease_ref
        return
    session_uri, archive_uri, task_id = identity
    lease = await fs._async_agfs.pathlock_acquire_exact(
        fs._uri_to_path(session_uri, ctx=ctx),
        owner_lease_ref=lease_ref or _write_lease.get(),
        timeout_secs=30.0,
    )
    token = _write_lease.set(lease)
    try:
        await validate_commit(fs, ctx, session_uri, archive_uri, task_id)
        yield lease
    finally:
        _write_lease.reset(token)
        await fs._async_agfs.pathlock_release(lease)


def commit_scope(method):
    """Carry the original queue identity through nested async extraction calls."""
    signature = inspect.signature(method)

    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        arguments = signature.bind(self, *args, **kwargs).arguments
        msg = arguments.get("msg")
        archive_uri = msg.archive_uri if msg is not None else arguments["archive_uri"]
        task_id = msg.task_id if msg is not None else arguments["task_id"]
        token = _commit.set((self._session_uri, archive_uri, task_id))
        try:
            async with commit_write(self._viking_fs, self.ctx):
                pass
            return await method(self, *args, **kwargs)
        finally:
            _commit.reset(token)

    return wrapped
