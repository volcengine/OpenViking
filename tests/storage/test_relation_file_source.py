# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for file resources as relation sources (issue #3067).

Unit tests for the _relation_table_path helper (core of the fix).
Covers plan §5 Test-plan rows via path logic + registration side effects.
"""

import pytest

from openviking.storage.viking_fs import VikingFS

pytestmark = pytest.mark.asyncio


class _MockAgfs:
    async def stat(self, path):
        is_dir = not str(path).endswith((".md", ".txt"))
        return {"isDir": is_dir, "is_dir": is_dir}

    async def read(self, path):
        raise FileNotFoundError

    async def write(self, path, content):
        return True

    async def ensure_parent_dirs(self, path):
        return True


@pytest.fixture
def vfs():
    vfs = VikingFS(agfs=_MockAgfs())
    return vfs


async def test_relation_table_path_dir(vfs):
    p = "/local/test_account/resources/project/"
    tbl = await vfs._relation_table_path(p)
    assert tbl == f"{p}/.relations.json"


async def test_relation_table_path_file(vfs, monkeypatch):
    async def file_stat(path):
        return {"isDir": False, "is_dir": False}

    monkeypatch.setattr(vfs._async_agfs, "stat", file_stat)
    p = "/local/test_account/resources/project/a.md"
    tbl = await vfs._relation_table_path(p)
    assert tbl == "/local/test_account/resources/project/.relations/a.md/.relations.json"


async def test_relation_table_path_fallback(vfs, monkeypatch):
    async def bad_stat(path):
        raise RuntimeError("boom")

    monkeypatch.setattr(vfs._async_agfs, "stat", bad_stat)
    p = "/local/test_account/resources/project/a.md"
    tbl = await vfs._relation_table_path(p)
    # falls back to legacy child path
    assert tbl == f"{p}/.relations.json"


# Table-driven coverage for plan §5 rows 1-4,5,7 (path logic for all Source×Target; read/write route)
@pytest.mark.parametrize(
    "source, is_dir, expected_suffix",
    [
        ("/local/test_account/resources/project/", True, "/.relations.json"),  # row1 dir->dir
        ("/local/test_account/resources/project/d/", True, "/.relations.json"),  # row2 dir->file
        (
            "/local/test_account/resources/project/a.md",
            False,
            "/.relations/a.md/.relations.json",
        ),  # row3 file->dir FIXED
        (
            "/local/test_account/resources/project/b.md",
            False,
            "/.relations/b.md/.relations.json",
        ),  # row4 file->file FIXED
    ],
)
async def test_relation_table_path_param(vfs, monkeypatch, source, is_dir, expected_suffix):
    async def stat_fn(path):
        return {"isDir": is_dir, "is_dir": is_dir}

    monkeypatch.setattr(vfs._async_agfs, "stat", stat_fn)
    tbl = await vfs._relation_table_path(source)
    assert tbl.endswith(expected_suffix)
    if not is_dir:
        assert "/.relations/" in tbl and tbl.endswith("/.relations.json")


async def test_relation_table_path_name_collision(vfs, monkeypatch):
    """row11: dir x and file x.md in same parent get distinct tables (no collision)."""
    results = {}

    async def stat_fn(path):
        return {"isDir": not str(path).endswith(".md"), "is_dir": not str(path).endswith(".md")}

    monkeypatch.setattr(vfs._async_agfs, "stat", stat_fn)
    for p in [
        "/local/test_account/resources/project/x",
        "/local/test_account/resources/project/x.md",
    ]:
        results[p] = await vfs._relation_table_path(p)
    assert results["/local/test_account/resources/project/x"].endswith("x/.relations.json")
    assert results["/local/test_account/resources/project/x.md"].endswith("x.md/.relations.json")
    assert (
        results["/local/test_account/resources/project/x"]
        != results["/local/test_account/resources/project/x.md"]
    )


# ---------------------------------------------------------------------------
# Real end-to-end: drive the ACTUAL VikingFS.link -> persist -> relations
# read-back -> unlink path (not just the path helper), through a backend that
# faithfully models localfs ENOTDIR semantics (a file cannot hold children).
# This both reproduces the #3067 bug (control assertion) and proves the fix
# routes a file source's table to the sidecar so the write succeeds.
# The entire fix lives in Python; the fake stands in only for the storage
# syscalls below the fix (Rust is untouched by design).
# ---------------------------------------------------------------------------

from openviking.storage.internal_names import (  # noqa: E402
    STORAGE_INTERNAL_ENTRY_NAMES,
    WEBDAV_RESERVED_FILENAMES,
)


class _FakeBackendFs:
    """In-memory fs that raises ENOTDIR when asked to create a child of a file."""

    def __init__(self):
        self.files = {}  # normalized path -> bytes
        self.dirs = {"/"}

    @staticmethod
    def _norm(path):
        return "/" + path.strip("/") if path.strip("/") else "/"

    def _enotdir_if_file_ancestor(self, path):
        # Mirrors localfs open(): open("a.md/child") fails because a.md is a file.
        cur = ""
        for part in [p for p in self._norm(path).strip("/").split("/") if p][:-1]:
            cur = cur + "/" + part
            if cur in self.files:
                raise OSError("failed to open file: Not a directory (os error 20)")

    async def stat(self, path):
        p = self._norm(path)
        if p in self.files:
            return {"isDir": False, "is_dir": False}
        if p in self.dirs:
            return {"isDir": True, "is_dir": True}
        raise FileNotFoundError(path)

    async def read(self, path):
        p = self._norm(path)
        if p in self.files:
            return self.files[p]
        raise FileNotFoundError(path)

    async def write(self, path, content):
        p = self._norm(path)
        self._enotdir_if_file_ancestor(p)
        self.files[p] = content
        return True

    async def mkdir(self, path):
        self.dirs.add(self._norm(path))
        return True

    async def ensure_parent_dirs(self, path):
        parent = self._norm(path).rsplit("/", 1)[0] or "/"
        self._enotdir_if_file_ancestor(parent + "/x")
        cur = ""
        for part in [p for p in parent.strip("/").split("/") if p]:
            cur = cur + "/" + part
            self.dirs.add(cur)
        return True

    async def rm(self, path, recursive=False):
        p = self._norm(path)
        self.files.pop(p, None)
        self.dirs.discard(p)
        if recursive:
            prefix = p.rstrip("/") + "/"
            for k in [k for k in self.files if k.startswith(prefix)]:
                del self.files[k]
            for k in [k for k in self.dirs if k.startswith(prefix)]:
                self.dirs.discard(k)
        return {}


@pytest.fixture
def e2e_vfs(monkeypatch):
    backend = _FakeBackendFs()
    # Pre-create the two file resources so stat() reports them as files.
    backend.files["/local/test_account/resources/project/a.md"] = b"a"
    backend.files["/local/test_account/resources/project/b.md"] = b"b"
    backend.dirs.update(
        {
            "/local",
            "/local/test_account",
            "/local/test_account/resources",
            "/local/test_account/resources/project",
        }
    )
    vfs = VikingFS(agfs=backend)
    # Drive the async backend directly (VikingFS wraps a *sync* agfs in a
    # threadpool client; our fake is already async, matching the exact call
    # surface the relation helpers use: stat/read/write/ensure_parent_dirs).
    vfs._async_agfs = backend
    # Focus on the relation-table routing (the fix), not access control.
    monkeypatch.setattr(vfs, "_ensure_mutable_access", lambda *a, **k: None)
    monkeypatch.setattr(vfs, "_ensure_access", lambda *a, **k: None)
    monkeypatch.setattr(
        vfs,
        "_uri_to_path",
        lambda uri, **k: uri.replace("viking://", "/local/test_account/").rstrip("/"),
    )
    return vfs, backend


async def test_file_source_link_persist_readback_unlink_e2e(e2e_vfs):
    """rows 3/4/5/6: real link -> persist -> relations -> unlink for a FILE source."""
    vfs, backend = e2e_vfs
    a = "viking://resources/project/a.md"
    b = "viking://resources/project/b.md"

    # Control: the pre-fix path (child of the file) genuinely raises ENOTDIR,
    # proving the backend models the bug the fix must route around.
    with pytest.raises(OSError, match="Not a directory"):
        await backend.write("/local/test_account/resources/project/a.md/.relations.json", b"x")

    # rows 3/4 file->file link now succeeds (was ENOTDIR).
    await vfs.link(a, [b], reason="test")

    sidecar = "/local/test_account/resources/project/.relations/a.md/.relations.json"
    assert sidecar in backend.files, "file-source table must land in the sidecar"
    assert "/local/test_account/resources/project/a.md/.relations.json" not in backend.files

    # row 5: read-back routes to the same sidecar and returns the target + reason.
    entries = await vfs.get_relation_table(a)
    assert len(entries) == 1
    assert entries[0].uris == [b]
    assert entries[0].reason == "test"

    # row 6: unlink removes the entry; table persists as empty.
    await vfs.unlink(a, b)
    assert await vfs.get_relation_table(a) == []


async def test_dir_source_link_unchanged_e2e(e2e_vfs):
    """rows 1/2 + row 10: dir source keeps writing to <dir>/.relations.json (byte-identical)."""
    vfs, backend = e2e_vfs
    d = "viking://resources/project/"  # existing dir source
    target = "viking://resources/project/b.md"
    await vfs.link(d, [target], reason="dir-test")

    assert "/local/test_account/resources/project/.relations.json" in backend.files
    # dir table is separate from any file sidecar (no collision).
    assert "/local/test_account/resources/project/.relations" not in backend.files
    entries = await vfs.get_relation_table(d)
    assert entries[0].uris == [target]


@pytest.fixture
def e2e_rm_mv_vfs(e2e_vfs, monkeypatch):
    """Extend e2e_vfs with the infra rm/mv touch beyond link/unlink, neutralized.

    rm/mv wrap work in a LockContext and consult the vector store; none of that is
    under test here — only the file-sidecar migration/cleanup the fix adds.
    """
    vfs, backend = e2e_vfs

    class _NoopLock:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("openviking.storage.transaction.LockContext", _NoopLock)
    monkeypatch.setattr("openviking.storage.transaction.get_lock_manager", lambda: None)
    monkeypatch.setattr(vfs, "_ensure_delete_access", lambda *a, **k: None)

    async def _no_uris(*a, **k):
        return []

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(vfs, "_collect_uris", _no_uris)
    monkeypatch.setattr(vfs, "_delete_from_vector_store", _noop)
    monkeypatch.setattr(vfs, "_update_vector_store_uris", _noop)
    monkeypatch.setattr(vfs, "_get_vector_store", lambda: None)
    monkeypatch.setattr(
        vfs,
        "_path_to_uri",
        lambda path, **k: path.replace("/local/test_account/", "viking://"),
    )
    return vfs, backend


async def test_file_source_rm_clears_stale_relations_e2e(e2e_rm_mv_vfs):
    """link -> rm -> recreate same URI: rebuilt file must not inherit stale relations."""
    vfs, backend = e2e_rm_mv_vfs
    a = "viking://resources/project/a.md"
    b = "viking://resources/project/b.md"
    a_path = "/local/test_account/resources/project/a.md"
    sidecar = "/local/test_account/resources/project/.relations/a.md/.relations.json"

    await vfs.link(a, [b], reason="test")
    assert sidecar in backend.files

    await vfs.rm(a)
    assert sidecar not in backend.files, "rm of a file must drop its relation sidecar"

    # Recreate the file at the same URI; its relation table must be empty.
    backend.files[a_path] = b"a"
    assert await vfs.get_relation_table(a) == []


async def test_file_source_rm_not_found_clears_sidecar_e2e(e2e_rm_mv_vfs):
    """rm's idempotent not-found branch must still drop the file sidecar.

    Body already gone (rm takes the not-found path) -> recreate at same URI must
    not inherit stale relations. refs #3067
    """
    vfs, backend = e2e_rm_mv_vfs
    a = "viking://resources/project/a.md"
    b = "viking://resources/project/b.md"
    a_path = "/local/test_account/resources/project/a.md"
    sidecar = "/local/test_account/resources/project/.relations/a.md/.relations.json"

    await vfs.link(a, [b], reason="test")
    assert sidecar in backend.files

    # Simulate the body already being gone so rm hits the not-found branch.
    backend.files.pop(a_path, None)
    await vfs.rm(a)
    assert sidecar not in backend.files, "not-found rm must still drop the sidecar"

    backend.files[a_path] = b"a"
    assert await vfs.get_relation_table(a) == []


async def test_file_source_mv_migrates_relations_e2e(e2e_rm_mv_vfs):
    """link -> mv: new URI keeps the relations, old sidecar is gone (no orphan)."""
    vfs, backend = e2e_rm_mv_vfs
    a = "viking://resources/project/a.md"
    a2 = "viking://resources/project/a2.md"
    b = "viking://resources/project/b.md"
    old_sidecar = "/local/test_account/resources/project/.relations/a.md/.relations.json"
    new_sidecar = "/local/test_account/resources/project/.relations/a2.md/.relations.json"

    await vfs.link(a, [b], reason="test")
    assert old_sidecar in backend.files

    await vfs.mv(a, a2)

    assert new_sidecar in backend.files, "moved file must carry its relations to the new sidecar"
    assert old_sidecar not in backend.files, "old sidecar must not be orphaned"

    entries = await vfs.get_relation_table(a2)
    assert len(entries) == 1
    assert entries[0].uris == [b]


async def test_relations_container_registered_internal():
    """rows 8/12: .relations dir is hidden from ls (storage) and WebDAV listings."""
    assert ".relations" in STORAGE_INTERNAL_ENTRY_NAMES
    assert ".relations" in WEBDAV_RESERVED_FILENAMES


# ---------------------------------------------------------------------------
# Review round 2: the write path must decide "create sidecar parent dirs"
# WITHOUT substring-matching an assumed-rooted path. It ensures parents only
# when the table routed to a file sidecar, not for any dir whose own path
# happens to contain a ".relations" segment. (refs #3067)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source, is_dir, expect_ensure",
    [
        # dir source -> <dir>/.relations.json ; parent (the dir) exists, no ensure
        ("/local/test_account/resources/project", True, False),
        # file source -> sidecar under <parent>/.relations/<name>/ ; must ensure
        ("/local/test_account/resources/project/a.md", False, True),
        # dir whose path itself contains a ".relations" segment: the OLD
        # substring guard ("/.relations/" in table_path) misfired here and
        # ensured parents for a plain dir write; the exact-compare guard must not.
        ("/local/test_account/resources/.relations/notes", True, False),
    ],
)
async def test_write_ensures_parents_only_for_file_sidecar(
    vfs, monkeypatch, source, is_dir, expect_ensure
):
    async def stat_fn(path):
        return {"isDir": is_dir, "is_dir": is_dir}

    monkeypatch.setattr(vfs._async_agfs, "stat", stat_fn)

    calls = []

    async def spy_ensure(path, ctx=None):
        calls.append(path)

    monkeypatch.setattr(vfs, "_ensure_parent_dirs", spy_ensure)

    await vfs._write_relation_table(source, [], ctx=None)
    assert bool(calls) is expect_ensure


async def test_file_sidecar_is_rooted_no_relative_path(vfs, monkeypatch):
    """Reject-evidence for the 'relative no-dirname' comment: callers derive
    source_path via _uri_to_path, which always yields a rooted /local/... path,
    so the sidecar is always well-formed (never a bare /.relations/... at root).
    """
    real = vfs._uri_to_path("viking://resources/project/a.md")
    assert real.startswith("/local/") and "/" in real.rstrip("/")

    async def file_stat(path):
        return {"isDir": False, "is_dir": False}

    monkeypatch.setattr(vfs._async_agfs, "stat", file_stat)
    tbl = await vfs._relation_table_path(real)
    assert tbl.startswith("/local/")
    assert not tbl.startswith("/.relations/")
    assert tbl.endswith("/.relations/a.md/.relations.json")
