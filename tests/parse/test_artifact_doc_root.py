# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for backend-agnostic parse-artifact structure resolution.

finalize needs to locate the single document root inside an artifact and decide
whether it is a single flattened file. That structural walk is identical for
AGFS and local artifacts (same layout), so it lives on the store interface. Only
the "read the artifact structure" part is store-driven here; resolving the final
target URI against the live resource tree stays on VikingFS elsewhere.
"""

import pytest

from openviking.parse.output import (
    AgfsParseOutputStore,
    LocalParseOutputStore,
    resolve_artifact_doc_root,
)


class _FakeVikingFS:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self._seq = 0

    def create_temp_uri(self, ctx=None) -> str:
        self._seq += 1
        return f"viking://temp/fake{self._seq}"

    async def mkdir(self, uri, exist_ok=False, ctx=None):
        pass

    async def write_file_bytes(self, uri, content, ctx=None):
        self.files[uri] = content

    async def read_file_bytes(self, uri, ctx=None):
        return self.files[uri]

    async def ls(self, uri, ctx=None, **kwargs):
        prefix = f"{uri.rstrip('/')}/"
        seen = {}
        for stored in self.files:
            if stored.startswith(prefix):
                rest = stored[len(prefix):]
                head = rest.split("/", 1)
                seen[head[0]] = {"name": head[0], "isDir": len(head) > 1}
        return list(seen.values())

    async def delete_temp(self, uri, ctx=None):
        pass


def _agfs_store():
    return AgfsParseOutputStore(viking_fs=_FakeVikingFS())


def _local_store(tmp_path):
    return LocalParseOutputStore(local_root=str(tmp_path / "out"))


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["agfs", "local"])
class TestResolveArtifactDocRoot:
    async def _store(self, backend, tmp_path):
        return _agfs_store() if backend == "agfs" else _local_store(tmp_path)

    async def test_single_directory_doc_root(self, backend, tmp_path) -> None:
        store = await self._store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "repo/a.py", b"a")
        await store.write_bytes(ref, "repo/b.py", b"b")

        resolved = await resolve_artifact_doc_root(store, ref, flatten_single_file=False)

        assert resolved.doc_name == "repo"
        assert resolved.doc_rel == "repo"
        assert resolved.root_is_file is False

    async def test_flatten_single_file_detected(self, backend, tmp_path) -> None:
        store = await self._store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "doc/report.md", b"# hi")

        resolved = await resolve_artifact_doc_root(store, ref, flatten_single_file=True)

        assert resolved.doc_name == "report.md"
        assert resolved.doc_rel == "doc/report.md"
        assert resolved.root_is_file is True

    async def test_flatten_keeps_directory_when_multiple_files(self, backend, tmp_path) -> None:
        store = await self._store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "doc/a.md", b"a")
        await store.write_bytes(ref, "doc/b.md", b"b")

        resolved = await resolve_artifact_doc_root(store, ref, flatten_single_file=True)

        assert resolved.doc_name == "doc"
        assert resolved.root_is_file is False

    async def test_rejects_when_not_exactly_one_doc_root(self, backend, tmp_path) -> None:
        store = await self._store(backend, tmp_path)
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "one/a.py", b"a")
        await store.write_bytes(ref, "two/b.py", b"b")

        with pytest.raises(ValueError, match="document"):
            await resolve_artifact_doc_root(store, ref, flatten_single_file=False)
