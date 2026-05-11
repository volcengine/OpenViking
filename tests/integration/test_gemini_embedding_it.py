# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
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


# ── Multimodal integration tests ────────────────────────────────────────────
# Shape mirrors tests/integration/test_dashscope_embedding_it.py from PR #1535.
# Uses bytes (PIL-generated tiny PNG) rather than URLs because Gemini's URL
# fetcher is restrictive in practice; bytes path is the reliable test.

GEMINI_MULTIMODAL_MODEL = "gemini-embedding-2"
GEMINI_MULTIMODAL_DIM = 768


@pytest.fixture(scope="session")
def gemini_multimodal_embedder():
    """Session-scoped multimodal-mode GeminiDenseEmbedder."""
    from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

    return GeminiDenseEmbedder(
        GEMINI_MULTIMODAL_MODEL,
        api_key=GOOGLE_API_KEY,
        input_type="multimodal",
        dimension=GEMINI_MULTIMODAL_DIM,
    )


@pytest.fixture(scope="session")
def tiny_png_bytes():
    """Generate a 32x32 solid-color PNG inline. PIL is a transitive dep
    via pdfplumber, so it's already available."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(120, 200, 80)).save(buf, format="PNG")
    return buf.getvalue()


def test_multimodal_embed_text_only(gemini_multimodal_embedder):
    """Multimodal mode handles a text-only content list."""
    r = gemini_multimodal_embedder.embed_content([{"text": "machine learning"}])
    assert r.dense_vector and len(r.dense_vector) == GEMINI_MULTIMODAL_DIM
    assert 0.99 < l2_norm(r.dense_vector) < 1.01


def test_multimodal_embed_text_plus_image_bytes(gemini_multimodal_embedder, tiny_png_bytes):
    """Multimodal mode aggregates text + image bytes into one fused embedding."""
    r = gemini_multimodal_embedder.embed_content(
        [
            {"text": "describe this image"},
            {"image": tiny_png_bytes, "mime_type": "image/png"},
        ]
    )
    assert r.dense_vector and len(r.dense_vector) == GEMINI_MULTIMODAL_DIM


def test_multimodal_embed_async(gemini_multimodal_embedder):
    """Async embed_content_async produces the same shape as sync."""
    import asyncio

    r = asyncio.run(
        gemini_multimodal_embedder.embed_content_async([{"text": "async multimodal"}])
    )
    assert r.dense_vector and len(r.dense_vector) == GEMINI_MULTIMODAL_DIM


def test_multimodal_count_tokens_telemetry(gemini_multimodal_embedder):
    """count_tokens fires after a successful embed_content; /metrics
    integration shows non-zero prompt_tokens for the call."""
    e = gemini_multimodal_embedder
    before = e.get_token_usage()["total_usage"]["prompt_tokens"]
    e.embed_content([{"text": "telemetry sanity check"}])
    after = e.get_token_usage()["total_usage"]["prompt_tokens"]
    assert after > before, "count_tokens telemetry must increment prompt_tokens"


def test_multimodal_rejects_unsupported_mime():
    """.gif extension must be rejected before any API call."""
    from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

    e = GeminiDenseEmbedder(
        GEMINI_MULTIMODAL_MODEL,
        api_key=GOOGLE_API_KEY,
        input_type="multimodal",
        dimension=GEMINI_MULTIMODAL_DIM,
    )
    with pytest.raises(ValueError, match="Unsupported file extension"):
        e.embed_content([{"image": "https://example.com/photo.gif"}])


def test_multimodal_ssrf_guard_rejects_imds():
    """169.254.169.254 (AWS IMDS) is rejected by the SSRF guard before
    any API call — the guard runs locally on every URL."""
    from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

    e = GeminiDenseEmbedder(
        GEMINI_MULTIMODAL_MODEL,
        api_key=GOOGLE_API_KEY,
        input_type="multimodal",
        dimension=GEMINI_MULTIMODAL_DIM,
    )
    with pytest.raises(ValueError, match="SSRF guard"):
        e.embed_content(
            [{"image": "http://169.254.169.254/latest/meta-data/iam.png"}]
        )
