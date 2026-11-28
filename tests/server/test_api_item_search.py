# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.server.identity import RequestContext, Role
from openviking.service import fs_service as fs_module
from openviking.storage.abstract_overview import body_for_preview
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


async def _post_result(client, path, payload):
    response = await client.post(f"/api/v1/{path}", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def test_mkdir_and_search_as_item_end_to_end(client, service, monkeypatch):
    vector_dim = service.viking_fs._get_vector_store().vector_dim
    monkeypatch.setattr(
        "openviking.models.embedder.base.embed_compat",
        AsyncMock(return_value=EmbedResult(dense_vector=[0.1] * vector_dim)),
    )
    root = "viking://resources/item-test"
    declarations = {
        "A": {"asset_id": "V1", "kind": "video"},
        "B": {"asset_id": "V2"},
    }
    declared_uuids = {}
    for name, attrs in declarations.items():
        uri = f"{root}/{name}"
        receipt = await _post_result(
            client, "fs/mkdir", {"uri": uri, "as_item": True, "item_attrs": attrs}
        )
        assert receipt["uri"] == uri and receipt["item_attrs"] == attrs
        UUID(receipt["item_uuid"])
        declared_uuids[uri] = receipt["item_uuid"]
        files = ("description.md", "asr.md") if name == "A" else ("description.md",)
        for filename in files:
            await _post_result(
                client,
                "content/write",
                {
                    "uri": f"{uri}/{filename}",
                    "content": "a boy running on the beach",
                    "wait": True,
                    "processing_mode": "vectors_only",
                },
            )

    listing = await client.get("/api/v1/fs/ls", params={"uri": root, "output": "original"})
    listing.raise_for_status()
    entries = listing.json()["result"]
    assert sorted(entry["uri"] for entry in entries) == sorted(declared_uuids)
    item_fields = {"as_item", "item_uuid", "item_uuids", "item_attrs", "item_extra_info"}
    assert all(entry["isDir"] and item_fields.isdisjoint(entry) for entry in entries)

    request = {"query": "boy on the beach", "target_uri": root, "search_as_item": {}, "limit": 5}
    filters = {
        name: {"op": "must", "field": "item_attrs", "conds": [f"asset_id={attrs['asset_id']}"]}
        for name, attrs in declarations.items()
    }

    async def retrieve(endpoint, **options):
        return await _post_result(client, f"search/{endpoint}", {**request, **options})

    result = await retrieve("search")
    assert set(result) == {"memories", "resources", "skills", "total"}
    assert result["total"] == 2
    assert {item["uri"]: item["item_uuid"] for item in result["resources"]} == declared_uuids
    for item in result["resources"]:
        assert item["context_type"] == "resource" and item["level"] == 0
        assert "score" in item and "item_score" not in item
        assert item["item_attrs"] == declarations[item["uri"].rsplit("/", 1)[-1]]
        evidence = item["item_extra_info"]
        assert evidence["candidate_members_count"] == len(evidence["candidate_members_uri"]) > 0

    filtered = await retrieve("find", filter=filters["B"])
    assert filtered["total"] == 1
    assert [item["uri"] for item in filtered["resources"]] == [f"{root}/B"]

    # A similarity threshold has no effect in filter-only Item retrieval.
    filter_request = {"query": "", "filter": filters["A"], "score_threshold": 100}
    filter_only = await retrieve("search", **filter_request)
    assert filter_only == await retrieve("find", **filter_request)
    assert filter_only["total"] == 1
    assert [(item["uri"], item["score"]) for item in filter_only["resources"]] == [
        (f"{root}/A", 0.0)
    ]

    ordinary = await _post_result(client, "search/search", {"query": "beach", "target_uri": root})
    assert "resources" in ordinary and "items" not in ordinary

    moved_uri = f"{root}/A-moved"
    await _post_result(client, "fs/mv", {"from_uri": f"{root}/A", "to_uri": moved_uri})
    moved = await retrieve("find", **filter_request)
    assert [
        (item["uri"], item["item_uuid"], item["item_attrs"]) for item in moved["resources"]
    ] == [(moved_uri, declared_uuids[f"{root}/A"], declarations["A"])]

    await _post_result(
        client,
        "fs/cp",
        {"from_uri": f"{root}/B", "to_uri": f"{root}/B-copy", "recursive": True},
    )
    copied = await retrieve("find", query="", filter=filters["B"])
    assert [(item["uri"], item["item_uuid"]) for item in copied["resources"]] == [
        (f"{root}/B", declared_uuids[f"{root}/B"])
    ]


async def test_mkdir_rejects_invalid_item_declarations(client):
    for options, field in (
        ({"item_attrs": {}}, "item_attrs"),
        ({"as_item": False, "item_attrs": {}}, "item_attrs"),
        ({"as_item": True, "item_attrs": None}, "item_attrs"),
        ({"as_item": True, "item_attrs": {"version": 1}}, "item_attrs"),
        ({"as_item": True, "item_uuid": "caller-controlled"}, "item_uuid"),
        ({"as_item": True, "item_uuids": ["caller-controlled"]}, "item_uuid"),
    ):
        response = await client.post(
            "/api/v1/fs/mkdir",
            json={"uri": "viking://resources/items/invalid", **options},
        )
        assert response.status_code == 400, f"{options}: {response.text}"
        assert field in response.text


async def test_mkdir_item_retry_rules(client, service):
    uri = "viking://resources/items/retry"
    attrs = {"asset_id": "V1"}
    owner = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)
    original = None
    item_uuid = None
    for payload in (
        {"uri": uri, "as_item": True, "item_attrs": attrs, "description": "Keep this summary"},
        {"uri": uri, "as_item": True, "description": "Do not overwrite the summary"},
        {"uri": uri, "as_item": True, "item_attrs": attrs},
    ):
        response = await client.post("/api/v1/fs/mkdir", json=payload)
        assert response.status_code == 200, response.text
        summary = await service.viking_fs.read_file(uri + "/.abstract.md", ctx=owner)
        if original is None:
            original = summary
            item_uuid = response.json()["result"]["item_uuid"]
        assert summary == original
        assert body_for_preview(summary) == "Keep this summary"
        assert response.json()["result"]["item_uuid"] == item_uuid

    response = await client.post(
        "/api/v1/fs/mkdir",
        json={"uri": uri, "as_item": True, "item_attrs": {}},
    )
    assert response.status_code == 400, response.text
    assert "cannot change" in response.text


@pytest.mark.parametrize("failure_stage", ["abstract_write", "index_enqueue"])
async def test_mkdir_item_retry_recovers_failed_initialization(
    client, service, monkeypatch, failure_stage
):
    fs = service.viking_fs
    owner = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)
    uri = f"viking://resources/items/failed-mkdir-{failure_stage}"
    payload = {
        "uri": uri,
        "as_item": True,
        "item_attrs": {"asset_id": "V1"},
        "description": "Original summary",
    }
    abstract_uri = uri + "/.abstract.md"
    vectorize = fs_module.vectorize_directory_meta

    with monkeypatch.context() as failure:
        failing_step = AsyncMock(side_effect=RuntimeError("injected initialization failure"))
        if failure_stage == "abstract_write":
            failure.setattr(fs, "write_file", failing_step)
        else:
            failure.setattr(fs_module, "vectorize_directory_meta", failing_step)
        response = await client.post("/api/v1/fs/mkdir", json=payload)
        assert response.status_code == 500, response.text
        assert failing_step.await_count == 1
        if failure_stage == "index_enqueue":
            # A second enqueue failure must remain an error even with an existing summary.
            response = await client.post("/api/v1/fs/mkdir", json=payload)
            assert response.status_code == 500, response.text
            assert failing_step.await_count == 2

    assert await fs.exists(abstract_uri, ctx=owner) is (failure_stage == "index_enqueue")
    original_summary = (
        await fs.read_file(abstract_uri, ctx=owner) if failure_stage == "index_enqueue" else None
    )
    identity = await fs._get_vector_store().catalog_execute(
        {"op": "resolve", "uris": [uri]}, ctx=owner
    )
    member_uri = uri + "/member.txt"
    await fs.write_file(member_uri, "Existing member", ctx=owner)
    resumed_enqueue = AsyncMock(wraps=vectorize)
    monkeypatch.setattr(fs_module, "vectorize_directory_meta", resumed_enqueue)
    response = await client.post("/api/v1/fs/mkdir", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["item_uuid"] == identity["data"][0]["item_uuid"]
    assert response.json()["result"]["item_attrs"] == {"asset_id": "V1"}
    summary = await fs.read_file(abstract_uri, ctx=owner)
    assert body_for_preview(summary) == "Original summary"
    if original_summary is not None:
        assert summary == original_summary
    assert await fs.read_file(member_uri, ctx=owner) == "Existing member"
    resumed_enqueue.assert_awaited_once()
    assert resumed_enqueue.await_args.kwargs["abstract"] == "Original summary"


@pytest.mark.parametrize("endpoint", ["find", "search"])
async def test_item_search_request_validation(client, endpoint):
    for options in (
        {"search_as_item": {"aggregation": "avg"}},
        {"search_as_item": {}, "read_content": True},
        {"search_as_item": {}, "include_provenance": True},
        {"search_as_item": {}, "events_time_decay_protection": "2026-01-01"},
    ):
        response = await client.post(
            f"/api/v1/search/{endpoint}",
            json={"query": "beach", **options},
        )
        assert response.status_code == 400, f"{options}: {response.text}"


async def test_write_does_not_accept_item_declaration_fields(client):
    for field, value in (("as_item", True), ("item_attrs", {}), ("item_uuid", "external")):
        response = await client.post(
            "/api/v1/content/write",
            json={
                "uri": f"viking://resources/items/{field}.md",
                "content": "content",
                field: value,
            },
        )
        assert response.status_code == 400, response.text


async def test_mkdir_preserves_legacy_ignored_fields(client):
    uri = "viking://resources/legacy-mkdir-extra"
    response = await client.post(
        "/api/v1/fs/mkdir",
        json={
            "uri": uri,
            "request_id": "caller-trace-1",
            "item_uuid": "legacy-extra-field",
            "item_uuids": ["legacy-extra-field"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"uri": uri}


async def test_add_resource_declares_the_actual_resolved_root(service, sample_markdown_file):
    ctx = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)
    parent = "viking://resources/import-items"
    await service.fs.mkdir(parent, ctx)
    result = await service.resources.add_resource(
        str(sample_markdown_file),
        parent=parent,
        ctx=ctx,
        wait=True,
        as_item=True,
        item_attrs={"source": "sample"},
    )
    root_uri = result["root_uri"]
    storage = service.viking_fs._get_vector_store()
    resolved = await storage.catalog_execute({"op": "resolve", "uris": [root_uri]}, ctx=ctx)
    item_uuid = resolved["data"][0]["item_uuid"]
    declaration = await storage.catalog_execute(
        {"op": "declare_item", "uri": root_uri, "item_uuids": [item_uuid]}, ctx=ctx
    )
    assert declaration["uri"] == root_uri
    assert declaration["item_uuid"] == item_uuid
    assert declaration["item_attrs"] == ["source=sample"]


async def test_add_resource_rejects_attrs_when_as_item_is_false(service, sample_markdown_file):
    ctx = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)
    with pytest.raises(InvalidArgumentError, match="only be provided"):
        await service.resources.add_resource(
            str(sample_markdown_file),
            ctx=ctx,
            as_item=False,
            item_attrs={},
        )


@pytest.mark.parametrize("endpoint", ["find", "search"])
async def test_item_groups_reuse_classified_results_without_content_reads(
    client, service, monkeypatch, endpoint
):
    roots = [
        "/user/test_user/memories/entities/M",
        "/resources/A",
        "/user/test_user/skills/S",
        "/resources/B",
        "/resources/C",
    ]
    groups = [
        {
            "entity_id": f"uuid-{i}",
            "entity_uri": root,
            "entity_attrs": [f"name={i}"],
            "entity_score": 4.0 - i,
            "candidate_members_uri": ["viking://" + root.lstrip("/") + "/member.md"],
        }
        for i, root in enumerate(roots)
    ]
    stats = {"candidate_members_count": 7, "candidate_members_truncated": True}
    groups[1].update(stats)

    async def ordered_groups(_ctx, **kwargs):
        return {"groups": groups[: kwargs["limit"]]}

    fs = service.viking_fs
    storage = fs._get_vector_store()
    grouping = AsyncMock(side_effect=ordered_groups)
    monkeypatch.setattr(storage, "search_items_in_tenant", grouping)
    embedder = Mock(side_effect=AssertionError("Filter-only Item queries need no embedder"))
    monkeypatch.setattr(fs, "_get_embedder", embedder)
    scope = AsyncMock(wraps=fs._ensure_retrieval_scope)
    monkeypatch.setattr(fs, "_ensure_retrieval_scope", scope)
    no_read = AsyncMock(side_effect=AssertionError("Item results must not read content"))
    monkeypatch.setattr(fs, "read_file", no_read)
    monkeypatch.setattr(fs, "_read_directory_summary", no_read)
    lookup = AsyncMock(side_effect=AssertionError("Native root metadata needs no UUID lookup"))
    monkeypatch.setattr(storage, "catalog_execute", lookup)

    result = await _post_result(
        client,
        f"search/{endpoint}",
        {
            "query": "",
            "target_uri": ["viking://resources", "viking://user/test_user"],
            "filter": {"op": "must", "field": "level", "conds": [2]},
            "limit": 4,
            "search_as_item": {},
        },
    )
    assert set(result) == {"memories", "resources", "skills", "total"}
    assert result["total"] == 4
    for bucket, kind, indices in (
        ("memories", "memory", (0,)),
        ("resources", "resource", (1, 3)),
        ("skills", "skill", (2,)),
    ):
        for item, i in zip(result[bucket], indices, strict=True):
            assert item["uri"] == "viking://" + roots[i].lstrip("/")
            assert item["item_uuid"] == groups[i]["entity_id"]
            assert item["context_type"] == kind
            assert item["score"] == 4.0 - i
            assert item["level"] == 0 and item["abstract"] == ""
            assert item["item_attrs"] == {"name": str(i)}
            assert "item_score" not in item
    assert result["resources"][0]["item_extra_info"] == {
        **stats,
        "candidate_members_uri": groups[1]["candidate_members_uri"],
    }
    assert "item_extra_info" not in result["resources"][1]
    assert all("item_extra_info" not in result[bucket][0] for bucket in ("memories", "skills"))
    grouping.assert_awaited_once()
    assert grouping.await_args.kwargs["limit"] == 4
    assert scope.await_count == 2  # Each retrieval target is checked once.
    embedder.assert_not_called()
    no_read.assert_not_awaited()
    lookup.assert_not_awaited()


def test_classified_item_metadata_round_trip_keeps_ordinary_hits_unchanged():
    from openviking_cli.retrieve import ContextType, FindResult, MatchedContext

    ordinary = MatchedContext(uri="viking://resources/plain.md", context_type=ContextType.RESOURCE)
    item = MatchedContext(
        uri="viking://user/owner/skills/S",
        context_type=ContextType.SKILL,
        level=0,
        score=1.7,
        item_uuid="uuid-skill",
        item_attrs={},
        item_extra_info={"candidate_members_count": 0, "candidate_members_uri": []},
    )
    serialized = FindResult(memories=[], resources=[ordinary], skills=[item]).to_dict()
    assert "item_uuid" not in serialized["resources"][0]
    assert "item_attrs" not in serialized["resources"][0]
    assert "item_extra_info" not in serialized["resources"][0]
    assert serialized["skills"][0]["item_attrs"] == {}
    assert serialized["total"] == 2
    assert FindResult.from_dict(serialized).to_dict() == serialized


async def test_item_search_does_not_load_unused_session_context(service, monkeypatch):
    load_context = AsyncMock(
        side_effect=AssertionError("Item queries must not read session content")
    )
    monkeypatch.setattr(service.search, "is_intent_enabled", lambda: True)
    storage = service.viking_fs._get_vector_store()
    monkeypatch.setattr(storage, "search_items_in_tenant", AsyncMock(return_value={"groups": []}))
    ctx = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)
    result = await service.search.search(
        "find an item",
        ctx=ctx,
        target_uri="viking://resources",
        session=SimpleNamespace(get_context_for_search=load_context),
        search_as_item={},
    )
    assert result.to_dict() == {"memories": [], "resources": [], "skills": [], "total": 0}
    load_context.assert_not_awaited()


@pytest.mark.parametrize("item_mode", [False, True])
async def test_search_route_only_loads_a_session_for_ordinary_search(
    client, service, monkeypatch, item_mode
):
    session = SimpleNamespace(load=AsyncMock())
    factory = Mock(return_value=session)
    monkeypatch.setattr(service.sessions, "session", factory)
    monkeypatch.setattr(service.search, "is_intent_enabled", lambda: True)
    search = AsyncMock(return_value={"memories": [], "resources": [], "skills": [], "total": 0})
    monkeypatch.setattr(service.search, "search", search)
    payload = {"query": "find a Skill", "session_id": "unused"}
    if item_mode:
        payload["search_as_item"] = {}
    response = await client.post("/api/v1/search/search", json=payload)
    assert response.status_code == 200, response.text
    if item_mode:
        factory.assert_not_called()
        session.load.assert_not_awaited()
        assert search.await_args.kwargs["session"] is None
    else:
        factory.assert_called_once()
        session.load.assert_awaited_once()
        assert search.await_args.kwargs["session"] is session


async def test_item_keywords_reach_the_backend_without_embedding(client, service, monkeypatch):
    grouping = AsyncMock(return_value={"groups": []})
    monkeypatch.setattr(service.viking_fs._get_vector_store(), "search_items_in_tenant", grouping)
    embedding = AsyncMock(side_effect=AssertionError("Keyword search must not embed"))
    monkeypatch.setattr("openviking.models.embedder.base.embed_compat", embedding)
    args = {"bm25_k1": 1.2, "bm25_b": 0.0, "post_process_input_limit": 32}
    result = await _post_result(
        client,
        "search/search",
        {"query": "a Skill", "search_type": "keywords", "search_as_item": {}, "args": args},
    )
    assert result == {"memories": [], "resources": [], "skills": [], "total": 0}
    assert grouping.await_args.kwargs["keywords_query"] == "a Skill"
    assert grouping.await_args.kwargs["keywords_args"] == args
    assert grouping.await_args.kwargs["query_vector"] is None
    embedding.assert_not_awaited()
