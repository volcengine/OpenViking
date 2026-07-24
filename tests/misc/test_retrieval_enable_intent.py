# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.retrieve.types import QueryResult
from openviking_cli.utils.config.retrieval_config import RetrievalConfig
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)


def _make_viking_fs(*, enable_intent: bool) -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = MagicMock(name="embedder")
    fs.rerank_config = None
    fs.retrieval_config = RetrievalConfig(enable_intent=enable_intent)
    fs.vector_store = MagicMock(name="vector_store")
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_intent_test", default=None)
    fs._ensure_access = MagicMock()
    fs._get_vector_store = MagicMock(return_value=fs.vector_store)
    fs._get_embedder = MagicMock(return_value=fs.query_embedder)
    fs._ctx_or_default = MagicMock(return_value=_ctx())
    fs.abstract = AsyncMock(return_value="")
    return fs


def test_retrieval_config_enable_intent_defaults_true():
    cfg = RetrievalConfig()
    assert cfg.enable_intent is True


def test_retrieval_config_enable_intent_can_disable():
    cfg = RetrievalConfig(enable_intent=False)
    assert cfg.enable_intent is False


@pytest.mark.asyncio
async def test_search_skips_intent_and_uses_raw_query_when_disabled(monkeypatch):
    fs = _make_viking_fs(enable_intent=False)
    captured = {}

    class ForbiddenIntentAnalyzer:
        def __init__(self, *args, **kwargs):
            raise AssertionError("intent analysis must not run when disabled")

    class FakeRetriever:
        def __init__(self, storage, embedder, rerank_config, retrieval_config):
            pass

        async def retrieve(self, typed_query, **kwargs):
            captured["typed_query"] = typed_query
            return QueryResult(
                query=typed_query,
                matched_contexts=[],
                searched_directories=typed_query.target_directories,
            )

    monkeypatch.setattr(
        "openviking.retrieve.intent_analyzer.IntentAnalyzer",
        ForbiddenIntentAnalyzer,
    )
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.HierarchicalRetriever",
        FakeRetriever,
    )

    result = await fs.search(
        "raw query",
        target_uri="viking://resources/docs",
        session_info={
            "latest_archive_overview": "previous summary",
            "current_messages": [{"role": "user", "content": "previous turn"}],
        },
        ctx=_ctx(),
    )

    assert result.query_plan is None
    assert captured["typed_query"].query == "raw query"
    assert captured["typed_query"].intent == ""
    assert captured["typed_query"].target_directories == ["viking://resources/docs"]


def test_search_service_is_intent_enabled_follows_config():
    from openviking.service.search_service import SearchService

    svc = SearchService(_make_viking_fs(enable_intent=False))
    assert svc.is_intent_enabled() is False

    svc.set_viking_fs(_make_viking_fs(enable_intent=True))
    assert svc.is_intent_enabled() is True

    empty = SearchService()
    assert empty.is_intent_enabled() is True


@pytest.mark.asyncio
async def test_search_service_skips_session_context_when_intent_disabled():
    from openviking.service.search_service import SearchService

    fs = _make_viking_fs(enable_intent=False)
    fs.search = AsyncMock(
        return_value=MagicMock(name="find_result", query_plan=None, total=0)
    )
    session = MagicMock()
    session.get_context_for_search = AsyncMock(
        side_effect=AssertionError("must not scan session when intent disabled")
    )

    svc = SearchService(fs)
    await svc.search(query="hello", ctx=_ctx(), session=session, target_uri="")

    session.get_context_for_search.assert_not_awaited()
    assert fs.search.await_args.kwargs.get("session_info") is None


@pytest.mark.asyncio
async def test_search_service_loads_session_context_when_intent_enabled():
    from openviking.service.search_service import SearchService

    fs = _make_viking_fs(enable_intent=True)
    fs.search = AsyncMock(
        return_value=MagicMock(name="find_result", query_plan=None, total=0)
    )
    session_info = {"latest_archive_overview": "ov", "current_messages": []}
    session = MagicMock()
    session.get_context_for_search = AsyncMock(return_value=session_info)

    svc = SearchService(fs)
    await svc.search(query="hello", ctx=_ctx(), session=session, target_uri="")

    session.get_context_for_search.assert_awaited_once()
    assert fs.search.await_args.kwargs.get("session_info") is session_info
