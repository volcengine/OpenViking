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
        ("/local/test_account/resources/project/a.md", False, "/.relations/a.md/.relations.json"),  # row3 file->dir FIXED
        ("/local/test_account/resources/project/b.md", False, "/.relations/b.md/.relations.json"),  # row4 file->file FIXED
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
    assert results["/local/test_account/resources/project/x"] != results["/local/test_account/resources/project/x.md"]
