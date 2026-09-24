# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.retrieve.types import QueryResult
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.retrieval_config import RetrievalConfig


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)


def _make_viking_fs(*, enable_intent: bool) -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.acl_manager = None
    fs.query_embedder = MagicMock(name="embedder")
    fs.rerank_config = None
    fs.retrieval_config = RetrievalConfig(enable_intent=enable_intent)
    fs.vector_store = MagicMock(name="vector_store")
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_intent_test", default=None)
    fs._ensure_access = AsyncMock()
    fs._get_vector_store = MagicMock(return_value=fs.vector_store)
    fs._get_embedder = MagicMock(return_value=fs.query_embedder)
    fs._ctx_or_default = MagicMock(return_value=_ctx())
    fs.abstract = AsyncMock(return_value="")
    return fs


def test_retrieval_config_enable_intent_defaults_true():
    cfg = RetrievalConfig()
    assert cfg.enable_intent is True
    assert cfg.query_rewrite_only is False


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
    fs.search = AsyncMock(return_value=MagicMock(name="find_result", query_plan=None, total=0))
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
    fs.search = AsyncMock(return_value=MagicMock(name="find_result", query_plan=None, total=0))
    session_info = {"latest_archive_overview": "ov", "current_messages": []}
    session = MagicMock()
    session.get_context_for_search = AsyncMock(return_value=session_info)

    svc = SearchService(fs)
    await svc.search(query="hello", ctx=_ctx(), session=session, target_uri="")

    session.get_context_for_search.assert_awaited_once()
    assert fs.search.await_args.kwargs.get("session_info") is session_info


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enable_intent,planner_configured,with_context,expected_planning",
    [
        (True, True, False, True),
        (False, True, False, False),
        (True, False, False, False),
        (True, None, False, False),
        (True, False, True, True),
    ],
)
async def test_standalone_search_uses_explicit_planner_only(
    monkeypatch, enable_intent, planner_configured, with_context, expected_planning
):
    from types import SimpleNamespace

    from openviking_cli.retrieve.types import ContextType, QueryPlan, TypedQuery

    fs = _make_viking_fs(enable_intent=enable_intent)
    planner = (
        None
        if planner_configured is None
        else SimpleNamespace(_has_any_config=lambda: planner_configured)
    )
    monkeypatch.setattr(
        "openviking.storage.viking_fs._semantic.get_openviking_config",
        lambda: SimpleNamespace(query_planner=planner),
    )
    analyze = AsyncMock(
        return_value=QueryPlan(
            queries=[
                TypedQuery(
                    query="rewritten question",
                    context_type=ContextType.MEMORY,
                    intent="",
                    target_directories=["viking://user/another-user/memories"],
                )
            ],
            session_context="",
            reasoning="",
        )
    )
    monkeypatch.setattr(
        "openviking.retrieve.intent_analyzer.IntentAnalyzer",
        lambda **kwargs: SimpleNamespace(analyze=analyze),
    )
    captured = []

    class Retriever:
        def __init__(self, **kwargs):
            pass

        async def retrieve(self, typed_query, **kwargs):
            captured.append((typed_query, kwargs))
            return QueryResult(
                query=typed_query,
                matched_contexts=[],
                searched_directories=typed_query.target_directories,
            )

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.HierarchicalRetriever", Retriever
    )
    scope = "viking://user/user1/memories"
    await fs.search(
        "original question",
        ctx=_ctx(),
        target_uri=scope,
        session_info={"latest_archive_overview": "earlier context"} if with_context else None,
        limit=100,
        level=[2],
    )
    assert analyze.await_count == int(expected_planning)
    assert len(captured) == 1
    assert captured[0][0].query == (
        "rewritten question" if expected_planning else "original question"
    )
    assert captured[0][0].target_directories == [scope]
    assert captured[0][1]["ctx"].user.user_id == "user1"
    assert captured[0][1]["limit"] == 100 and captured[0][1]["level"] == [2]
    if expected_planning:
        assert analyze.call_args.kwargs["current_message"] == "original question"
        assert analyze.call_args.kwargs["messages"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rewrite_only", [True, False])
@pytest.mark.parametrize("model_type", ["resource", "skill", "memory"])
@pytest.mark.parametrize("with_context", [True, False])
async def test_query_only_ignores_model_routing_preserves_caller_scope(
    monkeypatch, rewrite_only, model_type, with_context
):
    from types import SimpleNamespace

    from openviking_cli.retrieve.types import ContextType, QueryPlan, TypedQuery

    fs = _make_viking_fs(enable_intent=True)
    fs.retrieval_config.query_rewrite_only = rewrite_only
    monkeypatch.setattr(
        "openviking.storage.viking_fs._semantic.get_openviking_config",
        lambda: SimpleNamespace(query_planner=SimpleNamespace(_has_any_config=lambda: True)),
    )
    analyze = AsyncMock(
        return_value=QueryPlan(
            queries=[
                TypedQuery(
                    query=text,
                    context_type=ContextType(model_type),
                    intent="model intent",
                    priority=3,
                    target_directories=["viking://resources/other"],
                )
                for text in ["first rewritten query", "second rewritten query"]
            ],
            session_context="",
            reasoning="",
        )
    )
    monkeypatch.setattr(
        "openviking.retrieve.intent_analyzer.IntentAnalyzer",
        lambda **kwargs: SimpleNamespace(analyze=analyze),
    )
    captured = []

    class Retriever:
        def __init__(self, **kwargs):
            pass

        async def retrieve(self, typed_query, **kwargs):
            captured.append((typed_query, kwargs))
            return QueryResult(query=typed_query, matched_contexts=[], searched_directories=[])

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.HierarchicalRetriever", Retriever
    )
    scope = "viking://user/user1/memories"
    filter = {"op": "must", "field": "context_type", "conds": ["memory"]}
    result = await fs.search(
        "original question",
        target_uri=scope,
        filter=filter,
        session_info={"latest_archive_overview": "previous context"} if with_context else None,
        ctx=_ctx(),
    )
    assert analyze.call_args.kwargs.get("context_type") is None
    assert [t.query for t, _ in captured] == ["first rewritten query", "second rewritten query"]
    for tq, kwargs in captured:
        assert tq.context_type == (None if rewrite_only else ContextType(model_type))
        assert tq.intent == ("" if rewrite_only else "model intent")
        assert tq.priority == (1 if rewrite_only else 3)
        assert tq.target_directories == [scope]
        assert kwargs["scope_dsl"] == filter
        assert kwargs["ctx"].user.user_id == "user1"
    assert result.query_plan.queries == [t for t, _ in captured]
    serialized = result.to_dict(include_provenance=True)
    assert [q["context_type"] for q in serialized["query_plan"]["queries"]] == [
        None if rewrite_only else model_type
    ] * 2
