# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import hashlib

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError
from openviking_cli.retrieve.types import (
    ContextType,
    FindResult,
    MatchedContext,
    QueryResult,
    TypedQuery,
)
from openviking_cli.session.user_id import UserIdentifier


class _DummyAgfs:
    pass


class _FakeVectorStore:
    def __init__(self, *, fail_delete: bool = False):
        self.fail_delete = fail_delete
        self.deleted = []

    async def delete(self, ids, *, ctx):
        self.deleted.append((ctx.account_id, ids))
        if self.fail_delete:
            raise RuntimeError("delete failed")


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)


def _vector_id(uri: str, level: int = 2, account_id: str = "default") -> str:
    if level == 0 and not uri.endswith("/.abstract.md"):
        uri = f"{uri}/.abstract.md"
    elif level == 1 and not uri.endswith("/.overview.md"):
        uri = f"{uri}/.overview.md"
    return hashlib.md5(f"{account_id}:{uri}".encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_stale_retrieval_hits_are_filtered_and_cleaned(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())
    vector_store = _FakeVectorStore()
    monkeypatch.setattr(fs, "_get_vector_store", lambda: vector_store)
    stat_calls = []

    async def fake_stat(uri, ctx=None, skip_count=False):
        del ctx, skip_count
        stat_calls.append(uri)
        if uri == "viking://resources/live.md":
            return {"isDir": False}
        raise NotFoundError(uri, "file")

    monkeypatch.setattr(fs, "stat", fake_stat)

    live = MatchedContext(
        uri="viking://resources/live.md",
        context_type=ContextType.RESOURCE,
        level=2,
    )
    stale_file = MatchedContext(
        uri="viking://resources/missing.md",
        context_type=ContextType.RESOURCE,
        level=2,
    )
    stale_abstract = MatchedContext(
        uri="viking://resources/missing-dir/.abstract.md",
        context_type=ContextType.RESOURCE,
        level=0,
    )
    query_result = QueryResult(
        query=TypedQuery(query="missing", context_type=None, intent=""),
        matched_contexts=[live, stale_file, stale_abstract],
        searched_directories=[],
    )
    result = FindResult(
        memories=[],
        resources=[live, stale_file, stale_abstract],
        skills=[],
        query_results=[query_result],
    )

    await fs._drop_stale_retrieval_hits(result, _ctx())

    assert result.resources == [live]
    assert query_result.matched_contexts == [live]
    assert result.total == 1
    assert set(stat_calls) == {
        "viking://resources/live.md",
        "viking://resources/missing.md",
        "viking://resources/missing-dir/.abstract.md",
    }
    assert len(stat_calls) == 3
    assert vector_store.deleted == [
        (
            "default",
            sorted([
                _vector_id("viking://resources/missing-dir", level=0),
                _vector_id("viking://resources/missing.md"),
            ]),
        )
    ]


@pytest.mark.asyncio
async def test_percent_encoded_hit_is_not_dropped_when_decoded_uri_exists(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())
    vector_store = _FakeVectorStore()
    monkeypatch.setattr(fs, "_get_vector_store", lambda: vector_store)
    stat_calls = []

    async def fake_stat(uri, ctx=None, skip_count=False):
        del ctx, skip_count
        stat_calls.append(uri)
        if uri == "viking://resources/hello world.md":
            return {"isDir": False}
        raise NotFoundError(uri, "file")

    monkeypatch.setattr(fs, "stat", fake_stat)
    matched = MatchedContext(
        uri="viking://resources/hello%20world.md",
        context_type=ContextType.RESOURCE,
        level=2,
    )
    result = FindResult(memories=[], resources=[matched], skills=[])

    await fs._drop_stale_retrieval_hits(result, _ctx())

    assert result.resources == [matched]
    assert result.total == 1
    assert stat_calls == [
        "viking://resources/hello%20world.md",
        "viking://resources/hello world.md",
    ]
    assert vector_store.deleted == []


@pytest.mark.asyncio
async def test_stale_cleanup_failure_is_silent_but_hit_is_filtered(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())
    vector_store = _FakeVectorStore(fail_delete=True)
    monkeypatch.setattr(fs, "_get_vector_store", lambda: vector_store)

    async def fake_stat(uri, ctx=None, skip_count=False):
        del ctx, skip_count
        raise NotFoundError(uri, "file")

    monkeypatch.setattr(fs, "stat", fake_stat)
    stale = MatchedContext(
        uri="viking://resources/missing.md",
        context_type=ContextType.RESOURCE,
        level=2,
    )
    result = FindResult(memories=[], resources=[stale], skills=[])

    await fs._drop_stale_retrieval_hits(result, _ctx())

    assert result.resources == []
    assert result.total == 0
    assert vector_store.deleted == [("default", [_vector_id("viking://resources/missing.md")])]


@pytest.mark.asyncio
async def test_retrieval_stat_errors_keep_hit_and_skip_cleanup(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())
    vector_store = _FakeVectorStore()
    monkeypatch.setattr(fs, "_get_vector_store", lambda: vector_store)

    async def fake_stat(uri, ctx=None, skip_count=False):
        del uri, ctx, skip_count
        raise RuntimeError("agfs unavailable")

    monkeypatch.setattr(fs, "stat", fake_stat)
    matched = MatchedContext(
        uri="viking://resources/maybe-live.md",
        context_type=ContextType.RESOURCE,
        level=2,
    )
    result = FindResult(memories=[], resources=[matched], skills=[])

    await fs._drop_stale_retrieval_hits(result, _ctx())

    assert result.resources == [matched]
    assert result.total == 1
    assert vector_store.deleted == []
