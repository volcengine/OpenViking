# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Optional Gemini SDK contract: count HTTP sends through the real SDK."""

import httpx
import pytest

pytest.importorskip("google.genai")

from google import genai

from openviking.models.embedder.base import FailoverEmbedder
from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder
from openviking.utils.model_call import is_model_call_error, model_workload


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "workload,credentials,status,recover_after,expected",
    [
        ("offline", 1, 429, None, 4),
        ("offline", 2, 429, None, 4),
        ("online", 2, 429, None, 1),
        ("offline", 1, 401, None, 1),
        ("offline", 2, 400, None, 1),
        ("offline", 2, 429, 1, 2),
    ],
)
async def test_gemini_http_budget(
    monkeypatch, asynchronous, workload, credentials, status, recover_after, expected
):
    sent = []
    clients = []
    real_client = genai.Client

    def respond(request):
        sent.append(request)
        if recover_after is not None and len(sent) > recover_after:
            return httpx.Response(200, json={"embeddings": [{"values": [0.6, 0.8]}]})
        message = {429: "Rate limit exceeded", 401: "Unauthorized", 400: "Invalid request"}[status]
        return httpx.Response(status, json={"error": {"code": status, "message": message}})

    def client(**kwargs):
        options = kwargs["http_options"]
        assert options.retry_options.attempts == 1
        options.httpx_client = httpx.Client(transport=httpx.MockTransport(respond))
        options.httpx_async_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        instance = real_client(**kwargs)
        clients.append(instance)
        return instance

    monkeypatch.setattr(genai, "Client", client)
    monkeypatch.setattr("openviking.utils.model_call.random.uniform", lambda *_: 0)
    backends = [
        GeminiDenseEmbedder("fixture", api_key=f"fixture-{i}", dimension=2)
        for i in range(credentials)
    ]
    model = backends[0] if credentials == 1 else FailoverEmbedder(backends, ["first", "second"])

    async def invoke():
        return await model.embed_async("fixture") if asynchronous else model.embed("fixture")

    try:
        with model_workload("add_resource", workload=workload):
            if recover_after is None:
                with pytest.raises(Exception) as error:
                    await invoke()
                assert is_model_call_error(error.value)
            else:
                result = await invoke()
                assert result.dense_vector == pytest.approx([0.6, 0.8])
                assert model.active_credential_id == "second"
        assert len(sent) == expected
    finally:
        for instance in clients:
            await instance.aio.aclose()
            instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_gemini_invalid_key_400_advances_to_backup(monkeypatch, asynchronous):
    sent = []
    clients = []
    real_client = genai.Client

    def respond(request):
        sent.append(request)
        if len(sent) == 1:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": 400,
                        "message": "API key not valid. Please pass a valid API key.",
                        "status": "INVALID_ARGUMENT",
                    }
                },
            )
        return httpx.Response(200, json={"embeddings": [{"values": [0.6, 0.8]}]})

    def client(**kwargs):
        options = kwargs["http_options"]
        options.httpx_client = httpx.Client(transport=httpx.MockTransport(respond))
        options.httpx_async_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        instance = real_client(**kwargs)
        clients.append(instance)
        return instance

    monkeypatch.setattr(genai, "Client", client)
    backends = [
        GeminiDenseEmbedder("fixture", api_key=f"fixture-{index}", dimension=2)
        for index in range(2)
    ]
    model = FailoverEmbedder(backends, ["first", "second"])

    try:
        with model_workload("add_resource"):
            result = await model.embed_async("fixture") if asynchronous else model.embed("fixture")
        assert result.dense_vector == pytest.approx([0.6, 0.8])
        assert model.active_credential_id == "second"
        assert len(sent) == 2
    finally:
        for instance in clients:
            await instance.aio.aclose()
            instance.close()
