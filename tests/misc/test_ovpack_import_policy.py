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
from openviking.storage.expr import Eq
from openviking.storage.local_fs import export_ovpack, import_ovpack
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


class FakeExportVikingFS:
    def __init__(self) -> None:
        self.binary_files = {
            "viking://resources/demo/notes.txt": b"hello",
        }
        self.text_files = {
            "viking://resources/demo/.abstract.md": "root abstract",
            "viking://resources/demo/.overview.md": "root overview",
        }

    async def tree(self, uri: str, show_all_hidden: bool = False, ctx=None):
        assert uri == "viking://resources/demo"
        assert show_all_hidden is True
        return [
            {
                "rel_path": ".overview.md",
                "uri": "viking://resources/demo/.overview.md",
                "isDir": False,
                "size": 13,
            },
            {
                "rel_path": "notes.txt",
                "uri": "viking://resources/demo/notes.txt",
                "isDir": False,
                "size": 5,
            },
        ]

    async def exists(self, uri: str, ctx=None):
        return uri in self.text_files

    async def read_file(self, uri: str, ctx=None):
        return self.text_files[uri]

    async def read_file_bytes(self, uri: str, ctx=None):
        return self.binary_files[uri]


class FakeVectorStore:
    async def filter(self, filter, limit: int, output_fields, ctx=None):
        assert isinstance(filter, Eq)
        assert limit == 10
        records = {
            "viking://resources/demo": [
                {
                    "uri": "viking://resources/demo",
                    "context_type": "resource",
                    "level": 0,
                    "abstract": "root abstract",
                    "vector": [1, 2, 3],
                    "active_count": 2,
                }
            ],
            "viking://resources/demo/notes.txt": [
                {
                    "uri": "viking://resources/demo/notes.txt",
                    "context_type": "resource",
                    "level": 2,
                    "abstract": "note summary",
                    "active_count": 7,
                    "vector": [4, 5, 6],
                }
            ],
        }
        return records.get(filter.value, [])


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
async def test_export_ovpack_writes_v2_manifest_without_derived_files(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    await export_ovpack(
        FakeExportVikingFS(),
        "viking://resources/demo",
        str(temp_ovpack_path),
        ctx=request_ctx,
        vector_store=FakeVectorStore(),
    )

    with zipfile.ZipFile(temp_ovpack_path, "r") as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("demo/_._ovpack_manifest.json").decode("utf-8"))

    assert "demo/notes.txt" in names
    assert "demo/_._overview.md" not in names
    assert manifest["format_version"] == 2
    assert manifest["kind"] == "openviking.ovpack"
    assert manifest["vectors"][""][0]["text"] == "root abstract"
    assert manifest["vectors"]["notes.txt"][0]["scalars"]["active_count"] == 7
    assert "vector" not in manifest["vectors"]["notes.txt"][0]["scalars"]


@pytest.mark.asyncio
async def test_import_ovpack_on_conflict_skip_does_not_write(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    _write_ovpack(
        temp_ovpack_path,
        {
            "demo/": "",
            "demo/notes.txt": "hello",
        },
    )
    fake_fs = FakeVikingFS()

    async def existing_root(uri: str, ctx=None):
        if uri == "viking://resources/demo":
            return []
        raise NotFoundError(uri, "file")

    fake_fs.ls = existing_root

    imported_uri = await import_ovpack(
        fake_fs,
        str(temp_ovpack_path),
        "viking://resources",
        request_ctx,
        vectorize=False,
        on_conflict="skip",
    )

    assert imported_uri == "viking://resources/demo"
    assert fake_fs.written_files == []


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
