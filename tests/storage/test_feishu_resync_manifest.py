# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""#3029 Prompt B: guarded temp->target mirror for single-doc resyncs.

Drives a real resync through ``SemanticProcessor._sync_topdown_recursive``
against an in-memory viking_fs, covering the 16-row table in
``.wiki/issue-3029-feishu-resync-safe-sync-plan.md`` §7. The LEGACY_MIRROR row
(git resync) pins the no-regression guarantee.
"""

from __future__ import annotations

import json
import os

import pytest

from openviking.storage.ovpack.format import sha256_hex
from openviking.storage.queuefs import semantic_processor as sp
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.sync_manifest import SYNC_MANIFEST_FILENAME
from openviking.storage.transaction import NO_LOCK

TEMP = "viking://temp/imp"
TARGET = "viking://resources/doc"


def sha(s: str) -> str:
    return sha256_hex(s.encode("utf-8"))


class FakeFS:
    """Flat in-memory VFS: files keyed by URI (str content) + a dir set."""

    def __init__(self):
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()
        self.deleted_temp: list[str] = []

    # -- seeding helpers --
    def add_file(self, uri: str, content: str) -> None:
        self.files[uri] = content
        self._ensure_parents(uri)

    def add_dir(self, uri: str) -> None:
        self.dirs.add(uri.rstrip("/"))
        self._ensure_parents(uri.rstrip("/") + "/x")

    def _ensure_parents(self, uri: str) -> None:
        d = uri.rsplit("/", 1)[0]
        while d and d not in ("viking:", "viking://"):
            self.dirs.add(d)
            d = d.rsplit("/", 1)[0]

    def _parent(self, uri: str) -> str:
        return uri.rstrip("/").rsplit("/", 1)[0]

    # -- viking_fs surface --
    async def exists(self, uri, ctx=None):
        uri = uri.rstrip("/")
        return uri in self.files or uri in self.dirs

    async def ls(self, uri, show_all_hidden=False, node_limit=None, ctx=None):
        uri = uri.rstrip("/")
        out = []
        for f in self.files:
            if self._parent(f) == uri:
                out.append({"name": f.rsplit("/", 1)[1], "isDir": False})
        for d in self.dirs:
            if self._parent(d) == uri:
                out.append({"name": d.rsplit("/", 1)[1], "isDir": True})
        return out

    async def stat(self, uri, ctx=None):
        uri = uri.rstrip("/")
        if uri in self.dirs:
            return {"isDir": True, "size": 0}
        if uri in self.files:
            return {"isDir": False, "size": len(self.files[uri].encode("utf-8"))}
        raise FileNotFoundError(uri)

    async def read_file(self, uri, offset=0, limit=-1, ctx=None):
        uri = uri.rstrip("/")
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, lock_handle=None):
        uri = uri.rstrip("/")
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.files[uri] = content
        self._ensure_parents(uri)

    async def rm(self, uri, recursive=False, ctx=None, lock_handle=None):
        uri = uri.rstrip("/")
        self.files.pop(uri, None)
        if uri in self.dirs:
            if recursive:
                for f in [f for f in self.files if f.startswith(uri + "/")]:
                    self.files.pop(f, None)
                for d in [d for d in self.dirs if d == uri or d.startswith(uri + "/")]:
                    self.dirs.discard(d)
            else:
                self.dirs.discard(uri)

    async def mv(self, src, dst, ctx=None, lock_handle=None):
        src, dst = src.rstrip("/"), dst.rstrip("/")
        if src in self.files:
            self.files[dst] = self.files.pop(src)
            self._ensure_parents(dst)
            return
        if src in self.dirs:
            self.dirs.add(dst)
            for f in [f for f in self.files if f.startswith(src + "/")]:
                self.files[dst + f[len(src):]] = self.files.pop(f)
            for d in [d for d in self.dirs if d.startswith(src + "/")]:
                self.dirs.add(dst + d[len(src):])
                self.dirs.discard(d)
            self.dirs.discard(src)
            self._ensure_parents(dst)

    async def mkdir(self, uri, exist_ok=False, ctx=None):
        self.dirs.add(uri.rstrip("/"))

    async def delete_temp(self, uri, ctx=None):
        self.deleted_temp.append(uri)
        await self.rm(uri, recursive=True)

    async def glob(self, pattern, uri=None, ctx=None):
        return {"matches": []}


async def run_sync(fs, monkeypatch, ownership_tracked):
    monkeypatch.setattr(sp, "get_viking_fs", lambda: fs)
    proc = SemanticProcessor()

    async def _noop(*a, **k):
        return None

    proc._rewrite_target_image_uris = _noop
    return await proc._sync_topdown_recursive(
        TEMP, TARGET, lock=NO_LOCK, ownership_tracked=ownership_tracked
    )


def seed_manifest(fs, files, dirs=None):
    """Write a manifest recording `files` = {relpath: content-to-hash}."""
    payload = {
        "schema_version": 1,
        "source": {"kind": "feishu"},
        "synced_at": "2026-07-06T00:00:00Z",
        "files": [
            {"relpath": rel, "sha256": sha(content), "size": len(content.encode())}
            for rel, content in files.items()
        ],
        "dirs": dirs or [],
    }
    fs.files[f"{TARGET}/{SYNC_MANIFEST_FILENAME}"] = json.dumps(payload)


def read_manifest_raw(fs):
    raw = fs.files.get(f"{TARGET}/{SYNC_MANIFEST_FILENAME}")
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# Row 1: user-added files preserved under GUARDED
# ---------------------------------------------------------------------------
async def test_row1_user_file_preserved(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/content.md", "gen v1")
    fs.add_file(f"{TARGET}/notes.md", "my notes")
    fs.add_file(f"{TARGET}/ref.pdf", "PDF bytes")
    seed_manifest(fs, {"content.md": "gen v1"})
    fs.add_file(f"{TEMP}/content.md", "gen v2")

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert fs.files[f"{TARGET}/notes.md"] == "my notes"
    assert fs.files[f"{TARGET}/ref.pdf"] == "PDF bytes"
    assert fs.files[f"{TARGET}/content.md"] == "gen v2"


# ---------------------------------------------------------------------------
# Row 2: stale generated file (unchanged since sync) deleted
# ---------------------------------------------------------------------------
async def test_row2_stale_generated_deleted(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/old.md", "old gen")
    fs.add_file(f"{TARGET}/content.md", "gen v1")
    seed_manifest(fs, {"old.md": "old gen", "content.md": "gen v1"})
    fs.add_file(f"{TEMP}/content.md", "gen v2")

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert f"{TARGET}/old.md" not in fs.files  # our stale file, hash matched -> deleted
    assert fs.files[f"{TARGET}/content.md"] == "gen v2"


# ---------------------------------------------------------------------------
# Row 3: user-modified file removed upstream -> preserved + warn
# ---------------------------------------------------------------------------
async def test_row3_user_mod_source_removed(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/a.md", "USER EDIT")
    seed_manifest(fs, {"a.md": "our original"})  # recorded != current
    fs.add_file(f"{TEMP}/keep.md", "something")  # source no longer has a.md

    diff = await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert fs.files[f"{TARGET}/a.md"] == "USER EDIT"  # preserved
    assert any("a.md" in w for w in diff.warnings)


# ---------------------------------------------------------------------------
# Rows 4 / 12 / 13 / 14: MERGE_ONLY (no usable manifest) deletes nothing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "manifest_state",
    ["absent", "deleted", "corrupt", "newer_schema"],
)
async def test_rows_4_12_13_14_merge_only_deletes_nothing(monkeypatch, manifest_state):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/content.md", "gen")
    fs.add_file(f"{TARGET}/user.md", "mine")
    if manifest_state == "corrupt":
        fs.files[f"{TARGET}/{SYNC_MANIFEST_FILENAME}"] = "{not json"
    elif manifest_state == "newer_schema":
        fs.files[f"{TARGET}/{SYNC_MANIFEST_FILENAME}"] = json.dumps(
            {"schema_version": 99, "source": {}, "synced_at": "z", "files": [], "dirs": []}
        )
    # "absent"/"deleted": no manifest file at all
    fs.add_file(f"{TEMP}/content.md", "gen")  # subset (omits user.md)

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert fs.files[f"{TARGET}/user.md"] == "mine"  # nothing deleted
    manifest = read_manifest_raw(fs)
    assert manifest is not None  # rewritten
    rels = {f["relpath"] for f in manifest["files"]}
    assert rels == {"content.md"}  # generated only, never the user file


# ---------------------------------------------------------------------------
# Row 5: manifest written, valid, self-excluded
# ---------------------------------------------------------------------------
async def test_row5_manifest_written_self_excluded(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/content.md", "gen")
    seed_manifest(fs, {"content.md": "gen"})
    fs.add_file(f"{TEMP}/content.md", "gen2")

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    manifest = read_manifest_raw(fs)
    assert manifest["schema_version"] == 1
    rels = {f["relpath"] for f in manifest["files"]}
    assert SYNC_MANIFEST_FILENAME not in rels  # self-excluded
    assert rels == {"content.md"}


# ---------------------------------------------------------------------------
# Row 6: user-unchanged generated file is overwritten; manifest hash updated
# ---------------------------------------------------------------------------
async def test_row6_updated_generated_overwrites(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/content.md", "gen v1")
    seed_manifest(fs, {"content.md": "gen v1"})  # matches current -> not divergent
    fs.add_file(f"{TEMP}/content.md", "gen v2 new bytes")

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert fs.files[f"{TARGET}/content.md"] == "gen v2 new bytes"
    manifest = read_manifest_raw(fs)
    entry = next(f for f in manifest["files"] if f["relpath"] == "content.md")
    assert entry["sha256"] == sha("gen v2 new bytes")


# ---------------------------------------------------------------------------
# Row 7: user edited our file, still in source -> sidecar, no overwrite
# ---------------------------------------------------------------------------
async def test_row7_user_mod_still_in_source_sidecar(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/content.md", "USER EDITED")
    seed_manifest(fs, {"content.md": "our original"})  # recorded != current
    fs.add_file(f"{TEMP}/content.md", "fresh remote")

    diff = await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert fs.files[f"{TARGET}/content.md"] == "USER EDITED"  # NOT overwritten
    short = sha("fresh remote")[:8]
    sidecar = f"{TARGET}/content.remote-{short}.md"
    assert fs.files.get(sidecar) == "fresh remote"
    assert any("content.md" in w for w in diff.warnings)


# ---------------------------------------------------------------------------
# Row 8: manifest write crash leaves the OLD manifest intact
# ---------------------------------------------------------------------------
async def test_row8_atomic_write_crash_keeps_old_manifest(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/content.md", "gen v1")
    fs.add_file(f"{TARGET}/user.md", "mine")
    seed_manifest(fs, {"content.md": "gen v1"})
    old_raw = fs.files[f"{TARGET}/{SYNC_MANIFEST_FILENAME}"]
    fs.add_file(f"{TEMP}/content.md", "gen v2")

    async def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(sp, "write_manifest_atomic", _boom)

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    # flush swallows the error; the old manifest is still on disk, unchanged.
    assert fs.files[f"{TARGET}/{SYNC_MANIFEST_FILENAME}"] == old_raw
    assert fs.files[f"{TARGET}/user.md"] == "mine"  # no wrongful delete


# ---------------------------------------------------------------------------
# Row 9: Windows case-fold — "Content.md" recognized as our "content.md"
# ---------------------------------------------------------------------------
async def test_row9_windows_case_fold(monkeypatch):
    monkeypatch.setattr(os.path, "normcase", str.lower)
    fs = FakeFS()
    fs.add_file(f"{TARGET}/Content.md", "gen v1")  # on-disk different case
    seed_manifest(fs, {"content.md": "gen v1"})
    fs.add_file(f"{TEMP}/other.md", "x")  # source omits content

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert f"{TARGET}/Content.md" not in fs.files  # recognized as ours -> pruned


# ---------------------------------------------------------------------------
# Row 10: prune empty manifest dir; keep empty user dir
# ---------------------------------------------------------------------------
async def test_row10_empty_dir_prune(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/images/pic.png", "img")
    fs.add_dir(f"{TARGET}/scans")  # user empty dir
    seed_manifest(fs, {"images/pic.png": "img"}, dirs=["images"])
    fs.add_file(f"{TEMP}/content.md", "gen")  # no images this time

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert f"{TARGET}/images" not in fs.dirs  # tool dir, emptied -> pruned
    assert f"{TARGET}/scans" in fs.dirs  # user dir kept


# ---------------------------------------------------------------------------
# Row 11: nested subdir guarded — delete ours, keep user file + dir
# ---------------------------------------------------------------------------
async def test_row11_nested_subdir_guarded(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/sub/a.md", "gen a")
    fs.add_file(f"{TARGET}/sub/u.md", "user u")
    seed_manifest(fs, {"sub/a.md": "gen a"}, dirs=["sub"])
    fs.add_file(f"{TEMP}/content.md", "gen")  # source omits sub/

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert f"{TARGET}/sub/a.md" not in fs.files  # our stale file deleted
    assert fs.files[f"{TARGET}/sub/u.md"] == "user u"  # user file kept
    assert f"{TARGET}/sub" in fs.dirs  # non-empty -> dir kept


# ---------------------------------------------------------------------------
# Row 15: LEGACY_MIRROR regression — git resync still prunes deleted upstream
# ---------------------------------------------------------------------------
async def test_row15_git_resync_legacy_mirror(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/a.md", "a")
    fs.add_file(f"{TARGET}/b.md", "b")
    fs.add_file(f"{TEMP}/a.md", "a")  # upstream deleted b.md

    await run_sync(fs, monkeypatch, ownership_tracked=False)

    assert f"{TARGET}/b.md" not in fs.files  # legacy mirror still deletes
    assert f"{TARGET}/{SYNC_MANIFEST_FILENAME}" not in fs.files  # no manifest


# ---------------------------------------------------------------------------
# Row 16: file/dir type conflict — existing handling preserved
# ---------------------------------------------------------------------------
async def test_row16_file_dir_type_conflict(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/x/inner.md", "user in dir")  # target has dir x/
    seed_manifest(fs, {"x": "old file"})
    fs.add_file(f"{TEMP}/x", "new file")  # source produces file x

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert fs.files.get(f"{TARGET}/x") == "new file"  # file placed (source wins)
    assert f"{TARGET}/x/inner.md" not in fs.files  # conflicting dir removed


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
