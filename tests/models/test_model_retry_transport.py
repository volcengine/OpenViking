# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Count actual SDK HTTP requests, not mocked adapter invocations."""

import httpx
import pytest
import requests

from openviking.models.embedder.base import FailoverEmbedder
from openviking.models.embedder.volcengine_embedders import VolcengineDenseEmbedder
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM
from openviking.models.vlm.base import MultiCredentialVLM
from openviking.utils.model_call import get_model_call_error, is_model_call_error, model_workload


class FaultTransport:
    def __init__(self, status, code, recover_after=None):
        self.status, self.code, self.recover_after = status, code, recover_after
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.recover_after is not None and len(self.requests) > self.recover_after:
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "fixture",
                    "model": "fixture",
                    "object": "chat.completion",
                    "created": 0,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ],
                    "data": [{"index": 0, "object": "embedding", "embedding": [0.1, 0.2]}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                },
            )
        return httpx.Response(
            self.status, request=request, json={"error": {"code": self.code, "message": self.code}}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["embedding", "volcengine", "openai"])
@pytest.mark.parametrize(
    "status,code,workload,credentials,expected",
    [
        (429, "TooManyRequests", "offline", 1, 4),
        (429, "TooManyRequests", "offline", 2, 4),
        (429, "TooManyRequests", "online", 2, 1),
        (401, "Unauthorized", "offline", 1, 1),
        (400, "ContentSafety", "offline", 2, 1),
        (429, "AccountQuotaExceeded", "offline", 1, 1),
        (429, "insufficient_quota", "offline", 1, 1),
        (429, "insufficient_quota", "offline", 2, 2),
    ],
)
async def test_http_attempt_cap(
    monkeypatch, backend, status, code, workload, credentials, expected
):
    from openviking.models.embedder import volcengine_embedders as emb
    from openviking.models.vlm.backends import openai_vlm, volcengine_vlm

    transport = FaultTransport(status, code)
    clients = []

    def async_http(*args, **kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        clients.append(client)
        return client

    target = (
        emb if backend == "embedding" else volcengine_vlm if backend == "volcengine" else openai_vlm
    )
    monkeypatch.setattr(target, "create_optional_async_httpx_client", async_http)
    monkeypatch.setattr("openviking.utils.model_call.random.uniform", lambda *_: 0)
    models = []
    for i in range(credentials):
        config = {
            "model": "fixture",
            "api_key": f"fixture-{i}",
            "api_base": "https://fixture.invalid/v1",
            "max_retries": 3,
        }
        if backend == "embedding":
            model = VolcengineDenseEmbedder(
                "fixture",
                api_key=config["api_key"],
                api_base=config["api_base"],
                dimension=2,
                input_type="text",
                config={"max_retries": 3},
            )
            assert model.client.max_retries == 0
        else:
            model = (VolcEngineVLM if backend == "volcengine" else OpenAIVLM)(config)
            assert model.get_async_client().max_retries == 0
        models.append(model)
    model = models[0]
    if credentials > 1:
        wrapper = FailoverEmbedder if backend == "embedding" else MultiCredentialVLM
        model = wrapper(models, ["first", "second"])
    try:
        with model_workload("add_resource", workload=workload):
            with pytest.raises(Exception) as exc:
                if backend == "embedding":
                    await model.embed_async("fixture")
                else:
                    await model.get_completion_async("fixture")
        assert is_model_call_error(exc.value)
        assert len(transport.requests) == expected
    finally:
        for client in clients:
            await client.aclose()
        if backend == "embedding":
            for model in models:
                model.client.close()


@pytest.mark.asyncio
async def test_two_bad_routes_then_recovery_preserves_result_and_active_credential(monkeypatch):
    from openviking.models.vlm.backends import volcengine_vlm

    transport = FaultTransport(429, "TooManyRequests", recover_after=1)
    clients = []

    def client(*args, **kwargs):
        c = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        clients.append(c)
        return c

    monkeypatch.setattr(volcengine_vlm, "create_optional_async_httpx_client", client)
    monkeypatch.setattr("openviking.utils.model_call.random.uniform", lambda *_: 0)
    models = [VolcEngineVLM({"model": "fixture", "api_key": f"fixture-{i}"}) for i in range(2)]
    wrapper = MultiCredentialVLM(models, ["first", "second"])
    try:
        with model_workload("session_commit"):
            assert await wrapper.get_completion_async("fixture") == "ok"
        assert len(transport.requests) == 2
        assert wrapper.active_credential_id == "second"
    finally:
        for c in clients:
            await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("kind", ["dense", "sparse", "hybrid"])
@pytest.mark.parametrize(
    "status,retry_after,recover,expected,reason",
    [
        (429, "0", False, 4, "max_attempts"),
        (503, "0", False, 4, "max_attempts"),
        (401, "0", False, 1, "auth"),
        (429, "60", False, 1, "backoff_limit"),
        (429, "0", True, 2, None),
    ],
)
async def test_vikingdb_http_failures_reach_retry_owner(
    monkeypatch, asynchronous, kind, status, retry_after, recover, expected, reason
):
    import json

    from openviking.models.embedder.vikingdb_embedders import (
        VikingDBDenseEmbedder,
        VikingDBHybridEmbedder,
        VikingDBSparseEmbedder,
    )

    sent = []
    error_body = {"error": {"message": "provider rejected request"}}

    def response_data():
        code = 200 if recover and sent else status
        sent.append(code)
        dense_field = "dense" if kind == "hybrid" else "dense_embedding"
        body = (
            {"result": {"data": [{dense_field: [0.1, 0.2], "sparse": {"a": 1.0}}]}}
            if code == 200
            else error_body
        )
        return code, json.dumps(body).encode()

    class Transport(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            response = requests.Response()
            response.status_code, response._content = response_data()
            response.request = request
            response.url = request.url
            response.headers["Retry-After"] = retry_after
            return response

        def close(self):
            pass

    def async_transport(request):
        code, content = response_data()
        return httpx.Response(code, content=content, headers={"Retry-After": retry_after})

    model = {
        "dense": VikingDBDenseEmbedder,
        "sparse": VikingDBSparseEmbedder,
        "hybrid": VikingDBHybridEmbedder,
    }[kind]("fixture", ak="fixture", sk="fixture", host="fixture.invalid")
    model.client._session.mount("https://", Transport())
    monkeypatch.setattr("openviking.utils.model_call.random.uniform", lambda *_: 0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(async_transport)) as client:
        monkeypatch.setattr(model._async_client_cache, "get", lambda factory: client)
        try:
            with model_workload("add_resource"):
                if reason is None:
                    result = (
                        await model.embed_async("fixture")
                        if asynchronous
                        else model.embed("fixture")
                    )
                    if kind != "sparse":
                        assert result.dense_vector == [0.1, 0.2]
                    if kind != "dense":
                        assert result.sparse_vector == {"a": 1.0}
                else:
                    error_type = httpx.HTTPStatusError if asynchronous else requests.HTTPError
                    with pytest.raises(error_type) as caught:
                        if asynchronous:
                            await model.embed_async("fixture")
                        else:
                            model.embed("fixture")
                    terminal = get_model_call_error(caught.value)
                    assert (terminal.reason, terminal.attempts) == (reason, expected)
                    assert caught.value.response.status_code == status
                    assert caught.value.response.json() == error_body
                    assert caught.value.response.headers["Retry-After"] == retry_after
            assert len(sent) == expected
        finally:
            model.client._session.close()
