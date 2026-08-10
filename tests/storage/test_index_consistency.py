# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.server.identity import RequestContext, Role
from openviking.storage.index_consistency import check_index_consistency
from openviking_cli.session.user_id import UserIdentifier


class _NoSidecarsVikingFS:
    async def exists(self, uri: str, ctx=None) -> bool:
        return False


class _EmptyVectorStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def filter(self, **kwargs):
        self.calls.append(kwargs)
        return []


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("account", "user"),
        role=Role.USER,
    )


def _file_entry(name: str, *, size: int | None) -> dict:
    entry = {
        "name": name,
        "uri": f"viking://resources/demo/{name}",
        "rel_path": name,
        "isDir": False,
    }
    if size is not None:
        entry["size"] = size
    return entry


async def test_zero_byte_text_file_is_not_an_index_expectation():
    vector_store = _EmptyVectorStore()

    report = await check_index_consistency(
        _NoSidecarsVikingFS(),
        vector_store,
        "viking://resources/demo",
        [_file_entry("empty.md", size=0)],
        _ctx(),
    )

    assert report.ok is True
    assert report.expected == ()
    assert report.missing_records == ()
    assert vector_store.calls == []


async def test_non_empty_and_missing_size_text_files_remain_expected():
    vector_store = _EmptyVectorStore()

    report = await check_index_consistency(
        _NoSidecarsVikingFS(),
        vector_store,
        "viking://resources/demo",
        [
            _file_entry("non-empty.md", size=12),
            _file_entry("missing-size.md", size=None),
        ],
        _ctx(),
    )

    assert {item.key for item in report.expected} == {
        "missing-size.md#level=2",
        "non-empty.md#level=2",
    }
    assert report.missing_records == report.expected
    assert len(vector_store.calls) == 2
