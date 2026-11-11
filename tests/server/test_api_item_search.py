# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.server.identity import RequestContext, Role
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
    for name, attrs in declarations.items():
        response = await client.post(
            "/api/v1/fs/mkdir",
            json={"uri": f"{root}/{name}", "as_item": True, "item_attrs": attrs},
        )
        assert response.status_code == 200, response.text
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
    assert result["total"] == 2
    assert {item["uri"] for item in result["items"]} == {f"{root}/A", f"{root}/B"}
    for item in result["items"]:
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
    assert filtered["items"][0]["uri"] == f"{root}/B"

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
    assert filter_only["items"][0]["uri"] == f"{root}/A"
    assert filter_only["items"][0]["item_score"] == 0.0

    response = await client.post(
        "/api/v1/search/search",
        json={"query": "beach", "target_uri": root},
    )
    assert response.status_code == 200, response.text
    assert "resources" in response.json()["result"]
    assert "items" not in response.json()["result"]


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


async def test_mkdir_item_retry_rules(client):
    uri = "viking://resources/items/retry"
    attrs = {"asset_id": "V1"}
    for payload in (
        {"uri": uri, "as_item": True, "item_attrs": attrs},
        {"uri": uri, "as_item": True},
        {"uri": uri, "as_item": True, "item_attrs": attrs},
    ):
        response = await client.post("/api/v1/fs/mkdir", json=payload)
        assert response.status_code == 200, response.text

    response = await client.post(
        "/api/v1/fs/mkdir",
        json={"uri": uri, "as_item": True, "item_attrs": {}},
    )
    assert response.status_code == 400, response.text
    assert "cannot change" in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"search_as_item": {"aggregation": "avg"}},
        {"search_as_item": {}, "read_content": True},
        {"search_as_item": {}, "events_time_decay_protection": "2026-01-01"},
    ],
)
async def test_item_search_request_validation(client, payload):
    response = await client.post(
        "/api/v1/search/search",
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
