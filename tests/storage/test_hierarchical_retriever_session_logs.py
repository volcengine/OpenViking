# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openviking.retrieve import hierarchical_retriever as retriever_module
from openviking.retrieve.hierarchical_retriever import (
    HierarchicalRetriever,
    RetrieverMode,
    _classify_session_log_uri,
    _is_internal_session_log_result,
    _is_internal_session_log_uri,
    _search_children_excluding_session_logs,
    _search_in_tenant_excluding_session_logs,
    _SessionLogReplacementBudget,
)
from openviking.server.identity import Role
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking.storage.vikingdb_manager import VikingDBManagerProxy
from openviking_cli.retrieve import FindResult, QueryResult, TypedQuery


def _result(uri: str, *, score: float = 0.5, level: int = 2) -> dict:
    return {
        "uri": uri,
        "_score": score,
        "context_type": "memory",
        "level": level,
        "abstract": uri,
    }


@pytest.mark.parametrize(
    "uri",
    [
        "viking://session/s1/messages.jsonl",
        "VIKING://session/s1/messages.jsonl",
        "viking://session/s1/messages.jsonl/.abstract.md",
        "viking://user/sessions/s1/messages.jsonl",
        "viking://user/alice/sessions/s1/messages.jsonl",
        "viking://user/alice/sessions/s1/.overview.md",
        "viking://user/alice/sessions/s1/history/archive_001/messages.jsonl",
        "viking://user/alice/sessions/s1/history/archive_001/.abstract.md",
        "viking://user/alice/sessions/s1/history/archive_001/messages.jsonl/.overview.md",
        "viking://user/alice/sessions/s1/MESSAGES.JSONL",
        "viking://user/alice/sessions/s1/messages%2Ejsonl",
        "viking://user/alice/sessions/s1/messages%252Ejsonl",
        "viking://user/alice/sessions/s1/messages%2525252Ejsonl",
        "viking://user/alice/sessions/s1/messages.jsonl?version=1",
        "viking://user/sessions/sessions/s1/messages.jsonl",
    ],
)
def test_internal_session_log_uri_matches_transcripts_and_sidecars(uri):
    assert _is_internal_session_log_uri(uri) is True


@pytest.mark.parametrize(
    "uri",
    [
        "viking://resources/project/messages.jsonl",
        "viking://user/alice/memories/messages.jsonl",
        "viking://user/alice/resources/sessions/s1/messages.jsonl",
        "viking://session/s1/notes.txt",
        "viking://session/s1/export/messages.jsonl",
        "viking://session/s1/messages.jsonl/.summary.md",
        "viking://session/s1/messages.jsonl%3Fdraft",
        "viking://session/s1/messages.jsonl%23draft",
        "viking://user/alice/sessions/s1/history/archive_001/.summary.md",
        "messages.jsonl",
    ],
)
def test_internal_session_log_uri_allows_non_internal_files(uri):
    assert _is_internal_session_log_uri(uri) is False


def test_internal_session_log_uri_fails_closed_on_normalization_bounds():
    encoded_name = "messages%2Ejsonl"
    for _ in range(retriever_module._SESSION_URI_MAX_DECODE_PASSES):
        encoded_name = encoded_name.replace("%", "%25")
    assert _is_internal_session_log_uri(f"viking://session/s1/{encoded_name}") is True
    assert (
        _is_internal_session_log_uri(
            "viking://session/s1/" + "x" * retriever_module._SESSION_URI_MAX_LENGTH
        )
        is True
    )
    assert (
        _is_internal_session_log_uri(
            "viking://resources/" + "x" * retriever_module._SESSION_URI_MAX_LENGTH
        )
        is False
    )
    encoded_root = "viking://%73ession/s1/" + "x" * retriever_module._SESSION_URI_MAX_LENGTH
    classification = _classify_session_log_uri(encoded_root)
    assert classification.is_internal is True
    assert classification.incomplete is True


def test_internal_session_log_uri_uses_request_identity_for_ambiguous_user():
    uri = "viking://user/sessions/sessions/s1/messages.jsonl"
    assert _is_internal_session_log_uri(uri, user_id="sessions") is True
    assert _is_internal_session_log_uri(uri, user_id="alice") is False


@pytest.mark.parametrize(
    "result",
    [
        _result("viking://user/alice/sessions/s1", level=0),
        _result("viking://user/alice/sessions/s1", level=1),
        _result("viking://session/s1/history/archive_002", level=0),
        _result("viking://session/s1/history/archive_002", level=1),
    ],
)
def test_internal_session_log_result_matches_base_uri_sidecars(result):
    assert _is_internal_session_log_result(result) is True


@pytest.mark.parametrize(
    "result",
    [
        _result("viking://user/alice/sessions/s1", level=2),
        _result("viking://user/alice/sessions/s1/notes.txt", level=2),
        _result("viking://user/alice/sessions/s1/notes", level=1),
    ],
)
def test_internal_session_log_result_preserves_explicit_session_content(result):
    assert _is_internal_session_log_result(result) is False


@pytest.mark.asyncio
async def test_filtered_global_search_pages_past_internal_hits():
    class FakeProxy:
        def __init__(self):
            self.calls = []

        async def search_in_tenant(self, *, limit=10, offset=0, **kwargs):
            del kwargs
            self.calls.append((limit, offset))
            if offset == 0:
                return [
                    _result(f"viking://session/s{idx}/messages.jsonl", score=1 - idx / 100)
                    for idx in range(limit)
                ]
            return [
                _result("viking://session/s1/notes.txt", score=0.6),
                _result("viking://resources/guide.md", score=0.5),
            ]

    proxy = FakeProxy()
    (
        results,
        searches,
        scanned,
        truncated,
    ) = await _search_in_tenant_excluding_session_logs(
        proxy,
        desired_limit=2,
        page_limit=3,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=[],
        extra_filter=None,
        level=None,
    )

    assert [result["uri"] for result in results] == [
        "viking://session/s1/notes.txt",
        "viking://resources/guide.md",
    ]
    assert proxy.calls == [(3, 0), (3, 3)]
    assert (searches, scanned) == (2, 5)
    assert truncated is False


@pytest.mark.asyncio
async def test_filtered_child_search_pages_past_internal_hits():
    class FakeProxy:
        def __init__(self):
            self.calls = []

        async def search_children_in_tenant(self, *, limit=10, offset=0, **kwargs):
            del kwargs
            self.calls.append((limit, offset))
            if offset == 0:
                return [
                    _result(f"viking://session/s{idx}/messages.jsonl", score=1 - idx / 100)
                    for idx in range(limit)
                ]
            return [_result("viking://session/s1/notes.txt", score=0.4)]

    proxy = FakeProxy()
    (
        results,
        searches,
        scanned,
        truncated,
    ) = await _search_children_excluding_session_logs(
        proxy,
        parent_uri="viking://session/s1",
        desired_limit=1,
        page_limit=3,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=["viking://session/s1"],
        extra_filter=None,
    )

    assert [result["uri"] for result in results] == ["viking://session/s1/notes.txt"]
    assert proxy.calls == [(3, 0), (3, 3)]
    assert (searches, scanned) == (2, 4)
    assert truncated is False


@pytest.mark.asyncio
async def test_filtered_search_reports_scan_cap(monkeypatch):
    class FakeProxy:
        async def search_in_tenant(self, *, limit=10, **kwargs):
            del kwargs
            return [_result(f"viking://session/s{idx}/messages.jsonl") for idx in range(limit)]

    monkeypatch.setattr(retriever_module, "_SESSION_LOG_FILTER_MAX_SCAN_PAGES", 2)
    warning = MagicMock()
    monkeypatch.setattr(retriever_module.logger, "warning", warning)
    (
        results,
        searches,
        scanned,
        truncated,
    ) = await _search_in_tenant_excluding_session_logs(
        FakeProxy(),
        desired_limit=1,
        page_limit=3,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=[],
        extra_filter=None,
        level=None,
    )

    assert results == []
    assert (searches, scanned) == (2, 6)
    assert truncated is True
    warning.assert_called_once()
    assert "request-wide replacement-page budget" in warning.call_args.args[0]

    query_result = QueryResult(TypedQuery("notes", None, ""), [], [], truncated=truncated)
    output = FindResult([], [], [], query_results=[query_result]).to_dict(include_provenance=True)
    assert output["provenance"][0]["truncated"] is True


@pytest.mark.asyncio
async def test_filtered_search_reports_incomplete_uri_even_when_page_ends():
    class FakeProxy:
        async def search_in_tenant(self, **kwargs):
            del kwargs
            return [_result("viking://resources/" + "x" * retriever_module._SESSION_URI_MAX_LENGTH)]

    results, searches, scanned, truncated = await _search_in_tenant_excluding_session_logs(
        FakeProxy(),
        desired_limit=2,
        page_limit=2,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=[],
        extra_filter=None,
        level=None,
    )

    assert results == []
    assert (searches, scanned, truncated) == (1, 1, True)


@pytest.mark.asyncio
async def test_child_offset_flows_through_proxy_and_backend(monkeypatch):
    offsets = []

    async def fake_search(self, **kwargs):
        offsets.append(kwargs["offset"])
        return [_result("viking://resources/guide.md")]

    monkeypatch.setattr(VikingVectorIndexBackend, "search", fake_search)
    backend = object.__new__(VikingVectorIndexBackend)
    ctx = SimpleNamespace(
        account_id="acct",
        role=Role.USER,
        user=SimpleNamespace(user_id="alice"),
        actor_peer_id=None,
    )
    proxy = VikingDBManagerProxy(backend, ctx)

    results = await proxy.search_children_in_tenant(
        parent_uri="viking://resources",
        query_vector=None,
        limit=20,
        offset=40,
    )

    assert [result["uri"] for result in results] == ["viking://resources/guide.md"]
    assert offsets == [40]


@pytest.mark.asyncio
async def test_quick_retrieve_replaces_session_logs(monkeypatch):
    class FakeProxy:
        collection_name = "test"

        def __init__(self):
            self.calls = []

        async def collection_exists_bound(self):
            return True

        async def search_in_tenant(self, *, limit=10, offset=0, **kwargs):
            del kwargs
            self.calls.append((limit, offset))
            if offset == 0:
                return [
                    _result(f"viking://session/s{idx}/messages.jsonl", score=1 - idx / 100)
                    for idx in range(limit)
                ]
            return [
                _result("viking://session/s1/notes.txt", score=0.6),
                _result("viking://resources/guide.md", score=0.5),
            ]

    proxy = FakeProxy()
    monkeypatch.setattr(retriever_module, "VikingDBManagerProxy", lambda _storage, _ctx: proxy)
    retriever = HierarchicalRetriever(storage=MagicMock(), embedder=None)

    result = await retriever.retrieve(
        TypedQuery("notes", None, "", target_directories=["viking://session/s1"]),
        ctx=MagicMock(),
        limit=2,
        mode=RetrieverMode.QUICK,
    )

    assert [context.uri for context in result.matched_contexts] == [
        "viking://session/s1/notes.txt",
        "viking://resources/guide.md",
    ]
    assert proxy.calls == [(10, 0), (10, 10)]


@pytest.mark.asyncio
async def test_quick_retrieve_exposes_scan_cap(monkeypatch):
    class FakeProxy:
        collection_name = "test"

        async def collection_exists_bound(self):
            return True

        async def search_in_tenant(self, *, limit=10, **kwargs):
            del kwargs
            return [_result(f"viking://session/s{idx}/messages.jsonl") for idx in range(limit)]

    monkeypatch.setattr(retriever_module, "_SESSION_LOG_FILTER_MAX_SCAN_PAGES", 1)
    monkeypatch.setattr(
        retriever_module, "VikingDBManagerProxy", lambda _storage, _ctx: FakeProxy()
    )
    retriever = HierarchicalRetriever(storage=MagicMock(), embedder=None)

    result = await retriever.retrieve(
        TypedQuery("notes", None, ""),
        ctx=MagicMock(),
        limit=1,
        mode=RetrieverMode.QUICK,
    )

    assert result.matched_contexts == []
    assert result.truncated is True


@pytest.mark.asyncio
async def test_quick_image_search_keeps_current_candidate_budget(monkeypatch):
    class FakeProxy:
        collection_name = "test"

        def __init__(self):
            self.calls = []

        async def collection_exists_bound(self):
            return True

        async def search_in_tenant(self, *, limit=10, offset=0, level=None, **kwargs):
            del kwargs
            self.calls.append((limit, offset, level))
            return [
                _result(f"viking://resources/image-{idx}.png", score=1 - idx / 1000)
                for idx in range(limit)
            ]

    proxy = FakeProxy()
    monkeypatch.setattr(retriever_module, "VikingDBManagerProxy", lambda _storage, _ctx: proxy)
    retriever = HierarchicalRetriever(storage=MagicMock(), embedder=None)

    await retriever.retrieve(
        TypedQuery("image", None, "", image_query=True),
        ctx=MagicMock(),
        limit=5,
    )

    assert proxy.calls == [(50, 0, [2])]


@pytest.mark.asyncio
async def test_recursive_search_replaces_session_log_children():
    class FakeProxy:
        def __init__(self):
            self.calls = []

        async def search_children_in_tenant(self, *, limit=10, offset=0, **kwargs):
            del kwargs
            self.calls.append((limit, offset))
            if offset == 0:
                return [
                    _result(
                        f"viking://user/alice/sessions/s{idx}/messages.jsonl",
                        score=1 - idx / 100,
                    )
                    for idx in range(limit)
                ]
            return [_result("viking://user/alice/sessions/s1/notes.txt", score=0.4)]

    proxy = FakeProxy()
    retriever = HierarchicalRetriever(storage=MagicMock(), embedder=None)
    truncation_state = {}
    candidates = await retriever._recursive_search(
        vector_proxy=proxy,
        query="notes",
        query_vector=None,
        sparse_query_vector=None,
        starting_points=[("viking://user/alice/sessions/s1", 1.0)],
        limit=10,
        mode=RetrieverMode.THINKING,
        target_dirs=["viking://user/alice/sessions/s1"],
        truncation_state=truncation_state,
    )

    assert [candidate["uri"] for candidate in candidates] == [
        "viking://user/alice/sessions/s1/notes.txt"
    ]
    assert proxy.calls == [(20, 0), (20, 20)]
    assert truncation_state == {}


@pytest.mark.asyncio
async def test_recursive_search_shares_replacement_budget_across_parents():
    class FakeProxy:
        def __init__(self):
            self.calls = []

        async def search_children_in_tenant(self, *, parent_uri, limit=10, offset=0, **kwargs):
            del kwargs
            self.calls.append((parent_uri, limit, offset))
            if offset == 0:
                return [_result(f"viking://session/s{idx}/messages.jsonl") for idx in range(limit)]
            return [_result(f"{parent_uri}/note.md", score=0.4)]

    proxy = FakeProxy()
    retriever = HierarchicalRetriever(storage=MagicMock(), embedder=None)
    truncation_state = {}
    candidates = await retriever._recursive_search(
        vector_proxy=proxy,
        query="notes",
        query_vector=None,
        sparse_query_vector=None,
        starting_points=[
            ("viking://resources/one", 1.0),
            ("viking://resources/two", 0.9),
        ],
        limit=1,
        mode=RetrieverMode.THINKING,
        truncation_state=truncation_state,
        replacement_page_budget=_SessionLogReplacementBudget(remaining=1),
    )

    assert len(proxy.calls) == 3
    assert sum(offset > 0 for _, _, offset in proxy.calls) == 1
    assert [candidate["uri"] for candidate in candidates] == ["viking://resources/one/note.md"]
    assert truncation_state == {"truncated": True}
