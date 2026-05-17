# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from openviking.metrics.datasources.model_usage import (
    EmbeddingEventDataSource,
    RerankEventDataSource,
)
from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.models.rerank.base import RerankBase
from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.telemetry.span_models import RootSpanAttributes


class _DummyEmbedder(DenseEmbedderBase):
    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        return EmbedResult(dense_vector=[1.0])

    def get_dimension(self) -> int:
        return 1


def _bind_root_context_for_account(account_id: str | None):
    root = RootSpanAttributes(http_method="GET", http_route="/items", request_id="req-test")
    root.account_id = account_id
    return bind_root_observability_context(root)


def test_embedder_base_update_token_usage_emits_usage_audit_event(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_record_call(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(EmbeddingEventDataSource, "record_call", staticmethod(_fake_record_call))

    token = _bind_root_context_for_account("acct-embedding-callsite")
    try:
        _DummyEmbedder("e1", {"provider": "openai"}).update_token_usage(
            model_name="e1",
            provider="openai",
            prompt_tokens=7,
            completion_tokens=0,
        )
    finally:
        reset_root_observability_context(token)

    assert captured == {
        "provider": "openai",
        "model_name": "e1",
        "duration_seconds": 0.0,
        "prompt_tokens": 7,
        "completion_tokens": 0,
        "account_id": "acct-embedding-callsite",
    }


def test_rerank_base_update_token_usage_emits_usage_audit_event(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_record_call(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(RerankEventDataSource, "record_call", staticmethod(_fake_record_call))

    token = _bind_root_context_for_account("acct-rerank-callsite")
    try:
        RerankBase().update_token_usage(
            model_name="r1",
            provider="cohere",
            prompt_tokens=11,
            completion_tokens=0,
        )
    finally:
        reset_root_observability_context(token)

    assert captured == {
        "provider": "cohere",
        "model_name": "r1",
        "duration_seconds": 0.0,
        "prompt_tokens": 11,
        "completion_tokens": 0,
        "account_id": "acct-rerank-callsite",
    }
