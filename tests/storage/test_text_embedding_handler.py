# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.storage import collection_schemas
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.utils.circuit_breaker import CircuitBreaker


@pytest.mark.asyncio
async def test_embedding_write_with_empty_record_id_is_reported_as_failure(monkeypatch):
    class _VikingDB:
        is_closing = False
        has_queue_manager = False

        async def upsert(self, data, *, ctx, partial_update):
            del data, ctx, partial_update
            return ""

    async def _embed_compat(embedder, message, *, is_query):
        del embedder, message, is_query
        return EmbedResult(dense_vector=[0.1, 0.2])

    handler = TextEmbeddingHandler.__new__(TextEmbeddingHandler)
    handler._vikingdb = _VikingDB()
    handler._embedder = object()
    handler._vector_dim = 2
    handler._circuit_breaker = CircuitBreaker()
    handler._breaker_open_last_log_at = 0.0
    handler._breaker_open_suppressed_count = 0
    handler._breaker_open_log_interval = 30.0

    successes = []
    errors = []
    request_successes = []
    request_failures = []
    handler.set_callbacks(
        on_success=lambda: successes.append(True),
        on_requeue=lambda: None,
        on_error=lambda message, data: errors.append((message, data)),
    )
    handler._record_request_success = lambda message: request_successes.append(message)
    handler._record_request_failure = lambda message, error: request_failures.append(
        (message, error)
    )
    monkeypatch.setattr(collection_schemas, "embed_compat", _embed_compat)

    message = EmbeddingMsg(
        message="hello",
        context_data={
            "uri": "viking://resources/example.md",
            "level": 2,
            "abstract": "example",
            "account_id": "acc1",
        },
        telemetry_id="empty-record-id",
    )
    queue_data = {"data": message.to_json()}

    result = await handler.on_dequeue(queue_data)

    assert result is None
    assert successes == []
    assert request_successes == []
    assert len(errors) == 1
    assert "empty record id" in errors[0][0].lower()
    assert errors[0][1] == queue_data
    assert len(request_failures) == 1
    assert "empty record id" in request_failures[0][1].lower()
    stats = handler.consume_request_stats("empty-record-id")
    assert stats is not None
    assert stats.processed == 0
    assert stats.error_count == 1
