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
from openviking.storage.local_fs import backup_ovpack, export_ovpack, import_ovpack, restore_ovpack
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class FakeVikingFS:
    def __init__(self) -> None:
        self.written_files: list[str] = []
        self.created_dirs: list[str] = []
        self.tree_calls: list[str] = []

    async def stat(self, uri: str, ctx=None):
        return {"uri": uri, "isDir": True}

    async def mkdir(self, uri: str, exist_ok: bool = False, ctx=None):
        self.created_dirs.append(uri)

    async def ls(self, uri: str, ctx=None):
        raise NotFoundError(uri, "file")

    async def write_file_bytes(self, uri: str, data: bytes, ctx=None):
        self.written_files.append(uri)

    async def tree(self, uri: str, node_limit=None, level_limit=None, ctx=None):
        self.tree_calls.append(uri)
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

    async def tree(
        self,
        uri: str,
        show_all_hidden: bool = False,
        node_limit=None,
        level_limit=None,
        ctx=None,
    ):
        assert uri == "viking://resources/demo"
        assert show_all_hidden is True
        assert node_limit is None
        assert level_limit is None
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
        if uri in self.text_files:
            return self.text_files[uri].encode("utf-8")
        return self.binary_files[uri]


class FakeBackupVikingFS:
    def __init__(self) -> None:
        self.binary_files = {
            "viking://resources/README.md": b"hello",
            "viking://session/sess_1/.meta.json": b'{"session_id":"sess_1"}',
        }

    async def tree(
        self,
        uri: str,
        show_all_hidden: bool = False,
        node_limit=None,
        level_limit=None,
        ctx=None,
    ):
        assert show_all_hidden is True
        assert node_limit is None
        assert level_limit is None
        if uri == "viking://resources":
            return [
                {
                    "rel_path": "README.md",
                    "uri": "viking://resources/README.md",
                    "isDir": False,
                    "size": 5,
                }
            ]
        if uri == "viking://session":
            return [
                {
                    "rel_path": "sess_1",
                    "uri": "viking://session/sess_1",
                    "isDir": True,
                    "size": 0,
                },
                {
                    "rel_path": "sess_1/.meta.json",
                    "uri": "viking://session/sess_1/.meta.json",
                    "isDir": False,
                    "size": 23,
                },
            ]
        return []

    async def exists(self, uri: str, ctx=None):
        return False

    async def read_file(self, uri: str, ctx=None):
        raise FileNotFoundError(uri)

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
        "root": {
            "name": root_name,
            "uri": f"viking://resources/{root_name}",
            "scope": "resources",
        },
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
async def test_export_ovpack_writes_v2_manifest_with_semantic_sidecars(
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
    assert "demo/_._overview.md" in names
    assert manifest["format_version"] == 2
    assert manifest["kind"] == "openviking.ovpack"
    note_entry = next(entry for entry in manifest["entries"] if entry["path"] == "notes.txt")
    note_sha256 = hashlib.sha256(b"hello").hexdigest()
    assert note_entry["size"] == 5
    assert note_entry["sha256"] == note_sha256
    overview_entry = next(entry for entry in manifest["entries"] if entry["path"] == ".overview.md")
    overview_sha256 = hashlib.sha256(b"root overview").hexdigest()
    assert overview_entry["sha256"] == overview_sha256
    assert manifest["content_sha256"] == _content_sha256(
        [
            {"path": ".overview.md", "size": 13, "sha256": overview_sha256},
            {"path": "notes.txt", "size": 5, "sha256": note_sha256},
        ]
    )
    assert manifest["vectors"][""][0]["text"] == "root abstract"


@pytest.mark.asyncio
async def test_backup_restore_contract(temp_ovpack_path: Path, request_ctx: RequestContext):
    await backup_ovpack(
        FakeBackupVikingFS(),
        str(temp_ovpack_path),
        ctx=request_ctx,
    )

    with zipfile.ZipFile(temp_ovpack_path, "r") as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("openviking-backup/_._ovpack_manifest.json").decode("utf-8"))

    assert "openviking-backup/resources/README.md" in names
    assert "openviking-backup/session/sess_1/_._meta.json" in names
    assert manifest["root"] == {
        "name": "openviking-backup",
        "uri": "viking://",
        "scope": "root",
        "package_type": "backup",
    }
    assert manifest["scopes"] == ["resources", "user", "agent", "session"]

    with pytest.raises(InvalidArgumentError, match=r"must be restored"):
        await import_ovpack(FakeVikingFS(), str(temp_ovpack_path), "viking://", request_ctx)

    fake_fs = FakeVikingFS()
    assert await restore_ovpack(fake_fs, str(temp_ovpack_path), request_ctx) == "viking://"
    assert fake_fs.written_files == [
        "viking://resources/README.md",
        "viking://session/sess_1/.meta.json",
    ]
    assert fake_fs.tree_calls == ["viking://resources", "viking://user", "viking://agent"]


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
async def test_import_ovpack_restores_session_without_vectorization(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    files = {
        ".meta.json": json.dumps({"session_id": "victim"}),
        "messages.jsonl": '{"id":"msg_1","role":"user","parts":[{"type":"text","text":"hi"}]}\n',
    }
    manifest = _manifest_for_files("victim", files)
    manifest["root"] = {
        "name": "victim",
        "uri": "viking://session/victim",
        "scope": "session",
    }
    _write_ovpack_with_manifest(
        temp_ovpack_path,
        "victim",
        files,
        manifest=manifest,
    )
    fake_fs = FakeVikingFS()

    result = await import_ovpack(fake_fs, str(temp_ovpack_path), "viking://session", request_ctx)

    assert result == "viking://session/victim"
    assert fake_fs.written_files == [
        "viking://session/victim/.meta.json",
        "viking://session/victim/messages.jsonl",
    ]
    assert fake_fs.tree_calls == []

    invalid_fs = FakeVikingFS()
    with pytest.raises(InvalidArgumentError, match=r"source scope does not match target scope"):
        await import_ovpack(invalid_fs, str(temp_ovpack_path), "viking://resources", request_ctx)
    with pytest.raises(InvalidArgumentError, match=r"source path is incompatible"):
        await import_ovpack(
            invalid_fs,
            str(temp_ovpack_path),
            "viking://session/victim",
            request_ctx,
        )
    assert invalid_fs.written_files == []


@pytest.mark.asyncio
async def test_import_ovpack_rejects_scope_mismatch(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    manifest = _manifest_for_files("demo", {"notes.txt": "hello"})
    _write_ovpack_with_manifest(temp_ovpack_path, "demo", {"notes.txt": "hello"}, manifest=manifest)
    fake_fs = FakeVikingFS()

    with pytest.raises(InvalidArgumentError, match=r"source scope does not match target scope"):
        await import_ovpack(fake_fs, str(temp_ovpack_path), "viking://session", request_ctx)

    assert fake_fs.written_files == []


@pytest.mark.asyncio
async def test_import_top_level_scope_package_requires_root_target(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    manifest = _manifest_for_files("resources", {"README.md": "hello"})
    manifest["root"] = {
        "name": "resources",
        "uri": "viking://resources",
        "scope": "resources",
    }
    _write_ovpack_with_manifest(
        temp_ovpack_path,
        "resources",
        {"README.md": "hello"},
        manifest=manifest,
    )
    fake_fs = FakeVikingFS()

    with pytest.raises(InvalidArgumentError, match=r"must be imported to viking://"):
        await import_ovpack(fake_fs, str(temp_ovpack_path), "viking://resources", request_ctx)

    assert await import_ovpack(fake_fs, str(temp_ovpack_path), "viking://", request_ctx) == (
        "viking://resources"
    )
