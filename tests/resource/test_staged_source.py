# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for task-owned local source staging."""

from pathlib import Path

import pytest

from openviking.resource.staged_source import _copy_local_tree


class FakeVikingFS:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.directories: set[str] = set()

    async def mkdir(self, uri: str, exist_ok: bool = False, **_: object) -> None:
        self.directories.add(uri)

    async def write_file_bytes(self, uri: str, content: bytes, **_: object) -> None:
        self.files[uri] = content


@pytest.mark.asyncio
async def test_copy_local_tree_applies_filters_before_reading(tmp_path: Path) -> None:
    (tmp_path / "keep.md").write_text("keep", encoding="utf-8")
    (tmp_path / "skip.tmp").write_text("skip", encoding="utf-8")
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "secret.md").write_text("secret", encoding="utf-8")
    excluded = tmp_path / "excluded"
    excluded.mkdir()
    (excluded / "draft.md").write_text("draft", encoding="utf-8")

    fs = FakeVikingFS()
    await _copy_local_tree(
        tmp_path,
        "viking://temp/test/source/root",
        fs,
        None,
        ignore_dirs={"ignored"},
        include="*.md",
        exclude="excluded/",
    )

    assert set(fs.files) == {"viking://temp/test/source/root/keep.md"}
    assert all("ignored" not in uri and "excluded" not in uri for uri in fs.files)
