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


_INSTRUCTION = "Target_modality: text.\nInstruction: Retrieve the memory notes relevant to the question\nQuery:"


def test_dense_embedder_prefixes_text_queries_only():
    embedder = VolcengineDenseEmbedder(
        model_name="test", api_key="test-key", dimension=2, query_param=_INSTRUCTION
    )
    sent = []

    def create(input, model, extra_headers):
        sent.append(input)
        return SimpleNamespace(data=SimpleNamespace(embedding=[1.0, 0.0]), usage=None)

    embedder.client = SimpleNamespace(multimodal_embeddings=SimpleNamespace(create=create))
    image = [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}]

    embedder.embed("Which plan did we pick?", is_query=True)
    embedder.embed("document text")
    embedder.embed(image, is_query=True)

    assert sent == [
        [{"type": "text", "text": _INSTRUCTION + "Which plan did we pick?"}],
        [{"type": "text", "text": "document text"}],
        image,
    ]


@patch("openviking.models.embedder.volcengine_embedders.volcenginesdkarkruntime.Ark")
def test_embedding_config_passes_volcengine_query_param_unchanged(_mock_ark):
    from openviking_cli.utils.config.embedding_config import EmbeddingConfig

    config = EmbeddingConfig(
        dense={
            "provider": "volcengine",
            "model": "test",
            "api_key": "test-key",
            "dimension": 2,
            "query_param": _INSTRUCTION,
        }
    )

    assert config.get_embedder().query_param == _INSTRUCTION
