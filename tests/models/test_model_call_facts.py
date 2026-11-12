"""Provider attempts retain unknown usage, identity, and isolated timing."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder
from openviking.models.embedder.volcengine_embedders import VolcengineHybridEmbedder
from openviking.models.model_error import ModelError
from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM
from openviking.models.vlm.retry_vlm import RetryVLM
from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.observability.events import (
    register_event_subscriber,
    unregister_event_subscriber,
)
from openviking.observability.reporting import runtime
from openviking.observability.reporting.facts import bind_memory_batch
from openviking.server.identity import RequestContext, Role
from openviking.telemetry.span_models import RootSpanAttributes
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.embedding_config import (
    EmbeddingConfig,
    EmbeddingCredential,
    EmbeddingModelConfig,
)


@pytest.fixture
def calls(monkeypatch):
    events = []
    monkeypatch.setattr(runtime, "_reporter", object())
    register_event_subscriber("model_call_regression", events.append)
    try:
        yield events
    finally:
        unregister_event_subscriber("model_call_regression")


def _response(usage=True):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=3, total_tokens=11)
        if usage
        else None,
    )


def _provider(monkeypatch, kind, create, *, async_call=False):
    cls = {"openai": OpenAIVLM, "litellm": LiteLLMVLMProvider, "volcengine": VolcEngineVLM}[kind]
    provider = cls(
        {
            "provider": kind,
            "model": "gpt-4o-mini",
            "api_key": "test-key",
            "temperature": 0,
            "max_retries": 1,
            "cost_bearer": "customer",
        }
    )
    if kind == "litellm":
        name = "acompletion" if async_call else "completion"
        monkeypatch.setattr(f"openviking.models.vlm.backends.litellm_vlm.{name}", create)
    else:
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        name = "get_async_client" if async_call else "get_client"
        monkeypatch.setattr(provider, name, lambda: client)
    return provider


@pytest.mark.parametrize("kind", ["openai", "litellm", "volcengine"])
def test_retry_emits_one_fact_per_sdk_attempt_with_distinct_ids(monkeypatch, calls, kind):
    create = Mock(side_effect=[TimeoutError("provider timeout"), _response()])
    provider = _provider(monkeypatch, kind, create)
    monkeypatch.setattr("openviking.utils.model_retry.time.sleep", lambda _: None)

    assert RetryVLM(provider).get_completion(prompt="test") == "ok"
    assert create.call_count == 2
    assert len(calls) == 2
    failed, succeeded = [event.payload for event in calls]
    assert failed["prompt_tokens"] is None and failed["completion_tokens"] is None
    assert failed["error_code"] != "OK"
    assert (succeeded["prompt_tokens"], succeeded["completion_tokens"]) == (8, 3)
    assert succeeded["error_code"] == "OK"
    assert failed["event_id"] != succeeded["event_id"]
    assert all(event["cost_bearer"] == "customer" for event in (failed, succeeded))


@pytest.mark.parametrize("kind", ["openai", "litellm", "volcengine"])
@pytest.mark.parametrize("usage", [False, True])
async def test_async_provider_success_without_usage_is_still_one_call(
    monkeypatch, calls, kind, usage
):
    create = AsyncMock(return_value=_response(usage))
    provider = _provider(monkeypatch, kind, create, async_call=True)

    assert await provider.get_completion_async(prompt="test") == "ok"
    assert create.await_count == 1 and len(calls) == 1
    payload = calls[0].payload
    assert payload["error_code"] == "OK"
    assert payload["prompt_tokens"] == (8 if usage else None)
    assert payload["completion_tokens"] == (3 if usage else None)
    assert payload["duration_seconds"] >= 0


@pytest.mark.parametrize("kind", ["openai", "litellm", "volcengine"])
def test_response_parsing_failure_does_not_duplicate_success(monkeypatch, calls, kind):
    response = _response()
    response.choices = []
    create = Mock(return_value=response)
    provider = _provider(monkeypatch, kind, create)

    with pytest.raises(IndexError):
        provider.get_completion(prompt="test")
    assert create.call_count == 1
    assert len(calls) == 1 and calls[0].payload["error_code"] == "OK"


@pytest.mark.parametrize(
    "model,forward,expected",
    [
        ("openai/gpt-4o-mini", None, "customer"),
        ("openai/gpt-4o-mini", False, "unknown"),
        ("bedrock/anthropic.claude-v2", None, "unknown"),
        ("bedrock/anthropic.claude-v2", True, "customer"),
    ],
)
def test_litellm_cost_bearer_matches_forwarded_auth(monkeypatch, calls, model, forward, expected):
    monkeypatch.setenv("OPENAI_API_KEY", "existing-environment-key")
    create = Mock(return_value=_response())
    monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.completion", create)
    provider = LiteLLMVLMProvider(
        {
            "provider": "litellm",
            "model": model,
            "api_key": "instance-key",
            "forward_api_key": forward,
            "cost_bearer": "customer",
        }
    )

    assert provider.get_completion(prompt="test") == "ok"
    assert calls[0].payload["cost_bearer"] == expected
    assert ("api_key" in create.call_args.kwargs) == (expected == "customer")
    assert os.environ["OPENAI_API_KEY"] == "existing-environment-key"


class _Embedder(DenseEmbedderBase):
    def __init__(self):
        super().__init__("embedding-model", {"provider": "openai"})

    def embed(self, content, is_query=False):
        return EmbedResult(dense_vector=[1.0])

    def get_dimension(self):
        return 1


async def test_embedding_timers_are_task_local_and_preserve_completed_duration(monkeypatch, calls):
    clock = [10.0]
    monkeypatch.setattr(
        "openviking.models.embedder.base.time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    embedder = _Embedder()
    started = [asyncio.Event(), asyncio.Event()]
    finish = [asyncio.Event(), asyncio.Event()]

    async def run(index):
        async def sdk():
            started[index].set()
            await finish[index].wait()
            embedder.update_token_usage("embedding-model", "openai", index + 1, 0)

        await embedder._run_once_async(sdk, is_query=True, operation_name="test")

    a = asyncio.create_task(run(0))
    await started[0].wait()
    clock[0] = 20.0
    b = asyncio.create_task(run(1))
    await started[1].wait()
    clock[0] = 30.0
    finish[0].set()
    await a
    clock[0] = 40.0
    finish[1].set()
    await b
    assert [event.payload["duration_seconds"] for event in calls] == [20.0, 20.0]
    assert embedder._active_call.get() is None

    clock[0] = 50.0
    embedder._run_once(lambda: None)
    clock[0] = 100.0
    embedder.update_token_usage("embedding-model", "openai", 1, 0)
    assert calls[-1].payload["duration_seconds"] == 0.0

    # A separate late update keeps the finished request's measured duration.
    def sdk():
        clock[0] = 110.0

    embedder._run_once(sdk)
    clock[0] = 200.0
    embedder.update_token_usage("embedding-model", "openai", 1, 0)
    assert calls[-1].payload["duration_seconds"] == 10.0


@pytest.mark.parametrize("failure", [False, True])
def test_openai_embedding_records_missing_usage_and_failures(monkeypatch, calls, failure):
    response = SimpleNamespace(data=[SimpleNamespace(embedding=[1.0])], usage=None)
    create = (
        Mock(side_effect=TimeoutError("provider timeout"))
        if failure
        else Mock(return_value=response)
    )
    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_: SimpleNamespace(embeddings=SimpleNamespace(create=create)),
    )
    embedder = OpenAIDenseEmbedder("text-embedding-3-small", api_key="test-key", dimension=1)
    if failure:
        with pytest.raises(ModelError):
            embedder.embed("test")
    else:
        assert embedder.embed("test").dense_vector == [1.0]
    assert create.call_count == 1 and len(calls) == 1
    assert calls[0].payload["prompt_tokens"] is None
    assert calls[0].payload["completion_tokens"] is None
    assert (calls[0].payload["error_code"] != "OK") == failure


def test_ark_hybrid_records_each_sdk_call_not_the_outer_embed(monkeypatch, calls):
    responses = [
        SimpleNamespace(data=SimpleNamespace(embedding=[1.0]), usage=None),
        SimpleNamespace(
            data=SimpleNamespace(embedding=[1.0], sparse_embedding=[]),
            usage=SimpleNamespace(prompt_tokens=4, total_tokens=4),
        ),
    ]
    create = Mock(side_effect=responses)
    monkeypatch.setattr(
        "volcenginesdkarkruntime.Ark",
        lambda **_: SimpleNamespace(multimodal_embeddings=SimpleNamespace(create=create)),
    )
    embedder = VolcengineHybridEmbedder(
        "doubao-embedding-vision-251215", api_key="test-key", dimension=1, input_type="multimodal"
    )
    result = embedder.embed(
        [
            {"type": "text", "text": "test"},
            {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
        ]
    )
    assert result.dense_vector == [1.0]
    assert create.call_count == 2 and len(calls) == 2
    assert [event.payload["prompt_tokens"] for event in calls] == [None, 4]
    assert len({event.payload["event_id"] for event in calls}) == 2


@pytest.mark.parametrize("failure", [False, True])
def test_model_facts_use_batch_identity_instead_of_stale_root(monkeypatch, calls, failure):
    root = RootSpanAttributes(http_method="POST", http_route="/test", request_id="stale")
    root.account_id, root.user_id = "stale-account", "stale-user"
    token = bind_root_observability_context(root)
    ctx = RequestContext(user=UserIdentifier("batch-account", "batch-user"), role=Role.ROOT)
    provider = _provider(monkeypatch, "openai", Mock(return_value=_response()))
    embedder = _Embedder()
    try:
        with bind_memory_batch(ctx, ["task-a", "task-b"]):
            for model in (provider, embedder):
                if failure:
                    model.record_call_without_usage(duration_seconds=1, error=TimeoutError())
                else:
                    model.update_token_usage("test-model", "openai", 2, 0)
        assert len(calls) == 2
        assert all(event.account_id == "batch-account" for event in calls)
        assert all(event.user_id == "batch-user" for event in calls)
        assert all(event.payload["source_task_ids"] == ["task-a", "task-b"] for event in calls)
        assert all(event.payload["task_id"] == "" and event.request_id == "" for event in calls)
    finally:
        reset_root_observability_context(token)


def _embedding_response(usage=True):
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[1.0, 2.0])],
        usage=SimpleNamespace(prompt_tokens=4, total_tokens=4) if usage else None,
    )


@pytest.mark.parametrize("probe_outcome", ["known_usage", "missing_usage", "timeout"])
@pytest.mark.parametrize(
    "owners", [(), ("customer",), (None,), ("ov", "customer", None, "unknown")]
)
def test_embedding_dimension_probe_keeps_each_credential_cost_bearer(
    monkeypatch, calls, owners, probe_outcome
):
    creates = []

    def build_client(**kwargs):
        probe = (
            TimeoutError("dimension probe timeout")
            if probe_outcome == "timeout"
            else _embedding_response(usage=probe_outcome == "known_usage")
        )
        create = Mock(side_effect=[probe, _embedding_response()])
        creates.append((kwargs["api_key"], create))
        return SimpleNamespace(embeddings=SimpleNamespace(create=create), close=Mock())

    monkeypatch.setattr("openai.OpenAI", build_client)
    monkeypatch.setattr(
        "openviking.models.embedder.openai_embedders.create_optional_sync_httpx_client",
        lambda *args, **kwargs: Mock(),
    )
    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="direct-key",
            credentials=[
                EmbeddingCredential(id=f"key-{i}", api_key=f"key-{i}", cost_bearer=owner)
                for i, owner in enumerate(owners)
            ],
        ),
        max_retries=0,
    )
    embedder = config.get_embedder()
    expected_owners = list(owners) if owners else [None]
    try:
        probes = [event.payload for event in calls]
        assert len(probes) == len(expected_owners)
        assert [payload["cost_bearer"] for payload in probes] == expected_owners
        assert all(payload["provider"] == "openai" for payload in probes)
        expected_tokens = 4 if probe_outcome == "known_usage" else None
        assert all(payload["prompt_tokens"] == expected_tokens for payload in probes)
        assert all(
            payload["completion_tokens"] == (0 if expected_tokens is not None else None)
            for payload in probes
        )
        assert all(
            (payload["error_code"] != "OK") == (probe_outcome == "timeout") for payload in probes
        )
        expected_keys = [f"key-{i}" for i in range(len(owners))] if owners else ["direct-key"]
        assert [key for key, _ in creates] == expected_keys
        assert all(create.call_count == 1 for _, create in creates)

        candidates = embedder._embedders if len(owners) > 1 else [embedder]
        for candidate, owner in zip(candidates, expected_owners, strict=True):
            assert getattr(candidate, "cost_bearer", None) == owner
            assert getattr(candidate.inner, "cost_bearer", None) == owner
            assert candidate.get_dimension() == (1536 if probe_outcome == "timeout" else 2)
            assert candidate.embed("after probe").dense_vector == [1.0, 2.0]
        subsequent = [event.payload for event in calls[len(probes) :]]
        assert [payload["cost_bearer"] for payload in subsequent] == expected_owners
        assert all(payload["prompt_tokens"] == 4 for payload in subsequent)
        assert all(payload["error_code"] == "OK" for payload in subsequent)
        assert all(create.call_count == 2 for _, create in creates)
        assert len({event.payload["event_id"] for event in calls}) == len(calls)
    finally:
        embedder.close()


@pytest.mark.parametrize("provider", ["ollama", "openai", "azure"])
@pytest.mark.parametrize("async_call", [False, True])
async def test_embedding_provider_stays_consistent_across_usage_and_failure(
    monkeypatch, calls, provider, async_call
):
    responses = [_embedding_response(), _embedding_response(False), TimeoutError("SDK timeout")]
    create = AsyncMock(side_effect=responses) if async_call else Mock(side_effect=responses)
    constructors = []

    def stub_constructor(sdk_name):
        def build(**kwargs):
            constructors.append((sdk_name, kwargs))
            return SimpleNamespace(embeddings=SimpleNamespace(create=create), close=Mock())

        return build

    for sdk_name in ["OpenAI", "AzureOpenAI", "AsyncOpenAI", "AsyncAzureOpenAI"]:
        monkeypatch.setattr(f"openai.{sdk_name}", stub_constructor(sdk_name))
    for kind in ["sync", "async"]:
        monkeypatch.setattr(
            f"openviking.models.embedder.openai_embedders.create_optional_{kind}_httpx_client",
            lambda *args, **kwargs: Mock(),
        )
    embedder = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider=provider,
            model="text-embedding-3-small",
            api_key="test-key",
            api_base="https://embedding.example.test/v1",
            dimension=2,
        ),
        max_retries=0,
    ).get_embedder()

    async def invoke():
        if async_call:
            return await embedder.embed_async("test", is_query=True)
        return embedder.embed("test")

    try:
        assert (await invoke()).dense_vector == [1.0, 2.0]
        assert (await invoke()).dense_vector == [1.0, 2.0]
        with pytest.raises(ModelError):
            await invoke()
        payloads = [event.payload for event in calls]
        assert len(payloads) == 3
        assert [payload["provider"] for payload in payloads] == [provider] * 3
        assert [payload["prompt_tokens"] for payload in payloads] == [4, None, None]
        assert [payload["completion_tokens"] for payload in payloads] == [0, None, None]
        assert [payload["error_code"] for payload in payloads] == ["OK", "OK", "timeout"]
        assert len({payload["event_id"] for payload in payloads}) == 3
        assert create.call_count == 3
        if async_call:
            assert create.await_count == 3
        sdk = "AzureOpenAI" if provider == "azure" else "OpenAI"
        assert [name for name, _ in constructors] == [sdk] + (["Async" + sdk] if async_call else [])
        assert all(kwargs["max_retries"] == 0 for _, kwargs in constructors)
        assert all(kwargs["api_key"] == "test-key" for _, kwargs in constructors)
    finally:
        embedder.close()
        await asyncio.sleep(0)
