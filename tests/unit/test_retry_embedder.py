# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
from unittest.mock import AsyncMock

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
from openviking.models.model_error import ModelError, ModelErrorKind
from openviking.models.vlm.token_usage import TokenUsageTracker
from openviking.utils.model_retry import model_retry_scope
from openviking_cli.utils.config.embedding_config import (
    EmbeddingConfig,
    EmbeddingCredential,
    EmbeddingModelConfig,
)


class ProviderError(ModelError):
    def __init__(self, status_code: int):
        kinds = {
            400: ModelErrorKind.INVALID_REQUEST,
            401: ModelErrorKind.AUTHENTICATION_ERROR,
            503: ModelErrorKind.TRANSIENT_SERVICE_ERROR,
        }
        super().__init__(kinds[status_code], f"HTTP {status_code}", status_code=status_code)


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


def test_unknown_api_error_exhausts_retry_budget_without_credential_failover():
    errors = [ModelError(ModelErrorKind.UNKNOWN, "unclassified API error") for _ in range(3)]
    primary = FakeDenseEmbedder(errors, max_retries=2)
    backup = FakeDenseEmbedder([[0.3, 0.4]], max_retries=2)
    failover = FailoverEmbedder(
        [RetryEmbedder(primary), RetryEmbedder(backup)], ["primary", "backup"]
    )

    with pytest.raises(ModelError) as raised:
        failover.embed("query")

    assert raised.value is errors[-1]
    assert primary.calls == 3
    assert backup.calls == 0


def test_retry_embedder_sparse_dimension_remains_unsupported():
    embedder = RetryEmbedder(FakeSparseEmbedder([{"term": 1.0}]))

    assert embedder.is_sparse is True
    with pytest.raises(AttributeError, match="no get_dimension"):
        embedder.get_dimension()


@pytest.mark.asyncio
async def test_scoped_failure_restores_retries_and_releases_semaphore(monkeypatch):
    error = ProviderError(503)
    inner = FakeDenseEmbedder([error, ProviderError(503), [0.1, 0.2]], max_retries=1)
    embedder = RetryEmbedder(inner)
    semaphore = AsyncSemaphore(1)
    inner._account_semaphore = semaphore
    observed_values = []

    async def observe_backoff(_delay):
        observed_values.append(semaphore._value)

    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", observe_backoff)

    async with model_retry_scope(0):
        with pytest.raises(ProviderError) as raised:
            await embedder.embed_async("query")
    assert raised.value is error
    assert inner.calls == 1
    assert observed_values == []
    assert semaphore._value == 1

    result = await embedder.embed_async("query")

    assert result.dense_vector == [0.1, 0.2]
    assert inner.calls == 3
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
async def test_composite_retries_only_failed_branch(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("openviking.utils.model_retry.asyncio.sleep", no_sleep)
    dense = FakeDenseEmbedder([[0.1, 0.2], [0.3, 0.4]])
    sparse = FakeSparseEmbedder([ProviderError(503), {"term": 1.0}], max_retries=1)
    monkeypatch.setattr(dense, "embed_async", AsyncMock(wraps=dense.embed_async))
    monkeypatch.setattr(sparse, "embed_async", AsyncMock(wraps=sparse.embed_async))
    composite = CompositeHybridEmbedder(RetryEmbedder(dense), RetryEmbedder(sparse))
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}
    content = [{"type": "text", "text": "query"}, image]

    result = await composite.embed_async(composite.prepare_embedding_input(content), is_query=True)

    dense.embed_async.assert_awaited_once_with(content, is_query=True)
    sparse.embed_async.assert_awaited_with("query", is_query=True)
    assert result.dense_vector == [0.1, 0.2]
    assert result.sparse_vector == {"term": 1.0}
    assert dense.calls == 1
    assert sparse.calls == 2

    # Removing text must skip sparse entirely, including its retry machinery.
    image_result = await composite.embed_async([image], is_query=True)
    assert image_result.dense_vector == [0.3, 0.4]
    assert image_result.sparse_vector is None
    assert dense.calls == 2
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
