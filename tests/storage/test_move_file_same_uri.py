# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression test: ``move_file`` must reject an aliased source and target
before its copy+rm sequence deletes the only copy."""

from unittest.mock import AsyncMock

import pytest

from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError

URI = "viking://resources/acct/doc.md"
PATH = "/local/acct/resources/acct/doc.md"


class _AGFS:
    def __init__(self):
        self.files = {PATH: b"body"}

    async def stat(self, path, fs_ctx=None):
        if path in self.files:
            return {"isDir": False}
        raise FileNotFoundError(path)

    async def read(self, path, size=-1, fs_ctx=None):
        data = self.files[path]
        return data if size < 0 else data[:size]

    async def write(self, path, data, fs_ctx=None, auto_pathlock=True):
        self.files[path] = data

    async def rm(self, path, recursive=False, fs_ctx=None):
        self.files.pop(path, None)


def _viking_fs(monkeypatch, agfs) -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs._async_agfs = agfs
    fs.vector_store = None
    fs.acl_manager = None
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(fs, "_ensure_parent_dirs", AsyncMock())
    monkeypatch.setattr(fs, "authorize_read", lambda ctx, uris: ctx)
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx: ctx)
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, **_kwargs: "/local/acct/" + uri.removeprefix("viking://"),
    )
    return fs


async def test_move_file_rejects_same_uri_without_deleting_it(monkeypatch):
    agfs = _AGFS()
    fs = _viking_fs(monkeypatch, agfs)

    with pytest.raises(InvalidArgumentError, match="must be different"):
        await fs.move_file(URI, URI)

    assert agfs.files[PATH] == b"body"


async def test_move_file_still_moves_to_a_different_uri(monkeypatch):
    agfs = _AGFS()
    fs = _viking_fs(monkeypatch, agfs)

    await fs.move_file(URI, "viking://resources/acct/moved.md")

    assert agfs.files == {"/local/acct/resources/acct/moved.md": b"body"}
