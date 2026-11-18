"""Exercise the routing embedding wire and the real Shuffle with synthetic SDK responses."""

# ruff: noqa: F811 - reuse the Markdown pipeline fixture.

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from fastapi import FastAPI
from openviking_sdk.client import AsyncHTTPClient
from test_compile_markdown import MERGE, contract, pipeline  # noqa: F401
from vikingbot.compile.hashing import digest
from vikingbot.compile.ops.shuffle import Shuffle
from vikingbot.compile.plan import parse_plan
from vikingbot.compile.results import Document
from vikingbot.openviking_mount.ov_server import VikingClient

from openviking.models.embedder.base import FailoverEmbedder
from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder
from openviking.models.embedder.retry_embedder import RetryEmbedder
from openviking.models.model_error import ModelError
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.compile import router
from openviking_cli.exceptions import InvalidArgumentError, OpenVikingError
from openviking_cli.session.user_id import UserIdentifier

TARGET = "viking://resources/wiki"
FIRST, SECOND, REFUSED = "orchard seeds", "river depth", "orchard seeds warning"


@pytest.fixture
async def embedding_api(monkeypatch):
    calls = []
    refusal = {"message": "Synthetic provider refusal"}
    fs = SimpleNamespace(stat=AsyncMock())

    async def provider(request):
        payload = json.loads(request.content)
        text = payload["input"]
        calls.append((payload["model"], text))
        if text == REFUSED:
            return httpx.Response(451, json={"error": refusal})
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": payload["model"],
                "data": [{"index": 0, "object": "embedding", "embedding": [1.0, 0.0, 0.0]}],
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            },
        )

    monkeypatch.setattr("openviking.utils.model_retry._compute_delay", lambda *a, **kw: 0)
    async with openai.AsyncOpenAI(
        api_key="synthetic",
        base_url="https://embedding.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
    ) as upstream:
        embedders = []
        for model in ("primary", "backup"):
            embedder = OpenAIDenseEmbedder(
                model_name=model,
                api_key="synthetic",
                dimension=3,
                encoding_format="float",
                config={"max_retries": 2},
            )
            monkeypatch.setattr(embedder, "_get_async_client", lambda: upstream)
            embedders.append(RetryEmbedder(embedder))
        routed = FailoverEmbedder(embedders, ["primary", "backup"])
        fs._get_embedder = lambda _ctx: routed
        service = SimpleNamespace(
            viking_fs=fs,
            embedding_provider=SimpleNamespace(
                get_status=AsyncMock(return_value=SimpleNamespace(fingerprint="model-version"))
            ),
        )
        monkeypatch.setattr("openviking.server.routers.compile.get_service", lambda: service)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_request_context] = lambda: RequestContext(
            user=UserIdentifier("synthetic", "alice"), role=Role.USER
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as http:
            sdk = AsyncHTTPClient(url="http://localhost")
            sdk._http = http
            bot_client = VikingClient.__new__(VikingClient)
            bot_client.client = sdk
            yield http, bot_client, calls, refusal, fs


async def test_shuffle_keeps_successful_vectors_with_a_confirmed_451(embedding_api, pipeline):
    _, bot_client, calls, _, _ = embedding_api
    pipeline.client.compile_embeddings = bot_client.compile_embeddings
    pipeline.contract = contract(True)
    node = parse_plan(MERGE, pipeline.contract)[1]
    docs = []
    for i, text in enumerate((FIRST, SECOND, REFUSED)):
        path = f"drafts/{i}.md"
        await pipeline.files.sandbox.write_file(path, text)
        docs.append(Document(f"{i}.md", path, [str(i)]))

    async def decide(stage, system, data, response_type, validate, **kwargs):
        assert stage == "route"
        peers = {row["document"]: row["candidates"] for row in data["assignments"]}
        assert peers == {
            "drafts/0.md": ["drafts/1.md"],
            "drafts/1.md": ["drafts/0.md"],
            "drafts/2.md": ["drafts/0.md"],
        }
        result = response_type.model_validate(
            {"decisions": [{"document": name, "related": [], "history": []} for name in peers]}
        )
        validate(result)
        return result

    pipeline.model.ask = decide
    groups = await Shuffle(pipeline).run(node, docs)
    assert len(groups) == 3
    assert sorted(calls) == sorted(("primary", text) for text in (FIRST, SECOND, REFUSED))
    assert pipeline.metrics["embedding_451_fallback"] == 1
    assert any("HTTP 451" in warning for warning in pipeline.warnings)
    for text in (FIRST, SECOND):
        assert await pipeline.files.get(f"embeddings/{digest(['model-version', text])}") == [
            1.0,
            0.0,
            0.0,
        ]
    assert await pipeline.files.get(f"embeddings/{digest(['model-version', REFUSED])}") is None


async def test_legacy_embedding_request_keeps_all_or_error_shape(embedding_api):
    http, _, calls, _, _ = embedding_api
    response = await http.post(
        "/api/v1/compile/embeddings", json={"target_uri": TARGET, "texts": [FIRST]}
    )
    assert response.json()["result"] == {"model": "model-version", "vectors": [[1.0, 0.0, 0.0]]}
    with pytest.raises(ModelError) as error:
        await http.post(
            "/api/v1/compile/embeddings", json={"target_uri": TARGET, "texts": [REFUSED]}
        )
    assert error.value.status_code == 451
    assert calls == [("primary", FIRST), ("primary", REFUSED)]


@pytest.mark.parametrize("code", ["ContentPolicyViolation", "RateLimitExceeded"])
async def test_coded_refusal_stays_terminal_without_provider_switch(embedding_api, code):
    _, bot_client, calls, refusal, _ = embedding_api
    refusal["code"] = code
    with pytest.raises(InvalidArgumentError, match="Embedding provider refused"):
        await bot_client.compile_embeddings([REFUSED], target_uri=TARGET)
    assert calls == [("primary", REFUSED)]


async def test_null_embedding_without_refusal_reason_is_invalid(pipeline):
    pipeline.client.compile_embeddings = AsyncMock(
        side_effect=[{"model": "m", "vectors": []}, {"model": "m", "vectors": [None]}]
    )
    with pytest.raises(ValueError, match="without a partial refusal reason"):
        await Shuffle(pipeline).vectors([REFUSED])


async def test_scope_failure_never_calls_provider(embedding_api):
    _, bot_client, calls, _, fs = embedding_api
    fs.stat.side_effect = InvalidArgumentError("Synthetic target validation failed")
    with pytest.raises(InvalidArgumentError, match="target validation"):
        await bot_client.compile_embeddings([REFUSED], target_uri=TARGET)
    assert calls == []


@pytest.mark.parametrize(
    "error",
    [
        InvalidArgumentError("Embedding provider refused a Compile input"),
        RuntimeError("HTTP 451 without a structured response"),
        OpenVikingError(
            "Older endpoint returned a refusal",
            code="UNAVAILABLE",
            details={"upstream_status_code": 451},
        ),
    ],
)
async def test_shuffle_does_not_downgrade_unconfirmed_or_terminal_errors(pipeline, error):
    pipeline.client.compile_embeddings = AsyncMock(side_effect=error)
    pipeline.model.ask = AsyncMock()
    pipeline.contract = contract(True)
    node = parse_plan(MERGE, pipeline.contract)[1]
    await pipeline.files.sandbox.write_file("drafts/refused.md", REFUSED)
    with pytest.raises(type(error)) as caught:
        await Shuffle(pipeline).run(node, [Document("refused.md", "drafts/refused.md", [])])
    assert caught.value is error
    pipeline.model.ask.assert_not_awaited()
    assert not pipeline.warnings


async def test_cancellation_propagates_to_active_embedding(embedding_api):
    _, bot_client, calls, _, fs = embedding_api
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def embed(text, is_query=False):
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    fs._get_embedder = lambda _ctx: SimpleNamespace(
        prepare_embedding_input=lambda value: value, embed_async=embed
    )
    task = asyncio.create_task(bot_client.compile_embeddings([REFUSED], target_uri=TARGET))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set() and not calls
