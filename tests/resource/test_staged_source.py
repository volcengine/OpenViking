# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for task-owned source staging."""

from pathlib import Path
from typing import Callable

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.resource.staged_source import stage_source


class _FakeVikingFS:
    def __init__(self, on_first_mkdir: Callable[[], None] | None = None) -> None:
        self._on_first_mkdir = on_first_mkdir
        self.mkdir_calls = 0
        self.writes: dict[str, bytes] = {}
        self.deleted_temps: list[str] = []

    def create_temp_uri(self, *, ctx: object) -> str:
        return "viking://temp/test"

    async def mkdir(
        self,
        uri: str,
        *,
        exist_ok: bool,
        ctx: object,
    ) -> None:
        self.mkdir_calls += 1
        if self.mkdir_calls == 1 and self._on_first_mkdir is not None:
            self._on_first_mkdir()

    async def write_file_bytes(
        self,
        uri: str,
        content: bytes,
        *,
        ctx: object,
    ) -> None:
        self.writes[uri] = content

    async def delete_temp(self, uri: str, *, ctx: object) -> None:
        self.deleted_temps.append(uri)
        self.writes.clear()


def _resource(path: Path) -> LocalResource:
    return LocalResource(
        path=path,
        source_type=SourceType.LOCAL,
        original_source=str(path),
        meta={},
        is_temporary=False,
    )


@pytest.mark.asyncio
async def test_directory_stage_skips_file_removed_after_scan(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    kept = source / "kept.txt"
    removed = source / "removed.txt"
    kept.write_bytes(b"kept")
    removed.write_bytes(b"removed")
    viking_fs = _FakeVikingFS(on_first_mkdir=removed.unlink)

    staged = await stage_source(_resource(source), viking_fs=viking_fs, ctx=object())

    assert staged.source_uri == "viking://temp/test/source/source"
    assert viking_fs.writes == {
        "viking://temp/test/source/source/kept.txt": b"kept",
    }
    assert viking_fs.deleted_temps == []


@pytest.mark.asyncio
async def test_directory_stage_keeps_permission_errors_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    locked = source / "locked.txt"
    locked.write_bytes(b"locked")
    original_read_bytes = Path.read_bytes

    def deny_locked_file(path: Path) -> bytes:
        if path == locked:
            raise PermissionError(13, "Permission denied", str(path))
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_locked_file)
    viking_fs = _FakeVikingFS()

    with pytest.raises(PermissionError):
        await stage_source(_resource(source), viking_fs=viking_fs, ctx=object())

    assert viking_fs.writes == {}
    assert viking_fs.deleted_temps == ["viking://temp/test"]
