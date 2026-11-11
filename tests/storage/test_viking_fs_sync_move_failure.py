# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A failed move during tree sync must not be reported as success.

The caller deletes the staging tree once ``sync_tree`` returns, so a
swallowed ``mv`` failure destroys the only remaining copy of the file.
"""

from typing import Any, Dict, Optional

import pytest

from openviking.storage.viking_fs._sync import _SyncMixin


class _FakeSyncFS(_SyncMixin):
    def __init__(self, files: Dict[str, bytes], dirs: set[str]):
        self.files = dict(files)
        self.dirs = set(dirs)
        self.fail_moves: set[str] = set()
        self.deleted_temp: list[str] = []

    def _has_dir(self, uri: str) -> bool:
        prefix = uri.rstrip("/") + "/"
        return uri.rstrip("/") in self.dirs or any(
            path.startswith(prefix) for path in list(self.files) + list(self.dirs)
        )

    async def exists(self, uri: str, ctx: Any = None) -> bool:
        return uri in self.files or self._has_dir(uri)

    async def ls(
        self,
        uri: str,
        show_all_hidden: bool = True,
        node_limit: Any = None,
        ctx: Any = None,
    ) -> list[dict[str, Any]]:
        base = uri.rstrip("/") + "/"
        entries: dict[str, dict[str, Any]] = {}
        for path in list(self.files) + list(self.dirs):
            if not path.startswith(base):
                continue
            rest = path[len(base) :]
            name = rest.split("/", 1)[0]
            entries[name] = {"name": name, "isDir": "/" in rest or path in self.dirs}
        return list(entries.values())

    async def stat(self, uri: str, ctx: Any = None, skip_count: bool = False) -> dict[str, Any]:
        if uri in self.files:
            return {"isDir": False, "size": len(self.files[uri])}
        if self._has_dir(uri):
            return {"isDir": True, "size": 0}
        raise FileNotFoundError(uri)

    async def read_file(self, uri: str, ctx: Any = None) -> bytes:
        try:
            return self.files[uri]
        except KeyError as exc:
            raise FileNotFoundError(uri) from exc

    async def rm(
        self,
        uri: str,
        recursive: bool = False,
        ctx: Any = None,
        lease_ref: Optional[Dict[str, Any]] = None,
    ) -> None:
        if uri in self.files:
            del self.files[uri]
            return
        prefix = uri.rstrip("/") + "/"
        for path in [p for p in self.files if p.startswith(prefix)]:
            del self.files[path]
        self.dirs.discard(uri.rstrip("/"))

    async def mv(
        self,
        old_uri: str,
        new_uri: str,
        ctx: Any = None,
        lease_ref: Optional[Dict[str, Any]] = None,
    ) -> None:
        if old_uri in self.fail_moves:
            raise OSError(f"injected move failure: {old_uri}")
        if old_uri in self.files:
            self.files[new_uri] = self.files.pop(old_uri)
            return
        prefix = old_uri.rstrip("/") + "/"
        new_prefix = new_uri.rstrip("/") + "/"
        for path in [p for p in self.files if p.startswith(prefix)]:
            self.files[new_prefix + path[len(prefix) :]] = self.files.pop(path)
        self.dirs.add(new_uri.rstrip("/"))

    async def mkdir(
        self,
        uri: str,
        exist_ok: bool = True,
        ctx: Any = None,
        lease_ref: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.dirs.add(uri.rstrip("/"))

    async def delete_temp(self, uri: str, ctx: Any = None) -> None:
        self.deleted_temp.append(uri)
        await self.rm(uri, recursive=True, ctx=ctx)


SOURCE_ROOT = "viking://temp/user/staging"
TARGET_ROOT = "viking://resources/user/docs"


@pytest.mark.asyncio
async def test_failed_update_keeps_source_and_raises():
    source = f"{SOURCE_ROOT}/guide.md"
    target = f"{TARGET_ROOT}/guide.md"
    fs = _FakeSyncFS(
        files={source: b"new content", target: b"old content"},
        dirs={SOURCE_ROOT, TARGET_ROOT},
    )
    fs.fail_moves.add(source)

    with pytest.raises(OSError, match="injected move failure"):
        await fs.sync_tree(
            SOURCE_ROOT,
            TARGET_ROOT,
            file_change_status={source: True},
            delete_temp_after=True,
        )

    assert fs.files[source] == b"new content"
    assert fs.deleted_temp == []


@pytest.mark.asyncio
async def test_failed_add_keeps_source_and_raises():
    source = f"{SOURCE_ROOT}/new.md"
    fs = _FakeSyncFS(files={source: b"new content"}, dirs={SOURCE_ROOT, TARGET_ROOT})
    fs.fail_moves.add(source)

    with pytest.raises(OSError, match="injected move failure"):
        await fs.sync_tree(SOURCE_ROOT, TARGET_ROOT, delete_temp_after=True)

    assert fs.files[source] == b"new content"
    assert fs.deleted_temp == []


@pytest.mark.asyncio
async def test_successful_sync_still_reports_changes():
    source = f"{SOURCE_ROOT}/new.md"
    fs = _FakeSyncFS(files={source: b"new content"}, dirs={SOURCE_ROOT, TARGET_ROOT})

    diff = await fs.sync_tree(SOURCE_ROOT, TARGET_ROOT, delete_temp_after=True)

    assert diff.added_files == [f"{TARGET_ROOT}/new.md"]
    assert fs.files == {f"{TARGET_ROOT}/new.md": b"new content"}
    assert fs.deleted_temp == [SOURCE_ROOT]
