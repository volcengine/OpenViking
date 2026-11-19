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


def search(collection, *, filters=None, **kwargs):
    if filters is None:
        filters = {"op": "must", "field": "account_id", "conds": ["a"]}
    return collection.search_item_groups(
        "index", dense_vector=[0.1, 0.2], filters=filters, **kwargs
    )


def test_threshold_applies_to_aggregate_score_and_empty_groups_are_valid(collection):
    collection.client.result = {"groups": [group("a", 1.7), group("b", 0.8)]}
    result = search(collection, score_threshold=1.5)
    assert [g["entity_id"] for g in result["groups"]] == ["a"]
    assert result["groups"][0]["entity_score"] == 1.7
    collection.client.result = {"groups": []}
    assert search(collection) == {"groups": []}
    assert len(collection.client.calls) == 2


@pytest.mark.parametrize("case", ["shadow", "partition", "inherited_attrs", "filter_only"])
def test_unsupported_platform_combinations_fail_before_http(collection, case):
    kwargs = {}
    if case == "shadow":
        collection.meta_data["Fields"][1]["InheritanceMode"] = "shadow"
    elif case == "partition":
        collection.meta_data.pop("ShardBy")
    elif case == "inherited_attrs":
        collection.meta_data["Fields"][2]["MountPathField"] = "uri"
    elif case == "filter_only":
        kwargs["filter_only"] = True
    with pytest.raises(NotImplementedError):
        search(collection, **kwargs)
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
    assert "group_by" not in request
    assert "partition" not in request
    assert "partition_by" not in request


def test_unused_member_details_are_not_read_back_or_exposed(collection):
    row = group()
    row.update(candidate_members_count=7, candidate_members_truncated=True)
    collection.client.result = {"groups": [deepcopy(row)]}
    result = search(collection)
    assert "candidate_members_count" not in result["groups"][0]
    assert "candidate_members_truncated" not in result["groups"][0]
    assert len(collection.client.calls) == 1
