# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking_cli.client.http import AsyncHTTPClient


class _FakeHTTP:
    def __init__(self):
        self.calls = []

    async def post(self, path, json=None, files=None):
        self.calls.append({"path": path, "json": json, "files": files})
        return object()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "path"),
    [
        ("find", "/api/v1/search/find"),
        ("search", "/api/v1/search/search"),
    ],
)
async def test_tags_filter_sanitizes_empty_and_duplicate_tags(method_name: str, path: str):
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = _FakeHTTP()
    client._http = fake_http
    client._handle_response_data = lambda _resp: {"result": {}}

    method = getattr(client, method_name)
    await method(query="hello", tags="alpha, ,beta,alpha,,  ")

    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    assert call["path"] == path

    req_filter = call["json"]["filter"]
    assert req_filter["op"] == "and"
    assert req_filter["conds"] == [
        {"op": "contains", "field": "tags", "substring": "alpha"},
        {"op": "contains", "field": "tags", "substring": "beta"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["find", "search"])
async def test_tags_filter_rejects_all_empty_tags(method_name: str):
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = _FakeHTTP()
    client._http = fake_http
    client._handle_response_data = lambda _resp: {"result": {}}

    method = getattr(client, method_name)
    with pytest.raises(ValueError, match="must contain at least one non-empty tag"):
        await method(query="hello", tags=" ,  , ")

    assert fake_http.calls == []
