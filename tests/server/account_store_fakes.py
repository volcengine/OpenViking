# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Test doubles for file-backed account-store tests."""

import threading
import time
import uuid

from openviking.pyagfs import AGFSNotFoundError


class InMemoryAGFS:
    def __init__(self):
        self._files: dict[str, bytes] = {}
        self._modified_at: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._leases: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def read(self, path: str, ctx=None) -> bytes:
        del ctx
        with self._guard:
            try:
                return self._files[path]
            except KeyError as exc:
                raise AGFSNotFoundError(path) from exc

    def write(self, path: str, content: bytes, ctx=None) -> None:
        del ctx
        with self._guard:
            self._files[path] = bytes(content)
            self._modified_at[path] = time.monotonic()

    def stat(self, path: str, ctx=None, bypass_cache: bool = False) -> dict:
        del ctx, bypass_cache
        with self._guard:
            try:
                content = self._files[path]
            except KeyError as exc:
                raise AGFSNotFoundError(path) from exc
            return {
                "size": len(content),
                "modTime": self._modified_at[path],
                "isDir": False,
            }

    def ensure_parent_dirs(self, path: str, ctx=None) -> None:
        del path, ctx

    def pathlock_acquire_exact(
        self,
        ctx,
        path: str,
        timeout_secs: float = 0.0,
        owner_lease_ref=None,
    ) -> dict:
        del ctx, owner_lease_ref
        with self._guard:
            lock = self._locks.setdefault(path, threading.Lock())
        if not lock.acquire(timeout=timeout_secs):
            raise TimeoutError(f"timed out acquiring account store lock: {path}")
        lease_ref = uuid.uuid4().hex
        with self._guard:
            self._leases[lease_ref] = lock
        return {"lease_ref": lease_ref, "owned": True}

    def pathlock_release(self, ctx, owned_lease_ref: dict) -> None:
        del ctx
        lease_ref = owned_lease_ref["lease_ref"]
        with self._guard:
            lock = self._leases.pop(lease_ref)
        lock.release()
