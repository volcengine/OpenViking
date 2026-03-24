# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Integration tests for GeminiDenseEmbedder — require real GOOGLE_API_KEY.
Run: GOOGLE_API_KEY=<key> pytest tests/integration/test_gemini_embedding_it.py -v
Auto-skipped when GOOGLE_API_KEY is not set. No mocking — real API calls.
"""

import pytest

from tests.integration.conftest import (
    GEMINI_MODELS,
    GOOGLE_API_KEY,
    l2_norm,
    requires_api_key,
)

pytestmark = [requires_api_key]


def test_embed_returns_correct_dimension(gemini_embedder):
    r = gemini_embedder.embed("What is machine learning?")
    assert r.dense_vector and len(r.dense_vector) == 768
    assert 0.99 < l2_norm(r.dense_vector) < 1.01


def test_embed_batch_count(gemini_embedder):
    texts = ["apple", "banana", "cherry", "date", "elderberry"]
    results = gemini_embedder.embed_batch(texts)
    assert len(results) == len(texts)
    for r in results:
        assert r.dense_vector and len(r.dense_vector) == 768


def test_batch_over_100(gemini_embedder):
    """150 texts auto-split into 2 batches (100 + 50)."""
    texts = [f"sentence number {i}" for i in range(150)]
    results = gemini_embedder.embed_batch(texts)
    assert len(results) == 150
    for r in results:
        assert r.dense_vector and len(r.dense_vector) == 768


@pytest.mark.parametrize("model_name,_dim,token_limit", GEMINI_MODELS)
def test_large_text_chunking(model_name, _dim, token_limit):
    """Text exceeding the model's token limit is auto-chunked by base class."""
    from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

    phrase = "Machine learning is a subset of artificial intelligence. "
    large = phrase * ((token_limit * 2) // len(phrase.split()) + 10)
    e = GeminiDenseEmbedder(model_name, api_key=GOOGLE_API_KEY, dimension=768)
    r = e.embed(large)
    assert r.dense_vector and len(r.dense_vector) == 768
    norm = l2_norm(r.dense_vector)
    assert 0.99 < norm < 1.01, f"chunked vector not L2-normalized, norm={norm}"


@pytest.mark.parametrize(
    "task_type",
    [
        "RETRIEVAL_QUERY",
        "RETRIEVAL_DOCUMENT",
        "SEMANTIC_SIMILARITY",
        "CLASSIFICATION",
        "CLUSTERING",
        "CODE_RETRIEVAL_QUERY",
        "QUESTION_ANSWERING",
        "FACT_VERIFICATION",
    ],
)
def test_all_task_types_accepted(task_type):
    """All 8 Gemini task types must be accepted by the API without error."""
    from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

    e = GeminiDenseEmbedder(
        "gemini-embedding-2-preview",
        api_key=GOOGLE_API_KEY,
        task_type=task_type,
        dimension=768,
    )
    r = e.embed("test input for task type validation")
    assert r.dense_vector and len(r.dense_vector) == 768


def test_config_nonsymmetric_routing():
    """Single embedder uses is_query to route query_param/document_param task types."""
    from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig

    cfg = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            model="gemini-embedding-2-preview",
            provider="gemini",
            api_key=GOOGLE_API_KEY,
            dimension=768,
            query_param="RETRIEVAL_QUERY",
            document_param="RETRIEVAL_DOCUMENT",
        )
    )
    embedder = cfg.get_embedder()
    q_result = embedder.embed("search query", is_query=True)
    d_result = embedder.embed("document text", is_query=False)
    assert q_result.dense_vector is not None
    assert d_result.dense_vector is not None


def test_invalid_api_key_error_message():
    """Wrong API key must raise RuntimeError with 'Invalid API key' hint."""
    from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

    _fake_key = "INVALID_KEY_" + "XYZZY_123"
    bad = GeminiDenseEmbedder("gemini-embedding-2-preview", api_key=_fake_key)
    with pytest.raises(RuntimeError, match="Invalid API key"):
        bad.embed("hello")


def test_invalid_model_error_message():
    """Unknown model name must raise RuntimeError with model-not-found hint."""
    from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

    bad = GeminiDenseEmbedder("gemini-embedding-does-not-exist-xyz", api_key=GOOGLE_API_KEY)
    with pytest.raises(RuntimeError, match="Model not found"):
        bad.embed("hello")
