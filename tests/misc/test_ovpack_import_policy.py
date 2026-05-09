# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Security regression tests for ovpack import target-policy enforcement."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from openviking.server.identity import RequestContext, Role
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


def _content_sha256(entries: list[dict[str, object]]) -> str:
    payload = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _manifest_for_files(root_name: str, files: dict[str, str]) -> dict[str, object]:
    entries: list[dict[str, object]] = [{"path": "", "kind": "directory"}]
    content_entries: list[dict[str, object]] = []
    for rel_path, content in sorted(files.items()):
        data = content.encode("utf-8")
        file_entry = {
            "path": rel_path,
            "kind": "file",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        entries.append(file_entry)
        content_entries.append(
            {
                "path": rel_path,
                "size": file_entry["size"],
                "sha256": file_entry["sha256"],
            }
        )

    return {
        "kind": "openviking.ovpack",
        "format_version": 2,
        "root": {"name": root_name},
        "entries": entries,
        "content_sha256": _content_sha256(content_entries),
        "vectors": {},
    }


def _write_ovpack_with_manifest(
    path: Path,
    root_name: str,
    files: dict[str, str],
    *,
    manifest: dict[str, object] | None = None,
) -> None:
    manifest = manifest or _manifest_for_files(root_name, files)
    entries = {
        f"{root_name}/": "",
        f"{root_name}/_._ovpack_manifest.json": json.dumps(manifest),
    }
    entries.update({f"{root_name}/{rel_path}": content for rel_path, content in files.items()})
    _write_ovpack(path, entries)


@pytest.mark.asyncio
async def test_export_ovpack_writes_v2_manifest_without_derived_files(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    await export_ovpack(
        FakeExportVikingFS(),
        "viking://resources/demo",
        str(temp_ovpack_path),
        ctx=request_ctx,
    )

    with zipfile.ZipFile(temp_ovpack_path, "r") as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("demo/_._ovpack_manifest.json").decode("utf-8"))

    assert "demo/notes.txt" in names
    assert "demo/_._overview.md" not in names
    assert manifest["format_version"] == 2
    assert manifest["kind"] == "openviking.ovpack"
    note_entry = next(entry for entry in manifest["entries"] if entry["path"] == "notes.txt")
    note_sha256 = hashlib.sha256(b"hello").hexdigest()
    assert note_entry["size"] == 5
    assert note_entry["sha256"] == note_sha256
    assert manifest["content_sha256"] == _content_sha256(
        [{"path": "notes.txt", "size": 5, "sha256": note_sha256}]
    )
    assert manifest["vectors"][""][0]["text"] == "root abstract"


@pytest.mark.asyncio
async def test_import_legacy_ovpack_without_manifest_is_rejected(
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

    with pytest.raises(InvalidArgumentError, match=r"Missing ovpack manifest"):
        await import_ovpack(fake_fs, str(temp_ovpack_path), "viking://resources", request_ctx)

    assert fake_fs.written_files == []


@pytest.mark.asyncio
async def test_import_ovpack_rejects_manifest_file_hash_mismatch(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    manifest = _manifest_for_files("demo", {"notes.txt": "hello"})
    _write_ovpack_with_manifest(
        temp_ovpack_path,
        "demo",
        {"notes.txt": "jello"},
        manifest=manifest,
    )
    fake_fs = FakeVikingFS()

    with pytest.raises(InvalidArgumentError, match=r"sha256 does not match manifest"):
        await import_ovpack(fake_fs, str(temp_ovpack_path), "viking://resources", request_ctx)

    assert fake_fs.written_files == []


@pytest.mark.asyncio
async def test_import_ovpack_rejects_legacy_manifest_version(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    manifest = _manifest_for_files("demo", {"notes.txt": "hello"})
    manifest["format_version"] = 1
    _write_ovpack_with_manifest(temp_ovpack_path, "demo", {"notes.txt": "hello"}, manifest=manifest)
    fake_fs = FakeVikingFS()

    with pytest.raises(InvalidArgumentError, match=r"Unsupported ovpack format_version 1"):
        await import_ovpack(fake_fs, str(temp_ovpack_path), "viking://resources", request_ctx)

    assert fake_fs.written_files == []


@pytest.mark.asyncio
async def test_import_ovpack_rejects_manifest_unexpected_directory(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    manifest = _manifest_for_files("demo", {"notes.txt": "hello"})
    _write_ovpack(
        temp_ovpack_path,
        {
            "demo/": "",
            "demo/_._ovpack_manifest.json": json.dumps(manifest),
            "demo/notes.txt": "hello",
            "demo/empty/": "",
        },
    )
    fake_fs = FakeVikingFS()

    with pytest.raises(InvalidArgumentError, match=r"entries do not match manifest") as exc_info:
        await import_ovpack(fake_fs, str(temp_ovpack_path), "viking://resources", request_ctx)

    assert exc_info.value.details["unexpected_directories"] == ["empty"]
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
        await import_ovpack(fake_fs, str(temp_ovpack_path), "viking://session/default", request_ctx)

    assert fake_fs.written_files == []
