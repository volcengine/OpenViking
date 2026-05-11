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
from openviking.storage.index_consistency import IndexConsistencyReport, IndexExpectation
from openviking.storage.ovpack.operations import (
    backup_ovpack,
    export_ovpack,
    import_ovpack,
    restore_ovpack,
)
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


class OverviewOnlyExportVikingFS(FakeExportVikingFS):
    def __init__(self) -> None:
        super().__init__()
        self.text_files = {
            "viking://resources/demo/.overview.md": "root overview",
        }


class ReservedPathExportVikingFS(FakeExportVikingFS):
    def __init__(self) -> None:
        super().__init__()
        self.binary_files.update(
            {
                "viking://resources/demo/.ovpack/foo.txt": b"hello",
                "viking://resources/demo/.notes.txt": b"dot",
                "viking://resources/demo/_._notes.txt": b"escaped-looking",
            }
        )

    async def tree(
        self,
        uri: str,
        show_all_hidden: bool = False,
        node_limit=None,
        level_limit=None,
        ctx=None,
    ):
        assert uri == "viking://resources/demo"
        return [
            {
                "rel_path": ".ovpack",
                "uri": "viking://resources/demo/.ovpack",
                "isDir": True,
                "size": 0,
            },
            {
                "rel_path": ".ovpack/foo.txt",
                "uri": "viking://resources/demo/.ovpack/foo.txt",
                "isDir": False,
                "size": 5,
            },
            {
                "rel_path": ".notes.txt",
                "uri": "viking://resources/demo/.notes.txt",
                "isDir": False,
                "size": 3,
            },
            {
                "rel_path": "_._notes.txt",
                "uri": "viking://resources/demo/_._notes.txt",
                "isDir": False,
                "size": 15,
            },
        ]


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


class FakeRestoreVectorVikingFS(FakeVikingFS):
    async def tree(self, uri: str, node_limit=None, level_limit=None, ctx=None):
        self.tree_calls.append(uri)
        if uri == "viking://resources":
            return [
                {
                    "rel_path": "README.md",
                    "uri": "viking://resources/README.md",
                    "isDir": False,
                    "name": "README.md",
                }
            ]
        return []


class FakeVectorStore:
    def __init__(self) -> None:
        self.upserts: list[dict[str, object]] = []

    async def filter(self, **kwargs):
        uri = kwargs["filter"].value
        if uri == "viking://resources/demo":
            return [
                {
                    "uri": uri,
                    "context_type": "resource",
                    "level": 0,
                    "abstract": "root abstract",
                    "vector": [0.4, 0.5, 0.6],
                },
                {
                    "uri": uri,
                    "context_type": "resource",
                    "level": 1,
                    "abstract": "root overview",
                    "vector": [0.7, 0.8, 0.9],
                },
            ]
        if uri == "viking://resources/demo/notes.txt":
            return [
                {
                    "uri": uri,
                    "context_type": "resource",
                    "level": 2,
                    "abstract": "note summary",
                    "tags": ["snapshot"],
                    "vector": [0.1, 0.2, 0.3],
                }
            ]
        return []

    async def upsert(self, data, ctx=None):
        self.upserts.append(dict(data))
        return data.get("id", "")


class IncompleteVectorStore(FakeVectorStore):
    async def filter(self, **kwargs):
        return []


class OverviewOnlyVectorStore(FakeVectorStore):
    async def filter(self, **kwargs):
        uri = kwargs["filter"].value
        if uri == "viking://resources/demo":
            return [
                {
                    "uri": uri,
                    "context_type": "resource",
                    "level": 1,
                    "abstract": "root overview",
                    "vector": [0.7, 0.8, 0.9],
                }
            ]
        if uri == "viking://resources/demo/notes.txt":
            return await super().filter(**kwargs)
        return []


class HybridIndexVectorStore(FakeVectorStore):
    _index_name = "context_idx"

    def _get_default_backend(self):
        return self

    def _get_collection(self):
        return self

    def get_index_meta_data(self, index_name):
        assert index_name == self._index_name
        return {"VectorIndex": {"IndexType": "flat_hybrid"}}


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


def _index_records_bytes(records: list[dict[str, object]]) -> bytes:
    if not records:
        return b""
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _set_manifest_index(
    manifest: dict[str, object], records: list[dict[str, object]] | None = None
) -> bytes:
    records = records or []
    data = _index_records_bytes(records)
    manifest["index"] = {
        "records": {
            "path": "_ovpack/index_records.jsonl",
            "count": len(records),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    }
    return data


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

    manifest: dict[str, object] = {
        "kind": "openviking.ovpack",
        "format_version": 2,
        "root": {
            "name": root_name,
            "uri": f"viking://resources/{root_name}",
            "scope": "resources",
        },
        "entries": entries,
        "content_sha256": _content_sha256(content_entries),
    }
    _set_manifest_index(manifest)
    return manifest


def _write_ovpack_with_manifest(
    path: Path,
    root_name: str,
    files: dict[str, str],
    *,
    manifest: dict[str, object] | None = None,
    index_records: list[dict[str, object]] | None = None,
) -> None:
    manifest = manifest or _manifest_for_files(root_name, files)
    index_data = _set_manifest_index(manifest, index_records)
    entries = {
        f"{root_name}/": "",
        f"{root_name}/files/": "",
        f"{root_name}/_ovpack/": "",
        f"{root_name}/_ovpack/index_records.jsonl": index_data.decode("utf-8"),
        f"{root_name}/_ovpack/manifest.json": json.dumps(manifest),
    }
    entries.update(
        {f"{root_name}/files/{rel_path}": content for rel_path, content in files.items()}
    )
    _write_ovpack(path, entries)


def _rewrite_ovpack_index(
    path: Path,
    root_name: str,
    index_records: list[dict[str, object]],
) -> None:
    with zipfile.ZipFile(path, "r") as zf:
        members = {info.filename: zf.read(info.filename) for info in zf.infolist()}

    manifest_path = f"{root_name}/_ovpack/manifest.json"
    index_path = f"{root_name}/_ovpack/index_records.jsonl"
    manifest = json.loads(members[manifest_path].decode("utf-8"))
    index_data = _set_manifest_index(manifest, index_records)
    members[manifest_path] = json.dumps(manifest).encode("utf-8")
    members[index_path] = index_data

    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _rewrite_ovpack_manifest(path: Path, root_name: str, mutate) -> None:
    with zipfile.ZipFile(path, "r") as zf:
        members = {info.filename: zf.read(info.filename) for info in zf.infolist()}

    manifest_path = f"{root_name}/_ovpack/manifest.json"
    manifest = json.loads(members[manifest_path].decode("utf-8"))
    mutate(manifest)
    members[manifest_path] = json.dumps(manifest).encode("utf-8")

    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_index_consistency_report_limits_public_and_error_records():
    missing = tuple(
        IndexExpectation(
            uri=f"viking://resources/demo/file_{index}.md",
            rel_path=f"file_{index}.md",
            level=2,
        )
        for index in range(25)
    )
    report = IndexConsistencyReport(expected=missing, missing_records=missing)

    public = report.to_dict()
    assert "expected" not in public
    assert public["expected_count"] == 25
    assert public["missing_record_count"] == 25
    assert len(public["missing_records"]) == 20
    assert public["missing_records_truncated"] is True

    details = report.details()
    assert details["missing_record_count"] == 25
    assert details["missing_records"] == ["file_0.md#level=2"]
    assert details["missing_records_truncated"] is True


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
        manifest = json.loads(zf.read("demo/_ovpack/manifest.json").decode("utf-8"))
        index_records = [
            json.loads(line)
            for line in zf.read("demo/_ovpack/index_records.jsonl").decode("utf-8").splitlines()
        ]

    assert "demo/files/notes.txt" in names
    assert "demo/files/.overview.md" in names
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
    assert manifest["index"]["records"]["count"] == len(index_records)
    assert index_records[0]["path"] == ""
    assert index_records[0]["text"] == "root abstract"


@pytest.mark.asyncio
async def test_backup_restore_contract(temp_ovpack_path: Path, request_ctx: RequestContext):
    await backup_ovpack(
        FakeBackupVikingFS(),
        str(temp_ovpack_path),
        ctx=request_ctx,
    )

    with zipfile.ZipFile(temp_ovpack_path, "r") as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("openviking-backup/_ovpack/manifest.json").decode("utf-8"))

    assert "openviking-backup/files/resources/README.md" in names
    assert "openviking-backup/files/session/sess_1/.meta.json" in names
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
async def test_restore_ovpack_applies_backup_manifest_scalar_metadata(
    temp_ovpack_path: Path, request_ctx: RequestContext, monkeypatch: pytest.MonkeyPatch
):
    await backup_ovpack(
        FakeBackupVikingFS(),
        str(temp_ovpack_path),
        ctx=request_ctx,
    )

    _rewrite_ovpack_index(
        temp_ovpack_path,
        "openviking-backup",
        [
            {
                "record_id": "r000001",
                "path": "resources/README.md",
                "kind": "file",
                "level": 2,
                "text": "portable summary",
                "scalars": {
                    "abstract": "portable summary",
                    "description": "portable description",
                    "tags": ["portable"],
                },
            }
        ],
    )

    vectorized_files: list[dict[str, object]] = []

    async def capture_vectorize_file(**kwargs):
        vectorized_files.append(kwargs)

    monkeypatch.setattr(
        "openviking.storage.ovpack.operations.vectorize_file", capture_vectorize_file
    )

    await restore_ovpack(FakeRestoreVectorVikingFS(), str(temp_ovpack_path), request_ctx)

    assert len(vectorized_files) == 1
    assert vectorized_files[0]["file_path"] == "viking://resources/README.md"
    assert vectorized_files[0]["summary_dict"] == {
        "name": "README.md",
        "summary": "portable summary",
    }
    assert vectorized_files[0]["scalar_override"]["tags"] == ["portable"]


@pytest.mark.asyncio
async def test_export_include_vectors_rejects_missing_index_records(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    with pytest.raises(
        InvalidArgumentError,
        match=r"incomplete OpenViking vector index snapshot",
    ) as exc_info:
        await export_ovpack(
            FakeExportVikingFS(),
            "viking://resources/demo",
            str(temp_ovpack_path),
            ctx=request_ctx,
            vector_store=IncompleteVectorStore(),
            include_vectors=True,
        )

    assert exc_info.value.details["missing_record_count"] == 3
    assert exc_info.value.details["missing_records"] == [".#level=0"]
    assert exc_info.value.details["missing_records_truncated"] is True
    assert not temp_ovpack_path.read_bytes()


@pytest.mark.asyncio
async def test_export_include_vectors_allows_overview_without_abstract(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    await export_ovpack(
        OverviewOnlyExportVikingFS(),
        "viking://resources/demo",
        str(temp_ovpack_path),
        ctx=request_ctx,
        vector_store=OverviewOnlyVectorStore(),
        include_vectors=True,
    )

    with zipfile.ZipFile(temp_ovpack_path, "r") as zf:
        index_records = [
            json.loads(line)
            for line in zf.read("demo/_ovpack/index_records.jsonl").decode("utf-8").splitlines()
        ]

    root_levels = {record["level"] for record in index_records if record["path"] == ""}
    assert root_levels == {1}


@pytest.mark.asyncio
async def test_ovpack_roundtrips_dot_and_escaped_looking_user_paths(
    temp_ovpack_path: Path,
    request_ctx: RequestContext,
    monkeypatch: pytest.MonkeyPatch,
):
    async def noop_vectorization(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "openviking.storage.ovpack.operations._enqueue_direct_vectorization",
        noop_vectorization,
    )

    await export_ovpack(
        ReservedPathExportVikingFS(),
        "viking://resources/demo",
        str(temp_ovpack_path),
        ctx=request_ctx,
    )

    with zipfile.ZipFile(temp_ovpack_path, "r") as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("demo/_ovpack/manifest.json").decode("utf-8"))

    assert "demo/files/.ovpack/foo.txt" in names
    assert "demo/files/.notes.txt" in names
    assert "demo/files/_._notes.txt" in names
    manifest_paths = {entry["path"] for entry in manifest["entries"]}
    assert {".ovpack", ".ovpack/foo.txt", ".notes.txt", "_._notes.txt"} <= manifest_paths

    fake_fs = FakeVikingFS()
    await import_ovpack(fake_fs, str(temp_ovpack_path), "viking://resources/imported", request_ctx)

    assert fake_fs.written_files == [
        "viking://resources/imported/demo/.ovpack/foo.txt",
        "viking://resources/imported/demo/.notes.txt",
        "viking://resources/imported/demo/_._notes.txt",
    ]


@pytest.mark.asyncio
async def test_export_include_vectors_rejects_hybrid_index_snapshot(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    with pytest.raises(
        InvalidArgumentError,
        match=r"only support pure dense",
    ) as exc_info:
        await export_ovpack(
            FakeExportVikingFS(),
            "viking://resources/demo",
            str(temp_ovpack_path),
            ctx=request_ctx,
            vector_store=HybridIndexVectorStore(),
            include_vectors=True,
        )

    assert exc_info.value.details["reason"] == "current vector index type is hybrid"
    assert not temp_ovpack_path.read_bytes()


@pytest.mark.asyncio
async def test_import_ovpack_restores_required_dense_vector_snapshot(
    temp_ovpack_path: Path, request_ctx: RequestContext, monkeypatch: pytest.MonkeyPatch
):
    embedding_metadata = {
        "provider": "test",
        "model": "demo",
        "input": "text",
        "dimensions": 3,
    }
    monkeypatch.setattr(
        "openviking.storage.ovpack.vectors.embedding_snapshot_metadata",
        lambda dimensions: {**embedding_metadata, "dimensions": dimensions},
    )
    monkeypatch.setattr(
        "openviking.storage.ovpack.vectors.current_embedding_metadata",
        lambda: embedding_metadata,
    )

    await export_ovpack(
        FakeExportVikingFS(),
        "viking://resources/demo",
        str(temp_ovpack_path),
        ctx=request_ctx,
        vector_store=FakeVectorStore(),
        include_vectors=True,
    )

    with zipfile.ZipFile(temp_ovpack_path, "r") as zf:
        assert "demo/_ovpack/dense.f32" in set(zf.namelist())

    fake_fs = FakeVikingFS()
    vector_store = FakeVectorStore()
    result = await import_ovpack(
        fake_fs,
        str(temp_ovpack_path),
        "viking://resources/imported",
        request_ctx,
        vector_mode="require",
        vector_store=vector_store,
    )

    assert result == "viking://resources/imported/demo"
    assert fake_fs.tree_calls == []
    assert len(vector_store.upserts) == 3
    note_record = next(
        record
        for record in vector_store.upserts
        if record["uri"] == "viking://resources/imported/demo/notes.txt"
    )
    assert note_record["vector"] == pytest.approx([0.1, 0.2, 0.3])
    assert note_record["tags"] == ["snapshot"]
    assert note_record["created_at"]
    assert note_record["updated_at"]
    assert note_record["active_count"] == 0


@pytest.mark.asyncio
async def test_import_legacy_ovpack_without_manifest_is_rejected(
    temp_ovpack_path: Path, request_ctx: RequestContext
):
    _write_ovpack(
        temp_ovpack_path,
        {
            "demo/files/.overview.md": "ATTACKER_OVERVIEW",
            "demo/files/notes.txt": "hello",
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
            "demo/files/": "",
            "demo/_ovpack/": "",
            "demo/_ovpack/index_records.jsonl": "",
            "demo/_ovpack/manifest.json": json.dumps(manifest),
            "demo/files/notes.txt": "hello",
            "demo/files/empty/": "",
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

    _write_ovpack_with_manifest(
        temp_ovpack_path,
        "renamed",
        {"README.md": "hello"},
        manifest=manifest,
    )
    with pytest.raises(InvalidArgumentError, match=r"root name does not match zip root"):
        await import_ovpack(
            FakeVikingFS(), str(temp_ovpack_path), "viking://resources", request_ctx
        )
