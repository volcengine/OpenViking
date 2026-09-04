# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for filtered local-tree staging."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.resource.staged_source import _copy_local_tree, stage_source


class _FakeVikingFS:
    def __init__(self) -> None:
        self.dirs: set[str] = set()
        self.files: dict[str, bytes] = {}
        self._temp_seq = 0

    def create_temp_uri(self, ctx=None) -> str:
        self._temp_seq += 1
        return f"viking://temp/t{self._temp_seq}"

    async def delete_temp(self, uri: str, ctx=None) -> None:
        prefix = uri.rstrip("/")
        self.dirs = {d for d in self.dirs if not d.startswith(prefix)}
        self.files = {k: v for k, v in self.files.items() if not k.startswith(prefix)}

    async def mkdir(self, uri: str, exist_ok: bool = False, ctx=None) -> None:
        self.dirs.add(uri.rstrip("/"))

    async def write_file_bytes(self, uri: str, content: bytes, ctx=None) -> None:
        self.files[uri] = content


@pytest.mark.asyncio
async def test_copy_local_tree_honors_ignore_dirs_include_exclude(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / "docs").mkdir(parents=True)
    (root / "large-data").mkdir(parents=True)
    (root / "docs" / "keep.md").write_text("# keep\n", encoding="utf-8")
    (root / "docs" / "private.md").write_text("# private\n", encoding="utf-8")
    (root / "docs" / "skip.txt").write_text("nope\n", encoding="utf-8")
    (root / "large-data" / "ignored.bin").write_bytes(b"x" * 1024)

    fs = _FakeVikingFS()
    ctx = SimpleNamespace()
    await _copy_local_tree(
        root,
        "viking://temp/t/source/tree",
        fs,
        ctx,
        ignore_dirs="large-data",
        include="*.md",
        exclude="private*.md",
    )

    written = sorted(Path(uri).name for uri in fs.files)
    assert written == ["keep.md"]
    assert all("large-data" not in uri for uri in fs.files)
    assert all("ignored.bin" not in uri for uri in fs.files)
    assert all("private.md" not in uri for uri in fs.files)
    assert all("skip.txt" not in uri for uri in fs.files)

@pytest.mark.asyncio
async def test_copy_local_tree_does_not_mkdir_empty_excluded_dirs(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / "docs").mkdir(parents=True)
    (root / "large-data" / "nested").mkdir(parents=True)
    (root / "docs" / "keep.md").write_text("# keep\n", encoding="utf-8")
    (root / "large-data" / "nested" / "ignored.bin").write_bytes(b"x")

    fs = _FakeVikingFS()
    await _copy_local_tree(
        root,
        "viking://temp/t/source/tree",
        fs,
        SimpleNamespace(),
        ignore_dirs="large-data",
        include="*.md",
    )

    assert sorted(Path(uri).name for uri in fs.files) == ["keep.md"]
    assert all("large-data" not in uri for uri in fs.dirs)
    assert "viking://temp/t/source/tree" in fs.dirs
    assert "viking://temp/t/source/tree/docs" in fs.dirs


@pytest.mark.asyncio
async def test_stage_source_forwards_watch_shaped_filter_kwargs(tmp_path: Path) -> None:
    """Pin the #4570 seam: watch/processor kwargs must reach staging filters."""
    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "large-data" / "nested").mkdir(parents=True)
    (root / "docs" / "keep.md").write_text("# keep\n", encoding="utf-8")
    (root / "docs" / "private.md").write_text("# private\n", encoding="utf-8")
    (root / "large-data" / "nested" / "ignored.bin").write_bytes(b"x" * 64)

    # Shape matches watch-refresh / add-resource processor_kwargs fields.
    processor_kwargs = {
        "ignore_dirs": "large-data",
        "include": "*.md",
        "exclude": "private*.md",
    }
    fs = _FakeVikingFS()
    resource = LocalResource(
        path=root,
        source_type=SourceType.LOCAL,
        original_source=str(root),
        meta={},
    )

    staged = await stage_source(
        resource,
        viking_fs=fs,
        ctx=SimpleNamespace(),
        ignore_dirs=processor_kwargs.get("ignore_dirs"),
        include=processor_kwargs.get("include"),
        exclude=processor_kwargs.get("exclude"),
    )

    assert staged.source_type == SourceType.LOCAL
    written = sorted(Path(uri).name for uri in fs.files)
    assert written == ["keep.md"]
    assert all("large-data" not in uri for uri in fs.files)
    assert all("private.md" not in uri for uri in fs.files)
    assert any(uri.endswith("/docs/keep.md") for uri in fs.files)
