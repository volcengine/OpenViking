# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest
from openviking_sdk import AsyncHTTPClient


@pytest.mark.asyncio
async def test_search_as_item_and_declaration_payloads():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    item = {
        "item_uuid": "uuid-a",
        "uri": "viking://resources/A",
        "item_score": 0.8,
        "item_attrs": {},
        "item_extra_info": {
            "candidate_members_count": 1,
            "candidate_members_uri": ["viking://resources/A/a.md"],
        },
    }
    client._handle_response_data = lambda response: {"result": {"items": [item], "total": 1}}

    result = await client.search("beach", options={"search_as_item": {}})
    payload = client._request.await_args.kwargs["json"]
    assert payload["search_as_item"] == {}
    assert result["items"] == [item]

    await client.find("beach", options={"search_as_item": {"aggregation": "sum"}})
    assert client._request.await_args.kwargs["json"]["search_as_item"] == {"aggregation": "sum"}

    client._handle_response = lambda response: {}
    await client.mkdir("viking://resources/A", as_item=True, item_attrs={})
    assert client._request.await_args.kwargs["json"] == {
        "uri": "viking://resources/A",
        "as_item": True,
        "item_attrs": {},
    }

    await client.add_resource(
        "https://example.com/a",
        options={"as_item": True, "item_attrs": {"asset_id": "V1"}},
    )
    assert client._request.await_args.kwargs["json"]["as_item"] is True
    assert client._request.await_args.kwargs["json"]["item_attrs"] == {"asset_id": "V1"}


@pytest.mark.asyncio
async def test_write_options_do_not_expose_item_declarations():
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._request = AsyncMock(return_value=object())
    with pytest.raises(TypeError, match="Unknown option 'item_id'"):
        await client.write(
            "viking://resources/a.md",
            "a",
            options={"item_id": "custom"},
        )
    with pytest.raises(TypeError, match="Unknown option 'as_item'"):
        await client.write(
            "viking://resources/a.md",
            "a",
            options={"as_item": True},
        )
