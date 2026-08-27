# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Input-guard tests for Volcengine sparse-capable embedding modes."""

from unittest.mock import patch

from openviking.models.embedder.volcengine_embedders import (
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
