# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Native Catalog and local Item grouping contract tests."""

import gc

import pytest

from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import And, Eq
from openviking.storage.vectordb.engine import CatalogEngine
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter


def call(engine, op, scope="a", **kwargs):
    return engine.execute({"op": op, "scope": scope, **kwargs})


def nodes(engine, *uris, scope="a"):
    return call(
        engine,
        "upsert_nodes",
        scope=scope,
        nodes=[{"uri": uri, "node_type": "dir"} for uri in uris],
    )


def declare(engine, uri, item_uuid, attrs=None, *, scope="a", include_attrs=True):
    request = {"uri": uri, "item_uuid": item_uuid}
    if include_attrs:
        request["item_attrs"] = list(attrs or [])
    return call(engine, "declare_item", scope=scope, **request)


def test_declaration_is_shadowed_but_item_attrs_do_not_inherit():
    engine = CatalogEngine()
    assert call(engine, "capabilities")["enabled"] is False
    nodes(
        engine,
        "/resources/A",
        "/resources/A/frame",
        "/resources/A/shot",
        "/resources/A/shot/frame",
    )
    first = declare(engine, "/resources/A", "uuid-a", ["asset_id=V1"])
    nested = declare(engine, "/resources/A/shot", "uuid-shot", ["kind=shot"])

    resolved = call(
        engine,
        "resolve",
        uris=["/resources/A/frame", "/resources/A/shot/frame"],
    )["data"]
    assert [(row["item_uuid"], row["item_root_uri"]) for row in resolved] == [
        ("uuid-a", "/resources/A"),
        ("uuid-shot", "/resources/A/shot"),
    ]
    rows = call(engine, "query_items", item_uuids=["uuid-shot", "uuid-a"])["data"]
    assert rows == [
        {
            "item_uuid": "uuid-shot",
            "uri": "/resources/A/shot",
            "item_attrs": ["kind=shot"],
        },
        {
            "item_uuid": "uuid-a",
            "uri": "/resources/A",
            "item_attrs": ["asset_id=V1"],
        },
    ]
    assert first["item_attrs"] == ["asset_id=V1"]
    assert nested["item_attrs"] == ["kind=shot"]


def test_declaration_retries_reuse_uuid_and_attributes_are_immutable():
    engine = CatalogEngine()
    nodes(engine, "/resources/A")
    declared = declare(engine, "/resources/A", "uuid-a", ["asset_id=V1"])

    omitted = declare(
        engine,
        "/resources/A",
        "different-generated-uuid",
        include_attrs=False,
    )
    same = declare(engine, "/resources/A", "another-generated-uuid", ["asset_id=V1"])
    assert omitted["item_uuid"] == declared["item_uuid"] == "uuid-a"
    assert omitted["item_attrs"] == same["item_attrs"] == ["asset_id=V1"]

    with pytest.raises(ValueError, match="cannot change"):
        declare(engine, "/resources/A", "ignored", [])
    with pytest.raises(ValueError, match="cannot change"):
        declare(engine, "/resources/A", "ignored", ["asset_id=V2"])


def test_item_attrs_filter_uses_each_item_roots_own_values():
    engine = CatalogEngine()
    nodes(
        engine,
        "/resources/A",
        "/resources/A/frame",
        "/resources/A/shot",
        "/resources/A/shot/frame",
        "/resources/B",
        "/resources/B/frame",
    )
    declare(engine, "/resources/A", "uuid-a", ["asset_id=V1"])
    declare(engine, "/resources/A/shot", "uuid-shot", ["asset_id=V2"])
    declare(engine, "/resources/B", "uuid-b", [])

    compiled = call(
        engine,
        "compile_filter",
        filter={"op": "must", "field": "item_attrs", "conds": ["asset_id=V1"]},
    )["filter"]
    assert compiled == {
        "op": "must",
        "field": "uri",
        "conds": ["/resources/A", "/resources/A/frame"],
        "para": "-d=0",
    }
    missing = call(
        engine,
        "compile_filter",
        filter={"op": "must", "field": "item_attrs", "conds": ["missing=value"]},
    )["filter"]
    assert missing["op"] == "and"


def test_grouping_aggregates_in_engine_and_has_stable_ordering():
    engine = CatalogEngine()
    nodes(engine, "/resources/A", "/resources/A/a", "/resources/A/b", "/resources/B/x")
    declare(engine, "/resources/A", "uuid-a", [])
    declare(engine, "/resources/B", "uuid-b", [])
    candidates = [
        {"label": "a2", "uri": "/resources/A/b", "score": 0.4},
        {"label": "a1", "uri": "/resources/A/a", "score": 0.6},
        {"label": "b1", "uri": "/resources/B/x", "score": 0.6},
    ]

    maximum = call(
        engine,
        "group_candidates",
        candidates=candidates,
        aggregation="max",
        max_groups=10,
    )
    assert [(group["entity_id"], group["entity_score"]) for group in maximum["groups"]] == [
        ("uuid-a", pytest.approx(0.6)),
        ("uuid-b", pytest.approx(0.6)),
    ]
    assert maximum["groups"][0]["candidate_members_uri"] == [
        "/resources/A/a",
        "/resources/A/b",
    ]
    assert maximum["groups"][0]["count"] == 2

    summed = call(
        engine,
        "group_candidates",
        candidates=candidates,
        aggregation="sum",
        max_groups=1,
    )
    assert summed["groups"][0]["entity_id"] == "uuid-a"
    assert summed["groups"][0]["entity_score"] == pytest.approx(1.0)
    assert summed["group_count"] == 2
    assert summed["groups_truncated"] is True


def test_persistence_delete_and_account_scoped_revisions(tmp_path):
    path = str(tmp_path / "catalog")
    engine = CatalogEngine(path)
    nodes(engine, "/resources/A")
    nodes(engine, "/resources/B", scope="b")
    declare(engine, "/resources/A", "uuid-a", ["asset_id=A"])
    revision_a = call(engine, "resolve", uris=["/resources/A"])["revision"]
    declare(engine, "/resources/B", "uuid-b", [], scope="b")
    assert call(engine, "resolve", uris=["/resources/A"])["revision"] == revision_a
    del engine
    gc.collect()

    engine = CatalogEngine(path)
    assert call(engine, "query_items", item_uuids=["uuid-a"])["data"][0]["uri"] == ("/resources/A")
    call(engine, "delete_subtree", uri="/resources/A")
    assert call(engine, "query_items", item_uuids=["uuid-a"])["data"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"op": "upsert_nodes", "scope": "a", "nodes": ["bad"]},
        {"op": "upsert_nodes", "scope": "a", "nodes": [{"uri": "/a/../b", "node_type": "dir"}]},
        {"op": "upsert_nodes", "scope": "a\0b", "nodes": []},
        {
            "op": "declare_item",
            "scope": "a",
            "uri": "/missing",
            "item_uuid": "uuid",
            "item_attrs": ["bad"],
        },
    ],
)
def test_invalid_native_requests_raise_instead_of_aborting(payload):
    with pytest.raises(ValueError):
        CatalogEngine().execute(payload)


@pytest.fixture
def adapter(tmp_path):
    adapter = LocalCollectionAdapter("context", str(tmp_path), "default")
    adapter.create_collection(
        "context",
        CollectionSchemas.context_collection("context", 4),
        distance="cosine",
        sparse_weight=0,
        index_name="default",
    )
    yield adapter
    adapter.close()


def declare_adapter_item(adapter, uri, item_uuid, attrs):
    adapter.catalog_execute(
        {"op": "upsert_nodes", "scope": "a", "nodes": [{"uri": uri, "node_type": "dir"}]}
    )
    adapter.catalog_execute(
        {
            "op": "declare_item",
            "scope": "a",
            "uri": uri,
            "item_uuid": item_uuid,
            "item_attrs": attrs,
        }
    )


def test_local_adapter_filters_then_groups_candidates(adapter):
    root = "viking://resources/items"
    declare_adapter_item(adapter, f"{root}/A", "uuid-a", ["asset_id=V1"])
    declare_adapter_item(adapter, f"{root}/B", "uuid-b", ["asset_id=V2"])
    adapter.upsert(
        [
            {
                "id": "a1",
                "account_id": "a",
                "uri": f"{root}/A/frame1",
                "vector": [1, 0, 0, 0],
                "level": 2,
            },
            {
                "id": "a2",
                "account_id": "a",
                "uri": f"{root}/A/frame2",
                "vector": [0.9, 0.1, 0, 0],
                "level": 2,
            },
            {
                "id": "b1",
                "account_id": "a",
                "uri": f"{root}/B/frame1",
                "vector": [0.8, 0.2, 0, 0],
                "level": 2,
            },
        ]
    )
    result = adapter.search_item_groups(
        query_vector=[1, 0, 0, 0],
        filter=And([Eq("account_id", "a"), Eq("item_attrs", "asset_id=V2")]),
        max_groups=10,
        aggregation="max",
    )
    assert [group["entity_id"] for group in result["groups"]] == ["uuid-b"]
    assert result["groups"][0]["candidate_members_uri"] == [f"{root}/B/frame1"]


def test_filter_only_item_groups_have_zero_scores(adapter):
    root = "viking://resources/items/A"
    declare_adapter_item(adapter, root, "uuid-a", ["asset_id=V1"])
    adapter.upsert(
        [
            {
                "id": "a1",
                "account_id": "a",
                "uri": f"{root}/frame",
                "vector": [1, 0, 0, 0],
                "level": 2,
            }
        ]
    )
    result = adapter.search_item_groups(
        filter=And([Eq("account_id", "a"), Eq("item_attrs", "asset_id=V1")]),
        max_groups=10,
        aggregation="max",
        filter_only=True,
    )
    assert result["groups"][0]["entity_score"] == 0.0
