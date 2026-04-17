# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Integration tests for DashScopeDenseEmbedder — require real DASHSCOPE_API_KEY.
Run: DASHSCOPE_API_KEY=<key> pytest tests/integration/test_dashscope_embedding_it.py -v
Auto-skipped when DASHSCOPE_API_KEY is not set. No mocking — real API calls.
"""

import os

import pytest

from tests.integration.conftest import l2_norm

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
requires_dashscope = pytest.mark.skipif(not DASHSCOPE_API_KEY, reason="DASHSCOPE_API_KEY not set")

pytestmark = [requires_dashscope]

# ── Model constants ───────────────────────────────────────────────────────────
DASHSCOPE_TEXT_MODEL = "text-embedding-v4"
DASHSCOPE_TEXT_DIM = 1024
DASHSCOPE_MULTIMODAL_MODEL = "tongyi-embedding-vision-flash"
DASHSCOPE_MULTIMODAL_DIM = 768


# ── Fixtures (module-local, not in conftest) ──────────────────────────────────


@pytest.fixture(scope="session")
def dashscope_text_embedder():
    """Session-scoped DashScope text-mode embedder."""
    from openviking.models.embedder.dashscope_embedders import DashScopeDenseEmbedder

    e = DashScopeDenseEmbedder(DASHSCOPE_TEXT_MODEL, api_key=DASHSCOPE_API_KEY, input_type="text")
    yield e
    e.close()


@pytest.fixture(scope="session")
def dashscope_multimodal_embedder():
    """Session-scoped DashScope multimodal-mode embedder."""
    from openviking.models.embedder.dashscope_embedders import DashScopeDenseEmbedder

    e = DashScopeDenseEmbedder(
        DASHSCOPE_MULTIMODAL_MODEL, api_key=DASHSCOPE_API_KEY, input_type="multimodal"
    )
    yield e
    e.close()


# ── Text embedding tests ─────────────────────────────────────────────────────


def test_text_embedding_basic(dashscope_text_embedder):
    """Single Chinese text returns a non-zero vector of correct dimension."""
    r = dashscope_text_embedder.embed("你好世界")
    assert r.dense_vector and len(r.dense_vector) == DASHSCOPE_TEXT_DIM
    assert any(v != 0.0 for v in r.dense_vector)
    assert 0.99 < l2_norm(r.dense_vector) < 1.01


def test_text_embedding_dimension(dashscope_text_embedder):
    """Embedding with a custom dimension=512 produces a 512-dim vector."""
    from openviking.models.embedder.dashscope_embedders import DashScopeDenseEmbedder

    e = DashScopeDenseEmbedder(
        DASHSCOPE_TEXT_MODEL,
        api_key=DASHSCOPE_API_KEY,
        input_type="text",
        dimension=512,
    )
    try:
        r = e.embed("What is machine learning?")
        assert r.dense_vector and len(r.dense_vector) == 512
        assert 0.99 < l2_norm(r.dense_vector) < 1.01
    finally:
        e.close()


def test_text_embedding_batch(dashscope_text_embedder):
    """Batch of 3 texts returns 3 results, all correct dimension."""
    texts = ["苹果是水果", "香蕉也是水果", "樱桃很好吃"]
    results = dashscope_text_embedder.embed_batch(texts)
    assert len(results) == 3
    for r in results:
        assert r.dense_vector and len(r.dense_vector) == DASHSCOPE_TEXT_DIM


# ── Multimodal embedding tests ───────────────────────────────────────────────


def test_multimodal_text_only(dashscope_multimodal_embedder):
    """Multimodal API with text-only input returns correct dimension vector."""
    r = dashscope_multimodal_embedder.embed("这是一段测试文本")
    assert r.dense_vector and len(r.dense_vector) == DASHSCOPE_MULTIMODAL_DIM
    assert 0.99 < l2_norm(r.dense_vector) < 1.01


# ── Error handling ───────────────────────────────────────────────────────────


def test_error_invalid_api_key():
    """Invalid API key must raise RuntimeError."""
    from openviking.models.embedder.dashscope_embedders import DashScopeDenseEmbedder

    bad = DashScopeDenseEmbedder(
        DASHSCOPE_TEXT_MODEL, api_key="INVALID_KEY_XYZ_123", input_type="text"
    )
    with pytest.raises(RuntimeError, match="DashScope embedding failed"):
        bad.embed("hello")


# ── Image + multimodal content tests ─────────────────────────────────────────

IMAGE_URL = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"


def test_multimodal_with_image_url(dashscope_multimodal_embedder):
    """Multimodal API with text + image URL returns correct dimension vector."""
    r = dashscope_multimodal_embedder.embed_content(
        [
            {"text": "一只可爱的猫咪"},
            {"image": IMAGE_URL},
        ]
    )
    assert r.dense_vector and len(r.dense_vector) == DASHSCOPE_MULTIMODAL_DIM
    assert 0.99 < l2_norm(r.dense_vector) < 1.01


def test_multimodal_image_only(dashscope_multimodal_embedder):
    """Multimodal API with image-only input returns a vector."""
    r = dashscope_multimodal_embedder.embed_content(
        [
            {"image": IMAGE_URL},
        ]
    )
    assert r.dense_vector and len(r.dense_vector) == DASHSCOPE_MULTIMODAL_DIM


def test_multimodal_with_fusion(dashscope_multimodal_embedder):
    """Fused embedding with enable_fusion=True returns a vector."""
    from openviking.models.embedder.dashscope_embedders import DashScopeDenseEmbedder

    e = DashScopeDenseEmbedder(
        DASHSCOPE_MULTIMODAL_MODEL,
        api_key=DASHSCOPE_API_KEY,
        input_type="multimodal",
        enable_fusion=True,
    )
    try:
        r = e.embed_content(
            [
                {"text": "描述这张图片的内容"},
                {"image": IMAGE_URL},
            ]
        )
        assert r.dense_vector and len(r.dense_vector) == DASHSCOPE_MULTIMODAL_DIM
    finally:
        e.close()
