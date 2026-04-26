# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set, Tuple

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, *, dirs: Iterable[str], files: Dict[str, str]):
        self._dirs: Set[str] = set(dirs)
        self._files: Dict[str, str] = dict(files)
        self.mv_vector_store_calls: List[Tuple[str, str]] = []

    async def ls(self, uri, show_all_hidden: bool = False, ctx=None):
        del show_all_hidden, ctx
        entries = []
        for child in sorted(self._dirs):
            if child == uri or self._parent(child) != uri:
                continue
            entries.append({"name": child.rsplit("/", 1)[-1], "isDir": True})
        for child in sorted(self._files):
            if self._parent(child) != uri:
                continue
            entries.append({"name": child.rsplit("/", 1)[-1], "isDir": False})
        return entries

    async def exists(self, uri, ctx=None):
        del ctx
        return uri in self._dirs or uri in self._files

    async def mkdir(self, uri, exist_ok: bool = True, ctx=None):
        del exist_ok, ctx
        self._ensure_dir(uri)

    async def stat(self, uri, ctx=None):
        del ctx
        if uri in self._dirs:
            return {"isDir": True}
        if uri in self._files:
            return {"isDir": False, "size": len(self._files[uri])}
        raise FileNotFoundError(uri)

    async def read_file(self, uri, ctx=None):
        del ctx
        if uri not in self._files:
            raise FileNotFoundError(uri)
        return self._files[uri]

    async def rm(self, uri, recursive: bool = False, ctx=None, lock_handle=None):
        del ctx, lock_handle
        if uri in self._files:
            self._files.pop(uri, None)
            return
        if recursive:
            prefix = uri.rstrip("/") + "/"
            for path in [path for path in self._files if path.startswith(prefix)]:
                self._files.pop(path, None)
            for path in [path for path in self._dirs if path == uri or path.startswith(prefix)]:
                self._dirs.discard(path)
            return
        self._dirs.discard(uri)

    async def mv(self, src, dst, ctx=None, lock_handle=None):
        del ctx, lock_handle
        if src in self._files:
            self._ensure_dir(self._parent(dst))
            self._files[dst] = self._files.pop(src)
            return

        src_prefix = src.rstrip("/") + "/"
        dst_prefix = dst.rstrip("/") + "/"
        affected_dirs = [path for path in self._dirs if path == src or path.startswith(src_prefix)]
        affected_files = [
            path for path in self._files if path == src or path.startswith(src_prefix)
        ]

        for path in sorted(affected_dirs, key=len):
            self._dirs.discard(path)
        for path in affected_dirs:
            mapped = dst if path == src else dst_prefix + path[len(src_prefix) :]
            self._ensure_dir(mapped)
        for path in affected_files:
            mapped = dst if path == src else dst_prefix + path[len(src_prefix) :]
            self._ensure_dir(self._parent(mapped))
            self._files[mapped] = self._files.pop(path)

    async def delete_temp(self, uri, ctx=None):
        await self.rm(uri, recursive=True, ctx=ctx)

    async def _mv_vector_store_l0_l1(self, src, dst, ctx=None, lock_handle=None):
        del ctx, lock_handle
        self.mv_vector_store_calls.append((src, dst))

    def _ensure_dir(self, uri: Optional[str]) -> None:
        current = uri
        while current and current not in self._dirs:
            self._dirs.add(current)
            current = self._parent(current)

    @staticmethod
    def _parent(uri: str) -> Optional[str]:
        if "/" not in uri:
            return None
        parent = uri.rsplit("/", 1)[0]
        return parent or None


@pytest.mark.asyncio
async def test_sync_topdown_recursive_reports_unchanged_paths(monkeypatch):
    root_uri = "viking://resources/tmp-root"
    target_uri = "viking://resources/final-root"
    fake_fs = _FakeVikingFS(
        dirs={
            root_uri,
            f"{root_uri}/child",
            target_uri,
            f"{target_uri}/child",
        },
        files={
            f"{root_uri}/same.txt": "same",
            f"{target_uri}/same.txt": "same",
            f"{root_uri}/changed.txt": "new",
            f"{target_uri}/changed.txt": "old",
            f"{root_uri}/added.txt": "added",
            f"{target_uri}/removed.txt": "removed",
            f"{root_uri}/child/keep.txt": "keep",
            f"{target_uri}/child/keep.txt": "keep",
        },
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )

    processor = SemanticProcessor()
    ctx = RequestContext(user=UserIdentifier("acc", "user", "agent"), role=Role.USER)
    diff = await processor._sync_topdown_recursive(
        root_uri,
        target_uri,
        ctx=ctx,
        file_change_status={
            f"{root_uri}/same.txt": False,
            f"{root_uri}/changed.txt": True,
            f"{root_uri}/child/keep.txt": False,
        },
    )

    assert diff.added_files == [f"{root_uri}/added.txt"]
    assert diff.updated_files == [f"{root_uri}/changed.txt"]
    assert diff.deleted_files == [f"{target_uri}/removed.txt"]
    assert set(diff.unchanged_files) == {
        f"{root_uri}/same.txt",
        f"{root_uri}/child/keep.txt",
    }
    assert diff.unchanged_dirs == [f"{root_uri}/child"]
