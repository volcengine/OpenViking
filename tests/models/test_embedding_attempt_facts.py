# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Synthetic transport/owner acceptance cases; no model service is contacted."""

from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import openai
import pytest

from openviking.metrics.collectors.embedding import EmbeddingCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.metrics.exporters.prometheus import PrometheusExporter
from openviking.models.embedder.attempt import observe_embedding
from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder
from openviking.models.embedder.retry_embedder import RetryEmbedder
from openviking.models.vlm.token_usage import TokenUsageTracker
from openviking.observability.events import (
    _GLOBAL_EVENT_BUS,
    register_event_subscriber,
)
from openviking.observability.reporting.runtime import make_subscriber
from openviking.service.task_work_index import bind_task_context


@pytest.fixture
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(
        EventMetricDataSource,
        "_emit",
        staticmethod(lambda name, payload: seen.append((name, payload))),
    )
    return seen


def facts(events):
    return [payload for name, payload in events if name == "embedding.call"]


def make_openai(handler):
    embedder = OpenAIDenseEmbedder("text-embedding-3-small", api_key="synthetic", dimension=2)
    embedder.client.close()
    embedder.client = openai.OpenAI(
        api_key="synthetic",
        base_url="https://model.example.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    embedder._token_tracker = TokenUsageTracker()
    return embedder


def response_body(usage):
    value = {
        "object": "list",
        "model": "text-embedding-3-small",
        "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
    }
    if usage is not None:
        value["usage"] = usage
    return value


@pytest.mark.parametrize(
    "usage,expected,complete",
    [
        (None, (None, None), False),
        ({}, (None, 0), False),
        ({"prompt_tokens": 0, "total_tokens": 0}, (0, 0), True),
        ({"prompt_tokens": 17, "total_tokens": 17}, (17, 0), True),
        ({"total_tokens": 19}, (None, 0), False),
    ],
)
def test_real_openai_sdk_missing_usage_is_not_zero(events, usage, expected, complete):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response_body(usage))

    embedder = make_openai(handler)
    try:
        assert embedder.embed("synthetic input").dense_vector == [1.0, 0.0]
    finally:
        embedder.close()
    [event] = facts(events)
    assert len(requests) == 1
    assert (event["prompt_tokens"], event["completion_tokens"]) == expected
    assert event["usage_complete"] is complete
    assert event["outcome"] == "success"
    assert event["attempt_id"] == event["event_id"]
    assert event["observation_scope"] == "embedding_operation"
    assert event["duration_seconds"] >= 0
    assert set(event["usage_known_fields"]) == {
        name
        for name, value in zip(("prompt_tokens", "completion_tokens"), expected, strict=True)
        if value is not None
    }
    assert "synthetic input" not in str(event)
    assert "synthetic" not in event.values()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_actual_sdk_error_and_outer_retry_settle_once_each(events, monkeypatch, asynchronous):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, json={"error": {"message": "synthetic unavailable"}})
        return httpx.Response(200, json=response_body({"prompt_tokens": 5, "total_tokens": 5}))

    embedder = make_openai(handler)
    embedder.max_retries = 2
    retry = RetryEmbedder(embedder)
    monkeypatch.setattr("openviking.utils.model_retry.time.sleep", lambda seconds: None)

    async def no_wait(seconds):
        return None

    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", no_wait)
    async_client = openai.AsyncOpenAI(
        api_key="synthetic",
        base_url="https://model.example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(embedder, "_get_async_client", lambda: async_client)
    try:
        result = await retry.embed_async("synthetic") if asynchronous else retry.embed("synthetic")
        assert result.dense_vector == [1.0, 0.0]
    finally:
        embedder.close()
        await async_client.close()
    failed, success = facts(events)
    assert len(requests) == 2
    assert failed["prompt_tokens"] is None and failed["completion_tokens"] is None
    assert failed["usage_complete"] is False and failed["outcome"] == "failed"
    assert success["prompt_tokens"] == 5 and success["outcome"] == "success"
    assert failed["attempt_id"] != success["attempt_id"]
    assert embedder.get_token_usage()["total_usage"]["total_tokens"] == 5


class OwnerProbe(DenseEmbedderBase):
    def __init__(self):
        super().__init__("synthetic", {"max_concurrent": 1})
        self._token_tracker = TokenUsageTracker()

    def get_dimension(self):
        return 2

    @observe_embedding("probe")
    def embed(self, content, is_query=False):
        if content == "invalid":
            raise ValueError("synthetic validation")
        result = self._run_once(lambda: EmbedResult(dense_vector=[1.0, 0.0]))
        self.update_token_usage(self.model_name, "probe", len(content), 0)
        return result

    @observe_embedding("probe")
    async def embed_async(self, content, is_query=False):
        if content == "nested":
            return self.embed("nested", is_query=is_query)

        async def call():
            if isinstance(content, asyncio.Event):
                await content.wait()
                return EmbedResult(dense_vector=[1.0, 0.0])
            await asyncio.sleep(0)
            self.update_token_usage(self.model_name, "probe", len(content), 0)
            return EmbedResult(dense_vector=[1.0, 0.0])

        return await self._run_once_async(call, is_query=is_query, operation_name="probe")


async def test_cancellation_after_admission_is_unknown_but_waiter_is_not_an_attempt(events):
    embedder = OwnerProbe()
    release = asyncio.Event()
    first = asyncio.create_task(embedder.embed_async(release))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(embedder.embed_async("waiting"))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert facts(events) == []
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    [event] = facts(events)
    assert event["outcome"] == "cancelled" and event["usage_complete"] is False
    assert event["prompt_tokens"] is None
    assert await embedder.embed_async("next") == EmbedResult(dense_vector=[1.0, 0.0])
    assert len(facts(events)) == 2


async def test_query_admission_and_nested_fallback_remain_unchanged(events):
    embedder = OwnerProbe()
    blocked = asyncio.create_task(embedder.embed_async(asyncio.Event()))
    await asyncio.sleep(0)
    await embedder.embed_async("query", is_query=True)
    await embedder.embed_async("nested", is_query=True)
    blocked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocked
    assert [event["prompt_tokens"] for event in facts(events)] == [5, 6, None]


async def test_concurrent_tasks_do_not_share_mutable_usage(events):
    embedder = OwnerProbe()

    async def work(index):
        with bind_task_context(f"task-{index}", f"account-{index}", "synthetic-user"):
            return await embedder.embed_async("x" * index, is_query=True)

    await asyncio.gather(*(work(index) for index in range(1, 16)))
    observed = facts(events)
    assert len(observed) == 15
    assert len({event["attempt_id"] for event in observed}) == 15
    assert {(event["account_id"], event["prompt_tokens"]) for event in observed} == {
        (f"account-{index}", index) for index in range(1, 16)
    }


async def test_inherited_context_in_child_task_and_thread_owns_separate_scope(events):
    embedder = OwnerProbe()

    @observe_embedding("probe")
    async def parent(backend):
        await asyncio.create_task(backend.embed_async("child", is_query=True))
        copied = contextvars.copy_context()
        with ThreadPoolExecutor(1) as pool:
            await asyncio.get_running_loop().run_in_executor(
                pool, copied.run, backend.embed, "thread"
            )

    await parent(embedder)
    assert sorted(event["prompt_tokens"] for event in facts(events)) == [5, 6]
    assert len({event["attempt_id"] for event in facts(events)}) == 2


def test_estimate_after_request_remains_estimated(events, monkeypatch):
    from openviking.models.embedder.voyage_embedders import VoyageDenseEmbedder

    embedder = VoyageDenseEmbedder("voyage-4-lite", api_key="synthetic")
    monkeypatch.setattr(
        embedder.client.embeddings,
        "create",
        lambda **kwargs: SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0, 0.0])],
            usage=None,
        ),
    )
    try:
        embedder.embed("synthetic input")
    finally:
        embedder.close()
    [event] = facts(events)
    assert event["usage_quality"] == "estimated"
    assert event["usage_provenance"] == "character_estimate"
    assert event["usage_complete"] is False
    assert event["prompt_tokens"] > 0


@pytest.mark.parametrize("provider", ["voyage", "jina"])
def test_compatible_provider_report_wins_over_character_estimate(events, monkeypatch, provider):
    from openviking.models.embedder.jina_embedders import JinaDenseEmbedder
    from openviking.models.embedder.voyage_embedders import VoyageDenseEmbedder

    cls, model = (
        (VoyageDenseEmbedder, "voyage-4-lite")
        if provider == "voyage"
        else (JinaDenseEmbedder, "jina-embeddings-v3")
    )
    embedder = cls(model, api_key="synthetic")
    monkeypatch.setattr(
        embedder.client.embeddings,
        "create",
        lambda **kwargs: SimpleNamespace(
            data=[SimpleNamespace(embedding=[1.0, 0.0])],
            usage=SimpleNamespace(prompt_tokens=57, total_tokens=57),
        ),
    )
    try:
        embedder.embed("short")
    finally:
        embedder.close()
    [event] = facts(events)
    assert event["usage_quality"] == "actual" and event["prompt_tokens"] == 57


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("fail_second", [False, True])
async def test_ark_hybrid_reports_each_sdk_invocation_once(
    events, monkeypatch, asynchronous, fail_second
):
    from openviking.models.embedder.volcengine_embedders import VolcengineHybridEmbedder

    responses = [
        SimpleNamespace(
            data=SimpleNamespace(embedding=[1.0, 0.0]),
            usage=SimpleNamespace(prompt_tokens=11, total_tokens=11),
        ),
        SimpleNamespace(
            data=SimpleNamespace(
                embedding=[0.0, 1.0], sparse_embedding=[{"index": 9, "value": 0.8}]
            ),
            usage=SimpleNamespace(prompt_tokens=7, total_tokens=7),
        ),
    ]
    calls = []

    def call(**kwargs):
        calls.append(kwargs)
        if fail_second and len(calls) == 2:
            raise RuntimeError("synthetic failure")
        return responses[len(calls) - 1]

    async def acall(**kwargs):
        return call(**kwargs)

    monkeypatch.setattr(
        "volcenginesdkarkruntime.Ark",
        lambda **kwargs: SimpleNamespace(multimodal_embeddings=SimpleNamespace(create=call)),
    )
    embedder = VolcengineHybridEmbedder("synthetic", api_key="synthetic", dimension=2)
    monkeypatch.setattr(
        embedder,
        "_get_async_client",
        lambda: SimpleNamespace(multimodal_embeddings=SimpleNamespace(create=acall)),
    )
    content = [
        {"type": "text", "text": "synthetic"},
        {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
    ]
    if fail_second:
        with pytest.raises(RuntimeError):
            if asynchronous:
                await embedder.embed_async(content)
            else:
                embedder.embed(content)
    elif asynchronous:
        await embedder.embed_async(content)
    else:
        embedder.embed(content)
    first, second = facts(events)
    assert len(calls) == 2 and first["prompt_tokens"] == 11
    assert first["outcome"] == "success" and first["usage_complete"] is True
    assert second["prompt_tokens"] == (None if fail_second else 7)
    assert second["outcome"] == ("failed" if fail_second else "success")
    assert first["attempt_id"] != second["attempt_id"]
    assert first["observation_scope"] == second["observation_scope"] == "provider_attempt"


def test_validation_and_observer_failure_do_not_change_model_behavior(events, monkeypatch):
    embedder = OwnerProbe()
    with pytest.raises(ValueError):
        embedder.embed("invalid")
    assert facts(events) == []
    monkeypatch.setattr(
        EventMetricDataSource,
        "_emit",
        staticmethod(lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic observer"))),
    )
    assert embedder.embed("valid").dense_vector == [1.0, 0.0]


def test_nullable_and_estimated_events_do_not_fabricate_reported_token_metrics():
    registry = MetricRegistry()
    collector = EmbeddingCollector()
    common = {"provider": "synthetic", "model_name": "synthetic", "duration_seconds": 0.01}
    collector.receive(
        "embedding.call", {**common, "prompt_tokens": None, "completion_tokens": None}, registry
    )
    collector.receive(
        "embedding.call",
        {**common, "prompt_tokens": 100, "completion_tokens": 0, "usage_quality": "estimated"},
        registry,
    )
    collector.receive(
        "embedding.call", {**common, "prompt_tokens": 7, "completion_tokens": None}, registry
    )
    text = PrometheusExporter(registry=registry).render()
    assert any(
        line.startswith("openviking_embedding_tokens_input_total{")
        and float(line.rsplit(" ", 1)[1]) == 7
        for line in text.splitlines()
    )
    assert "openviking_embedding_tokens_total{" not in text
    assert 'quality="estimated"' in text
    assert 'quality="unknown"' in text or 'quality="actual"' in text


def test_official_bus_and_exporter_keep_content_free_attempt_fields():
    _GLOBAL_EVENT_BUS.clear()
    exported = []
    register_event_subscriber(
        "embedding-attempt-test",
        make_subscriber(SimpleNamespace(report=exported.append), {"telemetry": {"deployment": {}}}),
    )
    try:
        OwnerProbe().embed("synthetic input")
    finally:
        _GLOBAL_EVENT_BUS.clear()
    [event] = exported
    assert event["observation_scope"] == "embedding_operation"
    assert event["usage_quality"] == "actual"
    assert event["attempt_id"]
    assert event["usage_complete"] is True
    assert event["prompt_tokens"] == 15
    assert "synthetic input" not in str(event)


@pytest.mark.parametrize("value", [True, False, -1, "9", 1.5, None])
def test_invalid_reported_counts_stay_unknown(value):
    from openviking.models.embedder.attempt import embedding_usage

    assert embedding_usage({"prompt_tokens": value, "total_tokens": 9}) == (None, 0)


@pytest.mark.parametrize("cancelled", [False, True])
async def test_known_usage_survives_late_failure_or_cancellation(events, cancelled):
    from openviking.models.model_error import ModelError, ModelErrorKind

    @observe_embedding("probe")
    async def operation(backend):
        async def call():
            backend.update_token_usage("synthetic", "probe", 13, 0)
            if cancelled:
                raise asyncio.CancelledError()
            raise ModelError(ModelErrorKind.CONTENT_SAFETY, "synthetic refusal")

        return await backend._run_once_async(call, is_query=True, operation_name="probe")

    with pytest.raises(asyncio.CancelledError if cancelled else ModelError):
        await operation(OwnerProbe())
    [event] = facts(events)
    assert event["prompt_tokens"] == 13 and event["completion_tokens"] == 0
    assert event["outcome"] == ("cancelled" if cancelled else "refused")
    assert event["usage_quality"] == "actual"
    assert event["usage_complete"] is (not cancelled)
    assert "synthetic refusal" not in str(event)


async def test_real_async_sdk_cancellation_settles_unknown_usage_once(events, monkeypatch):
    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled transport must not finish")

    embedder = make_openai(lambda request: httpx.Response(200, json=response_body(None)))
    client = openai.AsyncOpenAI(
        api_key="synthetic",
        base_url="https://model.example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(embedder, "_get_async_client", lambda: client)
    task = asyncio.create_task(embedder.embed_async("synthetic"))
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        embedder.close()
        await client.close()
    [event] = facts(events)
    assert event["outcome"] == "cancelled"
    assert event["prompt_tokens"] is None and event["completion_tokens"] is None
    assert event["usage_complete"] is False


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_real_litellm_dispatch_uses_one_sdk_usage_observation(
    events, monkeypatch, asynchronous
):
    import litellm

    from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_body({"prompt_tokens": 23, "total_tokens": 23}))

    sync_client = openai.OpenAI(
        api_key="synthetic",
        base_url="https://model.example.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    async_client = openai.AsyncOpenAI(
        api_key="synthetic",
        base_url="https://model.example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    original_sync, original_async = litellm.embedding, litellm.aembedding

    def sync_dispatch(**kwargs):
        return original_sync(**kwargs, client=sync_client)

    async def async_dispatch(**kwargs):
        return await original_async(**kwargs, client=async_client)

    monkeypatch.setattr(litellm, "embedding", sync_dispatch)
    monkeypatch.setattr(litellm, "aembedding", async_dispatch)
    embedder = LiteLLMDenseEmbedder(
        "openai/text-embedding-3-small", api_key="synthetic", dimension=2
    )
    try:
        result = (
            await embedder.embed_async("synthetic") if asynchronous else embedder.embed("synthetic")
        )
        assert result.dense_vector == [1.0, 0.0]
    finally:
        sync_client.close()
        await async_client.close()
    [event] = facts(events)
    assert len(calls) == 1 and event["prompt_tokens"] == 23
    assert event["usage_complete"] is True


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_cohere_reported_tokens_are_not_billed_units_or_character_estimates(
    events, monkeypatch, asynchronous
):
    from openviking.models.embedder.cohere_embedders import CohereDenseEmbedder

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "embeddings": {"float": [[1.0, 0.0]]},
                "meta": {"tokens": {"input_tokens": 31}, "billed_units": {"input_tokens": 19}},
            },
        )

    embedder = CohereDenseEmbedder("embed-english-v3.0", api_key="synthetic", dimension=2)
    embedder._client.close()
    embedder._client = httpx.Client(
        base_url="https://model.example.test", transport=httpx.MockTransport(handler)
    )
    client = httpx.AsyncClient(
        base_url="https://model.example.test", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(embedder._async_client_cache, "get", lambda factory: client)
    try:
        result = await embedder.embed_async("short") if asynchronous else embedder.embed("short")
        assert len(result.dense_vector) == 2
    finally:
        embedder._client.close()
        await client.aclose()
    [event] = facts(events)
    assert len(calls) == 1
    assert event["prompt_tokens"] == 31 and event["completion_tokens"] == 0
    assert event["usage_quality"] == "actual" and event["usage_complete"] is True
