# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from copy import deepcopy

import pytest

from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.vector_ids import vector_record_id
from openviking.storage.vectordb.collection.vikingdb_collection import VikingDBCollection
from openviking.storage.vectordb.collection.volcengine_api_key_collection import (
    VolcengineApiKeyCollection,
)


def matches(row, condition):
    op = condition["op"]
    if op == "and":
        return all(matches(row, c) for c in condition["conds"])
    if op == "or":
        return any(matches(row, c) for c in condition["conds"])
    values = condition["conds"]
    field = condition["field"]
    actual = row.get(field)
    if field == "uri" and condition.get("para") == "-d=-1":
        result = any(actual == path or actual.startswith(path.rstrip("/") + "/") for path in values)
    else:
        result = any(v in actual for v in values) if isinstance(actual, list) else actual in values
    return not result if op == "must_not" else result


class Response:
    def __init__(self, result, status=200):
        self.status_code = status
        self.text = "test platform response"
        self.result = result

    def json(self):
        return {"result": self.result}


class RecordAPI:
    """Record-level API fake, not a substitute for an inheritance engine."""

    def __init__(self):
        self.rows = {}
        self.calls = []

    def do_req(self, method, path, *, req_body):
        data = deepcopy(req_body)
        self.calls.append((method, path, data))
        scope = data["partition"] if "/search/" in path else data.get("partition_by")
        if path.endswith("/vector") and "group_by" in data:
            return Response({"groups": []})
        if path.endswith("/upsert"):
            for row in data["data"]:
                self.rows[row["id"]] = row
            return Response({})
        if path.endswith("/delete"):
            for record_id in data["ids"]:
                row = self.rows.get(record_id)
                assert row is None or row["account_id"] == scope
                self.rows.pop(record_id, None)
            return Response({})
        if path.endswith("/fetch_in_collection"):
            rows = [self.rows[i] for i in data["ids"] if i in self.rows]
            assert all(r["account_id"] == scope for r in rows)
            selected = data.get("output_fields")
            return Response(
                {
                    "fetch": [
                        {
                            "id": r["id"],
                            "fields": {
                                k: v
                                for k, v in deepcopy(r).items()
                                if selected is None or k in selected
                            },
                        }
                        for r in rows
                    ]
                }
            )
        if "/search/" in path or path.endswith("/agg"):
            rows = sorted(self.rows.values(), key=lambda r: r["id"])
            if scope is not None:
                rows = [r for r in rows if r["account_id"] == scope]
            rows = [r for r in rows if matches(r, data["filter"])]
            if path.endswith("/agg"):
                return Response({"agg": {"_total": len(rows)}})
            offset = data.get("offset", 0)
            rows = rows[offset : offset + data.get("limit", 10)]
            selected = data.get("output_fields")
            return Response(
                {
                    "data": [
                        {
                            "id": r["id"],
                            "score": 0.0,
                            "fields": {
                                k: v for k, v in r.items() if selected is None or k in selected
                            },
                        }
                        for r in rows
                    ]
                }
            )
        raise AssertionError(path)


@pytest.fixture(params=["private", "api_key"])
def collection(request):
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
    api = RecordAPI()
    if request.param == "api_key":
        meta["IndexName"] = "catalog-index"
        coll = VolcengineApiKeyCollection("test-key", host="unused.invalid", meta_data=meta)
        coll.data_client = api
    else:
        coll = VikingDBCollection("https://unused.invalid", meta_data=meta)
    coll.client = api
    return coll


def call(collection, op, scope="a", **kwargs):
    return collection.catalog_execute({"op": op, "scope": scope, **kwargs})


def declare(collection, uri, item_uuid, *, scope="a", **kwargs):
    result = call(collection, "declare_item", scope, uri=uri, item_uuids=[item_uuid], **kwargs)
    if result.pop("index_with_root"):
        collection.upsert_data(
            [
                {
                    "id": vector_record_id(scope, "viking://" + uri.lstrip("/"), 0),
                    "account_id": scope,
                    "uri": uri,
                    "level": 0,
                    "vector": [1, 0],
                    "item_uuids": [result["item_uuid"]],
                    "item_attrs": result["item_attrs"],
                }
            ]
        )
    return result


def test_declaration_is_prepared_for_normal_root_and_keeps_uuid_on_retry(collection):
    uri = "/resources/A"
    prepared = call(collection, "declare_item", uri=uri, item_uuids=["uuid-a"])
    assert prepared == {
        "uri": uri,
        "item_uuid": "uuid-a",
        "item_attrs": [],
        "index_with_root": True,
    }
    assert collection.client.rows == {}
    declare(collection, uri, "uuid-a", item_attrs=["kind=case"])
    assert declare(collection, uri, "unused-retry-uuid")["item_uuid"] == "uuid-a"
    with pytest.raises(ValueError, match="cannot change"):
        declare(collection, uri, "ignored", item_attrs=[])
    root_id = vector_record_id("a", "viking://resources/A", 0)
    assert list(collection.client.rows) == [root_id]
    account_filter = {"op": "must", "field": "account_id", "conds": ["a"]}
    result = collection.search_by_vector(
        "catalog-index", dense_vector=[1, 0], filters=account_filter
    )
    assert [row.id for row in result.data] == [root_id]
    assert collection.aggregate_data("catalog-index", filters=account_filter).agg == {"_total": 1}
    assert collection.client.calls[-1][2]["filter"] == account_filter


@pytest.mark.parametrize("mode,expected", [("union", ["child", "parent"]), ("shadow", ["child"])])
def test_management_resolves_account_scoped_declarations_without_copying_attrs(
    collection, mode, expected
):
    collection.meta_data["Fields"][-2]["InheritanceMode"] = mode
    declare(collection, "/resources/A", "parent", item_attrs=["kind=parent"])
    declare(collection, "/resources/A/B", "child", item_attrs=["kind=child"])
    declare(collection, "/resources/A", "tenant-b", scope="b")
    result = call(collection, "resolve", uris=["/resources/A/B/hit.md"])["data"][0]
    assert result["item_uuids"] == expected
    assert result["item_root_uri"] == "/resources/A/B"
    assert "item_attrs" not in result
    assert call(collection, "resolve", "b", uris=["/resources/A/B/hit.md"])["data"][0][
        "item_uuids"
    ] == ["tenant-b"]
    assert declare(collection, "/resources/A", "_read")["item_attrs"] == ["kind=parent"]
    assert declare(collection, "/resources/A/B", "_read")["item_attrs"] == ["kind=child"]
    for _, path, request in collection.client.calls:
        if path.endswith("/upsert"):
            assert all(r["account_id"] in ("a", "b") for r in request["data"])
        elif path.endswith("/scalar"):
            assert request["partition"] in ("a", "b")
            assert "partition_by" not in request
            assert request["index_name"] == "catalog-index"
        else:
            assert request["partition_by"] in ("a", "b")


def test_resource_replacement_refuses_to_remove_child_items(collection):
    root = "/resources/A"
    child = root + "/child"
    declare(collection, root, "parent")
    declare(collection, child, "child")
    with pytest.raises(ValueError, match="independent child Items"):
        call(collection, "validate_resource_update", uri=root, uris=[child])


def test_collection_configuration_is_validated_without_metadata_http_calls(collection):
    caps = call(collection, "capabilities")
    assert caps["enabled"] is True
    assert caps["item_identity_mode"] == "union"
    assert caps["preserve_item_root"] is True
    assert collection.client.calls == []
    with pytest.raises(NotImplementedError, match="fixed"):
        call(collection, "configure", item_identity_mode="shadow")
    assert collection.client.calls == []


@pytest.mark.asyncio
async def test_account_cleanup_deletes_item_roots_with_regular_records(
    collection, vector_backend_factory
):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.vectordb.collection.collection import Collection
    from openviking.storage.vectordb_adapters.vikingdb_private_adapter import (
        VikingDBPrivateCollectionAdapter,
    )
    from openviking.storage.viking_vector_index_backend import _SingleAccountBackend
    from openviking_cli.session.user_id import UserIdentifier
    from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

    declare(collection, "/resources/A", "a-root")
    declare(collection, "/resources/A/child", "a-child")
    declare(collection, "/resources/A", "b-root", scope="b")
    adapter = VikingDBPrivateCollectionAdapter(
        host="https://unused.invalid",
        headers=None,
        project_name="project",
        collection_name="context",
        index_name="catalog-index",
    )
    adapter._collection = Collection(collection)
    config = VectorDBBackendConfig(
        backend="vikingdb", dimension=2, vikingdb={"host": "https://unused.invalid"}
    )
    vectors = vector_backend_factory(config)
    vectors._resolved_backends["a"] = _SingleAccountBackend(config, "a", shared_adapter=adapter)
    ctx = RequestContext(user=UserIdentifier("operator", "root"), role=Role.ROOT)
    await vectors.delete_account_data("a", ctx=ctx)
    assert call(collection, "resolve", uris=["/resources/A/child"])["data"][0]["item_uuids"] == []
    assert (
        call(collection, "resolve", "b", uris=["/resources/A"])["data"][0]["item_uuid"] == "b-root"
    )
    assert {row["account_id"] for row in collection.client.rows.values()} == {"b"}
