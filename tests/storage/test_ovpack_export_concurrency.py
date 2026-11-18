# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for bounded-concurrency OVPack export reads (issue #7621)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.ovpack.operations import (
    OVPACK_EXPORT_READ_CONCURRENCY,
    _read_ovpack_file_bytes,
    _write_ovpack_archive,
)
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def request_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)


@pytest.fixture
def temp_path() -> Path:
    fd, path = tempfile.mkstemp(suffix=".ovpack")
    os.close(fd)
    p = Path(path)
    try:
        yield p
    finally:
        p.unlink(missing_ok=True)


class ConcurrencyTrackingFS:
    """Fake VikingFS that delays reads and records peak in-flight count."""

    def __init__(self, files: dict[str, bytes], delay: float = 0.02, fail_uri: str | None = None):
        self.files = files
        self.delay = delay
        self.fail_uri = fail_uri
        self.in_flight = 0
        self.peak_in_flight = 0
        self.read_calls: list[str] = []
        self._lock = asyncio.Lock()

    async def read_file_bytes(self, uri: str, ctx=None) -> bytes:
        async with self._lock:
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
            self.read_calls.append(uri)
        await asyncio.sleep(self.delay)
        async with self._lock:
            self.in_flight -= 1
        if uri == self.fail_uri:
            raise FileNotFoundError(uri)
        return self.files[uri]


def _file_entries(root_uri: str, names: list[str]) -> list[dict]:
    return [
        {
            "rel_path": name,
            "uri": f"{root_uri}/{name}",
            "isDir": False,
        }
        for name in names
    ]


async def test_read_ovpack_file_bytes_reads_concurrently_and_in_order(request_ctx):
    names = [f"file_{i}.md" for i in range(24)]
    files = {f"viking://root/{n}": f"content-{n}".encode("utf-8") for n in names}
    fs = ConcurrencyTrackingFS(files)

    results = await _read_ovpack_file_bytes(
        _file_entries("viking://root", names), "viking://root", fs, request_ctx
    )

    assert [r.decode("utf-8") for r in results] == [f"content-{n}" for n in names]
    # Reads overlap (not serial) but never exceed the configured concurrency.
    assert fs.peak_in_flight > 1
    assert fs.peak_in_flight <= OVPACK_EXPORT_READ_CONCURRENCY


async def test_read_ovpack_file_bytes_fails_fast_and_cancels_outstanding(request_ctx):
    names = [f"file_{i}.md" for i in range(24)]
    files = {f"viking://root/{n}": f"content-{n}".encode("utf-8") for n in names}
    fs = ConcurrencyTrackingFS(files, fail_uri="viking://root/file_2.md")

    with pytest.raises(FileNotFoundError):
        await _read_ovpack_file_bytes(
            _file_entries("viking://root", names), "viking://root", fs, request_ctx
        )

    # The failure aborts the export; it must not read every remaining file.
    assert len(fs.read_calls) < len(names)
    assert "viking://root/file_2.md" in fs.read_calls


async def test_write_ovpack_archive_is_byte_identical(request_ctx, temp_path):
    names = [f"file_{i}.md" for i in range(20)]
    files = {f"viking://root/{n}": f"content-{n}".encode("utf-8") for n in names}
    fs = ConcurrencyTrackingFS(files, delay=0.0)

    entries = _file_entries("viking://root", names)
    manifest = {"kind": "openviking.ovpack", "entries": []}
    for name in names:
        data = files[f"viking://root/{name}"]
        manifest["entries"].append(
            {
                "path": name,
                "kind": "file",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    await _write_ovpack_archive(
        fs,
        "viking://root",
        str(temp_path),
        "pkg",
        entries,
        manifest,
        [],
        [],
        request_ctx,
        None,
        None,
    )

    with zipfile.ZipFile(temp_path, "r") as zf:
        for name in names:
            assert zf.read(f"pkg/files/{name}") == files[f"viking://root/{name}"]
        stored_manifest = json.loads(
            zf.read("pkg/_ovpack/manifest.json").decode("utf-8")
        )

    # Manifest sizes and hashes were filled from the read bytes.
    for entry in stored_manifest["entries"]:
        if entry["kind"] == "file":
            data = files[f"viking://root/{entry['path']}"]
            assert entry["size"] == len(data)
            assert entry["sha256"] == hashlib.sha256(data).hexdigest()


async def test_write_ovpack_archive_preserves_directory_entries(request_ctx, temp_path):
    files = {"viking://root/a.md": b"aaa", "viking://root/dir/b.md": b"bbb"}
    fs = ConcurrencyTrackingFS(files, delay=0.0)

    entries = [
        {"rel_path": "a.md", "uri": "viking://root/a.md", "isDir": False},
        {"rel_path": "dir", "uri": "viking://root/dir", "isDir": True},
        {"rel_path": "dir/b.md", "uri": "viking://root/dir/b.md", "isDir": False},
    ]
    manifest = {
        "kind": "openviking.ovpack",
        "entries": [
            {"path": "a.md", "kind": "file"},
            {"path": "dir/b.md", "kind": "file"},
        ],
    }

    await _write_ovpack_archive(
        fs,
        "viking://root",
        str(temp_path),
        "pkg",
        entries,
        manifest,
        [],
        [],
        request_ctx,
        None,
        None,
    )

    with zipfile.ZipFile(temp_path, "r") as zf:
        names = set(zf.namelist())
        assert "pkg/files/a.md" in names
        assert "pkg/files/dir/" in names
        assert "pkg/files/dir/b.md" in names
        assert zf.read("pkg/files/a.md") == b"aaa"
        assert zf.read("pkg/files/dir/b.md") == b"bbb"
