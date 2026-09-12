# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Contract tests for the parse output store abstraction.

Step 2a only ships the AGFS backend, which must forward 1:1 to the existing
VikingFS singleton so parser behaviour is unchanged. The local backend and its
parametrization arrive in Step 2b.
"""

import pytest

from openviking.parse.output import (
    AgfsParseOutputStore,
    ParseArtifactRef,
    build_parse_output_store,
)


class _FakeVikingFS:
    """Records the VikingFS calls a parser would make while writing artifacts."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: list[str] = []
        self.deleted: list[str] = []
        self._temp_seq = 0

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
        prefix = f"{uri.rstrip('/')}/"
        entries = []
        for stored in self.files:
            if stored.startswith(prefix):
                name = stored[len(prefix) :].split("/", 1)[0]
                entries.append({"name": name, "uri": f"{prefix}{name}", "isDir": False})
        return entries

    async def delete_temp(self, uri: str, ctx=None) -> None:
        self.deleted.append(uri)


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


@pytest.mark.asyncio
class TestAgfsParseOutputStore:
    async def test_create_artifact_allocates_temp_root(self) -> None:
        vfs = _FakeVikingFS()
        store = AgfsParseOutputStore(viking_fs=vfs)

        ref = await store.create_artifact(root_type="dir")

        assert ref.backend == "agfs"
        assert ref.root == "viking://temp/fake1"
        assert ref.root_type == "dir"

    async def test_write_and_read_bytes_roundtrip_via_vikingfs(self) -> None:
        vfs = _FakeVikingFS()
        store = AgfsParseOutputStore(viking_fs=vfs)
        ref = await store.create_artifact(root_type="dir")

        await store.mkdir(ref, "repo")
        await store.write_bytes(ref, "repo/main.py", b"print(1)")

        # The AGFS backend must round-trip through the VikingFS singleton, using
        # the artifact root to resolve the absolute URI.
        assert vfs.files["viking://temp/fake1/repo/main.py"] == b"print(1)"
        assert await store.read_bytes(ref, "repo/main.py") == b"print(1)"

    async def test_list_returns_relative_entries(self) -> None:
        vfs = _FakeVikingFS()
        store = AgfsParseOutputStore(viking_fs=vfs)
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "a.py", b"a")
        await store.write_bytes(ref, "b.py", b"b")

        names = {entry.name for entry in await store.list(ref, "")}
        assert names == {"a.py", "b.py"}

    async def test_cleanup_deletes_artifact_root(self) -> None:
        vfs = _FakeVikingFS()
        store = AgfsParseOutputStore(viking_fs=vfs)
        ref = await store.create_artifact(root_type="dir")

        await store.cleanup(ref)
        await store.cleanup(ref)  # idempotent

        assert vfs.deleted == ["viking://temp/fake1"]

    async def test_rejects_unsafe_relative_path(self) -> None:
        vfs = _FakeVikingFS()
        store = AgfsParseOutputStore(viking_fs=vfs)
        ref = await store.create_artifact(root_type="dir")

        with pytest.raises(ValueError):
            await store.write_bytes(ref, "../escape.py", b"x")


class TestBuildParseOutputStore:
    def test_defaults_to_agfs_backend(self) -> None:
        store = build_parse_output_store(viking_fs=_FakeVikingFS())
        assert isinstance(store, AgfsParseOutputStore)
