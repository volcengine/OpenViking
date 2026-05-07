# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for LocalBM25Embedder."""

import json
import math
import tempfile
from pathlib import Path

import pytest

from openviking.models.embedder.local_bm25_embedder import (
    DEFAULT_B,
    DEFAULT_K1,
    BM25Stats,
    LocalBM25Embedder,
    _hash_token,
    _tokenize,
)


class TestTokenize:
    def test_basic_english(self):
        tokens = _tokenize("Hello World")
        assert tokens == ["hello", "world"]

    def test_mixed_case(self):
        tokens = _tokenize("OpenViking BM25")
        assert tokens == ["openviking", "bm25"]

    def test_punctuation_stripped(self):
        tokens = _tokenize("hello, world! foo-bar")
        assert tokens == ["hello", "world", "foo", "bar"]

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_unicode_words(self):
        tokens = _tokenize("café naïve")
        assert "caf" in tokens[0] or tokens == ["café", "naïve"]

    def test_cjk_characters(self):
        tokens = _tokenize("信息检索系统")
        assert len(tokens) >= 1

    def test_numbers_included(self):
        tokens = _tokenize("version 3 release")
        assert "3" in tokens


class TestHashToken:
    def test_deterministic(self):
        assert _hash_token("hello") == _hash_token("hello")

    def test_different_tokens_different_hashes(self):
        assert _hash_token("hello") != _hash_token("world")

    def test_returns_uint32(self):
        h = _hash_token("test")
        assert 0 <= h <= 0xFFFFFFFF


class TestBM25Stats:
    def test_initial_state(self):
        stats = BM25Stats()
        assert stats.doc_count == 0
        assert stats.total_tokens == 0
        assert stats.avgdl == 1.0

    def test_add_document(self):
        stats = BM25Stats()
        hashes = [_hash_token("hello"), _hash_token("world")]
        stats.add_document(hashes, 2)
        assert stats.doc_count == 1
        assert stats.total_tokens == 2
        assert stats.avgdl == 2.0

    def test_term_doc_freq_counts_unique(self):
        stats = BM25Stats()
        h = _hash_token("hello")
        stats.add_document([h, h, h], 3)
        assert stats.term_doc_freq[h] == 1

    def test_save_and_load(self, tmp_path):
        stats = BM25Stats()
        stats.add_document([_hash_token("a"), _hash_token("b")], 2)
        stats.add_document([_hash_token("a"), _hash_token("c")], 2)

        path = tmp_path / "stats.json"
        stats.save(path)

        loaded = BM25Stats()
        loaded.load(path)
        assert loaded.doc_count == 2
        assert loaded.total_tokens == 4
        assert loaded.term_doc_freq[_hash_token("a")] == 2
        assert loaded.term_doc_freq[_hash_token("b")] == 1

    def test_load_missing_file(self, tmp_path):
        stats = BM25Stats()
        stats.load(tmp_path / "nonexistent.json")
        assert stats.doc_count == 0


class TestLocalBM25Embedder:
    def test_embed_document_returns_sparse(self):
        embedder = LocalBM25Embedder()
        result = embedder.embed("hello world", is_query=False)
        assert result.sparse_vector is not None
        assert result.dense_vector is None
        assert len(result.sparse_vector) == 2

    def test_embed_query_empty_corpus(self):
        embedder = LocalBM25Embedder()
        result = embedder.embed("hello", is_query=True)
        assert result.sparse_vector == {}

    def test_embed_query_after_docs(self):
        embedder = LocalBM25Embedder()
        embedder.embed("hello world", is_query=False)
        embedder.embed("hello foo", is_query=False)

        result = embedder.embed("hello", is_query=True)
        assert result.sparse_vector is not None
        assert len(result.sparse_vector) > 0

        h_hello = str(_hash_token("hello"))
        assert h_hello in result.sparse_vector
        assert result.sparse_vector[h_hello] > 0

    def test_idf_rare_term_higher_weight(self):
        embedder = LocalBM25Embedder()
        embedder.embed("common rare_xyz", is_query=False)
        embedder.embed("common another", is_query=False)
        embedder.embed("common third", is_query=False)

        result = embedder.embed("common rare_xyz", is_query=True)
        h_common = str(_hash_token("common"))
        h_rare = str(_hash_token("rare_xyz"))

        assert result.sparse_vector[h_rare] > result.sparse_vector[h_common]

    def test_dot_product_ranking(self):
        """Verify that dot product of query x doc vectors produces correct BM25 ranking."""
        embedder = LocalBM25Embedder()
        doc_a = embedder.embed("openviking memory provider", is_query=False)
        doc_b = embedder.embed("hermes model provider", is_query=False)

        query = embedder.embed("openviking", is_query=True)

        def dot_product(q, d):
            score = 0.0
            for k, v in q.items():
                if k in d:
                    score += v * d[k]
            return score

        score_a = dot_product(query.sparse_vector, doc_a.sparse_vector)
        score_b = dot_product(query.sparse_vector, doc_b.sparse_vector)

        assert score_a > score_b, f"Doc A ({score_a}) should rank higher than Doc B ({score_b})"

    def test_empty_text(self):
        embedder = LocalBM25Embedder()
        result = embedder.embed("", is_query=False)
        assert result.sparse_vector == {}

    def test_persistence(self, tmp_path):
        stats_path = tmp_path / "bm25_stats.json"

        embedder1 = LocalBM25Embedder(stats_path=str(stats_path))
        embedder1.embed("hello world test", is_query=False)
        embedder1.close()

        embedder2 = LocalBM25Embedder(stats_path=str(stats_path))
        assert embedder2.stats.doc_count == 1
        assert embedder2.stats.total_tokens == 3

    def test_is_sparse_property(self):
        embedder = LocalBM25Embedder()
        assert embedder.is_sparse is True

    def test_custom_k1_b(self):
        embedder = LocalBM25Embedder(k1=2.0, b=0.5)
        result = embedder.embed("test document here", is_query=False)
        assert result.sparse_vector is not None
        assert len(result.sparse_vector) == 3


class TestConfigIntegration:
    def test_local_bm25_provider_validation(self):
        from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig

        config = EmbeddingModelConfig(provider="local_bm25", model="bm25")
        assert config.provider == "local_bm25"

    def test_local_bm25_model_defaults(self):
        from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig

        config = EmbeddingModelConfig(provider="local_bm25")
        assert config.model == "bm25"

    def test_embedding_config_composite(self):
        from openviking_cli.utils.config.embedding_config import EmbeddingConfig

        config = EmbeddingConfig(
            dense={"provider": "ollama", "model": "qwen3-embedding:0.6b", "dimension": 1024},
            sparse={"provider": "local_bm25"},
        )
        assert config.sparse.provider == "local_bm25"
        assert config.dense.provider == "ollama"

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("llama_cpp"),
        reason="llama_cpp not installed",
    )
    def test_factory_creates_composite(self):
        from openviking.models.embedder.base import CompositeHybridEmbedder
        from openviking_cli.utils.config.embedding_config import EmbeddingConfig

        config = EmbeddingConfig(
            dense={"provider": "local", "model": "bge-small-zh-v1.5-f16"},
            sparse={"provider": "local_bm25"},
        )
        embedder = config.get_embedder()
        assert isinstance(embedder, CompositeHybridEmbedder)


class TestDenseOnlyRegression:
    """Ensure dense-only configs still work after adding local_bm25."""

    def test_dense_only_still_works(self):
        from openviking_cli.utils.config.embedding_config import EmbeddingConfig

        config = EmbeddingConfig(
            dense={"provider": "ollama", "model": "qwen3-embedding:0.6b", "dimension": 1024},
        )
        assert config.dense.provider == "ollama"
        assert config.sparse is None
