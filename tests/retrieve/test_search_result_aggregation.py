# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Multi-typed-query search aggregation tests.

Intent analysis emits several near-duplicate phrasings, so the same URI can
surface under multiple typed queries. These tests pin the aggregation
contract: dedupe by URI keeping the best score, order each bucket by score
(not by query block position), and honor the caller's limit per bucket.
"""

from openviking.storage.viking_fs._semantic import (
    MAX_TYPED_QUERIES,
    _cap_typed_queries,
    _merge_query_results,
)
from openviking_cli.retrieve.types import (
    ContextType,
    MatchedContext,
    QueryResult,
    TypedQuery,
)


def _matched(uri, score, context_type=ContextType.MEMORY):
    return MatchedContext(uri=uri, context_type=context_type, score=score)


def _query_result(*contexts):
    return QueryResult(
        query=TypedQuery(query="q", context_type=None, intent=""),
        matched_contexts=list(contexts),
        searched_directories=[],
    )


def test_same_uri_across_queries_kept_once_with_best_score():
    merged = _merge_query_results(
        [
            _query_result(_matched("viking://a/m1", 0.6)),
            _query_result(_matched("viking://a/m1", 0.8)),
        ],
        limit=10,
    )
    memories = merged[0]
    assert [m.uri for m in memories] == ["viking://a/m1"]
    assert memories[0].score == 0.8


def test_tie_keeps_first_seen_occurrence():
    merged = _merge_query_results(
        [
            _query_result(_matched("viking://a/m1", 0.5, context_type=ContextType.RESOURCE)),
            _query_result(_matched("viking://a/m1", 0.5, context_type=ContextType.RESOURCE)),
        ],
        limit=10,
    )
    resources = merged[1]
    assert len(resources) == 1


def test_buckets_are_score_ordered_across_queries():
    # A later query's high-scoring hit must outrank an earlier query's
    # low-scoring hit; the merge may not preserve query-block order.
    merged = _merge_query_results(
        [
            _query_result(_matched("viking://a/low", 0.4)),
            _query_result(_matched("viking://a/high", 0.9)),
        ],
        limit=10,
    )
    assert [m.uri for m in merged[0]] == ["viking://a/high", "viking://a/low"]


def test_each_bucket_honors_limit_after_dedupe():
    # Two queries each returning four distinct memories; limit=3 must bound
    # the merged bucket regardless of how many queries contributed.
    merged = _merge_query_results(
        [
            _query_result(*[_matched("viking://a/m%d" % i, 0.9 - i * 0.1) for i in range(4)]),
            _query_result(*[_matched("viking://a/n%d" % i, 0.85 - i * 0.1) for i in range(4)]),
        ],
        limit=3,
    )
    memories = merged[0]
    assert len(memories) == 3
    scores = [m.score for m in memories]
    assert scores == sorted(scores, reverse=True)


def test_cross_bucket_independence():
    merged = _merge_query_results(
        [
            _query_result(
                _matched("viking://a/m1", 0.9, context_type=ContextType.MEMORY),
                _matched("viking://a/r1", 0.8, context_type=ContextType.RESOURCE),
                _matched("viking://a/s1", 0.7, context_type=ContextType.SKILL),
            ),
        ],
        limit=1,
    )
    memories, resources, skills = merged
    assert len(memories) == 1
    assert len(resources) == 1
    assert len(skills) == 1


def test_cap_typed_queries_bounds_fanout():
    queries = [
        TypedQuery(query="q%d" % i, context_type=None, intent="") for i in range(6)
    ]
    capped = _cap_typed_queries(queries)
    assert len(capped) == MAX_TYPED_QUERIES
    assert capped[0].query == "q0"


def test_cap_typed_queries_returns_input_within_bound():
    queries = [
        TypedQuery(query="q%d" % i, context_type=None, intent="") for i in range(3)
    ]
    assert _cap_typed_queries(queries) is queries
