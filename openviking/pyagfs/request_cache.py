# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-owned L0 identities; never serialize these into background work."""

import asyncio
import logging
import posixpath
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

logger = logging.getLogger(__name__)


class RequestCacheScope:
    def __init__(self):
        self.cache_id = uuid4().hex
        self.accounts = set()
        self.bindings = {}
        self.active = True
        self.operations = set()
        self._close_task = None

    async def close(self):
        from openviking.service.task_tracker_concurrency import run_to_completion

        self.active = False
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._drain_and_release())
        await run_to_completion(lambda: self._close_task)

    async def _drain_and_release(self):
        # A failed gather sibling need not cancel calls queued in the executor.
        # Those calls must finish before release, or they can recreate registry entries.
        await asyncio.gather(*self.operations, return_exceptions=True)
        for client, account_id in self.bindings.values():
            try:
                # Release only drops a registry reference; no backend IO is involved.
                client.release_request_cache(account_id, self.cache_id)
            except Exception:
                logger.exception("Failed to release request stat cache")
        self.bindings.clear()


request_cache_scope: ContextVar[RequestCacheScope | None] = ContextVar(
    "agfs_request_cache_scope", default=None
)


def bind_request_cache(ctx):
    scope = request_cache_scope.get()
    if scope is not None and scope.active:
        scope.accounts.add(ctx.account_id)
        ctx.cache_id = scope.cache_id
    return ctx


def request_cache_fs_ctx(client, fs_ctx, path=None):
    scope = request_cache_scope.get()
    if scope is None or not scope.active or fs_ctx is None:
        return fs_ctx
    account_id = fs_ctx.get("account_id")
    if account_id not in scope.accounts:
        return fs_ctx
    if isinstance(path, str):
        normalized = posixpath.normpath("/" + path.lstrip("/"))
        if normalized == "/queue" or normalized.startswith("/queue/"):
            return fs_ctx
    if not callable(getattr(client, "release_request_cache", None)):
        return fs_ctx
    scope.bindings[(id(client), account_id)] = (client, account_id)
    return {**fs_ctx, "cache_id": scope.cache_id}


def without_request_cache(func):
    """Bypass L0 for polling or work that must observe cross-request updates."""

    @wraps(func)
    async def wrapped(*args, **kwargs):
        token = request_cache_scope.set(None)
        try:
            return await func(*args, **kwargs)
        finally:
            request_cache_scope.reset(token)

    return wrapped
