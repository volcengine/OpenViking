# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from copy import deepcopy
from types import SimpleNamespace

import pytest

from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import And, Eq
from openviking.storage.vectordb.collection.volcengine_api_key_collection import (
    VolcengineApiKeyCollection,
)
from openviking.storage.vectordb_adapters.volcengine_adapter import VolcengineCollectionAdapter
from openviking.storage.viking_vector_index_backend import _SingleAccountBackend
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


@pytest.fixture
def schema():
    meta = CollectionSchemas.context_collection("context", 2)
    meta.update(ProjectName="project", ShardBy="account_id")
    meta["Fields"].extend(
        [
            {
                "FieldName": "item_uuids",
                "FieldType": "list<string>",
                "MountPathField": "uri",
                "InheritanceMode": "union",
            },
            {"FieldName": "item_attrs", "FieldType": "list<string>"},
        ]
    )
    return meta


class Client:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def do_req(self, method, path, *, req_body):
        self.calls.append((method, path, deepcopy(req_body)))
        result = self.result
        if path.endswith("fetch_in_collection"):
            account = req_body["partition_by"]
            result = {
                "fetch": [{"id": account, "fields": {"account_id": account, "uri": "/resources/a"}}]
            }
        return SimpleNamespace(status_code=200, json=lambda: {"result": result})


def adapter_for(schema):
    config = VectorDBBackendConfig(
        backend="volcengine",
        name="context",
        project="project",
        dimension=2,
        volcengine={"host": "unused.invalid", "api_key": "test-key", "collection_meta": schema},
    )
    adapter = VolcengineCollectionAdapter.from_config(config)
    return config, adapter, adapter.get_collection()._Collection__collection


@pytest.mark.parametrize(("provide_metadata", "aggregation"), [(True, "max"), (False, "sum")])
def test_api_key_adapter_uses_native_group_metadata_and_passes_attrs_filter_once(
    schema, aggregation, provide_metadata
):
    _, adapter, collection = adapter_for(schema if provide_metadata else None)
    collection.data_client = Client(
        {
            "groups": [
                {
                    "item_id": "blue",
                    "item_uri": "/resources/blue",
                    "item_attrs": ["kind=parent"],
                    "item_score": 0.8,
                },
                {
                    "item_id": "red",
                    "item_uri": "/resources/red",
                    "item_attrs": [],
                    "item_score": 0.9,
                },
            ]
        }
    )
    predicate = {"op": "must", "field": "item_attrs", "conds": ["color=red"]}
    result = adapter.search_item_groups(
        query_vector=[1.0, 0.0],
        filter=And([Eq("account_id", "a"), Eq("item_attrs", "color=red")]),
        max_groups=2,
        aggregation=aggregation,
    )
    assert len(collection.data_client.calls) == 1
    _, path, body = collection.data_client.calls[0]
    assert path == "/api/vikingdb/data/search/vector"
    assert body["partition"] == "a"
    assert body["group_by"]["op"] == aggregation
    assert body["group_by"]["group_limit"] == 2
    assert predicate in body["filter"]["conds"]
    assert [g["entity_id"] for g in result["groups"]] == ["blue", "red"]
    assert result["groups"][0]["entity_attrs"] == ["kind=parent"]
    assert result["groups"][0]["entity_uri"] == "viking://resources/blue"


@pytest.mark.asyncio
@pytest.mark.parametrize("provide_metadata", [True, False])
async def test_api_key_adapter_passes_each_accounts_point_scope(schema, provide_metadata):
    config, adapter, collection = adapter_for(schema if provide_metadata else None)
    collection.data_client = Client({})
    a = _SingleAccountBackend(config, "a", shared_adapter=adapter)
    b = _SingleAccountBackend(config, "b", shared_adapter=adapter)
    assert [r["id"] for r in await a.get_strict(["a", "b"])] == ["a"]
    assert [r["id"] for r in await b.get_strict(["a", "b"])] == ["b"]
    assert [body["partition_by"] for _, _, body in collection.data_client.calls] == ["a", "b"]
    await a.strict_delete(["a"])
    assert collection.data_client.calls[-1][1].endswith("/delete")
    assert collection.data_client.calls[-1][2]["partition_by"] == "a"
    adapter.update_data(
        [{"id": "a", "account_id": "a", "uri": "viking://resources/a"}], partition_by="a"
    )
    assert collection.data_client.calls[-1][2]["partition_by"] == "a"
    assert "account_id" not in collection.data_client.calls[-1][2]["data"][0]
    with pytest.raises(ValueError, match="cannot change"):
        adapter.update_data([{"id": "a", "account_id": "b"}], partition_by="a")


def test_api_key_schema_snapshot_is_preserved_and_not_mutated(schema):
    _, adapter, collection = adapter_for(schema)
    assert collection.get_meta_data()["ShardBy"] == "account_id"
    schema["Fields"].clear()
    result = collection.get_meta_data()
    assert any(f.get("InheritanceMode") == "union" for f in result["Fields"])
    result["Fields"].clear()
    assert collection.get_meta_data()["Fields"]
    assert adapter.supports_partition_scope


def test_api_key_without_metadata_forwards_item_request_and_keeps_unscoped_ordinary_query():
    collection = VolcengineApiKeyCollection(
        "test-key", host="unused.invalid", meta_data={"CollectionName": "context"}
    )
    collection.data_client = Client({"data": []})
    collection.search_by_vector("default", dense_vector=[1.0, 0.0])
    body = collection.data_client.calls[0][2]
    assert not body.get("filter")
    assert "partition" not in body and "group_by" not in body
    condition = {"op": "must", "field": "account_id", "conds": ["a"]}
    collection.data_client.result = {"groups": []}
    assert collection.search_item_groups("default", dense_vector=[1.0, 0.0], filters=condition) == {
        "groups": []
    }
    assert len(collection.data_client.calls) == 2
    body = collection.data_client.calls[-1][2]
    assert body["partition"] == "a" and body["group_by"]["item_ids_field"] == "item_uuids"
    assert body["filter"] == condition


def test_api_key_metadata_for_another_collection_fails_before_io(schema):
    schema["CollectionName"] = "other"
    with pytest.raises(ValueError, match="does not match"):
        adapter_for(schema)
