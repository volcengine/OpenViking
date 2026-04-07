# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.viking_fs import VikingFS


def _make_viking_fs() -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = None
    fs.rerank_config = None
    fs.vector_store = None
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_test", default=None)
    fs._encryptor = None
    return fs


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("viking://resources/demo", True),
        ("viking://resources/demo/", True),
        ("viking://resources/demo/subdir", False),
        ("viking://skills/demo", False),
        ("viking://resources", False),
    ],
)
def test_is_resource_root_uri(uri: str, expected: bool):
    fs = _make_viking_fs()
    assert fs._is_resource_root_uri(uri) is expected


@pytest.mark.asyncio
async def test_read_resource_meta_skips_non_root_uri_without_io():
    fs = _make_viking_fs()
    fs.read = AsyncMock(side_effect=AssertionError("read should not be called"))

    meta = await fs._read_resource_meta("viking://resources/demo/subdir")

    assert meta == {}
    fs.read.assert_not_called()


@pytest.mark.asyncio
async def test_batch_fetch_abstracts_reads_tags_only_for_resource_root():
    fs = _make_viking_fs()
    fs.abstract = AsyncMock(return_value="summary")
    fs._read_resource_meta = AsyncMock(return_value={"tags": "t1,t2"})

    entries = [
        {"uri": "viking://resources/demo", "isDir": True},
        {"uri": "viking://resources/demo/subdir", "isDir": True},
    ]

    await fs._batch_fetch_abstracts(entries, abs_limit=128)

    fs._read_resource_meta.assert_awaited_once_with("viking://resources/demo", ctx=None)
    assert entries[0]["tags"] == "t1,t2"
    assert "tags" not in entries[1]
