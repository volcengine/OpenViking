# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests: ``VikingFS.read`` must distinguish a missing file from
an empty one, like ``read_file`` / ``read_file_bytes`` already do."""

from unittest.mock import AsyncMock

import pytest

from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError

ROOT = "/local/acct/resources/acct"
DOC_URI = "viking://resources/acct/doc.md"
EMPTY_URI = "viking://resources/acct/empty.md"
MISSING_URI = "viking://resources/acct/missing.md"
DIR_URI = "viking://resources/acct"


class _AGFS:
    """AGFS surface whose ``read`` returns empty bytes for a missing path."""

    def __init__(self):
        self.files = {
            f"{ROOT}/doc.md": b"body",
            f"{ROOT}/empty.md": b"",
        }
        self.directories = {ROOT}

    async def stat(self, path, fs_ctx=None):
        if path in self.directories:
            return {"isDir": True}
        if path in self.files:
            return {"isDir": False}
        raise FileNotFoundError(path)

    async def read(self, path, offset=0, size=-1):
        data = self.files.get(path, b"")
        return data[offset:] if size < 0 else data[offset : offset + size]


def _viking_fs(monkeypatch, agfs) -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs._async_agfs = agfs
    fs.vector_store = None
    fs.acl_manager = None
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(fs, "authorize_read", lambda ctx, uris: ctx)
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx: ctx)
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, **_kwargs: "/local/acct/" + uri.removeprefix("viking://"),
    )
    return fs


async def test_read_missing_file_raises_not_found(monkeypatch):
    fs = _viking_fs(monkeypatch, _AGFS())

    with pytest.raises(NotFoundError):
        await fs.read(MISSING_URI)


async def test_read_empty_file_still_returns_empty_bytes(monkeypatch):
    fs = _viking_fs(monkeypatch, _AGFS())

    assert await fs.read(EMPTY_URI) == b""


async def test_read_directory_uri_is_rejected(monkeypatch):
    fs = _viking_fs(monkeypatch, _AGFS())

    with pytest.raises(InvalidArgumentError):
        await fs.read(DIR_URI)


async def test_read_existing_file_and_slice(monkeypatch):
    fs = _viking_fs(monkeypatch, _AGFS())

    assert await fs.read(DOC_URI) == b"body"
    assert await fs.read(DOC_URI, offset=1, size=2) == b"od"
