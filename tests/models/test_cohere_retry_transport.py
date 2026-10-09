# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""The direct HTTP adapter follows the same contract as SDK-backed adapters."""

import httpx
import pytest

from openviking.models.embedder.base import FailoverEmbedder
from openviking.models.embedder.cohere_embedders import CohereDenseEmbedder
from openviking.utils.model_call import is_model_call_error, model_workload


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "workload,credentials,error,expected",
    [
        ("offline", 1, 429, 4),
        ("offline", 2, 429, 4),
        ("online", 2, 429, 1),
        ("offline", 1, 401, 1),
        ("offline", 2, 400, 1),
        ("offline", 1, "timeout", 4),
    ],
)
async def test_cohere_http_budget(
    monkeypatch, asynchronous, workload, credentials, error, expected
):
    sent = []
    clients = []
    async_clients = []
    real_sync = httpx.Client
    real_async = httpx.AsyncClient

    def respond(request):
        sent.append(request)
        if error == "timeout":
            raise httpx.ReadTimeout("", request=request)
        return httpx.Response(error, json={"message": "fixture failure"})

    def sync_client(**kwargs):
        instance = real_sync(**kwargs, transport=httpx.MockTransport(respond))
        clients.append(instance)
        return instance

    def async_client(**kwargs):
        instance = real_async(**kwargs, transport=httpx.MockTransport(respond))
        async_clients.append(instance)
        return instance

    monkeypatch.setattr(httpx, "Client", sync_client)
    monkeypatch.setattr(httpx, "AsyncClient", async_client)
    monkeypatch.setattr("openviking.utils.model_call.random.uniform", lambda *_: 0)
    backends = [CohereDenseEmbedder(api_key=f"fixture-{i}") for i in range(credentials)]
    model = backends[0] if credentials == 1 else FailoverEmbedder(backends, ["first", "second"])

    try:
        with model_workload("add_resource", workload=workload), pytest.raises(Exception) as caught:
            if asynchronous:
                await model.embed_async("fixture")
            else:
                model.embed("fixture")
        assert is_model_call_error(caught.value)
        assert len(sent) == expected
    finally:
        for instance in async_clients:
            await instance.aclose()
        for instance in clients:
            instance.close()
