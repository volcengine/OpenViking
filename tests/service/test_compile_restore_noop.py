# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from openviking.server.identity import RequestContext, Role
from openviking.service.compile_targets import CompileVersions
from openviking_cli.session.user_id import UserIdentifier


class _Store:
    def __init__(self, record):
        self._record = record

    @asynccontextmanager
    async def lock_version_index(self, *_args, **_kwargs):
        yield None

    async def get(self, _account_id, _target_to):
        return self._record


class _Fs:
    def __init__(self, commit_oid):
        self.commit_oid = commit_oid

    async def _ensure_access(self, *_args, **_kwargs):
        return None

    async def log(self, *_args, **_kwargs):
        return [{"oid": self.commit_oid}]

    async def show(self, _ref, *, path, ctx):
        assert path
        return b"restored content"


async def test_restore_noop_without_versions_returns_metadata():
    uri = "viking://resources/project/generated.md"
    target = "viking://resources/project"
    commit = "a" * 40
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role(Role.USER))
    versions = CompileVersions(_Store({"to": target}), _Fs(commit))

    versions._managed = AsyncMock(return_value={"to": target})
    versions._running = AsyncMock(return_value=False)
    versions.load = AsyncMock(return_value=None)
    versions.record_write = AsyncMock(return_value=None)
    # Git produced no snapshot for the restore commit (no-op commit or no Git support).
    versions._commit = AsyncMock(return_value=None)

    async def record_operation(_uris, _ctx, _type, _params, operation, **_kwargs):
        return await operation()

    versions.record_operation = record_operation

    written = []

    async def write(content, commit_content):
        written.append(content)
        return await commit_content([uri], lease_ref=None, write=None)

    result = await versions.restore(uri, commit, commit, ctx, write)

    assert written == [b"restored content"]
    assert set(result) == {"uri", "version_id", "version_number", "meta", "result"}
    assert result["uri"] == uri
    assert result["result"] == "noop"
    assert result["version_id"] is None
    assert result["version_number"] is None
