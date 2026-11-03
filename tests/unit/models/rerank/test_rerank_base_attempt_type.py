# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from openviking.models.rerank.base import RerankBase


class FakeReranker(RerankBase):
    provider = "fake"
    model_name = "fake-rerank"


def test_rerank_success_defaults_to_initial_attempt(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "openviking.metrics.datasources.RerankEventDataSource.record_call",
        lambda **kwargs: calls.append(kwargs),
    )

    FakeReranker().update_token_usage(
        model_name="fake-rerank",
        provider="fake",
        prompt_tokens=3,
        completion_tokens=0,
    )

    assert len(calls) == 1
    assert calls[0]["error_code"] == "OK"
    assert calls[0]["attempt_type"] == "initial"


def test_rerank_failure_defaults_to_initial_attempt(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "openviking.metrics.datasources.RerankEventDataSource.record_call",
        lambda **kwargs: calls.append(kwargs),
    )

    FakeReranker().record_failed_call(
        duration_seconds=0.25,
        error=RuntimeError("provider failed"),
    )

    assert len(calls) == 1
    assert calls[0]["error_code"] == "unknown"
    assert calls[0]["attempt_type"] == "initial"
