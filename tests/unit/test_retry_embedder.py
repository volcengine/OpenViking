# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest

from openviking.concurrency import AsyncSemaphore
from openviking.config.embedding import _bind_account_state
from openviking.models.embedder.base import (
    CompositeHybridEmbedder,
    DenseEmbedderBase,
    EmbedResult,
    FailoverEmbedder,
    SparseEmbedderBase,
)
from openviking.models.embedder.retry_embedder import RetryEmbedder
from openviking.models.vlm.token_usage import TokenUsageTracker
from openviking_cli.utils.config.embedding_config import (
    EmbeddingConfig,
    EmbeddingCredential,
    EmbeddingModelConfig,
)


class ProviderError(RuntimeError):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class FakeDenseEmbedder(DenseEmbedderBase):
    def __init__(self, outcomes, *, max_retries: int = 2):
        super().__init__(
            "fake-dense",
            {"provider": "fake", "max_retries": max_retries, "max_concurrent": 1},
        )
        self.provider = "fake"
        self.outcomes = list(outcomes)
        self.calls = 0
        self.closed = False

    def _next(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return EmbedResult(dense_vector=outcome)

    def embed(self, content, is_query=False, **kwargs):
        return self._run_once(self._next)

    async def embed_async(self, content, is_query=False, **kwargs):
        return await self._run_once_async(
            self._next_async,
            operation_name="fake embedding",
        )

    async def _next_async(self):
        return self._next()

    def get_dimension(self):
        return 2

    @property
    def supports_multimodal(self):
        return True

    def close(self):
        self.closed = True


class FakeSparseEmbedder(SparseEmbedderBase):
    def __init__(self, outcomes, *, max_retries: int = 2):
        super().__init__("fake-sparse", {"provider": "fake", "max_retries": max_retries})
        self.provider = "fake"
        self.outcomes = list(outcomes)
        self.calls = 0

    def _next(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return EmbedResult(sparse_vector=outcome)

    def embed(self, content, is_query=False, **kwargs):
        return self._next()

    async def embed_async(self, content, is_query=False, **kwargs):
        return self._next()


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr("openviking.utils.model_retry.time.sleep", lambda _delay: None)


def test_retry_embedder_retries_transient_and_preserves_capabilities():
    inner = FakeDenseEmbedder([ProviderError(503), [0.1, 0.2]])
    embedder = RetryEmbedder(inner)

    result = embedder.embed("query", is_query=True)

    assert result.dense_vector == [0.1, 0.2]
    assert inner.calls == 2
    assert embedder.provider == "fake"
    assert embedder.get_dimension() == 2
    assert embedder.is_dense is True
    assert embedder.is_sparse is False
    assert embedder.is_hybrid is False
    assert embedder.supports_multimodal is True


@pytest.mark.parametrize("status_code", [400, 401])
def test_retry_embedder_does_not_retry_permanent_or_auth_errors(status_code):
    error = ProviderError(status_code)
    inner = FakeDenseEmbedder([error, [0.1, 0.2]])

    with pytest.raises(ProviderError) as raised:
        RetryEmbedder(inner).embed("query")

    assert raised.value is error
    assert inner.calls == 1


def test_retry_embedder_uses_max_retries_as_additional_attempts():
    errors = [ProviderError(503) for _ in range(3)]
    inner = FakeDenseEmbedder(errors, max_retries=2)

    with pytest.raises(ProviderError) as raised:
        RetryEmbedder(inner).embed("query")

    assert raised.value is errors[-1]
    assert inner.calls == 3


def test_retry_embedder_sparse_dimension_remains_unsupported():
    embedder = RetryEmbedder(FakeSparseEmbedder([{"term": 1.0}]))

    assert embedder.is_sparse is True
    with pytest.raises(AttributeError, match="no get_dimension"):
        embedder.get_dimension()


@pytest.mark.asyncio
async def test_retry_releases_semaphore_before_backoff(monkeypatch):
    inner = FakeDenseEmbedder([ProviderError(503), [0.1, 0.2]], max_retries=1)
    semaphore = AsyncSemaphore(1)
    inner._account_semaphore = semaphore
    observed_values = []

    async def observe_backoff(_delay):
        observed_values.append(semaphore._value)

    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", observe_backoff)

    result = await RetryEmbedder(inner).embed_async("query")

    assert result.dense_vector == [0.1, 0.2]
    assert inner.calls == 2
    assert observed_values == [1]
    assert semaphore._value == 1


@pytest.mark.asyncio
async def test_cancelled_attempt_releases_semaphore():
    started = asyncio.Event()

    class BlockingDenseEmbedder(FakeDenseEmbedder):
        async def _next_async(self):
            self.calls += 1
            started.set()
            await asyncio.Event().wait()

    inner = BlockingDenseEmbedder([], max_retries=2)
    semaphore = AsyncSemaphore(1)
    inner._account_semaphore = semaphore
    task = asyncio.create_task(RetryEmbedder(inner).embed_async("query"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert inner.calls == 1
    assert semaphore._value == 1


@pytest.mark.asyncio
async def test_stalled_attempt_times_out_and_is_retried(monkeypatch):
    # SDK/httpx timeouts bound each socket read; a response that keeps trickling
    # bytes never trips them. ``timeout`` must bound the whole attempt.
    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", _no_sleep)

    class StallOnceDenseEmbedder(FakeDenseEmbedder):
        async def _next_async(self):
            if self.calls == 0:
                self.calls += 1
                await asyncio.Event().wait()
            return self._next()

    inner = StallOnceDenseEmbedder([[0.1, 0.2]], max_retries=1)
    inner.config["timeout"] = 0.05
    semaphore = AsyncSemaphore(1)
    inner._account_semaphore = semaphore

    result = await asyncio.wait_for(RetryEmbedder(inner).embed_async("query"), 5)

    assert result.dense_vector == [0.1, 0.2]
    assert inner.calls == 2
    assert semaphore._value == 1


def test_factory_passes_timeout_to_provider(monkeypatch):
    monkeypatch.setattr("openai.OpenAI", lambda **_kwargs: object())
    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="key",
            dimension=2,
        ),
        timeout=7,
    )

    assert config.get_embedder().config["timeout"] == 7


@pytest.mark.asyncio
async def test_composite_retries_only_failed_branch(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", no_sleep)
    dense = FakeDenseEmbedder([[0.1, 0.2]])
    sparse = FakeSparseEmbedder([ProviderError(503), {"term": 1.0}], max_retries=1)
    composite = CompositeHybridEmbedder(RetryEmbedder(dense), RetryEmbedder(sparse))

    result = await composite.embed_async("query")

    assert result.dense_vector == [0.1, 0.2]
    assert result.sparse_vector == {"term": 1.0}
    assert dense.calls == 1
    assert sparse.calls == 2


def test_failover_runs_after_current_credential_retry_exhaustion():
    primary = FakeDenseEmbedder([ProviderError(503), ProviderError(503)], max_retries=1)
    backup = FakeDenseEmbedder([[0.3, 0.4]], max_retries=1)
    failover = FailoverEmbedder(
        [RetryEmbedder(primary), RetryEmbedder(backup)],
        ["primary", "backup"],
    )

    result = failover.embed("query")

    assert result.dense_vector == [0.3, 0.4]
    assert primary.calls == 2
    assert backup.calls == 1


def test_account_state_reaches_retry_inner_and_close_is_delegated_once():
    inner = FakeDenseEmbedder([[0.1, 0.2]])
    embedder = RetryEmbedder(inner)
    semaphore = AsyncSemaphore(2)
    tracker = TokenUsageTracker()

    _bind_account_state(embedder, semaphore, tracker, "account-a")

    assert embedder._account_semaphore is semaphore
    assert inner._account_semaphore is semaphore
    assert inner._token_tracker is tracker
    assert inner._account_id == "account-a"
    embedder.close()
    assert inner.closed is True


def test_factory_wraps_single_remote_provider(monkeypatch):
    monkeypatch.setattr("openai.OpenAI", lambda **_kwargs: object())
    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="key",
            dimension=2,
        )
    )

    embedder = config.get_embedder()

    assert isinstance(embedder, RetryEmbedder)
    assert embedder.inner.__class__.__name__ == "OpenAIDenseEmbedder"


def test_factory_places_retry_inside_failover(monkeypatch):
    monkeypatch.setattr("openai.OpenAI", lambda **_kwargs: object())
    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            dimension=2,
            credentials=[
                EmbeddingCredential(id="primary", api_key="key-a"),
                EmbeddingCredential(id="backup", api_key="key-b"),
            ],
        )
    )

    embedder = config.get_embedder()

    assert isinstance(embedder, FailoverEmbedder)
    assert all(isinstance(child, RetryEmbedder) for child in embedder._embedders)


def test_factory_places_retry_inside_composite(monkeypatch):
    monkeypatch.setattr("openai.OpenAI", lambda **_kwargs: object())
    monkeypatch.setattr("volcenginesdkarkruntime.Ark", lambda **_kwargs: object())
    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="openai-key",
            dimension=2,
        ),
        sparse=EmbeddingModelConfig(
            provider="volcengine",
            model="doubao-embedding",
            api_key="ark-key",
        ),
    )

    embedder = config.get_embedder()

    assert isinstance(embedder, CompositeHybridEmbedder)
    assert isinstance(embedder.dense_embedder, RetryEmbedder)
    assert isinstance(embedder.sparse_embedder, RetryEmbedder)
