# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the sync manifest core (issue #3029, Prompt A)."""

from __future__ import annotations

import json
import os

import pytest

from openviking.storage.queuefs import sync_manifest as sm


class _FakeVikingFS:
    """In-memory stub mirroring the viking_fs methods the manifest touches.

    Only read_file / write_file / exists are exercised; content is stored as
    bytes keyed by URI, matching how the real viking_fs treats control files.
    """

    def __init__(self):
        self.store: dict[str, bytes] = {}

    async def write_file(self, uri, content, ctx=None, lock_handle=None):
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.store[uri] = content

    async def read_file(self, uri, offset=0, limit=-1, ctx=None):
        if uri not in self.store:
            raise FileNotFoundError(uri)
        return self.store[uri].decode("utf-8")

    async def exists(self, uri, ctx=None):
        return uri in self.store


ROOT = "viking://user/u1/resources/doc"


def _manifest():
    return sm.Manifest(
        source={"kind": "feishu", "url": "https://x/docx/abc", "doc_id": "d1", "doc_type": "docx"},
        synced_at="2026-07-06T10:20:57Z",
        files=[
            sm.ManifestFile(relpath="content.md", sha256="a" * 64, size=4096),
            sm.ManifestFile(relpath="images/pic.png", sha256="b" * 64, size=10),
        ],
        dirs=["images"],
    )


async def test_round_trip_write_read_equality():
    fs = _FakeVikingFS()
    m = _manifest()
    await sm.write_manifest_atomic(ROOT, m, fs, ctx=None, lock_handle=None)
    got = await sm.read_manifest(ROOT, fs, ctx=None, lock_handle=None)
    assert got == m


async def test_absent_file_returns_none():
    fs = _FakeVikingFS()
    assert await sm.read_manifest(ROOT, fs, ctx=None, lock_handle=None) is None


async def test_corrupt_json_returns_none():
    fs = _FakeVikingFS()
    uri = f"{ROOT}/{sm.SYNC_MANIFEST_FILENAME}"
    fs.store[uri] = b"{not valid json"
    assert await sm.read_manifest(ROOT, fs, ctx=None, lock_handle=None) is None


@pytest.mark.parametrize("version", [2, 99, 1000])
async def test_newer_schema_returns_none(version):
    fs = _FakeVikingFS()
    uri = f"{ROOT}/{sm.SYNC_MANIFEST_FILENAME}"
    fs.store[uri] = json.dumps(
        {"schema_version": version, "source": {}, "synced_at": "z", "files": [], "dirs": []}
    ).encode()
    assert await sm.read_manifest(ROOT, fs, ctx=None, lock_handle=None) is None


async def test_written_manifest_is_posix_and_valid_json():
    fs = _FakeVikingFS()
    await sm.write_manifest_atomic(ROOT, _manifest(), fs, ctx=None, lock_handle=None)
    raw = json.loads(fs.store[f"{ROOT}/{sm.SYNC_MANIFEST_FILENAME}"].decode())
    assert raw["schema_version"] == sm.SUPPORTED_SCHEMA_VERSION
    assert {f["relpath"] for f in raw["files"]} == {"content.md", "images/pic.png"}
    # POSIX slashes, never backslashes
    assert all("\\" not in f["relpath"] for f in raw["files"])


async def test_casefold_lookup_windows(monkeypatch):
    # Simulate Windows normcase (lowercase) so "Content.md" matches "content.md".
    monkeypatch.setattr(os.path, "normcase", str.lower)
    m = _manifest()
    assert m.get("Content.md") is not None
    assert m.get("CONTENT.MD").sha256 == "a" * 64
    assert m.get("images/PIC.png") is not None


async def test_casefold_no_match_on_posix():
    # Default POSIX normcase is case-sensitive: "Content.md" != "content.md".
    m = _manifest()
    if os.path.normcase("A") == "A":  # POSIX
        assert m.get("Content.md") is None
    assert m.get("content.md") is not None


async def test_atomic_crash_leaves_old_manifest_intact():
    fs = _FakeVikingFS()
    old = _manifest()
    await sm.write_manifest_atomic(ROOT, old, fs, ctx=None, lock_handle=None)

    async def _boom(*a, **k):
        raise OSError("disk full mid-write")

    monkeypatch_target = fs
    monkeypatch_target.write_file = _boom  # simulate crash in the write primitive

    new = sm.Manifest(source={"kind": "feishu"}, synced_at="2026-07-07T00:00:00Z", files=[], dirs=[])
    with pytest.raises(OSError):
        await sm.write_manifest_atomic(ROOT, new, fs, ctx=None, lock_handle=None)

    # Old manifest must still be readable and unchanged.
    fs.write_file = _FakeVikingFS.write_file.__get__(fs)  # restore for read path (read is separate)
    got = await sm.read_manifest(ROOT, fs, ctx=None, lock_handle=None)
    assert got == old


@pytest.mark.parametrize(
    "current_hash, relpath, expected",
    [
        ("a" * 64, "content.md", False),  # hash matches -> not divergent
        ("f" * 64, "content.md", True),  # hash differs -> user edited our file
        ("f" * 64, "not-tracked.md", False),  # relpath absent -> not divergent
    ],
)
def test_divergent(current_hash, relpath, expected):
    assert sm.divergent(current_hash, _manifest(), relpath) is expected


def test_divergent_none_manifest():
    assert sm.divergent("a" * 64, None, "content.md") is False


def test_manifest_entry_hashes_via_sha256_hex():
    from openviking.storage.ovpack.format import sha256_hex

    data = b"hello viking"
    entry = sm.manifest_entry("dir\\sub\\f.md", data)
    assert entry.sha256 == sha256_hex(data)
    assert entry.size == len(data)
    assert entry.relpath == "dir/sub/f.md"  # normalized to POSIX slashes


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
