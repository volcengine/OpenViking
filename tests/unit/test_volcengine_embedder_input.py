# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Input-guard tests for Volcengine sparse-capable embedding modes."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
def test_hybrid_embedder_downgrades_multimodal_input_to_text(_mock_ark):
    embedder = VolcengineHybridEmbedder(model_name="test", api_key="test-key")

    assert embedder.supports_multimodal is False
    assert embedder.prepare_embedding_input(_MULTIMODAL_INPUT) == "diagram of a heat exchanger"


@patch("openviking.models.embedder.volcengine_embedders.volcenginesdkarkruntime.Ark")
@patch("openviking.models.embedder.volcengine_embedders.time.perf_counter")
def test_dense_embedder_passes_local_provider_duration_to_metrics(mock_perf_counter, mock_ark):
    """Embedding call duration must come from this SDK request, not shared embedder state."""
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=3, total_tokens=3),
        data=[SimpleNamespace(embedding=[1.0, 0.0])],
    )
    client = MagicMock()
    client.embeddings.create.return_value = response
    mock_ark.return_value = client
    mock_perf_counter.side_effect = [100.0, 100.25]

    embedder = VolcengineDenseEmbedder(
        model_name="test", api_key="test-key", input_type="text", dimension=2
    )
    embedder.update_token_usage = MagicMock()

    embedder.embed("hello")

    assert embedder.update_token_usage.call_args.kwargs["duration_seconds"] == 0.25
