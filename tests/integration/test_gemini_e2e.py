# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
End-to-end integration tests for GeminiDenseEmbedder.
Calls the real Gemini API — requires GEMINI_API_KEY env var.
Run: pytest tests/integration/test_gemini_e2e.py -v -m integration
"""
import math
import os
import struct
import zlib

import pytest

from openviking.core.context import ModalContent, Vectorize
from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

_API_KEY = os.getenv("GEMINI_API_KEY")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _API_KEY, reason="GEMINI_API_KEY not set"),
]


def _make_tiny_png() -> bytes:
    """Create a minimal valid 1x1 white PNG (89 bytes)."""

    def chunk(name, data):
        c = struct.pack(">I", len(data)) + name + data
        return c + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


@pytest.fixture(scope="module")
def embedder():
    e = GeminiDenseEmbedder(
        "gemini-embedding-2-preview",
        api_key=_API_KEY,
        # dimension defaults to 3072 — test the actual default
        task_type="RETRIEVAL_DOCUMENT",
    )
    yield e
    e.close()


def test_default_dimension_is_3072(embedder):
    """Default output dimension must match model's native 3072."""
    assert embedder.get_dimension() == 3072
    result = embedder.embed("hello")
    assert len(result.dense_vector) == 3072


class TestGeminiE2ETextEmbedding:
    def test_embed_text_returns_correct_dimension(self, embedder):
        result = embedder.embed("OpenViking is a knowledge management system")
        assert result.dense_vector is not None
        assert len(result.dense_vector) == 3072

    def test_embed_text_vector_is_normalized(self, embedder):
        result = embedder.embed("test normalization")
        norm = math.sqrt(sum(x * x for x in result.dense_vector))
        assert abs(norm - 1.0) < 0.01, f"Vector norm {norm} not close to 1.0"

    def test_embed_batch_matches_individual(self, embedder):
        texts = ["hello world", "foo bar", "test embed"]
        batch_results = embedder.embed_batch(texts)
        individual_results = [embedder.embed(t) for t in texts]
        assert len(batch_results) == 3
        for br, ir in zip(batch_results, individual_results):
            sim = _cosine_similarity(br.dense_vector, ir.dense_vector)
            assert sim > 0.99, f"Batch vs individual similarity {sim} too low"

    def test_semantic_similarity_related_texts(self, embedder):
        r1 = embedder.embed("a golden retriever playing in the park")
        r2 = embedder.embed("a dog running outside in a field")
        r3 = embedder.embed("quantum computing and cryptography")
        sim_related = _cosine_similarity(r1.dense_vector, r2.dense_vector)
        sim_unrelated = _cosine_similarity(r1.dense_vector, r3.dense_vector)
        assert sim_related > sim_unrelated


class TestGeminiE2EMultimodalEmbedding:
    @pytest.mark.xfail(reason="gemini-embedding-2-preview may not support multimodal on free tier")
    def test_embed_multimodal_image_returns_correct_dimension(self, embedder):
        v = Vectorize(
            text="a tiny white pixel",
            media=ModalContent(mime_type="image/png", uri="test.png", data=_make_tiny_png()),
        )
        result = embedder.embed_multimodal(v)
        assert result.dense_vector is not None
        assert len(result.dense_vector) == 3072

    def test_multimodal_fallback_on_no_media(self, embedder):
        v = Vectorize(text="just text, no image")
        result = embedder.embed_multimodal(v)
        text_result = embedder.embed("just text, no image")
        sim = _cosine_similarity(result.dense_vector, text_result.dense_vector)
        assert sim > 0.99, f"Fallback similarity {sim:.3f} too low"


class TestGeminiE2EAsyncBatch:
    @pytest.mark.anyio
    async def test_async_embed_batch_concurrent(self):
        try:
            import anyio  # noqa: F401
        except ImportError:
            pytest.skip("anyio not installed")
        import time
        embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview", api_key=_API_KEY, dimension=128
        )
        texts = [f"sentence {i}" for i in range(300)]  # 3 batches of 100
        t0 = time.monotonic()
        results = await embedder.async_embed_batch(texts)
        elapsed = time.monotonic() - t0
        assert len(results) == 300
        assert all(len(r.dense_vector) == 128 for r in results)
        assert elapsed < 15  # concurrent should be << 3× serial RTT
        embedder.close()


class TestGeminiE2ETaskType:
    def test_query_vs_document_task_types(self):
        doc_embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview",
            api_key=_API_KEY,
            task_type="RETRIEVAL_DOCUMENT",
        )
        query_embedder = GeminiDenseEmbedder(
            "gemini-embedding-2-preview",
            api_key=_API_KEY,
            task_type="RETRIEVAL_QUERY",
        )
        text = "machine learning algorithms"
        doc_result = doc_embedder.embed(text)
        query_result = query_embedder.embed(text)
        sim = _cosine_similarity(doc_result.dense_vector, query_result.dense_vector)
        # gemini-embedding-2-preview may return identical vectors for same text
        # across task types; assert vectors are at least highly correlated
        assert sim > 0.8, f"Task type similarity {sim:.3f} unexpectedly low"
        doc_embedder.close()
        query_embedder.close()
