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
from openviking.storage.vikingdb_manager import VikingDBManagerProxy
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


async def _write_vector_file(client, uri: str, content: str) -> None:
    response = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": content,
            "wait": True,
            "processing_mode": "vectors_only",
        },
    )
    assert response.status_code == 200, response.text


async def test_mkdir_and_search_as_item_end_to_end(client, service, monkeypatch):
    fs = service.viking_fs
    vector_dim = fs._get_vector_store().vector_dim
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
        response = await client.post(
            "/api/v1/fs/mkdir",
            json={"uri": f"{root}/{name}", "as_item": True, "item_attrs": attrs},
        )
        assert response.status_code == 200, response.text
        receipt = response.json()["result"]
        assert receipt["uri"] == f"{root}/{name}"
        assert receipt["item_attrs"] == attrs
        UUID(receipt["item_uuid"])
        declared_uuids[receipt["uri"]] = receipt["item_uuid"]
        files = ["description.md", "asr.md"] if name == "A" else ["description.md"]
        for filename in files:
            await _write_vector_file(
                client,
                f"{root}/{name}/{filename}",
                "a boy running on the beach",
            )

    request = {
        "query": "boy on the beach",
        "target_uri": root,
        "search_as_item": {},
        "limit": 5,
    }
    response = await client.post("/api/v1/search/search", json=request)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert set(result) == {"memories", "resources", "skills", "total"}
    assert result["total"] == 2
    assert {item["uri"] for item in result["resources"]} == {f"{root}/A", f"{root}/B"}
    item_uuid_by_uri = {item["uri"]: item["item_uuid"] for item in result["resources"]}
    assert item_uuid_by_uri == declared_uuids
    for item in result["resources"]:
        assert item["context_type"] == "resource"
        assert item["level"] == 0
        assert "score" in item and "item_score" not in item
        UUID(item["item_uuid"])
        assert item["item_attrs"] == declarations[item["uri"].rsplit("/", 1)[-1]]
        assert item["item_extra_info"]["candidate_members_count"] == len(
            item["item_extra_info"]["candidate_members_uri"]
        )
        assert item["item_extra_info"]["candidate_members_count"] > 0

    response = await client.post(
        "/api/v1/search/find",
        json={
            **request,
            "filter": {
                "op": "must",
                "field": "item_attrs",
                "conds": ["asset_id=V2"],
            },
        },
    )
    assert response.status_code == 200, response.text
    filtered = response.json()["result"]
    assert filtered["total"] == 1
    assert filtered["resources"][0]["uri"] == f"{root}/B"

    response = await client.post(
        "/api/v1/search/search",
        json={
            "query": "",
            "target_uri": root,
            "search_as_item": {},
            "filter": {
                "op": "must",
                "field": "item_attrs",
                "conds": ["asset_id=V1"],
            },
        },
    )
    assert response.status_code == 200, response.text
    filter_only = response.json()["result"]
    assert filter_only["total"] == 1
    assert filter_only["resources"][0]["uri"] == f"{root}/A"
    assert filter_only["resources"][0]["score"] == 0.0

    response = await client.post(
        "/api/v1/search/search",
        json={"query": "beach", "target_uri": root},
    )
    assert response.status_code == 200, response.text
    assert "resources" in response.json()["result"]
    assert "items" not in response.json()["result"]

    moved_uri = f"{root}/A-moved"
    response = await client.post(
        "/api/v1/fs/mv",
        json={"from_uri": f"{root}/A", "to_uri": moved_uri},
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        "/api/v1/search/find",
        json={
            "query": "",
            "target_uri": root,
            "search_as_item": {},
            "filter": {
                "op": "must",
                "field": "item_attrs",
                "conds": ["asset_id=V1"],
            },
        },
    )
    assert response.status_code == 200, response.text
    moved = response.json()["result"]["resources"]
    assert [(item["uri"], item["item_uuid"]) for item in moved] == [
        (moved_uri, item_uuid_by_uri[f"{root}/A"])
    ]
    assert moved[0]["item_attrs"] == declarations["A"]

    response = await client.post(
        "/api/v1/fs/cp",
        json={
            "from_uri": f"{root}/B",
            "to_uri": f"{root}/B-copy",
            "recursive": True,
        },
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        "/api/v1/search/find",
        json={
            "query": "",
            "target_uri": root,
            "search_as_item": {},
            "filter": {
                "op": "must",
                "field": "item_attrs",
                "conds": ["asset_id=V2"],
            },
        },
    )
    assert response.status_code == 200, response.text
    copied = response.json()["result"]["resources"]
    assert [(item["uri"], item["item_uuid"]) for item in copied] == [
        (f"{root}/B", item_uuid_by_uri[f"{root}/B"])
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"uri": "viking://resources/items/false-empty", "item_attrs": {}},
        {
            "uri": "viking://resources/items/explicit-false",
            "as_item": False,
            "item_attrs": {},
        },
        {
            "uri": "viking://resources/items/null-attrs",
            "as_item": True,
            "item_attrs": None,
        },
        {
            "uri": "viking://resources/items/non-string",
            "as_item": True,
            "item_attrs": {"version": 1},
        },
    ],
)
async def test_mkdir_rejects_invalid_item_declarations(client, payload):
    response = await client.post("/api/v1/fs/mkdir", json=payload)
    assert response.status_code == 400, response.text


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


@pytest.mark.parametrize(
    "payload",
    [
        {"search_as_item": {"aggregation": "avg"}},
        {"search_as_item": {}, "read_content": True},
        {"search_as_item": {}, "include_provenance": True},
        {"search_as_item": {}, "events_time_decay_protection": "2026-01-01"},
    ],
)
@pytest.mark.parametrize("endpoint", ["find", "search"])
async def test_item_search_request_validation(client, payload, endpoint):
    response = await client.post(
        f"/api/v1/search/{endpoint}",
        json={"query": "beach", **payload},
    )
    assert response.status_code == 400, response.text


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


@pytest.mark.parametrize(
    "identity", [{"item_uuid": "caller-controlled"}, {"item_uuids": ["caller-controlled"]}]
)
async def test_mkdir_rejects_external_item_uuid(client, identity):
    response = await client.post(
        "/api/v1/fs/mkdir",
        json={
            "uri": "viking://resources/items/external-uuid",
            "as_item": True,
            **identity,
        },
    )
    assert response.status_code == 400, response.text
    assert "item_uuid" in response.text


async def test_mkdir_preserves_legacy_ignored_fields(client):
    uri = "viking://resources/legacy-mkdir-extra"
    response = await client.post(
        "/api/v1/fs/mkdir",
        json={"uri": uri, "request_id": "caller-trace-1"},
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
    proxy = VikingDBManagerProxy(service.viking_fs._get_vector_store(), ctx)
    resolved = await proxy.catalog_execute({"op": "resolve", "uris": [root_uri]})
    item_uuid = resolved["data"][0]["item_uuid"]
    declaration = await proxy.catalog_execute({"op": "query_items", "item_uuids": [item_uuid]})
    assert declaration["data"] == [
        {
            "item_uuid": item_uuid,
            "uri": root_uri,
            "item_attrs": ["source=sample"],
        }
    ]


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
@pytest.mark.parametrize(("empty", "statistics"), [(False, True), (False, False), (True, True)])
async def test_item_groups_reuse_classified_results_without_content_reads(
    client, service, monkeypatch, endpoint, empty, statistics
):
    roots = [
        "/user/test_user/memories/entities/M",
        "/resources/A",
        "/user/test_user/skills/S",
        "/resources/B",
    ]
    groups = [
        {
            "entity_id": f"uuid-{i}",
            "entity_uri": root,
            "entity_attrs": [f"name={i}"],
            "entity_score": 4.0 - i,
            "candidate_members_count": 7,
            "candidate_members_truncated": True,
            "candidate_members_uri": ["viking://" + root.lstrip("/") + "/member.md"],
        }
        for i, root in enumerate(roots)
    ]
    if not statistics:
        for group in groups:
            group.pop("candidate_members_count")
            group.pop("candidate_members_truncated")

    async def ordered_groups(_ctx, **kwargs):
        return {"groups": [] if empty else groups[: kwargs["limit"]]}

    storage = service.viking_fs._get_vector_store()
    grouping = AsyncMock(side_effect=ordered_groups)
    monkeypatch.setattr(storage, "search_items_in_tenant", grouping)
    embedder = Mock(side_effect=AssertionError("Filter-only Item queries need no embedder"))
    monkeypatch.setattr(service.viking_fs, "_get_embedder", embedder)
    scope = AsyncMock(wraps=service.viking_fs._ensure_retrieval_scope)
    monkeypatch.setattr(service.viking_fs, "_ensure_retrieval_scope", scope)
    no_read = AsyncMock(side_effect=AssertionError("Item results must not read content"))
    monkeypatch.setattr(service.viking_fs, "read_file", no_read)
    monkeypatch.setattr(service.viking_fs, "_read_directory_summary", no_read)
    lookup = AsyncMock(side_effect=AssertionError("Native root metadata needs no UUID lookup"))
    monkeypatch.setattr(storage, "catalog_execute", lookup)
    for limit in (4, 2):
        response = await client.post(
            f"/api/v1/search/{endpoint}",
            json={
                "query": "",
                "target_uri": ["viking://resources", "viking://user/test_user"],
                "filter": {"op": "must", "field": "level", "conds": [2]},
                "limit": limit,
                "search_as_item": {},
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert set(result) == {"memories", "resources", "skills", "total"}
        assert result["total"] == (0 if empty else limit)
        for bucket, indices, kind in [
            ("memories", [0], "memory"),
            ("resources", [1, 3], "resource"),
            ("skills", [2], "skill"),
        ]:
            expected = [] if empty else [i for i in indices if i < limit]
            assert [item["uri"] for item in result[bucket]] == [
                "viking://" + roots[i].lstrip("/") for i in expected
            ]
            for item, i in zip(result[bucket], expected, strict=True):
                assert item["context_type"] == kind
                assert item["score"] == 4.0 - i
                assert item["level"] == 0 and item["abstract"] == ""
                assert item["item_attrs"] == {"name": str(i)}
                if statistics:
                    assert item["item_extra_info"] == {
                        "candidate_members_count": 7,
                        "candidate_members_uri": [
                            "viking://" + roots[i].lstrip("/") + "/member.md"
                        ],
                        "candidate_members_truncated": True,
                    }
                else:
                    assert "item_extra_info" not in item
                assert "item_score" not in item
        assert grouping.await_args.kwargs["limit"] == limit
    assert grouping.await_count == 2
    assert scope.await_count == 4  # Two targets per query, checked once each.
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


async def test_item_keywords_do_not_probe_fulltext(client, service, monkeypatch):
    fulltext = AsyncMock(side_effect=AssertionError("Item grouping does not use BM25"))
    monkeypatch.setattr(service.viking_fs, "_collection_has_fulltext", fulltext)
    response = await client.post(
        "/api/v1/search/search",
        json={"query": "a Skill", "search_type": "keywords", "search_as_item": {}},
    )
    assert response.status_code == 501, response.text
    fulltext.assert_not_awaited()
