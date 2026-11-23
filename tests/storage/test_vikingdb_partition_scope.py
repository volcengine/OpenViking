# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace

import pytest

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.vikingdb_collection import VikingDBCollection
from openviking.storage.vectordb.collection.volcengine_clients import ClientForDataApiWithApiKey
from openviking.storage.vectordb_adapters import create_collection_adapter
from openviking.storage.viking_vector_index_backend import _SingleAccountBackend
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


class Client:
    def __init__(self):
        self.calls = []

    def do_req(self, method, path, *, req_body):
        self.calls.append((path, req_body))
        partition_key = "partition" if "/search/" in path else "partition_by"
        scope = req_body[partition_key]
        result = {"fetch": [{"id": scope, "fields": {"account_id": scope, "uri": "/resources/a"}}]}
        return SimpleNamespace(status_code=200, json=lambda: {"result": result})


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["vikingdb", "volcengine"])
async def test_shared_adapter_passes_partition_per_point_request_without_mutable_binding(
    backend, monkeypatch
):
    client = Client()
    config = VectorDBBackendConfig(
        backend=backend,
        dimension=2,
        vikingdb={"host": "https://unused.invalid"},
        volcengine={"host": "https://unused.invalid", "api_key": "test-only"},
    )
    adapter = create_collection_adapter(config)
    if backend == "volcengine":
        monkeypatch.setattr(ClientForDataApiWithApiKey, "do_req", client.do_req)
    else:
        collection = VikingDBCollection(
            "https://unused.invalid",
            meta_data={"CollectionName": "context", "ShardBy": "account_id"},
        )
        collection.client = client
        adapter._collection = Collection(collection)
    a = _SingleAccountBackend(config, "a", shared_adapter=adapter)
    b = _SingleAccountBackend(config, "b", shared_adapter=adapter)
    results = await asyncio.gather(a.get_strict(["a", "b"]), b.get_strict(["a", "b"]))
    assert [[r["id"] for r in rows] for rows in results] == [["a"], ["b"]]
    assert len(client.calls) == 2
    assert {body["partition_by"] for _, body in client.calls} == {"a", "b"}
    client.calls.clear()
    assert await a.strict_delete(["a"]) == 1
    assert [path.rsplit("/", 1)[-1] for path, _ in client.calls] == [
        "fetch_in_collection",
        "delete",
    ]
    assert all(body["partition_by"] == "a" for _, body in client.calls)


def test_partitioned_point_operations_without_scope_fail_before_io():
    collection = VikingDBCollection(
        "https://unused.invalid",
        meta_data={"CollectionName": "context", "ShardBy": "account_id"},
    )
    collection.client = Client()
    for operation in (lambda: collection.fetch_data(["a"]), lambda: collection.delete_data(["a"])):
        with pytest.raises(ValueError, match="require partition_by"):
            operation()
    assert collection.client.calls == []


@pytest.mark.parametrize("shard_key", ["ShardBy", "PartitionBy"])
def test_ordinary_partitioned_search_adds_routing_without_an_extra_request(shard_key):
    collection = VikingDBCollection(
        "https://unused.invalid",
        meta_data={"CollectionName": "context", shard_key: "account_id"},
    )
    collection.client = Client()
    collection.search_by_vector(
        "index", dense_vector=[1, 0], filters={"op": "must", "field": "account_id", "conds": ["a"]}
    )
    assert len(collection.client.calls) == 1
    request = collection.client.calls[0][1]
    assert request["partition"] == "a"
    assert "partition_by" not in request
    assert "group_by" not in request
