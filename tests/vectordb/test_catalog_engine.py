# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Native Catalog and local Item grouping contract tests."""

import gc
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import And, Eq, TimeRange
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
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
    request = {"uri": uri, "item_uuids": [item_uuid]}
    if include_attrs:
        request["item_attrs"] = list(attrs or [])
    return call(engine, "declare_item", scope=scope, **request)


def test_declaration_is_shadowed_but_item_attrs_do_not_inherit():
    engine = CatalogEngine()
    assert call(engine, "capabilities")["enabled"] is False
    call(engine, "configure", item_identity_mode="shadow")
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
    resolved_roots = call(engine, "resolve", uris=["/resources/A/shot", "/resources/A"])["data"]
    assert [(row["item_uuid"], row["item_root_uri"]) for row in resolved_roots] == [
        ("uuid-shot", "/resources/A/shot"),
        ("uuid-a", "/resources/A"),
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


def test_list_identity_protocol_and_persisted_union_mode(tmp_path):
    path = str(tmp_path / "union-catalog")
    engine = CatalogEngine(path)
    assert call(engine, "capabilities")["item_identity_mode"] == "union"
    nodes(engine, "/resources/A/B", "/resources/elsewhere")
    call(
        engine, "declare_item", uri="/resources/A", item_uuids=["uuid-a"], item_attrs=["project=A"]
    )
    declared = call(
        engine, "declare_item", uri="/resources/A/B", item_uuids=["uuid-b"], item_attrs=["asset=B"]
    )
    assert declared["item_uuids"] == ["uuid-b"]
    with pytest.raises(ValueError, match="one item_uuids"):
        call(engine, "declare_item", uri="/resources/elsewhere", item_uuids=["x", "y"])
    assert call(engine, "resolve", uris=["/resources/A/B/hit"])["data"][0]["item_uuids"] == [
        "uuid-b",
        "uuid-a",
    ]
    call(engine, "configure", item_identity_mode="shadow")
    assert call(engine, "resolve", uris=["/resources/A/B/hit"])["data"][0]["item_uuids"] == [
        "uuid-b"
    ]
    call(engine, "configure", item_identity_mode="union")
    del engine
    gc.collect()
    engine = CatalogEngine(path)
    assert call(engine, "capabilities")["item_identity_mode"] == "union"
    resolved = call(engine, "resolve", uris=["/resources/A/B/hit"])["data"][0]
    assert resolved["item_uuids"] == ["uuid-b", "uuid-a"]
    result = call(
        engine,
        "group_candidates",
        candidates=[{"label": "hit", "uri": "/resources/A/B/hit", "score": 0.8}],
        aggregation="sum",
        max_groups=10,
    )
    assert result["group_field"] == "item_uuids"
    assert [(g["entity_id"], g["entity_uri"], g["entity_attrs"]) for g in result["groups"]] == [
        ("uuid-a", "/resources/A", ["project=A"]),
        ("uuid-b", "/resources/A/B", ["asset=B"]),
    ]
    assert all(g["entity_score"] == pytest.approx(0.8) for g in result["groups"])
    call(engine, "move_subtree", from_uri="/resources/A/B", to_uri="/resources/elsewhere/B")
    assert call(engine, "resolve", uris=["/resources/elsewhere/B/hit"])["data"][0][
        "item_uuids"
    ] == ["uuid-b"]


def test_shadow_filter_frontier_handles_prefix_siblings_and_deep_items():
    engine = CatalogEngine()
    call(engine, "configure", item_identity_mode="shadow")
    nodes(engine, "/resources/A/B/C", "/resources/A-sibling")
    declare(engine, "/resources/A", "a", ["kind=asset"])
    declare(engine, "/resources/A-sibling", "sibling", ["kind=other"])
    declare(engine, "/resources/A/B", "b", ["kind=other"])
    declare(engine, "/resources/A/B/C", "c", ["kind=other"])
    compiled = call(
        engine,
        "compile_filter",
        filter={"op": "must", "field": "item_attrs", "conds": ["kind=asset"]},
    )["filter"]
    assert compiled["conds"][1]["conds"] == ["/resources/A/B"]
    assert len(compiled["conds"]) == 2  # The nearest child already hides C.


def test_identity_mode_is_collection_wide_but_declarations_and_revisions_are_scoped(tmp_path):
    path = str(tmp_path / "collection-mode")
    engine = CatalogEngine(path)
    call(engine, "configure", item_identity_mode="shadow")
    for scope in ("a", "b"):
        nodes(engine, "/resources/A/B", scope=scope)
        declare(engine, "/resources/A", "parent", [f"account={scope}"], scope=scope)
        declare(engine, "/resources/A/B", "child", [f"account={scope}"], scope=scope)

    def resolve(scope):
        return call(engine, "resolve", scope=scope, uris=["/resources/A/B/hit"])

    scopes = ("a", "b", "new-account")
    before = {scope: resolve(scope)["revision"] for scope in scopes}
    call(engine, "configure", scope="config-account", item_identity_mode="union")
    changed = {scope: resolve(scope)["revision"] for scope in scopes}
    assert all(changed[scope] != before[scope] for scope in scopes)
    for scope in scopes:
        assert call(engine, "capabilities", scope=scope)["item_identity_mode"] == "union"
    for scope in ("a", "b"):
        assert resolve(scope)["data"][0]["item_uuids"] == ["child", "parent"]
        assert declare(engine, "/resources/A", "_read", include_attrs=False, scope=scope)[
            "item_attrs"
        ] == [f"account={scope}"]
    assert resolve("new-account")["data"][0]["item_uuids"] == []

    # Equivalent or omitted settings do not invalidate any account's recall.
    call(engine, "configure", scope="b", item_identity_mode="union")
    call(engine, "configure", scope="new-account")
    assert {scope: resolve(scope)["revision"] for scope in scopes} == changed
    nodes(engine, "/resources/new", scope="a")
    declare(engine, "/resources/new", "only-a", scope="a")
    assert resolve("a")["revision"] != changed["a"]
    assert resolve("b")["revision"] == changed["b"]
    assert (
        call(engine, "resolve", scope="b", uris=["/resources/new"])["data"][0]["item_uuids"] == []
    )
    before = {scope: resolve(scope)["revision"] for scope in scopes}
    with pytest.raises(ValueError, match="shadow or union"):
        call(engine, "configure", scope="b", item_identity_mode="invalid")
    assert {scope: resolve(scope)["revision"] for scope in scopes} == before
    call(engine, "configure", scope="b", item_identity_mode="shadow")
    assert all(resolve(scope)["revision"] != before[scope] for scope in scopes)
    del engine
    gc.collect()
    engine = CatalogEngine(path)
    for scope in ("a", "b"):
        assert call(engine, "capabilities", scope=scope)["item_identity_mode"] == "shadow"
        assert resolve(scope)["data"][0]["item_uuids"] == ["child"]


def test_new_catalog_persists_union_without_enabling_declarations(tmp_path):
    path = str(tmp_path / "new-default")
    engine = CatalogEngine(path)
    assert call(engine, "capabilities")["enabled"] is False
    assert call(engine, "capabilities")["item_identity_mode"] == "union"
    del engine
    gc.collect()
    engine = CatalogEngine(path)
    assert call(engine, "capabilities")["enabled"] is False
    assert call(engine, "capabilities")["item_identity_mode"] == "union"


def test_collection_default_union_survives_reload_before_activation(tmp_path):
    path = tmp_path / "collection"
    collection = get_or_create_local_collection(
        CollectionSchemas.context_collection("context", 4), str(path)
    )
    collection.close()
    collection = get_or_create_local_collection(path=str(path))
    caps = collection.catalog_execute({"op": "capabilities", "scope": "a"})
    assert caps["enabled"] is False
    assert caps["item_identity_mode"] == "union"
    assert not (path / "catalog").exists()
    collection.catalog_execute({"op": "upsert_nodes", "scope": "a", "nodes": []})
    collection.catalog_execute({"op": "configure", "scope": "a", "item_identity_mode": "shadow"})
    collection.close()
    collection = get_or_create_local_collection(path=str(path))
    assert (
        collection.catalog_execute({"op": "capabilities", "scope": "a"})["item_identity_mode"]
        == "shadow"
    )
    collection.close()


@pytest.mark.parametrize("mode", ["shadow", "union"])
@pytest.mark.parametrize("aggregation", ["max", "sum"])
@pytest.mark.parametrize(("sign", "max_members"), [(1, 10), (-1, 1)])
def test_duplicate_labels_contribute_each_input_row(mode, aggregation, sign, max_members):
    engine = CatalogEngine()
    nodes(engine, "/resources/A/B")
    declare(engine, "/resources/A", "parent")
    declare(engine, "/resources/A/B", "child")
    call(engine, "configure", item_identity_mode=mode)
    rows = [
        {"label": "same", "uri": "/resources/A/B/first", "score": sign * 0.5},
        {"label": "same", "uri": "/resources/A/B/second", "score": sign * 0.75},
        {"label": "same", "uri": "/resources/A/third", "score": sign * 0.25},
    ]
    result = call(
        engine,
        "group_candidates",
        candidates=rows,
        aggregation=aggregation,
        max_groups=10,
        max_members_per_group=max_members,
    )
    groups = {g["entity_id"]: g for g in result["groups"]}
    for uuid, inputs in [("child", rows[:2]), ("parent", rows if mode == "union" else rows[2:])]:
        group = groups[uuid]
        scores = [row["score"] for row in inputs]
        assert group["candidate_members_count"] == len(inputs)
        assert group["entity_score"] == pytest.approx(
            max(scores) if aggregation == "max" else sum(scores)
        )
        assert group["candidate_members"] == ["same"] * min(len(inputs), max_members)
        assert group["candidate_members_truncated"] is (len(inputs) > max_members)
        assert set(group["candidate_members_uri"]).issubset({row["uri"] for row in inputs})


def test_noop_registrations_do_not_advance_identity_revision():
    engine = CatalogEngine()
    nodes(engine, "/resources/A")
    declare(engine, "/resources/A", "a")
    before = call(engine, "resolve", uris=["/resources/A/hit"])["revision"]
    nodes(engine, "/resources/A", "/resources/unrelated/new")
    assert call(engine, "resolve", uris=["/resources/A/hit"])["revision"] == before


def test_group_evidence_budget_does_not_truncate_scores_or_counts():
    engine = CatalogEngine()
    nodes(engine, "/resources/A")
    declare(engine, "/resources/A", "a", ["kind=asset"])
    result = call(
        engine,
        "group_candidates",
        candidates=[
            {"label": str(i), "uri": f"/resources/A/{i}", "score": float(i)} for i in range(12)
        ],
        aggregation="sum",
        max_groups=1,
        max_members_per_group=3,
    )
    group = result["groups"][0]
    assert group["entity_score"] == 66
    assert group["candidate_members_count"] == 12
    assert group["candidate_members"] == ["11", "10", "9"]
    assert group["candidate_members_truncated"] is True
    assert len(group["candidate_members_uri"]) == 3


def test_item_attrs_filter_uses_each_item_roots_own_values():
    engine = CatalogEngine()
    call(engine, "configure", item_identity_mode="shadow")
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
        "op": "and",
        "conds": [
            {
                "op": "must",
                "field": "uri",
                "conds": ["/resources/A"],
                "para": "-d=-1",
            },
            {
                "op": "must_not",
                "field": "uri",
                "conds": ["/resources/A/shot"],
                "para": "-d=-1",
            },
        ],
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
    assert maximum["groups"][0]["candidate_members_count"] == 2

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
    assert call(engine, "resolve", uris=["/resources/A"])["data"][0]["item_uuid"] == "uuid-a"
    call(engine, "delete_subtree", uri="/resources/A")
    assert call(engine, "resolve", uris=["/resources/A"])["data"][0]["item_uuids"] == []


def test_move_subtree_preserves_item_identity_attrs_and_nested_items():
    engine = CatalogEngine()
    call(engine, "configure", item_identity_mode="shadow")
    nodes(
        engine,
        "/resources/A",
        "/resources/A/frame",
        "/resources/A/shot",
        "/resources/A/shot/frame",
    )
    declare(engine, "/resources/A", "uuid-a", ["asset_id=V1"])
    declare(engine, "/resources/A/shot", "uuid-shot", ["kind=shot"])

    moved = call(
        engine,
        "move_subtree",
        from_uri="/resources/A",
        to_uri="/resources/B",
    )
    assert moved["moved_nodes"] == 4
    assert call(
        engine,
        "resolve",
        uris=["/resources/A/frame", "/resources/B/frame", "/resources/B/shot/frame"],
    )["data"] == [
        {"uri": "/resources/A/frame", "item_uuids": []},
        {
            "uri": "/resources/B/frame",
            "item_uuid": "uuid-a",
            "item_uuids": ["uuid-a"],
            "item_root_uri": "/resources/B",
        },
        {
            "uri": "/resources/B/shot/frame",
            "item_uuid": "uuid-shot",
            "item_uuids": ["uuid-shot"],
            "item_root_uri": "/resources/B/shot",
        },
    ]
    moved_roots = call(engine, "resolve", uris=["/resources/B", "/resources/B/shot"])["data"]
    assert [(row["item_uuid"], row["item_root_uri"]) for row in moved_roots] == [
        ("uuid-a", "/resources/B"),
        ("uuid-shot", "/resources/B/shot"),
    ]
    assert declare(engine, "/resources/B", "_read", include_attrs=False)["item_attrs"] == [
        "asset_id=V1"
    ]
    assert declare(engine, "/resources/B/shot", "_read", include_attrs=False)["item_attrs"] == [
        "kind=shot"
    ]


@pytest.mark.parametrize("source_is_item", [False, True])
@pytest.mark.parametrize("identity_mode", ["shadow", "union"])
def test_move_subtree_merges_and_undo_restores_both_trees(source_is_item, identity_mode):
    engine = CatalogEngine()
    call(engine, "configure", item_identity_mode=identity_mode)
    nodes(engine, "/resources/source", "/resources/source/a")
    nodes(engine, "/resources/target", "/resources/target/kept")
    if source_is_item:
        declare(engine, "/resources/source", "uuid-source", ["asset_id=source"])
    declare(engine, "/resources/target", "uuid-target", ["asset_id=target"])
    moved_uuid = "uuid-source" if source_is_item else "uuid-target"
    moved_attrs = ["asset_id=source"] if source_is_item else ["asset_id=target"]
    before = call(engine, "resolve", uris=["/resources/source/a"])["data"][0]

    moved = call(
        engine,
        "move_subtree",
        from_uri="/resources/source",
        to_uri="/resources/target",
    )
    assert moved["moved_nodes"] == 2
    assert call(
        engine,
        "resolve",
        uris=["/resources/source/a", "/resources/target/a", "/resources/target/kept"],
    )["data"] == [
        {"uri": "/resources/source/a", "item_uuids": []},
        {
            "uri": "/resources/target/a",
            "item_uuid": moved_uuid,
            "item_uuids": [moved_uuid],
            "item_root_uri": "/resources/target",
        },
        {
            "uri": "/resources/target/kept",
            "item_uuid": moved_uuid,
            "item_uuids": [moved_uuid],
            "item_root_uri": "/resources/target",
        },
    ]
    target_root = call(engine, "resolve", uris=["/resources/target"])["data"][0]
    assert target_root["item_uuid"] == moved_uuid
    assert target_root["item_root_uri"] == "/resources/target"
    assert (
        declare(engine, "/resources/target", "_read", include_attrs=False)["item_attrs"]
        == moved_attrs
    )

    call(engine, "restore_move", undo=moved["undo"])
    assert call(
        engine,
        "resolve",
        uris=["/resources/source/a", "/resources/target/a", "/resources/target/kept"],
    )["data"] == [
        before,
        {
            "uri": "/resources/target/a",
            "item_uuid": "uuid-target",
            "item_uuids": ["uuid-target"],
            "item_root_uri": "/resources/target",
        },
        {
            "uri": "/resources/target/kept",
            "item_uuid": "uuid-target",
            "item_uuids": ["uuid-target"],
            "item_root_uri": "/resources/target",
        },
    ]


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
            "item_uuids": [item_uuid],
            "item_attrs": attrs,
        }
    )


def test_item_recall_excludes_ordinary_records_and_keeps_sum_stable(adapter, monkeypatch):
    declare_adapter_item(adapter, "viking://resources/A", "uuid-a", [])
    declare_adapter_item(adapter, "viking://resources/B", "uuid-b", [])
    records = []
    for name, count, score in [("A", 1000, 0.9), ("B", 1200, 0.8), ("A-ordinary", 1000, 1.0)]:
        for i in range(count):
            records.append(
                {
                    "id": f"{name}-{i}",
                    "account_id": "a",
                    "uri": f"viking://resources/{name}/{i}.md",
                    "level": 2,
                    "vector": [score, math.sqrt(1 - score * score), 0, 0],
                }
            )
    adapter.upsert(records)
    local = adapter.get_collection()._Collection__collection

    def no_python_members(*args, **kwargs):
        pytest.fail("Native Item search must keep members inside the engine")

    monkeypatch.setattr(local, "search_by_vector", no_python_members)
    monkeypatch.setattr(local.store_mgr, "fetch_cands_fields", no_python_members)

    def item_groups(limit):
        return adapter.search_item_groups(
            query_vector=[1, 0, 0, 0],
            filter=Eq("account_id", "a"),
            max_groups=limit,
            aggregation="sum",
        )["groups"]

    top = item_groups(1)
    groups = item_groups(10)
    assert top == groups[:1]
    assert [(group["entity_id"], group["candidate_members_count"]) for group in groups] == [
        ("uuid-b", 1200),
        ("uuid-a", 1000),
    ]


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


def test_item_attrs_filter_includes_members_indexed_before_declaration(adapter):
    root = "viking://resources/items/A"
    adapter.upsert(
        [
            {
                "id": "before-declaration",
                "account_id": "a",
                "uri": f"{root}/frame",
                "vector": [1, 0, 0, 0],
                "level": 2,
            }
        ]
    )
    declare_adapter_item(adapter, root, "uuid-a", ["asset_id=V1"])

    result = adapter.search_item_groups(
        query_vector=[1, 0, 0, 0],
        filter=And([Eq("account_id", "a"), Eq("item_attrs", "asset_id=V1")]),
        max_groups=10,
        aggregation="max",
    )
    assert result["groups"][0]["candidate_members_uri"] == [f"{root}/frame"]


@pytest.mark.parametrize(("mode", "aggregation"), [("shadow", "max"), ("union", "sum")])
def test_filter_scan_keeps_complete_counts_without_python_members(
    adapter, monkeypatch, mode, aggregation
):
    root = "viking://resources/items"
    declare_adapter_item(adapter, f"{root}/A", "z-parent", ["project=parent"])
    declare_adapter_item(adapter, f"{root}/A/B", "z-nested", ["kind=asset"])
    declare_adapter_item(adapter, f"{root}/late", "a-late", ["kind=asset"])
    adapter.catalog_execute({"op": "configure", "scope": "a", "item_identity_mode": mode})
    total = 1307  # Cross both the native batch boundary and the evidence budget.
    records = [
        {
            "id": f"成员-{i:04d}",
            "account_id": "a",
            "uri": f"{root}/A/B/{i}",
            "vector": [1, 0, 0, 0],
            "level": 2,
        }
        for i in range(total)
    ]
    records.extend(
        [
            {**records[0], "id": "last", "uri": f"{root}/late/member"},
            {**records[0], "id": "other-account", "account_id": "b"},
            {**records[0], "id": "unmatched-parent", "uri": f"{root}/A/member"},
        ]
    )
    adapter.upsert(records)
    local = adapter.get_collection()._Collection__collection

    def no_python_members(*_args, **_kwargs):
        pytest.fail("Pure filtering must not recall, count or decode members through Python")

    monkeypatch.setattr(local, "search_by_scalar", no_python_members)
    monkeypatch.setattr(local, "search_by_vector", no_python_members)
    for name in ("fetch_cands_data", "fetch_cands_fields", "iter_all_cands_fields"):
        monkeypatch.setattr(local.store_mgr, name, no_python_members)
    monkeypatch.setattr(local.indexes.get("default"), "aggregate", no_python_members)
    options = {
        "filter": And([Eq("account_id", "a"), Eq("item_attrs", "kind=asset")]),
        "aggregation": aggregation,
        "filter_only": True,
    }
    top = adapter.search_item_groups(**options, max_groups=1)
    assert top["group_count"] == (3 if mode == "union" else 2)
    assert top["groups_truncated"] is True
    assert top["groups"][0]["entity_id"] == "a-late"  # Last input wins the UUID tie.
    result = adapter.search_item_groups(**options, max_groups=10)
    expected_ids = ["a-late", "z-nested", "z-parent"] if mode == "union" else ["a-late", "z-nested"]
    assert [(g["entity_id"], g["entity_score"]) for g in result["groups"]] == [
        (uuid, 0.0) for uuid in expected_ids
    ]
    groups = {g["entity_id"]: g for g in result["groups"]}
    assert groups["a-late"]["candidate_members_count"] == 1
    assert groups["z-nested"]["entity_attrs"] == ["kind=asset"]
    for uuid in expected_ids[1:]:
        group = groups[uuid]
        assert group["candidate_members_count"] == total
        assert group["candidate_members_truncated"] is True
        assert group["candidate_members"] == [f"成员-{i:04d}" for i in range(1000)]
        assert group["candidate_members_uri"] == [f"{root}/A/B/{i}" for i in range(1000)]
    if mode == "union":
        assert groups["z-parent"]["entity_attrs"] == ["project=parent"]
        parent_filter = {
            **options,
            "filter": And([Eq("account_id", "a"), Eq("item_attrs", "project=parent")]),
        }
        parent_groups = adapter.search_item_groups(**parent_filter, max_groups=10)["groups"]
        assert {g["entity_id"]: g["candidate_members_count"] for g in parent_groups} == {
            "z-nested": total,
            "z-parent": total + 1,
        }
    assert adapter.search_item_groups(**options, score_threshold=0.1)["group_count"] == 0


@pytest.mark.parametrize("aggregation", ["max", "sum"])
def test_filter_scan_projects_live_fields_after_update_delete_and_reopen(adapter, aggregation):
    root = "viking://resources/items"
    declare_adapter_item(adapter, f"{root}/A", "a", ["kind=asset"])
    declare_adapter_item(adapter, f"{root}/B", "b", ["kind=asset"])
    adapter.upsert(
        [
            {
                "id": name,
                "account_id": "a",
                "uri": f"{root}/A/{name}",
                "updated_at": timestamp,
                "vector": [1, 0, 0, 0],
                "level": 2,
            }
            for name, timestamp in [
                ("old", "2026-11-01T00:00:00Z"),
                ("move", "2026-11-12T00:00:00Z"),
                ("delete", "2026-11-13T00:00:00Z"),
            ]
        ]
    )
    options = {
        "filter": And(
            [
                Eq("account_id", "a"),
                Eq("item_attrs", "kind=asset"),
                TimeRange("updated_at", start="2026-11-10T00:00:00Z"),
            ]
        ),
        "aggregation": aggregation,
        "filter_only": True,
    }
    assert adapter.search_item_groups(**options)["groups"][0]["candidate_members_count"] == 2
    adapter.upsert(
        [
            {
                "id": "move",
                "account_id": "a",
                "uri": f"{root}/B/moved",
                "updated_at": "2026-11-12T00:00:00Z",
                "vector": [1, 0, 0, 0],
                "level": 2,
            }
        ]
    )
    adapter.delete(ids=["delete"])
    result = adapter.search_item_groups(**options)
    assert result["group_count"] == 1
    group = result["groups"][0]
    assert (group["entity_id"], group["entity_score"], group["candidate_members_count"]) == (
        "b",
        0.0,
        1,
    )
    assert group["candidate_members"] == ["move"]
    assert group["candidate_members_uri"] == [f"{root}/B/moved"]
    adapter.close()
    reopened = adapter.search_item_groups(**options)
    # The in-process revision token restarts on Catalog reopen.
    assert {k: v for k, v in reopened.items() if k != "revision"} == {
        k: v for k, v in result.items() if k != "revision"
    }


@pytest.mark.parametrize("key_type", ["int64", "auto"])
def test_filter_scan_preserves_integer_and_auto_primary_keys(tmp_path, key_type):
    adapter = LocalCollectionAdapter("keys", str(tmp_path), "default")
    schema = CollectionSchemas.context_collection("keys", 4)
    id_field = next(f for f in schema["Fields"] if f["FieldName"] == "id")
    if key_type == "auto":
        id_field.pop("IsPrimaryKey")
    else:
        id_field["FieldType"] = key_type
    try:
        adapter.create_collection(
            "keys", schema, distance="cosine", sparse_weight=0, index_name="default"
        )
        root = "viking://resources/items/A"
        declare_adapter_item(adapter, root, "a", ["kind=asset"])
        record = {"account_id": "a", "uri": f"{root}/member", "vector": [1, 0, 0, 0], "level": 2}
        if key_type != "auto":
            record["id"] = -9
        ids = (
            adapter.get_collection().upsert_data([record]).ids
            if key_type == "auto"
            else adapter.upsert([record])
        )
        group = adapter.search_item_groups(filter=Eq("account_id", "a"), filter_only=True)[
            "groups"
        ][0]
        assert group["candidate_members"] == [str(ids[0])]
        assert group["candidate_members_count"] == 1
    finally:
        adapter.close()


def test_filter_scan_waits_for_store_and_index_to_share_a_snapshot(adapter, monkeypatch):
    root = "viking://resources/items"
    for name in ("A", "B"):
        declare_adapter_item(adapter, f"{root}/{name}", name, ["kind=asset"])
    record = {
        "id": "move",
        "account_id": "a",
        "uri": f"{root}/A/member",
        "vector": [1, 0, 0, 0],
        "level": 2,
    }
    adapter.upsert([record])
    local = adapter.get_collection()._Collection__collection
    index = local.indexes.get("default")
    store_committed, release_writer, query_started, scan_started = (
        threading.Event() for _ in range(4)
    )
    original_upsert, original_aggregate = index.upsert_data, index.aggregate_items

    def pause_after_store_commit(*args, **kwargs):
        store_committed.set()
        assert release_writer.wait(5)
        return original_upsert(*args, **kwargs)

    def aggregate(*args, **kwargs):
        scan_started.set()
        return original_aggregate(*args, **kwargs)

    def query():
        query_started.set()
        return adapter.search_item_groups(filter=Eq("account_id", "a"), filter_only=True)

    monkeypatch.setattr(index, "upsert_data", pause_after_store_commit)
    monkeypatch.setattr(index, "aggregate_items", aggregate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(adapter.upsert, [{**record, "uri": f"{root}/B/moved"}])
        try:
            assert store_committed.wait(5)
            reader = pool.submit(query)
            assert query_started.wait(5)
            assert not scan_started.wait(0.1)
        finally:
            release_writer.set()
        writer.result(timeout=5)
        groups = reader.result(timeout=5)["groups"]
    assert [(g["entity_id"], g["candidate_members_count"]) for g in groups] == [("B", 1)]


@pytest.mark.parametrize("finish_during_recall", [False, True])
def test_semantic_item_scan_ignores_another_accounts_write(
    adapter, monkeypatch, finish_during_recall
):
    root = "viking://resources/item-a"
    declare_adapter_item(adapter, root, "A", [])
    record = {"id": "a-member", "account_id": "a", "uri": f"{root}/member", "level": 2}
    adapter.upsert([{**record, "vector": [1, 0, 0, 0]}])
    local = adapter.get_collection()._Collection__collection
    index = local.indexes.get("default")
    original_upsert, original_search = index.upsert_data, index.search_items
    store_committed, release_writer, writer_finished = (threading.Event() for _ in range(3))
    recalls = 0

    def pause_after_store_commit(deltas):
        store_committed.set()
        assert release_writer.wait(5)
        return original_upsert(deltas)

    def search(*args, **kwargs):
        nonlocal recalls
        result = original_search(*args, **kwargs)
        recalls += 1
        if finish_during_recall:
            release_writer.set()
            assert writer_finished.wait(5)
        return result

    def write():
        try:
            adapter.upsert(
                [
                    {
                        "id": "b-member",
                        "account_id": "b",
                        "uri": "viking://resources/b.md",
                        "level": 2,
                        "vector": [1, 0, 0, 0],
                    }
                ]
            )
        finally:
            writer_finished.set()

    monkeypatch.setattr(index, "upsert_data", pause_after_store_commit)
    monkeypatch.setattr(index, "search_items", search)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        try:
            assert store_committed.wait(5)
            groups = pool.submit(
                adapter.search_item_groups, query_vector=[1, 0, 0, 0], filter=Eq("account_id", "a")
            ).result(timeout=5)["groups"]
        finally:
            release_writer.set()
        writer.result(timeout=5)
    assert [(g["entity_id"], g["entity_score"]) for g in groups] == [("A", 1.0)]
    assert recalls == 1


@pytest.mark.parametrize("filter_by_attrs", [False, True])
def test_semantic_item_scan_retries_if_store_fields_change_before_return(
    adapter, monkeypatch, filter_by_attrs
):
    root = "viking://resources/items"
    for name in ("A", "B"):
        declare_adapter_item(adapter, f"{root}/{name}", name, [f"kind={name}"])
    record = {
        "id": "move",
        "account_id": "a",
        "uri": f"{root}/A/member",
        "vector": [1, 0, 0, 0],
        "level": 2,
    }
    adapter.upsert([record])
    local = adapter.get_collection()._Collection__collection
    index = local.indexes.get("default")
    native_search = index.search_items
    recalled, updated = threading.Event(), threading.Event()
    calls = 0

    def pause_after_search(*args, **kwargs):
        nonlocal calls
        result = native_search(*args, **kwargs)
        calls += 1
        if calls == 1:
            recalled.set()
            assert updated.wait(5)
        return result

    monkeypatch.setattr(index, "search_items", pause_after_search)
    scope = Eq("account_id", "a")
    condition = And([scope, Eq("item_attrs", "kind=A")]) if filter_by_attrs else scope
    with ThreadPoolExecutor(max_workers=1) as pool:
        reader = pool.submit(
            adapter.search_item_groups,
            query_vector=[1, 0, 0, 0],
            filter=condition,
        )
        try:
            assert recalled.wait(5)
            adapter.upsert([{**record, "uri": f"{root}/B/member", "vector": [0, 1, 0, 0]}])
        finally:
            updated.set()
        groups = reader.result(timeout=5)["groups"]
    if filter_by_attrs:
        # The retry counts zero eligible members after the record moves to B.
        assert groups == []
    else:
        assert [(g["entity_id"], g["entity_score"]) for g in groups] == [("B", 0.5)]
    assert calls == 2


def test_semantic_item_scan_retries_after_index_replacement(monkeypatch):
    adapter = LocalCollectionAdapter("context", "", "default")
    adapter.create_collection(
        "context",
        CollectionSchemas.context_collection("context", 4),
        distance="cosine",
        sparse_weight=0,
        index_name="default",
    )
    try:
        root = "viking://resources/item-a"
        declare_adapter_item(adapter, root, "A", [])
        adapter.upsert(
            {
                "id": "a-member",
                "account_id": "a",
                "uri": f"{root}/member",
                "level": 2,
                "vector": [1, 0, 0, 0],
            }
        )
        local = adapter.get_collection()._Collection__collection
        old_index = local.get_index("default")
        original = type(old_index).search_items
        searched = []

        def search_then_replace(index, *args, **kwargs):
            result = original(index, *args, **kwargs)
            searched.append(index)
            if index is old_index:
                local._rebuild_index("default", old_index)
            return result

        monkeypatch.setattr(type(old_index), "search_items", search_then_replace)
        result = adapter.search_item_groups(query_vector=[1, 0, 0, 0], filter=Eq("account_id", "a"))
        new_index = local.get_index("default")
        assert new_index is not old_index
        assert searched == [old_index, new_index]
        assert [(g["entity_id"], g["entity_score"]) for g in result["groups"]] == [("A", 1.0)]
    finally:
        adapter.close()


def test_noop_node_updates_do_not_invalidate_every_item_recall(adapter, monkeypatch):
    root = "viking://resources/read-item"
    declare_adapter_item(adapter, root, "uuid-read", [])
    declare_adapter_item(adapter, "viking://resources/unrelated", "uuid-unrelated", [])
    adapter.upsert(
        [
            {
                "id": "hit",
                "account_id": "a",
                "uri": root + "/hit.md",
                "level": 2,
                "vector": [1, 0, 0, 0],
            }
        ]
    )
    collection = adapter.get_collection()._Collection__collection
    index = collection.indexes.get("default")
    original_search = index.search_items
    recalls = []

    def search_then_noop_upsert(*args, **kwargs):
        recalled = original_search(*args, **kwargs)
        before = recalled["revision"]
        after = collection.catalog_execute(
            {
                "op": "upsert_nodes",
                "scope": "a",
                "nodes": [{"uri": "/resources/unrelated", "node_type": "dir"}],
            }
        )["revision"]
        recalls.append({"before": before, "after": after})
        return recalled

    monkeypatch.setattr(index, "search_items", search_then_noop_upsert)
    result = adapter.search_item_groups(
        query_vector=[1, 0, 0, 0], filter=Eq("account_id", "a"), max_groups=1
    )
    assert result["groups"][0]["entity_id"] == "uuid-read"
    assert len(recalls) == 1
    assert recalls[0]["before"] == recalls[0]["after"]


def test_item_root_type_is_validated_before_a_resource_replacement():
    engine = CatalogEngine()
    nodes(engine, "/resources/A")
    declare(engine, "/resources/A", "stable")
    with pytest.raises(ValueError, match="Cannot change an Item root node type"):
        call(engine, "validate_resource_update", uri="/resources/A", node_type="file")
    assert call(engine, "resolve", uris=["/resources/A"])["data"][0]["item_uuids"] == ["stable"]


def test_file_catalog_node_cannot_be_declared_as_item():
    engine = CatalogEngine()
    uri = "/resources/leaf.md"
    call(engine, "upsert_nodes", nodes=[{"uri": uri, "node_type": "file"}])
    with pytest.raises(ValueError, match="require a directory Catalog node"):
        declare(engine, uri, "leaf")


def test_resource_catalog_reset_preserves_declaration_and_rejects_independent_children():
    engine = CatalogEngine()
    nodes(engine, "/resources/A")
    declared = declare(engine, "/resources/A", "stable", ["kind=asset"])
    call(engine, "upsert_nodes", nodes=[{"uri": "/resources/A/swap", "node_type": "file"}])
    reset = call(engine, "delete_subtree", uri="/resources/A", preserve_item_root=True)
    assert reset["revision"] == declared["revision"]
    nodes(engine, "/resources/A/swap/inner")  # The old file type no longer conflicts.
    assert call(engine, "resolve", uris=["/resources/A"])["data"][0]["item_uuid"] == "stable"
    assert declare(engine, "/resources/A", "_read", include_attrs=False)["item_attrs"] == [
        "kind=asset"
    ]
    declare(engine, "/resources/A/swap", "independent")
    with pytest.raises(ValueError, match="independent child Items"):
        call(engine, "delete_subtree", uri="/resources/A", preserve_item_root=True)
    assert call(engine, "resolve", uris=["/resources/A/swap/inner"])["data"][0]["item_uuids"] == [
        "independent",
        "stable",
    ]


def test_uuid_collision_and_concurrent_declaration_replays_are_atomic(tmp_path):
    path = str(tmp_path / "uuid-catalog")
    engine = CatalogEngine(path)
    nodes(engine, "/resources/A", "/resources/B")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda uuid: declare(engine, "/resources/A", uuid), ["first", "second"])
        )
    winner = results[0]["item_uuid"]
    assert results[1]["item_uuid"] == winner
    with pytest.raises(ValueError, match="already belongs to another root"):
        declare(engine, "/resources/B", winner)
    del engine
    gc.collect()
    engine = CatalogEngine(path)
    assert call(engine, "resolve", uris=["/resources/A"])["data"][0]["item_uuid"] == winner


def test_filter_scan_errors_release_snapshot_and_do_not_return_partial_counts(adapter):
    from openviking.storage.vectordb.store.store_manager import StoreManager
    from openviking.storage.vectordb.utils.str_to_uint64 import str_to_uint64

    root = "viking://resources/items/A"
    declare_adapter_item(adapter, root, "a", ["kind=asset"])
    record = {
        "id": "missing",
        "account_id": "a",
        "uri": f"{root}/member",
        "vector": [1, 0, 0, 0],
        "level": 2,
    }
    adapter.upsert([record])
    local = adapter.get_collection()._Collection__collection
    # Simulate a Store/index inconsistency; returning an incomplete count hides it.
    local.store_mgr.storage.delete([str(str_to_uint64("missing"))], StoreManager.CandsTable)
    with pytest.raises(RuntimeError, match="missing from Store snapshot"):
        adapter.search_item_groups(filter=Eq("account_id", "a"), filter_only=True)
    adapter.upsert([record])
    group = adapter.search_item_groups(filter=Eq("account_id", "a"), filter_only=True)["groups"][0]
    assert group["candidate_members_count"] == 1


@pytest.mark.parametrize("label", ["", "bad\0label"])
def test_filter_scan_keeps_the_semantic_candidate_label_contract(adapter, label):
    root = "viking://resources/items/A"
    declare_adapter_item(adapter, root, "a", [])
    adapter.get_collection().upsert_data(
        [{"id": label, "account_id": "a", "uri": f"{root}/member", "vector": [1, 0, 0, 0]}]
    )
    with pytest.raises(ValueError, match="candidate label"):
        adapter.search_item_groups(filter=Eq("account_id", "a"), filter_only=True)
