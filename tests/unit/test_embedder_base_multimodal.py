# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for multimodal protocol on EmbedderBase."""
from openviking.core.context import ModalContent, Vectorize
from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult


class _TextOnlyEmbedder(DenseEmbedderBase):
    def embed(self, text: str) -> EmbedResult:
        return EmbedResult(dense_vector=[0.1, 0.2])

    def get_dimension(self) -> int:
        return 2


def test_supports_multimodal_default_false():
    e = _TextOnlyEmbedder("test-model")
    assert e.supports_multimodal is False


def test_embed_multimodal_falls_back_to_text():
    e = _TextOnlyEmbedder("test-model")
    v = Vectorize(
        text="a dog photo",
        media=ModalContent(mime_type="image/jpeg", uri="viking://img.jpg"),
    )
    result = e.embed_multimodal(v)
    assert result.dense_vector == [0.1, 0.2]


def test_embed_multimodal_text_only_vectorize():
    e = _TextOnlyEmbedder("test-model")
    v = Vectorize(text="just text")
    result = e.embed_multimodal(v)
    assert result.dense_vector == [0.1, 0.2]
