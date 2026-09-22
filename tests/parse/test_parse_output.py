# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Contract tests for the parse output store abstraction.

The AGFS backend forwards 1:1 to the VikingFS singleton; the local backend
lays artifacts out under a configured root directory. Both must satisfy the same
byte/directory contract so parsers stay backend-agnostic.
"""

from pathlib import Path

import pytest

from openviking.parse.base import ParseResult, ResourceNode
from openviking.parse.output import (
    ARTIFACT_MANIFEST_NAME,
    AgfsParseOutputStore,
    LocalParseOutputStore,
    ParseArtifactRef,
    ParseArtifactWriter,
    build_parse_output_store,
    copy_artifact_tree,
    read_artifact_manifest,
    resolve_artifact_doc_root,
)
from openviking.utils.content_hash import content_md5


class _FakeVikingFS:
    """Records the VikingFS calls a parser would make while writing artifacts."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: list[str] = []
        self.deleted: list[str] = []
        self._temp_seq = 0
        self.ls_kwargs = []

    def create_temp_uri(self, ctx=None) -> str:
        self._temp_seq += 1
        return f"viking://temp/fake{self._temp_seq}"

    async def mkdir(self, uri: str, exist_ok: bool = False, ctx=None) -> None:
        self.dirs.append(uri)

    async def write_file_bytes(self, uri: str, content: bytes, ctx=None) -> None:
        self.files[uri] = content

    async def read_file_bytes(self, uri: str, ctx=None) -> bytes:
        return self.files[uri]

    async def ls(self, uri: str, ctx=None, **kwargs) -> list[dict]:
        self.ls_kwargs.append(kwargs)
        prefix = f"{uri.rstrip('/')}/"
        entries = {}
        for stored in self.files:
            if stored.startswith(prefix):
                relative = stored[len(prefix) :]
                name, separator, _ = relative.partition("/")
                entries[name] = {
                    "name": name,
                    "uri": f"{prefix}{name}",
                    "isDir": bool(separator),
                }
        return list(entries.values())

    async def delete_temp(self, uri: str, ctx=None) -> None:
        self.deleted.append(uri)


# ---------------------------------------------------------------------------
# ParseArtifactRef
# ---------------------------------------------------------------------------


class TestParseArtifactRef:
    def test_serialization_roundtrip(self) -> None:
        ref = ParseArtifactRef(
            backend="agfs",
            root="viking://temp/abc",
            resource_rel="repository",
            root_type="dir",
        )
        assert ParseArtifactRef.from_dict(ref.to_dict()) == ref

    def test_rejects_unknown_backend(self) -> None:
        with pytest.raises(ValueError, match="backend"):
            ParseArtifactRef.from_dict(
                {"backend": "s3", "root": "x", "resource_rel": "", "root_type": "dir"}
            )

    def test_rejects_invalid_root_type(self) -> None:
        with pytest.raises(ValueError, match="root_type"):
            ParseArtifactRef.from_dict(
                {"backend": "agfs", "root": "x", "resource_rel": "", "root_type": "blob"}
            )


# ---------------------------------------------------------------------------
# Shared contract, parametrized over both backends
# ---------------------------------------------------------------------------


def _make_store(backend: str, tmp_path):
    if backend == "agfs":
        return AgfsParseOutputStore(viking_fs=_FakeVikingFS())
    return LocalParseOutputStore(local_root=str(tmp_path / "parse-out"))


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["agfs", "local"])
class TestParseOutputStoreContract:
    async def test_create_artifact_reports_backend(self, backend, tmp_path) -> None:
        store = _make_store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")
        assert ref.backend == backend
        assert ref.root_type == "dir"
        assert ref.root

    async def test_write_and_read_bytes_roundtrip(self, backend, tmp_path) -> None:
        store = _make_store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")

        await store.mkdir(ref, "repo")
        await store.write_bytes(ref, "repo/main.py", b"print(1)")

        assert await store.read_bytes(ref, "repo/main.py") == b"print(1)"

    async def test_write_and_read_text(self, backend, tmp_path) -> None:
        store = _make_store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")
        await store.write_text(ref, "note.md", "# hi")
        assert await store.read_text(ref, "note.md") == "# hi"

    async def test_list_returns_relative_entries(self, backend, tmp_path) -> None:
        store = _make_store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "a.py", b"a")
        await store.write_bytes(ref, "b.py", b"b")

        names = {entry.name for entry in await store.list(ref, "")}
        assert names == {"a.py", "b.py"}

    async def test_cleanup_is_idempotent(self, backend, tmp_path) -> None:
        store = _make_store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "a.py", b"a")

        await store.cleanup(ref)
        await store.cleanup(ref)  # must not raise

    async def test_rejects_unsafe_relative_path(self, backend, tmp_path) -> None:
        store = _make_store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")
        with pytest.raises(ValueError):
            await store.write_bytes(ref, "../escape.py", b"x")

    async def test_ref_roundtrip_reopens_same_artifact(self, backend, tmp_path) -> None:
        store = _make_store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "a.py", b"a")

        # A serialized ref must reopen the same artifact bytes.
        reopened = ParseArtifactRef.from_dict(ref.to_dict())
        assert await store.read_bytes(reopened, "a.py") == b"a"


# ---------------------------------------------------------------------------
# AGFS-specific: must forward to the VikingFS singleton
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAgfsParseOutputStore:
    async def test_explicit_context_is_forwarded(self) -> None:
        class _ContextVikingFS(_FakeVikingFS):
            def __init__(self):
                super().__init__()
                self.contexts = []

            async def read_file_bytes(self, uri: str, ctx=None) -> bytes:
                self.contexts.append(ctx)
                return await super().read_file_bytes(uri, ctx=ctx)

        ctx = object()
        vfs = _ContextVikingFS()
        store = AgfsParseOutputStore(viking_fs=vfs, ctx=ctx)
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "a.py", b"a")

        assert await store.read_bytes(ref, "a.py") == b"a"
        assert vfs.contexts == [ctx]

    async def test_write_round_trips_through_vikingfs(self) -> None:
        vfs = _FakeVikingFS()
        store = AgfsParseOutputStore(viking_fs=vfs)
        ref = await store.create_artifact(root_type="dir")

        await store.write_bytes(ref, "repo/main.py", b"print(1)")

        assert vfs.files["viking://temp/fake1/repo/main.py"] == b"print(1)"

    async def test_list_requests_all_children(self) -> None:
        from openviking.storage.viking_fs import LS_ALL_NODES

        vfs = _FakeVikingFS()
        store = AgfsParseOutputStore(viking_fs=vfs)
        ref = await store.create_artifact(root_type="dir")

        await store.list(ref)

        assert vfs.ls_kwargs == [{"show_all_hidden": True, "node_limit": LS_ALL_NODES}]

    async def test_cleanup_deletes_via_vikingfs(self) -> None:
        vfs = _FakeVikingFS()
        store = AgfsParseOutputStore(viking_fs=vfs)
        ref = await store.create_artifact(root_type="dir")

        await store.cleanup(ref)
        await store.cleanup(ref)

        assert vfs.deleted == ["viking://temp/fake1"]

    async def test_move_forwards_explicit_context(self) -> None:
        class _MovingVikingFS(_FakeVikingFS):
            def __init__(self):
                super().__init__()
                self.moves = []

            async def move_file(self, source, target, ctx=None):
                self.moves.append((source, target, ctx))

        ctx = object()
        vfs = _MovingVikingFS()
        store = AgfsParseOutputStore(viking_fs=vfs, ctx=ctx)
        source = await store.create_artifact()
        target = await store.create_artifact()

        await store.move_file(source, "a.txt", target, "nested/a.txt")

        assert vfs.moves == [(f"{source.root}/a.txt", f"{target.root}/nested/a.txt", ctx)]


# ---------------------------------------------------------------------------
# Local-specific: filesystem layout, isolation, case conflicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLocalParseOutputStore:
    async def test_artifacts_are_isolated(self, tmp_path) -> None:
        store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
        a = await store.create_artifact(root_type="dir")
        b = await store.create_artifact(root_type="dir")
        assert a.root != b.root

        await store.write_bytes(a, "f.py", b"a")
        await store.write_bytes(b, "f.py", b"b")
        assert await store.read_bytes(a, "f.py") == b"a"
        assert await store.read_bytes(b, "f.py") == b"b"

    async def test_root_stays_under_configured_local_root(self, tmp_path) -> None:
        root = tmp_path / "out"
        store = LocalParseOutputStore(local_root=str(root))
        ref = await store.create_artifact(root_type="dir")
        assert str(root) in ref.root

    async def test_cleanup_removes_directory(self, tmp_path) -> None:
        from pathlib import Path

        store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "a.py", b"a")
        assert Path(ref.root).exists()

        await store.cleanup(ref)
        assert not Path(ref.root).exists()

    async def test_cleanup_rejects_artifact_outside_store_root(self, tmp_path) -> None:
        store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
        outside = tmp_path / "outside"
        outside.mkdir()
        ref = ParseArtifactRef(backend="local", root=str(outside))

        with pytest.raises(ValueError, match="escapes"):
            await store.cleanup(ref)

        assert outside.exists()

    async def test_case_only_conflict_is_detected(self, tmp_path) -> None:
        store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "Readme.md", b"a")
        with pytest.raises(ValueError, match="case"):
            await store.write_bytes(ref, "README.MD", b"b")

    async def test_missing_artifact_read_raises(self, tmp_path) -> None:
        store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
        ref = await store.create_artifact(root_type="dir")
        with pytest.raises(FileNotFoundError):
            await store.read_bytes(ref, "missing.py")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestBuildParseOutputStore:
    def test_defaults_to_agfs_backend(self) -> None:
        store = build_parse_output_store(viking_fs=_FakeVikingFS())
        assert isinstance(store, AgfsParseOutputStore)

    def test_local_backend_requires_root(self) -> None:
        with pytest.raises(ValueError, match="local_root"):
            build_parse_output_store(backend="local", local_root=None)

    def test_builds_local_backend(self, tmp_path) -> None:
        store = build_parse_output_store(backend="local", local_root=str(tmp_path))
        assert isinstance(store, LocalParseOutputStore)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["agfs", "local"])
async def test_artifact_writer_records_final_bytes_in_manifest(backend, tmp_path) -> None:
    store = _make_store(backend, tmp_path)
    writer = await ParseArtifactWriter.create(store)

    await writer.write_bytes("doc/a.txt", b"final bytes")
    ref = await writer.finalize(resource_rel="doc")

    assert ref.resource_rel == "doc"
    assert await read_artifact_manifest(store, ref) == {"doc/a.txt": content_md5(b"final bytes")}


@pytest.mark.asyncio
async def test_copy_artifact_tree_merges_files_without_manifest_sidecar(tmp_path) -> None:
    source_store = LocalParseOutputStore(local_root=str(tmp_path / "source"))
    target_store = LocalParseOutputStore(local_root=str(tmp_path / "target"))
    source = await ParseArtifactWriter.create(source_store)
    target = await ParseArtifactWriter.create(target_store)
    await source.write_bytes("wrapper/a.txt", b"a")
    await source.write_bytes("wrapper/nested/b.txt", b"b")
    source_ref = await source.finalize(resource_rel="wrapper")

    copied = await copy_artifact_tree(
        source_store=source_store,
        source_ref=source_ref,
        source_rel="wrapper",
        target=target,
        target_rel="dest",
    )
    target_ref = await target.finalize(resource_rel="dest")

    assert copied == 2
    assert await target_store.read_bytes(target_ref, "dest/a.txt") == b"a"
    assert await target_store.read_bytes(target_ref, "dest/nested/b.txt") == b"b"
    assert await read_artifact_manifest(target_store, target_ref) == {
        "dest/a.txt": content_md5(b"a"),
        "dest/nested/b.txt": content_md5(b"b"),
    }
    assert all(
        entry.name != ARTIFACT_MANIFEST_NAME
        for entry in await target_store.list(target_ref, "dest")
    )


@pytest.mark.asyncio
async def test_copy_artifact_tree_flattens_single_wrapped_file(tmp_path) -> None:
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    source = await ParseArtifactWriter.create(store)
    target = await ParseArtifactWriter.create(store)
    await source.write_text("wrapper/file.md", "body")
    source_ref = await source.finalize(resource_rel="wrapper")

    copied = await copy_artifact_tree(
        source_store=store,
        source_ref=source_ref,
        source_rel="",
        target=target,
        target_rel="dest",
        flatten_single_output=True,
    )
    target_ref = await target.finalize(resource_rel="dest")

    assert copied == 1
    assert await store.read_text(target_ref, "dest/file.md") == "body"
    assert not Path(target_ref.root, "dest", "wrapper").exists()


def test_parse_result_does_not_guess_local_path_is_agfs(tmp_path) -> None:
    result = ParseResult(
        root=ResourceNode(type="root"),
        temp_dir_path=str(tmp_path / "artifact"),
    )

    with pytest.raises(ValueError, match="artifact_ref"):
        result.ensure_artifact_ref()


@pytest.mark.asyncio
async def test_resolve_artifact_doc_root_accepts_flat_single_file(tmp_path) -> None:
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    writer = await ParseArtifactWriter.create(store)
    await writer.write_text("document.md", "body")
    ref = await writer.finalize()

    resolved = await resolve_artifact_doc_root(store, ref, flatten_single_file=True)

    assert resolved.doc_name == "document.md"
    assert resolved.doc_rel == "document.md"
    assert resolved.root_is_file is True


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["agfs", "local"])
async def test_resolve_artifact_doc_root_accepts_single_directory(backend, tmp_path) -> None:
    store = _make_store(backend, tmp_path)
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "repo/a.py", b"a")
    await store.write_bytes(ref, "repo/b.py", b"b")

    resolved = await resolve_artifact_doc_root(store, ref, flatten_single_file=False)

    assert resolved.doc_name == "repo"
    assert resolved.doc_rel == "repo"
    assert resolved.root_is_file is False


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["agfs", "local"])
async def test_resolve_artifact_doc_root_flattens_wrapped_single_file(backend, tmp_path) -> None:
    store = _make_store(backend, tmp_path)
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "doc/report.md", b"# hi")

    resolved = await resolve_artifact_doc_root(store, ref, flatten_single_file=True)

    assert resolved.doc_name == "report.md"
    assert resolved.doc_rel == "doc/report.md"
    assert resolved.root_is_file is True


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["agfs", "local"])
async def test_resolve_artifact_doc_root_keeps_multi_file_directory(backend, tmp_path) -> None:
    store = _make_store(backend, tmp_path)
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "doc/a.md", b"a")
    await store.write_bytes(ref, "doc/b.md", b"b")

    resolved = await resolve_artifact_doc_root(store, ref, flatten_single_file=True)

    assert resolved.doc_name == "doc"
    assert resolved.root_is_file is False


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["agfs", "local"])
async def test_resolve_artifact_doc_root_rejects_multiple_roots(backend, tmp_path) -> None:
    store = _make_store(backend, tmp_path)
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "one/a.py", b"a")
    await store.write_bytes(ref, "two/b.py", b"b")

    with pytest.raises(ValueError, match="document"):
        await resolve_artifact_doc_root(store, ref, flatten_single_file=False)
