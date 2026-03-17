# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace

from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder
from openviking.models.embedder.volcengine_embedders import VolcengineDenseEmbedder
from openviking.telemetry.backends.memory import MemoryOperationTelemetry
from openviking.telemetry.context import bind_telemetry


def _usage(prompt_tokens: int, total_tokens: int):
    return SimpleNamespace(prompt_tokens=prompt_tokens, total_tokens=total_tokens)


def test_openai_dense_embedder_reports_embedding_telemetry_usage(monkeypatch):
    response = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])],
        usage=_usage(prompt_tokens=9, total_tokens=9),
    )

    fake_client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: response))
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: fake_client)

    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    with bind_telemetry(telemetry):
        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test",
            dimension=3,
        )
        result = embedder.embed("hello")

    assert result.dense_vector == [0.1, 0.2, 0.3]
    summary = telemetry.finish().summary
    assert summary["tokens"]["embedding"] == {"total": 9}
    assert summary["tokens"]["total"] == 9


def test_volcengine_dense_embedder_reports_embedding_telemetry_usage(monkeypatch):
    response = SimpleNamespace(
        data=SimpleNamespace(embedding=[0.4, 0.5, 0.6]),
        usage=_usage(prompt_tokens=16, total_tokens=16),
    )

    fake_client = SimpleNamespace(
        multimodal_embeddings=SimpleNamespace(create=lambda **kwargs: response),
    )
    monkeypatch.setattr(
        "volcenginesdkarkruntime.Ark",
        lambda **kwargs: fake_client,
    )

    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)
    with bind_telemetry(telemetry):
        embedder = VolcengineDenseEmbedder(
            model_name="doubao-embedding-vision-250615",
            api_key="test",
            input_type="multimodal",
            dimension=3,
        )
        result = embedder.embed("hello")

    assert result.dense_vector == [0.4, 0.5, 0.6]
    summary = telemetry.finish().summary
    assert summary["tokens"]["embedding"] == {"total": 16}
    assert summary["tokens"]["total"] == 16


def test_volcengine_dense_embedder_reports_embedding_telemetry_usage_from_dict_usage(
    monkeypatch,
):
    response = SimpleNamespace(
        data=SimpleNamespace(embedding=[0.4, 0.5, 0.6]),
        usage={
            "prompt_tokens": 16,
            "prompt_tokens_details": {"image_tokens": 0, "text_tokens": 16},
            "total_tokens": 16,
        },
    )

    fake_client = SimpleNamespace(
        multimodal_embeddings=SimpleNamespace(create=lambda **kwargs: response),
    )
    monkeypatch.setattr(
        "volcenginesdkarkruntime.Ark",
        lambda **kwargs: fake_client,
    )

    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    with bind_telemetry(telemetry):
        embedder = VolcengineDenseEmbedder(
            model_name="doubao-embedding-vision-250615",
            api_key="test",
            input_type="multimodal",
            dimension=3,
        )
        result = embedder.embed("hello")

    assert result.dense_vector == [0.4, 0.5, 0.6]
    summary = telemetry.finish().summary
    assert summary["tokens"]["embedding"] == {"total": 16}
