# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Provider regressions through real SDK parsing and synthetic HTTP transports."""

from __future__ import annotations

import httpx
import openai
import pytest
import volcenginesdkarkruntime

from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder
from openviking.models.embedder.volcengine_embedders import VolcengineDenseEmbedder
from openviking.models.model_error import ModelError


@pytest.mark.parametrize("provider", ["openai", "ark"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "usage,failed",
    [
        (None, False),
        ({"total_tokens": 19}, False),
        ({"prompt_tokens": 0, "total_tokens": 0}, False),
        ({"prompt_tokens": 11, "total_tokens": 11}, False),
        (None, True),
    ],
)
async def test_sdk_usage_boundary(monkeypatch, provider, asynchronous, usage, failed):
    events, requests = [], []
    monkeypatch.setattr(
        EventMetricDataSource,
        "_emit",
        staticmethod(lambda name, payload: events.append((name, payload))),
    )

    def handle(request):
        requests.append(request)
        if failed:
            return httpx.Response(503, json={"error": {"message": "synthetic unavailable"}})
        body = {
            "id": "synthetic",
            "object": "list",
            "model": "synthetic",
            "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
        }
        if usage is not None:
            body["usage"] = usage
        return httpx.Response(200, json=body)

    if provider == "openai":
        embedder = OpenAIDenseEmbedder("text-embedding-3-small", api_key="synthetic", dimension=2)
        sdk, async_sdk = openai.OpenAI, openai.AsyncOpenAI
    else:
        embedder = VolcengineDenseEmbedder(
            "synthetic", api_key="synthetic", dimension=2, input_type="text"
        )
        sdk, async_sdk = volcenginesdkarkruntime.Ark, volcenginesdkarkruntime.AsyncArk
    embedder.client.close()
    embedder.client = sdk(
        api_key="synthetic",
        base_url="https://model.example.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    asynchronous_client = async_sdk(
        api_key="synthetic",
        base_url="https://model.example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setattr(embedder, "_get_async_client", lambda: asynchronous_client)
    try:
        if failed:
            with pytest.raises((ModelError, RuntimeError)):
                if asynchronous:
                    await embedder.embed_async("synthetic")
                else:
                    embedder.embed("synthetic")
        else:
            result = (
                await embedder.embed_async("synthetic")
                if asynchronous
                else embedder.embed("synthetic")
            )
            assert result.dense_vector == [1.0, 0.0]
    finally:
        embedder.client.close()
        await asynchronous_client.close()
    assert len(requests) == 1
    [event] = [value for name, value in events if name == "embedding.call"]
    expected = usage.get("prompt_tokens") if usage else None
    assert event["prompt_tokens"] == expected
    assert event["completion_tokens"] == (0 if usage is not None and not failed else None)
    assert event["outcome"] == ("failed" if failed else "success")
    assert event["usage_complete"] is (expected is not None and not failed)
    assert event["duration_seconds"] >= 0
    assert event["attempt_id"]
