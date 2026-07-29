# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the durable batch-import manifest store (issue #3120, blocking #3).

Covers atomic save/load, ``.bak`` recovery when the primary file is corrupt,
``list_batch_ids``, the deterministic batch id (idempotent re-submit) and the
control-URI guard. The FS is a real in-memory double so the tmp → bak → rename
rotation is exercised through the actual code path.
"""

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from openviking.service import batch_manifest_store as batch_store


class _InMemoryFS:
    """Minimal async FS capturing dirs/files (mirrors the real VikingFS API)."""

    def __init__(self) -> None:
        self.dirs: set[str] = set()
        self.files: Dict[str, str] = {}

    def _norm(self, uri: str) -> str:
        return uri.rstrip("/")

    async def mkdir(
        self, uri: str, mode: str = "755", exist_ok: bool = False, ctx: Optional[Any] = None
    ) -> None:
        u = self._norm(uri)
        parent = u.rsplit("/", 1)[0] if "/" in u else u
        if parent != u:
            self.dirs.add(parent)
        self.dirs.add(u)

    async def exists(self, uri: str, ctx: Optional[Any] = None) -> bool:
        u = self._norm(uri)
        return u in self.dirs or u in self.files

    async def write_file(self, uri: str, data: str, ctx: Optional[Any] = None) -> None:
        u = self._norm(uri)
        self.files[u] = data
        parent = u.rsplit("/", 1)[0] if "/" in u else u
        if parent != u:
            self.dirs.add(parent)

    async def read_file(self, uri: str, ctx: Optional[Any] = None) -> str:
        return self.files.get(self._norm(uri), "")

    async def mv(self, src: str, dst: str, ctx: Optional[Any] = None) -> None:
        s, d = self._norm(src), self._norm(dst)
        if s in self.files:
            self.files[d] = self.files.pop(s)
        elif s in self.dirs:
            self.dirs.discard(s)
            self.dirs.add(d)

    async def rm(self, uri: str, ctx: Optional[Any] = None) -> None:
        u = self._norm(uri)
        self.files.pop(u, None)
        self.dirs.discard(u)

    async def ls(self, uri: str, ctx: Optional[Any] = None) -> List[Dict[str, Any]]:
        u = self._norm(uri)
        prefix = u + "/"
        names: set[str] = set()
        for f in self.files:
            if f.startswith(prefix) and "/" not in f[len(prefix) :]:
                names.add(f[len(prefix) :])
        for d in self.dirs:
            if d.startswith(prefix) and "/" not in d[len(prefix) :]:
                names.add(d[len(prefix) :])
        return [{"name": n, "isDir": False} for n in names]


def _manifest(
    batch_id: str = "bi_abc", items: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    return batch_store.build_manifest(
        batch_id=batch_id,
        source_url="https://x.feishu.cn/wiki/settings/space1",
        source_kind="feishu_wiki_space",
        parent_uri="viking://resources/my_wiki",
        created_at="2026-07-28T00:00:00",
        items=items or [{"url": "https://x.feishu.cn/docx/d1", "task_id": "task-d1"}],
    )


def test_save_and_load_roundtrip():
    fs = _InMemoryFS()

    async def _run():
        await batch_store.save_manifest(_manifest(), fs, ctx=None)
        return await batch_store.load_manifest("bi_abc", fs, ctx=None)

    manifest = asyncio.run(_run())
    assert manifest is not None
    assert manifest["batch_id"] == "bi_abc"
    assert manifest["source_url"] == "https://x.feishu.cn/wiki/settings/space1"
    assert manifest["items"][0]["task_id"] == "task-d1"


def test_load_falls_back_to_bak_when_primary_corrupt():
    """If the primary manifest is corrupt, the .bak sidecar must be used
    (restart safety, issue #3120 review, blocking #3)."""
    fs = _InMemoryFS()

    async def _run():
        # Save twice so the tmp → bak → rename rotation populates the .bak
        # sidecar (the first save has no prior primary to rotate from).
        await batch_store.save_manifest(_manifest(), fs, ctx=None)
        await batch_store.save_manifest(_manifest(), fs, ctx=None)
        # Corrupt the primary file in place.
        primary = batch_store._batch_uri("bi_abc")
        await fs.write_file(primary, "{not valid json", ctx=None)
        return await batch_store.load_manifest("bi_abc", fs, ctx=None)

    manifest = asyncio.run(_run())
    assert manifest is not None  # recovered from .bak
    assert manifest["batch_id"] == "bi_abc"


def test_load_returns_none_for_unknown_batch():
    fs = _InMemoryFS()

    async def _run():
        return await batch_store.load_manifest("bi_missing", fs, ctx=None)

    assert asyncio.run(_run()) is None


def test_list_batch_ids():
    fs = _InMemoryFS()

    async def _run():
        await batch_store.save_manifest(_manifest("bi_one"), fs, ctx=None)
        await batch_store.save_manifest(_manifest("bi_two"), fs, ctx=None)
        return await batch_store.list_batch_ids(fs, ctx=None)

    ids = asyncio.run(_run())
    assert set(ids) == {"bi_one", "bi_two"}


def test_derive_batch_id_is_stable_and_scoped():
    a1 = batch_store.derive_batch_id(
        "https://x.feishu.cn/wiki/settings/space1", "viking://resources/p"
    )
    a2 = batch_store.derive_batch_id(
        "https://x.feishu.cn/wiki/settings/space1", "viking://resources/p"
    )
    assert a1 == a2  # idempotent re-submit
    # Different parent or source → different id.
    assert a1 != batch_store.derive_batch_id(
        "https://x.feishu.cn/wiki/settings/space1", "viking://resources/other"
    )
    assert a1 != batch_store.derive_batch_id(
        "https://x.feishu.cn/wiki/settings/space2", "viking://resources/p"
    )
    assert a1.startswith("bi_")


def test_is_batch_import_control_uri():
    assert batch_store.is_batch_import_control_uri("viking://resources/.batch_imports/bi_x.json")
    assert batch_store.is_batch_import_control_uri("viking://resources/.batch_imports/")
    assert not batch_store.is_batch_import_control_uri("viking://resources/my_wiki")
    assert not batch_store.is_batch_import_control_uri("viking://resources/.watch_tasks.json")


def test_save_manifest_rejects_non_serializable_payload():
    """A manifest with a non-serializable payload must not be written."""
    fs = _InMemoryFS()

    bad = _manifest()
    bad["items"] = [{"url": object()}]  # object() is not JSON-serializable

    with pytest.raises(TypeError):
        asyncio.run(batch_store.save_manifest(bad, fs, ctx=None))
    # Nothing was persisted.
    assert asyncio.run(fs.exists(batch_store._batch_uri("bi_abc"), ctx=None)) is False
