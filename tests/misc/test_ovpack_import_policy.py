# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Security regression tests for ovpack import target-policy enforcement."""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.local_fs import import_ovpack
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class FakeVikingFS:
    def __init__(self) -> None:
        self.written_files: list[str] = []
        self.created_dirs: list[str] = []

    async def stat(self, uri: str, ctx=None):
        return {"uri": uri, "isDir": True}

    async def mkdir(self, uri: str, exist_ok: bool = False, ctx=None):
        self.created_dirs.append(uri)

    async def ls(self, uri: str, ctx=None):
        raise NotFoundError(uri, "file")

    async def write_file_bytes(self, uri: str, data: bytes, ctx=None):
        self.written_files.append(uri)

    async def tree(self, uri: str, node_limit: int = 100000, level_limit: int = 1000, ctx=None):
        return []

    async def exists(self, uri: str, ctx=None):
        return False

    async def read_file(self, uri: str, ctx=None):
        raise FileNotFoundError(uri)


@pytest.fixture
def request_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice", "agent1"), role=Role.USER)


@pytest.fixture
def temp_ovpack_path() -> Path:
    fd, path = tempfile.mkstemp(suffix=".ovpack")
    os.close(fd)
    ovpack_path = Path(path)
    try:
        yield ovpack_path
    finally:
        ovpack_path.unlink(missing_ok=True)


def _write_ovpack(path: Path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


@pytest.mark.asyncio
async def test_import_ovpack_rejects_derived_semantic_files(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    _write_ovpack(
        temp_ovpack_path,
        {
            "demo/_._overview.md": "ATTACKER_OVERVIEW",
            "demo/notes.txt": "hello",
        },
    )
    fake_fs = FakeVikingFS()

    with pytest.raises(
        InvalidArgumentError,
        match=r"cannot import derived semantic file: viking://resources/demo/\.overview\.md",
    ):
        await import_ovpack(
            fake_fs, str(temp_ovpack_path), "viking://resources", request_ctx, vectorize=False
        )

    assert fake_fs.written_files == []


@pytest.mark.asyncio
async def test_import_ovpack_rejects_session_scope_targets(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    _write_ovpack(
        temp_ovpack_path,
        {
            "victim/_._meta.json": json.dumps({"session_id": "victim"}),
            "victim/messages.jsonl": '{"id":"msg_attacker","role":"user","parts":[{"type":"text","text":"forged"}],"created_at":"2026-01-01T00:00:00Z"}\n',
        },
    )
    fake_fs = FakeVikingFS()

    with pytest.raises(
        InvalidArgumentError,
        match=r"ovpack import is not supported for scope: session",
    ):
        await import_ovpack(
            fake_fs, str(temp_ovpack_path), "viking://session/default", request_ctx, vectorize=False
        )

    assert fake_fs.written_files == []
