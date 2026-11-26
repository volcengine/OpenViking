# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Input-guard tests for Volcengine sparse-capable embedding modes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from openviking.models.embedder.base import embed_compat
from openviking.models.embedder.volcengine_embedders import (
    VolcengineDenseEmbedder,
    VolcengineHybridEmbedder,
    VolcengineSparseEmbedder,
)

_MULTIMODAL_INPUT = [
    {"type": "text", "text": "diagram of a heat exchanger"},
    {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
    {"type": "video_url", "video_url": {"url": "https://example.test/video.mp4"}},
]


@patch("openviking.models.embedder.volcengine_embedders.volcenginesdkarkruntime.Ark")
def test_sparse_embedder_downgrades_multimodal_input_to_text(_mock_ark):
    embedder = VolcengineSparseEmbedder(model_name="test", api_key="test-key")

    assert embedder.supports_multimodal is False
    assert embedder.prepare_embedding_input(_MULTIMODAL_INPUT) == "diagram of a heat exchanger"


@patch("openviking.models.embedder.volcengine_embedders.volcenginesdkarkruntime.Ark")
@patch("openviking.models.embedder.volcengine_embedders.volcenginesdkarkruntime.AsyncArk")
async def test_hybrid_embedder_routes_media_to_dense_and_text_to_sparse(mock_async_ark, mock_ark):
    dense_response = SimpleNamespace(data=SimpleNamespace(embedding=[1.0, 0.0]), usage=None)
    sparse_response = SimpleNamespace(
        data=SimpleNamespace(embedding=[0.0, 1.0], sparse_embedding=[{"index": 9, "value": 0.8}]),
        usage=None,
    )
    create = mock_ark.return_value.multimodal_embeddings.create
    create.side_effect = [dense_response, sparse_response, sparse_response]
    async_create = AsyncMock(side_effect=[dense_response, sparse_response, dense_response])
    mock_async_ark.return_value.multimodal_embeddings.create = async_create
    embedder = VolcengineHybridEmbedder(
        model_name="existing-model", api_key="test-key", dimension=2
    )
    expected_requests = [
        call(model="existing-model", extra_headers=embedder.extra_headers, input=_MULTIMODAL_INPUT),
        call(
            model="existing-model",
            extra_headers=embedder.extra_headers,
            input=_MULTIMODAL_INPUT[:1],
            sparse_embedding={"type": "enabled"},
        ),
    ]

    result = embedder.embed(embedder.prepare_embedding_input(_MULTIMODAL_INPUT), is_query=True)
    assert result.dense_vector == [1.0, 0.0]
    assert result.sparse_vector == {"9": 0.8}
    assert create.call_args_list == expected_requests

    async_result = await embed_compat(embedder, _MULTIMODAL_INPUT, is_query=True)
    assert async_result == result
    assert async_create.await_args_list == expected_requests

    image_result = await embed_compat(embedder, _MULTIMODAL_INPUT[1:], is_query=True)
    assert image_result.dense_vector == [1.0, 0.0]
    assert image_result.sparse_vector is None
    assert async_create.await_args_list[2:] == [
        call(
            model="existing-model",
            extra_headers=embedder.extra_headers,
            input=_MULTIMODAL_INPUT[1:],
        )
    ]

    text_result = embedder.embed("hello", is_query=True)
    assert text_result.dense_vector == [0.0, 1.0]
    assert text_result.sparse_vector == {"9": 0.8}
    assert create.call_args_list[2:] == [
        call(
            model="existing-model",
            extra_headers=embedder.extra_headers,
            input=[{"type": "text", "text": "hello"}],
            sparse_embedding={"type": "enabled"},
        )
    ]


@patch("openviking.models.embedder.volcengine_embedders.volcenginesdkarkruntime.Ark")
@patch("openviking.models.embedder.volcengine_embedders.time.perf_counter")
def test_dense_embedder_passes_local_provider_duration_to_metrics(
    mock_perf_counter, mock_ark, monkeypatch
):
    """Embedding call duration must come from this SDK request, not shared embedder state."""
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=3, total_tokens=3),
        data=[SimpleNamespace(embedding=[1.0, 0.0])],
    )
    client = MagicMock()
    client.embeddings.create.return_value = response
    mock_ark.return_value = client
    monkeypatch.setattr(
        "openviking.models.embedder.volcengine_embedders.get_current_telemetry", MagicMock()
    )
    mock_perf_counter.side_effect = [100.0, 100.25]

    embedder = VolcengineDenseEmbedder(
        model_name="test", api_key="test-key", input_type="text", dimension=2
    )
    embedder.update_token_usage = MagicMock()

    embedder.embed("hello")

    assert embedder.update_token_usage.call_args.kwargs["duration_seconds"] == 0.25
