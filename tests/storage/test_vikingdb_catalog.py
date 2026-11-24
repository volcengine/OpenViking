# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.errors import VikingDBException
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
        self.fail_delete_once = False
        self.stale_index = False

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
            if self.fail_delete_once:
                self.fail_delete_once = False
                return Response({}, status=503)
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
            if self.stale_index:
                rows = []
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
        coll = VikingDBCollection(
            "https://unused.invalid", meta_data=meta, index_name="catalog-index"
        )
    coll.client = api
    return coll


def call(collection, op, scope="a", **kwargs):
    return collection.catalog_execute({"op": op, "scope": scope, **kwargs})


def declare(collection, uri, item_uuid, *, scope="a", **kwargs):
    call(collection, "upsert_nodes", scope, nodes=[{"uri": uri, "node_type": "dir"}])
    return call(collection, "declare_item", scope, uri=uri, item_uuids=[item_uuid], **kwargs)


def test_declarations_are_stable_and_separate_from_semantic_upserts(collection):
    first = declare(collection, "/resources/A", "uuid-a", item_attrs=["kind=case"])
    retry = declare(collection, "/resources/A", "unused-retry-uuid")
    assert retry == first
    assert first == {"uri": "/resources/A", "item_uuid": "uuid-a", "item_attrs": ["kind=case"]}
    with pytest.raises(ValueError, match="cannot change"):
        declare(collection, "/resources/A", "ignored", item_attrs=[])
    before = deepcopy(collection.client.rows)
    calls = len(collection.client.calls)
    record_id = vector_record_id("a", "viking://resources/A", 0)
    collection.upsert_data(
        [{"id": record_id, "account_id": "a", "uri": "/resources/A", "level": 0, "vector": [1, 0]}]
    )
    assert len(collection.client.calls) == calls + 1
    assert all(collection.client.rows[k] == v for k, v in before.items())
    account_filter = {"op": "must", "field": "account_id", "conds": ["a"]}
    result = collection.search_by_vector(
        "catalog-index", dense_vector=[1, 0], filters=account_filter
    )
    assert [r.id for r in result.data] == [record_id]
    assert collection.aggregate_data("catalog-index", filters=account_filter).agg == {"_total": 1}
    assert collection.client.calls[-1][2]["partition"] == "a"


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


def test_preserve_root_rejects_independent_children_before_deletion(collection):
    declare(collection, "/resources/A", "parent", item_attrs=[])
    call(collection, "delete_subtree", uri="/resources/A", preserve_item_root=True)
    assert call(collection, "resolve", uris=["/resources/A"])["data"][0]["item_uuid"] == "parent"
    declare(collection, "/resources/A/child", "child")
    before = deepcopy(collection.client.rows)
    for op in ("validate_resource_update", "delete_subtree"):
        with pytest.raises(ValueError, match="independent child Items"):
            call(collection, op, uri="/resources/A", preserve_item_root=True)
    assert collection.client.rows == before
    call(collection, "delete_subtree", uri="/resources/A")
    assert collection.client.rows == {}


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("source_is_item", [False, True])
def test_move_and_rollback_preserve_source_and_preexisting_target(collection, fail, source_is_item):
    if source_is_item:
        declare(collection, "/resources/A", "source", item_attrs=["kind=source"])
    else:
        call(collection, "upsert_nodes", nodes=[{"uri": "/resources/A", "node_type": "dir"}])
    declare(collection, "/resources/A/child", "child")
    declare(collection, "/resources/B", "old-target", item_attrs=["kind=target"])
    declare(collection, "/resources/B/kept", "target-only", item_attrs=["kind=kept"])
    declare(collection, "/resources/A", "other-account", scope="b")
    before = deepcopy(collection.client.rows)
    moved_uuid = "source" if source_is_item else "old-target"
    moved_attrs = ["kind=source"] if source_is_item else ["kind=target"]
    before_move = len(collection.client.calls)
    collection.client.fail_delete_once = fail
    if fail:
        with pytest.raises(VikingDBException):
            call(collection, "move_subtree", from_uri="/resources/A", to_uri="/resources/B")
    else:
        moved = call(collection, "move_subtree", from_uri="/resources/A", to_uri="/resources/B")
        assert moved["moved_nodes"] == 2
        reads = collection.client.calls[before_move:]
        assert [path.rsplit("/", 1)[-1] for _, path, _ in reads[:3]] == [
            "scalar",
            "fetch_in_collection",
            "upsert",
        ]
        assert len(reads[1][2]["ids"]) == 4
        assert call(collection, "resolve", uris=["/resources/B/child/hit"])["data"][0][
            "item_uuids"
        ] == ["child", moved_uuid]
        assert call(collection, "resolve", uris=["/resources/B/kept/hit"])["data"][0][
            "item_uuids"
        ] == ["target-only", moved_uuid]
        moved_roots = call(collection, "resolve", uris=["/resources/B", "/resources/B/kept"])[
            "data"
        ]
        assert [(r["item_uuid"], r["item_root_uri"]) for r in moved_roots] == [
            (moved_uuid, "/resources/B"),
            ("target-only", "/resources/B/kept"),
        ]
        assert declare(collection, "/resources/B", "_read")["item_attrs"] == moved_attrs
        assert declare(collection, "/resources/B/kept", "_read")["item_attrs"] == ["kind=kept"]
        with pytest.raises(ValueError, match="different account"):
            call(collection, "restore_move", "b", undo=moved["undo"])
        call(collection, "restore_move", undo=moved["undo"])
    assert collection.client.rows == before


def test_retry_after_index_visibility_timeout_keeps_committed_uuid(collection, monkeypatch):
    import openviking.storage.vectordb.collection.vikingdb_catalog as catalog

    monkeypatch.setattr(catalog, "_INDEX_WAIT_SECONDS", 0)
    collection.client.stale_index = True
    with pytest.raises(VikingDBException) as exc:
        declare(collection, "/resources/A", "committed", item_attrs=["kind=case"])
    assert exc.value.retryable is True
    assert exc.value.status_code == 503
    collection.client.stale_index = False
    retry = declare(collection, "/resources/A", "new-retry-id")
    assert retry["item_uuid"] == "committed"
    assert retry["item_attrs"] == ["kind=case"]


def test_index_visibility_wait_does_not_block_another_account(collection, monkeypatch):
    declare(collection, "/resources/B", "b-item", scope="b")
    entered = threading.Event()
    release = threading.Event()
    original = collection._catalog._wait_visible

    def wait_for_index(scope, expected):
        if scope == "a":
            entered.set()
            assert release.wait(5)
        return original(scope, expected)

    monkeypatch.setattr(collection._catalog, "_wait_visible", wait_for_index)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(declare, collection, "/resources/A", "a-item")
        try:
            assert entered.wait(5)
            second = pool.submit(call, collection, "resolve", "b", uris=["/resources/B/file"])
            assert second.result(timeout=3)["data"][0]["item_uuid"] == "b-item"
        finally:
            release.set()
        assert first.result(timeout=5)["item_uuid"] == "a-item"


def test_collection_configuration_is_validated_without_metadata_http_calls(collection):
    caps = call(collection, "capabilities")
    assert caps["enabled"] is True
    assert caps["item_identity_mode"] == "union"
    assert caps["preserve_item_root"] is True
    assert collection.client.calls == []
    with pytest.raises(NotImplementedError, match="fixed"):
        call(collection, "configure", item_identity_mode="shadow")
    assert collection.client.calls == []


def test_other_account_rollback_makes_no_requests(collection):
    with pytest.raises(ValueError, match="different account"):
        call(collection, "restore_move", undo={"scope": "b", "rows": [], "added_uris": []})
    assert collection.client.calls == []


@pytest.mark.asyncio
async def test_account_cleanup_deletes_hidden_catalog_only_in_target_partition(
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


@pytest.mark.parametrize("op", ["must", "must_not"])
def test_item_attribute_filters_are_forwarded_without_catalog_reads(collection, op):
    declare(collection, "/resources/A", "parent", item_attrs=["project=P1"])
    declare(collection, "/resources/A/B", "child", item_attrs=["asset=V1"])
    declare(collection, "/resources/A/B", "other-tenant", scope="b", item_attrs=["asset=V1"])
    before = len(collection.client.calls)
    filters = {
        "op": "and",
        "conds": [
            {"op": "must", "field": "account_id", "conds": ["a"]},
            {"op": op, "field": "item_attrs", "conds": ["asset=V1"]},
        ],
    }
    original = deepcopy(filters)
    result = collection.search_item_groups(
        "catalog-index",
        dense_vector=[1, 0],
        filters=filters,
    )
    assert result == {"groups": []}
    calls = collection.client.calls[before:]
    assert [path.rsplit("/", 1)[-1] for _, path, _ in calls] == ["vector"]
    request = calls[-1][2]
    assert request["partition"] == "a"
    assert "partition_by" not in request
    assert request["filter"] == {
        "op": "and",
        "conds": [original, {"op": "must_not", "field": "level", "conds": [-1]}],
    }
    assert filters == original


@pytest.mark.parametrize("op", ["and", "or"])
def test_item_attribute_combinations_preserve_the_original_predicate(collection, op):
    declare(collection, "/resources/A", "parent", item_attrs=["project=P1"])
    declare(collection, "/resources/A/B", "child", item_attrs=["asset=V1"])
    filters = {
        "op": "and",
        "conds": [
            {"op": "must", "field": "account_id", "conds": ["a"]},
            {
                "op": op,
                "conds": [
                    {"op": "must", "field": "item_attrs", "conds": ["project=P1"]},
                    {"op": "must_not", "field": "item_attrs", "conds": ["asset=V1"]},
                ],
            },
        ],
    }
    before = len(collection.client.calls)
    original = deepcopy(filters)
    collection.search_item_groups("catalog-index", dense_vector=[1, 0], filters=filters)
    calls = collection.client.calls[before:]
    assert [path.rsplit("/", 1)[-1] for _, path, _ in calls] == ["vector"]
    assert calls[-1][2]["filter"] == {
        "op": "and",
        "conds": [original, {"op": "must_not", "field": "level", "conds": [-1]}],
    }
    assert filters == original


def test_item_attribute_filters_do_not_short_circuit_on_missing_declarations(collection):
    filters = {
        "op": "and",
        "conds": [
            {"op": "must", "field": "account_id", "conds": ["a"]},
            {"op": "must", "field": "item_attrs", "conds": ["missing=value"]},
        ],
    }
    before = len(collection.client.calls)
    result = collection.search_item_groups(
        "catalog-index",
        dense_vector=[1, 0],
        filters=filters,
    )
    assert result == {"groups": []}
    assert [path.rsplit("/", 1)[-1] for _, path, _ in collection.client.calls[before:]] == [
        "vector"
    ]
    assert collection.client.calls[-1][2]["filter"] == {
        "op": "and",
        "conds": [filters, {"op": "must_not", "field": "level", "conds": [-1]}],
    }


def test_subtree_scan_and_point_fetches_respect_record_batch_limit(collection):
    for i in range(121):
        uri = f"/resources/batch-{i}"
        row = {
            "id": "ov-item:" + vector_record_id("a", "viking://" + uri.lstrip("/")),
            "uri": uri,
            "account_id": "a",
            "level": -1,
            "type": "dir",
            "item_uuids": [f"uuid-{i}"],
            "item_attrs": None,
        }
        collection.client.rows[row["id"]] = row
    assert len(collection.client.rows) == 121
    call(collection, "delete_subtree", uri="/resources")
    assert collection.client.rows == {}
    for _, path, request in collection.client.calls:
        if path.endswith("/fetch_in_collection"):
            assert len(request["ids"]) <= 100
