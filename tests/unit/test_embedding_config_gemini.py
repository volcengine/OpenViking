# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for Gemini-specific EmbeddingModelConfig and EmbeddingConfig behavior."""

from unittest.mock import patch

import pytest

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def _gcfg(**kw) -> EmbeddingModelConfig:
    """Helper: build a Gemini EmbeddingModelConfig with defaults."""
    return EmbeddingModelConfig(
        model="gemini-embedding-2-preview", provider="gemini", api_key="test-key", **kw
    )


class TestGeminiDimension:
    def test_preview_defaults_3072(self):
        assert _gcfg().get_effective_dimension() == 3072

    def test_001_defaults_3072(self):
        cfg = EmbeddingModelConfig(model="gemini-embedding-001", provider="gemini", api_key="k")
        assert cfg.get_effective_dimension() == 3072

    def test_004_defaults_768(self):
        cfg = EmbeddingModelConfig(model="text-embedding-004", provider="gemini", api_key="k")
        assert cfg.get_effective_dimension() == 768

    def test_unknown_model_defaults_3072(self):
        cfg = EmbeddingModelConfig(model="gemini-embedding-future", provider="gemini", api_key="k")
        assert cfg.get_effective_dimension() == 3072

    def test_explicit_dimension_overrides_default(self):
        assert _gcfg(dimension=1536).get_effective_dimension() == 1536

    def test_text_embedding_prefix_defaults_768(self):
        """text-embedding-* future models default to 768 via prefix rule."""
        cfg = EmbeddingModelConfig(model="text-embedding-005", provider="gemini", api_key="k")
        assert cfg.get_effective_dimension() == 768

    def test_future_gemini_model_defaults_3072(self):
        """Future gemini-embedding-* models default to 3072 via fallback."""
        for model in ["gemini-embedding-2", "gemini-embedding-2.1", "gemini-embedding-3-preview"]:
            cfg = EmbeddingModelConfig(model=model, provider="gemini", api_key="k")
            assert cfg.get_effective_dimension() == 3072


class TestGeminiContextRouting:
    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_nonsymmetric_passes_query_document_params(self, _mock):
        """get_embedder() passes query_param/document_param to GeminiDenseEmbedder."""
        cfg = EmbeddingConfig(
            dense=_gcfg(query_param="RETRIEVAL_QUERY", document_param="RETRIEVAL_DOCUMENT")
        )
        embedder = cfg.get_embedder()
        assert embedder.query_param == "retrieval_query"
        assert embedder.document_param == "retrieval_document"

    @patch("openviking.models.embedder.gemini_embedders.genai.Client")
    def test_only_query_param_set(self, _mock):
        """When only query_param is set, document_param is None."""
        cfg = EmbeddingConfig(dense=_gcfg(query_param="RETRIEVAL_QUERY"))
        embedder = cfg.get_embedder()
        assert embedder.query_param == "retrieval_query"
        assert embedder.document_param is None


class TestGeminiConfigValidation:
    def test_missing_api_key_raises(self):
        with pytest.raises(ValueError, match="api_key"):
            EmbeddingModelConfig(model="gemini-embedding-2-preview", provider="gemini")

    def test_invalid_query_param_raises(self):
        with pytest.raises(ValueError, match="Invalid query_param"):
            _gcfg(query_param="NOT_A_VALID_TYPE")

    def test_invalid_document_param_raises(self):
        with pytest.raises(ValueError, match="Invalid document_param"):
            _gcfg(document_param="ALSO_INVALID")

    def test_query_document_param_case_normalized(self):
        """query_param/document_param are lowercased by the generic normalizer."""
        cfg = _gcfg(query_param="RETRIEVAL_QUERY", document_param="RETRIEVAL_DOCUMENT")
        assert cfg.query_param == "retrieval_query"
        assert cfg.document_param == "retrieval_document"
