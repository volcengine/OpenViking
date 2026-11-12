# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import SearchItemResult, SearchResult
from openviking.storage.vectordb_adapters.vikingdb_private_adapter import (
    VikingDBPrivateCollectionAdapter,
)
from openviking.storage.vectordb_adapters.volcengine_adapter import VolcengineCollectionAdapter

# Synthetic epoch near a float32 rounding boundary: an 18s age can become 131072ms.
UPDATED = datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc)
WEEK_MS = 7 * 24 * 60 * 60 * 1000


@pytest.fixture(params=["vikingdb", "volcengine"])
def remote(request):
    common = {"project_name": "default", "collection_name": "context", "index_name": "default"}
    if request.param == "vikingdb":
        adapter = VikingDBPrivateCollectionAdapter(host="example.invalid", headers=None, **common)
    else:
        adapter = VolcengineCollectionAdapter(
            ak="test-ak",
            sk="test-sk",
            region="cn-beijing",
            session_token=None,
            api_key=None,
            host=None,
            **common,
        )
    collection = Mock(spec=Collection)
    adapter._collection = collection

    def set_rows(rows):
        def search(**kwargs):
            selected = rows[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]]
            fields = kwargs["output_fields"]
            return SearchResult(
                [
                    SearchItemResult(
                        id=row.id,
                        score=row.score,
                        fields={k: v for k, v in row.fields.items() if not fields or k in fields},
                    )
                    for row in selected
                ]
            )

        collection.search_by_vector.side_effect = search

    return adapter, collection, set_rows


def event(id="event", score=0.8, updated_at=UPDATED.isoformat()):
    return SearchItemResult(id=id, score=score, fields={"uri": f"/{id}", "updated_at": updated_at})


def query(adapter, *, protection="1m", age_ms=18_000, **kwargs):
    return adapter.query(
        advance={
            "time_decay": {
                "protection": protection,
                "origin": (UPDATED + timedelta(milliseconds=age_ms)).isoformat(),
            }
        },
        **({"query_vector": [1.0]} | kwargs),
    )


@pytest.mark.parametrize(
    "protection,age_ms",
    [
        ("1m", 18_000),
        ("1m", 40_000),
        ("2m", 18_000),
        ("2m", 40_000),
        ("7d", 40_000),
        ("1m", 59_999),
        ("1m", 60_000),
        ("2m", 120_000),
        ("1m", -18_000),
        ("1m", -60_000),
    ],
)
def test_protected_events_keep_exact_original_score(remote, protection, age_ms):
    adapter, collection, set_rows = remote
    set_rows([event()])
    result = query(adapter, protection=protection, age_ms=age_ms, limit=1)
    assert result[0]["_time_score"] == 1.0
    assert result[0]["_score"] == result[0]["_origin_score"] == event().score
    call = collection.search_by_vector.call_args.kwargs
    assert (call["limit"], call["offset"]) == (3, 0)
    assert call["advance"] is None
    assert call["return_detail_info"] is False


@pytest.mark.parametrize(
    "protection,age_ms,excess_ms",
    [
        ("1m", 60_001, 1),
        ("2m", 120_001, 1),
        ("1m", -60_001, 1),
        ("0", 18_000, 18_000),
        ("1m", WEEK_MS + 60_000, WEEK_MS),
    ],
)
def test_decay_uses_precise_distance_beyond_protection(remote, protection, age_ms, excess_ms):
    adapter, _, set_rows = remote
    set_rows([event()])
    result = query(adapter, protection=protection, age_ms=age_ms, limit=1)[0]
    expected = math.exp(math.log(0.5) * excess_ms / WEEK_MS)
    assert result["_time_score"] < 1.0
    assert result["_time_score"] == pytest.approx(expected, rel=1e-12)
    assert result["_score"] == pytest.approx(event().score * expected, rel=1e-12)


@pytest.mark.parametrize("output_fields", [None, [], ["uri"], ["uri", "updated_at"]])
def test_fusion_precedes_pagination_and_preserves_scope_and_projection(remote, output_fields):
    adapter, collection, set_rows = remote
    set_rows(
        [
            event("old", 0.95, (UPDATED - timedelta(days=14)).isoformat()),
            event("recent-a", 0.8),
            event("recent-b", 0.7),
            event("recent-c", 0.6),
        ]
    )
    original_fields = None if output_fields is None else list(output_fields)
    scope = And(
        [
            Eq("account_id", "acct"),
            Eq("search_tags", "memory_type=events"),
            PathScope("uri", "viking://user/alice/memories", depth=-1),
        ]
    )
    results = query(adapter, filter=scope, limit=2, offset=1, output_fields=output_fields)
    assert [row["id"] for row in results] == ["recent-b", "recent-c"]
    assert all(row["_score"] == row["_origin_score"] for row in results)
    assert all(
        ("updated_at" in row) == (not output_fields or "updated_at" in output_fields)
        for row in results
    )
    assert output_fields == original_fields
    call = collection.search_by_vector.call_args.kwargs
    assert (call["limit"], call["offset"]) == (9, 0)
    assert call["filters"] == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "account_id", "conds": ["acct"]},
            {"op": "must", "field": "search_tags", "conds": ["memory_type=events"]},
            {"op": "must", "field": "uri", "conds": ["/user/alice/memories"], "para": "-d=-1"},
        ],
    }
    assert call["output_fields"] == (
        ["uri", "updated_at"] if output_fields == ["uri"] else output_fields
    )


@pytest.mark.parametrize("timestamp", [None, "invalid", True, float("nan"), float("inf")])
def test_invalid_or_missing_timestamps_keep_original_score(remote, timestamp):
    adapter, _, set_rows = remote
    row = event(updated_at=timestamp)
    if timestamp is None:
        row.fields.pop("updated_at")
    set_rows([row])
    result = query(adapter, limit=1)[0]
    assert result["_score"] == result["_origin_score"] == row.score
    assert "_time_score" not in result


@pytest.mark.parametrize(
    "timestamp",
    [
        UPDATED.timestamp() * 1000,
        UPDATED,
        UPDATED.replace(tzinfo=None),
        UPDATED.astimezone(timezone(timedelta(hours=8))).isoformat(),
    ],
)
def test_indexed_timestamp_representations_share_the_same_protection(remote, timestamp):
    adapter, _, set_rows = remote
    set_rows([event(updated_at=timestamp)])
    assert query(adapter, limit=1)[0]["_time_score"] == 1.0


def test_sparse_only_queries_use_the_same_fusion(remote):
    adapter, collection, set_rows = remote
    set_rows([event()])
    assert (
        query(adapter, query_vector=None, sparse_query_vector={"event": 1.0}, limit=1)[0][
            "_time_score"
        ]
        == 1.0
    )
    assert collection.search_by_vector.call_args.kwargs["sparse_vector"] == {"event": 1.0}


@pytest.mark.parametrize(
    "limit,offset,budget", [(10, 0, 30), (40_000, 0, 100_000), (1, 99_999, 100_000)]
)
def test_candidate_budget_is_bounded(remote, limit, offset, budget):
    adapter, collection, set_rows = remote
    set_rows([])
    assert query(adapter, limit=limit, offset=offset) == []
    assert collection.search_by_vector.call_args.kwargs["limit"] == budget


@pytest.mark.parametrize("limit,offset", [(0, 0), (1, -1), (100_001, 0), (1, 100_000)])
def test_invalid_remote_decay_windows_fail_before_query(remote, limit, offset):
    adapter, collection, _ = remote
    with pytest.raises(ValueError, match="limit|offset"):
        query(adapter, limit=limit, offset=offset)
    collection.search_by_vector.assert_not_called()


def test_queries_without_decay_keep_backend_pagination(remote):
    adapter, collection, set_rows = remote
    set_rows([event("first"), event("second")])
    result = adapter.query(query_vector=[1.0], limit=1, offset=1, output_fields=["uri"])
    assert result == [{"id": "second", "uri": "viking://second", "_score": event().score}]
    call = collection.search_by_vector.call_args.kwargs
    assert (call["limit"], call["offset"], call["output_fields"]) == (1, 1, ["uri"])
