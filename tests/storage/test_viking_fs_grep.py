# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.viking_fs import VikingFS


class _DummyAgfs:
    pass


@pytest.fixture
def fs(monkeypatch):
    viking_fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(viking_fs, "stat", _fake_stat)
    monkeypatch.setattr(
        viking_fs,
        "_uri_to_path",
        lambda uri, ctx=None: uri.replace("viking://", "/"),
    )
    monkeypatch.setattr(
        viking_fs,
        "_path_to_uri",
        lambda path, ctx=None: path.replace("/", "viking://", 1),
    )
    return viking_fs


async def _fake_stat(uri, ctx=None):
    return {"name": uri.rsplit("/", 1)[-1], "isDir": True}


@pytest.mark.asyncio
async def test_grep_delegates_to_agfs_with_expected_filters(monkeypatch, fs):
    calls = []

    async def fake_grep(**kwargs):
        calls.append(kwargs)
        return {"matches": [], "files_scanned": 0}

    monkeypatch.setattr(fs._async_agfs, "grep", fake_grep)

    result = await fs.grep(
        "viking://resources",
        pattern="needle",
        exclude_uri="viking://resources/archive",
        case_insensitive=True,
        node_limit=10,
        level_limit=3,
    )

    assert result == {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
    assert calls == [
        {
            "path": "/resources",
            "pattern": "needle",
            "recursive": True,
            "case_insensitive": True,
            "stream": False,
            "node_limit": 10,
            "exclude_path": "/resources/archive",
            "level_limit": 3,
        }
    ]


@pytest.mark.asyncio
async def test_grep_maps_agfs_matches_to_viking_uris(monkeypatch, fs):
    async def fake_grep(**kwargs):
        return {
            "matches": [
                {"file": "dir/a.md", "line": 2, "content": "first match"},
                {"file": "/dir/b.md", "line_number": 5, "content": "second match"},
            ],
            "files_scanned": 7,
        }

    monkeypatch.setattr(fs._async_agfs, "grep", fake_grep)

    result = await fs.grep("viking://resources", pattern="match")

    assert result == {
        "matches": [
            {
                "line": 2,
                "uri": "viking://resources/dir/a.md",
                "content": "first match",
            },
            {
                "line": 5,
                "uri": "viking://resources/dir/b.md",
                "content": "second match",
            },
        ],
        "count": 2,
        "match_count": 2,
        "files_scanned": 7,
    }


@pytest.mark.asyncio
async def test_grep_applies_node_limit_to_backend_results(monkeypatch, fs):
    async def fake_grep(**kwargs):
        return {
            "matches": [
                {"file": "a.md", "line": 1, "content": "a"},
                {"file": "b.md", "line": 1, "content": "b"},
                {"file": "c.md", "line": 1, "content": "c"},
            ]
        }

    monkeypatch.setattr(fs._async_agfs, "grep", fake_grep)

    result = await fs.grep("viking://resources", pattern="match", node_limit=2)

    assert result["count"] == 2
    assert result["match_count"] == 2
    assert result["files_scanned"] == 2
    assert [match["uri"] for match in result["matches"]] == [
        "viking://resources/a.md",
        "viking://resources/b.md",
    ]
