# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Consistency checks must distinguish an unreadable index from an empty one."""

from __future__ import annotations

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.index_consistency import check_index_consistency
from openviking_cli.session.user_id import UserIdentifier

_ENTRIES = [
    {
        "rel_path": "notes.txt",
        "uri": "viking://resources/notes.txt",
        "isDir": False,
        "name": "notes.txt",
        "size": 12,
    }
]


class _FakeVikingFS:
    async def exists(self, uri, ctx=None):
        return False


class _CtxAcceptingVectorStore:
    async def filter(self, filter, limit=10, output_fields=None, ctx=None):
        raise RuntimeError("vector backend unavailable")


class _CtxlessVectorStore:
    async def filter(self, filter, limit=10, output_fields=None):
        raise RuntimeError("vector backend unavailable")


class _EmptyVectorStore:
    async def filter(self, filter, limit=10, output_fields=None, ctx=None):
        return []


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)


async def test_backend_failure_propagates_instead_of_reporting_missing_records():
    with pytest.raises(RuntimeError, match="vector backend unavailable"):
        await check_index_consistency(
            _FakeVikingFS(),
            _CtxAcceptingVectorStore(),
            "viking://resources",
            _ENTRIES,
            _ctx(),
        )


async def test_backend_failure_propagates_through_ctx_fallback():
    with pytest.raises(RuntimeError, match="vector backend unavailable"):
        await check_index_consistency(
            _FakeVikingFS(),
            _CtxlessVectorStore(),
            "viking://resources",
            _ENTRIES,
            _ctx(),
        )


async def test_empty_index_still_reports_missing_records():
    report = await check_index_consistency(
        _FakeVikingFS(),
        _EmptyVectorStore(),
        "viking://resources",
        _ENTRIES,
        _ctx(),
    )

    assert not report.ok
    assert [item.key for item in report.missing_records] == ["notes.txt#level=2"]
