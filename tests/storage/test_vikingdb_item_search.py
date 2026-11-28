# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from copy import deepcopy

import pytest

from openviking.storage.errors import VikingDBException
from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.vikingdb_collection import VikingDBCollection
from openviking.storage.vectordb_adapters.vikingdb_private_adapter import (
    VikingDBPrivateCollectionAdapter,
)


class RecordingClient:
    def __init__(self, result, status=200):
        self.result = result
        self.status_code = status
        self.text = "platform response"
        self.calls = []

    def do_req(self, method, path, *, req_body):
        self.calls.append((method, path, req_body))
        return self

    def json(self):
        return {"result": self.result}


@pytest.fixture
def collection():
    meta = {
        "ProjectName": "project",
        "CollectionName": "context",
        "ShardBy": "account_id",
        "Fields": [
            {"FieldName": "uri", "FieldType": "path"},
            {
                "FieldName": "item_uuids",
                "FieldType": "list<string>",
                "MountPathField": "uri",
                "InheritanceMode": "union",
            },
            {"FieldName": "item_attrs", "FieldType": "list<string>"},
        ],
    }
    coll = VikingDBCollection("https://unused.invalid", meta_data=meta)
    coll.client = RecordingClient({"groups": []})
    return coll


def group(item_id="a", score=1.7, attrs=None):
    return {
        "item_id": item_id,
        "item_score": score,
        "item_uri": f"/resources/{item_id}",
        "item_attrs": attrs,
        "found_records": [
            {"id": "member", "score": 0.9, "fields": {"uri": "/resources/a/b/hit.md"}}
        ],
    }


@pytest.mark.parametrize("aggregation", ["max", "sum"])
def test_private_adapter_uses_platform_groups_without_extra_io(collection, aggregation):
    typed_group = group()
    typed_group["found_records"][0]["fields"] = {"context_type": "skill"}
    typed_group.update(candidate_members_count=7, candidate_members_truncated=True)
    collection.client.result = {"groups": [typed_group, group("b", 0.8, ["kind=child"])]}
    adapter = VikingDBPrivateCollectionAdapter(
        host="https://unused.invalid",
        headers=None,
        project_name="project",
        collection_name="context",
        index_name="index",
    )
    adapter._collection = Collection(collection)
    result = adapter.search_item_groups(
        query_vector=[0.1, 0.2],
        sparse_query_vector={"1": 0.7},
        filter=And([Eq("account_id", "tenant-a"), PathScope("uri", "viking://resources/a")]),
        max_groups=2,
        aggregation=aggregation,
    )
    assert len(collection.client.calls) == 1
    method, path, request = collection.client.calls[0]
    assert (method, path) == ("POST", "/api/vikingdb/data/search/vector")
    assert request["partition"] == "tenant-a"
    assert "partition_by" not in request
    assert request["sparse_vector"] == {"1": 0.7}
    assert request["group_by"] == {
        "item_ids_field": "item_uuids",
        "item_attrs_field": "item_attrs",
        "op": aggregation,
        "group_limit": 2,
        "record_per_group_limit": 1,
        "found_records_output_fields": [],
    }
    assert "limit" not in request
    assert "udf" not in request
    assert request["filter"]["conds"][0] == {
        "op": "must",
        "field": "account_id",
        "conds": ["tenant-a"],
    }
    assert [(g["entity_id"], g["entity_uri"], g["entity_attrs"]) for g in result["groups"]] == [
        ("a", "viking://resources/a", None),
        ("b", "viking://resources/b", ["kind=child"]),
    ]
    assert all("context_type" not in g for g in result["groups"])
    assert all(g["candidate_members_uri"] == [] for g in result["groups"])
    assert all("candidate_members_count" not in g for g in result["groups"])
    assert all("candidate_members_truncated" not in g for g in result["groups"])


def search(collection, *, filters=None, **kwargs):
    if filters is None:
        filters = {"op": "must", "field": "account_id", "conds": ["a"]}
    return collection.search_item_groups(
        "index", dense_vector=[0.1, 0.2], filters=filters, **kwargs
    )


def test_item_attribute_filter_preserves_platform_groups_and_uses_one_request(collection):
    # Member filtering and root group metadata are separate platform concerns.
    collection.meta_data["Fields"].append({"FieldName": "level", "FieldType": "int64"})
    collection.client.result = {"groups": [group("a", 0.9, ["kind=root"])]}
    filters = {
        "op": "and",
        "conds": [
            {"op": "must", "field": "account_id", "conds": ["a"]},
            {
                "op": "or",
                "conds": [
                    {"op": "must", "field": "item_attrs", "conds": ["color=red", "lang=en"]},
                    {"op": "must_not", "field": "item_attrs", "conds": ["kind=archived"]},
                ],
            },
        ],
    }
    original = deepcopy(filters)
    result = search(collection, filters=filters, aggregation="sum")
    assert len(collection.client.calls) == 1
    _, path, request = collection.client.calls[0]
    assert path == "/api/vikingdb/data/search/vector"
    assert request["partition"] == "a"
    assert request["filter"] == original
    assert filters == original
    assert result["groups"] == [
        {
            "entity_id": "a",
            "entity_score": 0.9,
            "entity_uri": "/resources/a",
            "entity_attrs": ["kind=root"],
        }
    ]


def test_threshold_applies_to_aggregate_score_and_empty_groups_are_valid(collection):
    collection.client.result = {"groups": [group("a", 1.7), group("b", 0.8)]}
    result = search(collection, score_threshold=1.5)
    assert [g["entity_id"] for g in result["groups"]] == ["a"]
    assert result["groups"][0]["entity_score"] == 1.7
    collection.client.result = {"groups": []}
    assert search(collection) == {"groups": []}
    assert len(collection.client.calls) == 2


@pytest.mark.parametrize("case", ["shadow", "partition", "inherited_attrs"])
def test_unsupported_platform_combinations_fail_before_http(collection, case):
    if case == "shadow":
        collection.meta_data["Fields"][1]["InheritanceMode"] = "shadow"
    elif case == "partition":
        collection.meta_data.pop("ShardBy")
    elif case == "inherited_attrs":
        collection.meta_data["Fields"][2]["MountPathField"] = "uri"
    with pytest.raises(NotImplementedError):
        search(collection)
    assert collection.client.calls == []


@pytest.mark.parametrize(
    "filters",
    [
        {
            "op": "or",
            "conds": [
                {"op": "must", "field": "account_id", "conds": ["a"]},
                {"op": "must", "field": "account_id", "conds": ["b"]},
            ],
        },
        {"op": "must", "field": "account_id", "conds": ["a", "b"]},
        {"op": "and", "conds": []},
    ],
)
def test_item_partition_cannot_be_inferred_from_optional_or_multi_account_filters(
    collection, filters
):
    with pytest.raises(ValueError, match="unconditional account_id"):
        search(collection, filters=filters)
    assert collection.client.calls == []


@pytest.mark.parametrize(
    "result",
    [
        {"data": []},  # An old server silently ignoring group_by is not an empty Item result.
        {"groups": None},
        {"groups": [{**group(), "item_uri": None}]},
        {"groups": [{**group(), "item_score": float("nan")}]},
        {"groups": [{**group(), "item_attrs": {"kind": "unexpected-map"}}]},
        {"groups": [group(), group()]},
    ],
)
def test_malformed_groups_fail_without_fallback_requests(collection, result):
    collection.client.result = result
    with pytest.raises(VikingDBException, match="Invalid VikingDB Item group response"):
        search(collection)
    assert len(collection.client.calls) == 1


def test_platform_errors_keep_status_and_retryability(collection):
    collection.client.status_code = 429
    with pytest.raises(VikingDBException) as exc:
        search(collection)
    assert exc.value.status_code == 429
    assert exc.value.retryable is True
    assert len(collection.client.calls) == 1


def test_ordinary_search_never_checks_item_metadata_or_adds_grouping(collection):
    collection.meta_data = {}
    collection.client.result = {
        "data": [{"id": "normal", "score": 0.8, "fields": {"uri": "/resources/a"}}]
    }
    result = collection.search_by_vector("index", dense_vector=[0.1, 0.2], limit=3)
    assert result.data[0].id == "normal"
    assert len(collection.client.calls) == 1
    request = collection.client.calls[0][2]
    assert request["limit"] == 3
    assert not request.get("filter")
    assert "group_by" not in request
    assert "partition" not in request
    assert "partition_by" not in request


def test_keywords_use_native_groups_and_preserve_filters_and_args(collection):
    collection.client.result = {"groups": [group("a", 2.5, ["kind=root"])]}
    filters = {"op": "must", "field": "account_id", "conds": ["a"]}
    result = collection.search_item_groups(
        "index",
        keywords_query="fresh",
        filters=filters,
        aggregation="sum",
        keywords_args={"bm25_k1": 1.2, "bm25_b": 0.0, "post_process_input_limit": 32},
    )
    assert result["groups"][0]["entity_score"] == 2.5
    assert len(collection.client.calls) == 1
    _, path, request = collection.client.calls[0]
    assert path == "/api/vikingdb/data/search/keywords"
    assert request["query"] == "fresh"
    assert request["bm25_k1"] == 1.2
    assert request["bm25_b"] == 0.0
    assert request["advance"] == {"post_process_input_limit": 32}
    assert request["filter"] == filters
    assert request["group_by"]["op"] == "sum"
    assert "dense_vector" not in request


def test_scalar_groups_scan_past_repeated_members_and_use_own_roots(collection):
    from openviking.storage.vectordb.collection.vikingdb_catalog import _id

    collection.meta_data["Fields"].extend(
        {"FieldName": name, "FieldType": kind}
        for name, kind in (
            ("id", "string"),
            ("account_id", "string"),
            ("level", "int64"),
            ("type", "string"),
        )
    )
    filters = {"op": "must", "field": "account_id", "conds": ["a"]}
    parent = {
        "id": _id("a", "/resources/p"),
        "account_id": "a",
        "level": 0,
        "uri": "/resources/p",
        "item_uuids": ["p"],
        "item_attrs": ["kind=parent"],
    }
    child = {
        "id": _id("a", "/resources/p/c"),
        "account_id": "a",
        "level": 0,
        "uri": "/resources/p/c",
        "item_uuids": ["c"],
        "item_attrs": ["kind=child"],
    }
    calls = []

    def post(path, request):
        calls.append((path, deepcopy(request)))
        if path.endswith("fetch_in_collection"):
            rows = [r for r in (parent, child) if r["id"] in request["ids"]]
            return {"fetch": [{"id": r["id"], "fields": r} for r in rows]}
        assert path.endswith("search/scalar")
        assert request["filter"] == filters
        assert "group_by" not in request and "dense_vector" not in request
        if request["offset"] == 0:
            return {"data": [{"fields": {"uri": "/resources/p/hit.md", "item_uuids": ["p"]}}] * 100}
        assert request["offset"] == 100
        return {"data": [{"fields": {"uri": "/resources/p/c/hit.md", "item_uuids": ["p", "c"]}}]}

    collection._data_post = post
    result = collection.search_item_groups(
        "index", filters=filters, filter_only=True, max_groups=2, aggregation="sum"
    )
    assert [(g["entity_id"], g["entity_score"], g["entity_attrs"]) for g in result["groups"]] == [
        ("c", 0.0, ["kind=child"]),
        ("p", 0.0, ["kind=parent"]),
    ]
    assert len([c for c in calls if c[0].endswith("search/scalar")]) == 2


def test_scalar_empty_results_and_threshold(collection):
    collection.client.result = {"data": []}
    assert search(collection, filter_only=True) == {"groups": []}
    request = collection.client.calls[0][2]
    assert "group_by" not in request and "dense_vector" not in request
