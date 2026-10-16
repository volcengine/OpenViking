# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VikingDB sync/async request and failure contracts."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from openviking.models.embedder.vikingdb_embedders import VikingDBEmbedder
from openviking.models.model_error import ModelError, ModelErrorKind


@pytest.mark.parametrize("output", ["dense", "dense_sparse"])
@pytest.mark.parametrize("use_async", [False, True])
async def test_vikingdb_single_model_output(output, use_async):
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://example.test"),
        json={
            "code": "Success",
            "result": {
                "data": [{"dense": [0.1, 0.2], "sparse": {"hello": 0.5}}],
                "token_usage": {"bge-m3__default": {"prompt_tokens": 3, "completion_tokens": 0}},
            },
        },
    )
    embedder = VikingDBEmbedder(
        model_name="bge-m3",
        model_version="default",
        ak="ak",
        sk="sk",
        output=output,
        dimension=1024,
    )
    if use_async:
        client = MagicMock(request=AsyncMock(return_value=response))
        with patch.object(embedder._async_client_cache, "get", return_value=client):
            result = await embedder.embed_async("hello", is_query=True)
        request = client.request.call_args.kwargs
        body = json.loads(request["content"])
        assert request["url"].startswith(f"https://{embedder.client.host}/")
    else:
        with patch.object(embedder.client, "do_req", return_value=response) as call:
            result = embedder.embed("hello", is_query=True)
        body = call.call_args.kwargs["req_body"]
    assert body["data"] == [{"text": "hello"}]
    assert body["dense_model"] == {"name": "bge-m3", "version": "default", "dim": 1024}
    assert body.get("sparse_model") == (
        {"name": "bge-m3", "version": "default"} if output == "dense_sparse" else None
    )
    assert result.dense_vector == [0.1, 0.2]
    assert result.sparse_vector == ({"hello": 0.5} if output == "dense_sparse" else None)
    embedder.close()


@pytest.mark.parametrize("use_async", [False, True])
async def test_vikingdb_http_failure_is_not_an_empty_vector(use_async):
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.test"))
    embedder = VikingDBEmbedder(model_name="bge-m3", ak="ak", sk="sk", dimension=1024)
    with pytest.raises(ModelError) as error:
        if use_async:
            with patch.object(
                embedder._async_client_cache,
                "get",
                return_value=MagicMock(request=AsyncMock(return_value=response)),
            ):
                await embedder.embed_async("hello")
        else:
            with patch.object(embedder.client, "do_req", return_value=response):
                embedder.embed("hello")
    assert error.value.kind == ModelErrorKind.RATE_LIMIT_ERROR
    assert error.value.status_code == 429
    embedder.close()
