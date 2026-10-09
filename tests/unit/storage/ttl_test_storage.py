# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Minimal raw storage for snapshot lifecycle tests."""

import asyncio

from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs.exceptions import AGFSDirectoryNotEmptyError


class MemoryAGFS(AsyncAGFSClient):
    def __init__(self):
        self.files = {}
        self.ensure_calls = []
        self.write_calls = []
        self.read_calls = []
        self.stat_calls = []
        self.rm_calls = []
        self.ls_calls = []
        self.locks = {}

    async def ensure_parent_dirs(self, path, **kwargs):
        self.ensure_calls.append(path)

    async def pathlock_acquire_exact(self, path, **kwargs):
        lock = self.locks.setdefault(path, asyncio.Lock())
        await lock.acquire()
        return path

    pathlock_acquire_tree = pathlock_acquire_exact

    async def pathlock_release(self, lease):
        self.locks[lease].release()

    async def write(self, path, data, **kwargs):
        self.write_calls.append(path)
        self.files[path] = data

    async def read(self, path, **kwargs):
        self.read_calls.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def stat(self, path, **kwargs):
        self.stat_calls.append(path)
        if path in self.files:
            return {"isDir": False, "size": len(self.files[path])}
        if any(key.startswith(path.rstrip("/") + "/") for key in self.files):
            return {"isDir": True}
        raise FileNotFoundError(path)

    async def rm(self, path, **kwargs):
        self.rm_calls.append(path)
        if path in self.files:
            del self.files[path]
        elif any(key.startswith(path + "/") for key in self.files):
            raise AGFSDirectoryNotEmptyError("directory not empty")
        else:
            raise FileNotFoundError(path)

    async def ls(self, path, *, offset=0, limit=None, sort_by=None, **kwargs):
        self.ls_calls.append((path, limit))
        prefix = path.rstrip("/") + "/"
        names = {}
        for key in self.files:
            if key.startswith(prefix):
                suffix = key[len(prefix) :]
                name = suffix.split("/")[0]
                names[name] = {"name": name, "isDir": "/" in suffix}
        values = [names[name] for name in sorted(names)]
        return values[offset:] if limit is None else values[offset : offset + limit]


async def read_record(fs, account_id, uri):
    """Read a queue candidate from authoritative directory metadata."""
    from openviking.core.ttl import ttl_object_for_uri
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.directory_ttl import read_directory_fields
    from openviking.storage.ttl_registry import TTLRecord
    from openviking_cli.session.user_id import UserIdentifier

    target = ttl_object_for_uri(uri)
    if target is None or target[1] != uri:
        return None
    user_id = uri.removeprefix("viking://").split("/")[1]
    ctx = RequestContext(user=UserIdentifier(account_id, user_id), role=Role.ROOT)
    fields = await read_directory_fields(fs, uri, ctx=ctx)
    return TTLRecord(uri, target[0], account_id) if fields.get("expires_at") else None
